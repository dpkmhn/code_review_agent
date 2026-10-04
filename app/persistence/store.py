"""Review projections and a cross-worker lock for checkpoint resume."""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas.persistence import StoredReview


class ReviewBusyError(Exception):
    pass


class ReviewNotFoundError(Exception):
    pass


class ReviewStore:
    def __init__(self, pool):
        self.pool = pool

    async def create(self, pull_request) -> UUID:
        review_id = uuid4()
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO review_runs (id, status, pull_request) VALUES (%s, 'pending', %s)",
                (review_id, Jsonb(pull_request.model_dump(mode="json"))),
            )
        return review_id

    @asynccontextmanager
    async def lock(self, review_id):
        # Session locks are released by PostgreSQL on worker death. Keep this
        # dedicated connection until graph execution and projection commits end.
        # Using the query pool here could exhaust it with waiting review workers.
        async with await AsyncConnection.connect(
            self.pool.conninfo, autocommit=True, row_factory=dict_row,
        ) as conn:
            cursor = await conn.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired",
                (str(review_id),),
            )
            if not (await cursor.fetchone())["acquired"]:
                raise ReviewBusyError()
            yield  # Closing this connection releases the lock, including on cancellation.

    async def get(self, review_id: UUID) -> StoredReview:
        async with self.pool.connection() as conn:
            # A single snapshot prevents mixed status/findings while another worker commits.
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor = await conn.execute("SELECT * FROM review_runs WHERE id = %s", (review_id,))
                row = await cursor.fetchone()
                if row is None:
                    raise ReviewNotFoundError()
                cursor = await conn.execute(
                    "SELECT payload FROM findings WHERE review_id = %s ORDER BY ordinal", (review_id,),
                )
                findings = [item["payload"] for item in await cursor.fetchall()]
                cursor = await conn.execute(
                    "SELECT payload FROM tool_calls WHERE review_id = %s ORDER BY ordinal", (review_id,),
                )
                calls = [item["payload"] for item in await cursor.fetchall()]
        return StoredReview(review_id=row["id"], status=row["status"], pull_request=row["pull_request"],
                            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
                            findings=findings, tool_calls=calls)

    async def status(self, review_id, status, error=None):
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE review_runs SET status = %s, error = %s, updated_at = now() WHERE id = %s",
                (status, error, review_id),
            )

    async def project(self, review_id, state, *, complete=False):
        """Idempotently mirror checkpoint accumulators; completion is atomic with findings."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                for ordinal, finding in enumerate(state.get("findings", [])):
                    await conn.execute(
                        "INSERT INTO findings VALUES (%s, %s, %s) ON CONFLICT (review_id, ordinal) "
                        "DO UPDATE SET payload = EXCLUDED.payload",
                        (review_id, ordinal, Jsonb(finding.model_dump(mode="json"))),
                    )
                for ordinal, call in enumerate(state.get("tool_records", [])):
                    await conn.execute(
                        "INSERT INTO tool_calls VALUES (%s, %s, %s, %s) ON CONFLICT (review_id, call_id) "
                        "DO UPDATE SET payload = EXCLUDED.payload, ordinal = EXCLUDED.ordinal",
                        (review_id, call.call_id, ordinal, Jsonb(call.model_dump(mode="json"))),
                    )
                    if call.name in {"run_test", "run_static_analysis"} and call.evidence is not None:
                        await conn.execute(
                            "INSERT INTO executions VALUES (%s, %s, %s) ON CONFLICT (review_id, call_id) "
                            "DO UPDATE SET payload = EXCLUDED.payload",
                            (review_id, call.call_id, Jsonb(call.evidence.model_dump(mode="json"))),
                        )
                await conn.execute(
                    "UPDATE review_runs SET updated_at = now(), status = %s, error = NULL WHERE id = %s",
                    ("reviewed" if complete else "running", review_id),
                )
