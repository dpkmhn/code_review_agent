"""PostgreSQL resources owned by the FastAPI lifespan, never at import time."""

from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import sys

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def postgres_loop_factory():
    """Uvicorn loop factory compatible with psycopg on Windows."""
    return asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()


def checkpoint_serializer():
    # Only trusted application model constructors may be restored from storage.
    models = {
        "app.schemas.github": ["PullRequestData", "PullRequestFile"],
        "app.schemas.ai_review": ["ReviewResult", "ReviewFinding"],
        "app.schemas.context": ["ContextRequest"],
        "app.schemas.hypothesis": ["BugHypothesis", "VerificationStep", "VerificationEvidence", "EvidenceEvaluation"],
        "app.schemas.persistence": ["ToolCallRecord"],
    }
    return JsonPlusSerializer(allowed_msgpack_modules=[
        (module, name) for module, names in models.items() for name in names
    ])


@asynccontextmanager
async def database_pool(url: str):
    if sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
        raise RuntimeError("PostgreSQL on Windows requires the launcher: python -m app.scripts.run_api")
    pool = AsyncConnectionPool(
        url, open=False, min_size=1, max_size=10, timeout=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        await pool.open(wait=True)
        yield pool
    finally:
        await pool.close()


async def initialize_database(pool):
    """Run once before starting workers; the schema and saver setup are idempotent."""
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(Path(__file__).with_name("schema.sql").read_text(), prepare=False)
    await AsyncPostgresSaver(pool, serde=checkpoint_serializer()).setup()
