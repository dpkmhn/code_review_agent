import os
import asyncio
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain_core.messages import AIMessage

from app.graph import nodes
from app.graph.review_graph import build_review_graph
from app.main import app
from app.persistence.database import checkpoint_serializer, database_pool, initialize_database
from app.persistence.store import ReviewBusyError, ReviewNotFoundError, ReviewStore
from app.schemas.context import ContextRequest
from app.schemas.github import PullRequestData
from app.schemas.hypothesis import HypothesisResult
from app.schemas.persistence import StoredReview
from app.services.persistent_review import PersistentReviewRunner
from app.services.sandbox_service import ExecutionResult, SandboxService
import test_review_graph


def pull_request():
    return PullRequestData(owner="owner", repository="repo", number=1, title="Test",
                           body=None, base_branch="main", head_branch="fix",
                           base_sha="base", head_sha="head", files=[])


class MemoryStore:
    """Test-only projection storage; production always uses PostgreSQL."""
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.review = StoredReview(review_id=uuid4(), status="pending", pull_request=pull_request(),
                                   created_at=now, updated_at=now)
        self.projections = []

    @asynccontextmanager
    async def lock(self, review_id):
        yield

    async def get(self, review_id):
        return self.review

    async def status(self, review_id, status, error=None):
        self.review.status = status
        self.review.error = error

    async def project(self, review_id, state, complete=False):
        self.projections.append(state)
        self.review.findings = state.get("findings", [])
        self.review.tool_calls = state.get("tool_records", [])
        self.review.status = "reviewed" if complete else "running"
        self.review.error = None


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_resume_and_completed_retry_do_not_restart_graph(self):
        saver = InMemorySaver(serde=checkpoint_serializer())
        store = MemoryStore()
        review_id = store.review.review_id
        context = AsyncMock(return_value=ContextRequest(needs_context=False, reason="Self-contained"))
        hypotheses = AsyncMock(side_effect=[RuntimeError("secret-key"), HypothesisResult(hypotheses=[])])
        with patch.object(nodes.context_service, "analyse_context_need", context), patch.object(
            nodes.review_service, "form_hypotheses", hypotheses,
        ):
            runner = PersistentReviewRunner(store, build_review_graph(saver))
            with self.assertRaises(RuntimeError):
                await runner.run(review_id)
            self.assertEqual(store.review.status, "failed")
            self.assertNotIn("secret-key", store.review.error)
            # Recompile to simulate a new worker. The checkpoint supplies typed state.
            runner = PersistentReviewRunner(store, build_review_graph(saver))
            result = await runner.run(review_id)
            self.assertEqual(result.status, "reviewed")
            context.assert_awaited_once()
            self.assertEqual(hypotheses.await_count, 2)
            await runner.run(review_id)
            self.assertEqual(hypotheses.await_count, 2)

    async def test_final_checkpoint_recovers_projection_without_model_call(self):
        saver = InMemorySaver(serde=checkpoint_serializer())
        graph = build_review_graph(saver)
        store = MemoryStore()
        config = {"configurable": {"thread_id": str(store.review.review_id)}}
        with patch.object(nodes.context_service, "analyse_context_need", AsyncMock(
            return_value=ContextRequest(needs_context=False, reason="Self-contained"),
        )), patch.object(nodes.review_service, "form_hypotheses", AsyncMock(
            return_value=HypothesisResult(hypotheses=[]),
        )):
            await graph.ainvoke({"pull_request": store.review.pull_request}, config, durability="sync")
        store.review.status = "running"
        runner = PersistentReviewRunner(store, build_review_graph(saver))
        result = await runner.run(store.review.review_id)
        self.assertEqual(result.status, "reviewed")
        self.assertEqual(len(store.projections), 1)


class PersistenceAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous = getattr(app.state, "review_runner", None)
        self.store = MemoryStore()
        app.state.review_runner = PersistentReviewRunner(self.store, None)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        app.state.review_runner = self.previous

    async def test_lookup_unknown_and_invalid_ids(self):
        review_id = self.store.review.review_id
        response = await self.client.get(f"/api/v1/reviews/{review_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review_id"], str(review_id))
        with patch.object(self.store, "get", AsyncMock(side_effect=ReviewNotFoundError)):
            response = await self.client.get(f"/api/v1/reviews/{uuid4()}")
            self.assertEqual(response.status_code, 404)
        self.assertEqual((await self.client.get("/api/v1/reviews/invalid")).status_code, 422)

    async def test_disabled_persistence_and_conflicting_resume(self):
        review_id = self.store.review.review_id
        with patch.object(app.state.review_runner, "run", AsyncMock(side_effect=ReviewBusyError)):
            response = await self.client.post(f"/api/v1/reviews/{review_id}/resume")
            self.assertEqual(response.status_code, 409)
        app.state.review_runner = None
        self.assertEqual((await self.client.get(f"/api/v1/reviews/{review_id}")).status_code, 503)

    async def test_failure_returns_recoverable_id_without_exception_text(self):
        review_id = self.store.review.review_id
        with patch.object(app.state.review_runner, "run", AsyncMock(side_effect=RuntimeError("secret"))):
            response = await self.client.post(f"/api/v1/reviews/{review_id}/resume")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["review_id"], str(review_id))
        self.assertEqual(response.headers["Location"], f"/api/v1/reviews/{review_id}")
        self.assertNotIn("secret", response.text)

    async def test_create_returns_id_and_preserves_ingestion_error_mapping(self):
        review_id = self.store.review.review_id
        app.state.review_runner.store.create = AsyncMock(return_value=review_id)
        app.state.review_runner.run = AsyncMock(return_value=self.store.review)
        from app.api.reviews import review_service
        with patch.object(review_service, "ingest_pull_request", AsyncMock(return_value=pull_request())):
            response = await self.client.post("/api/v1/reviews", json={
                "repository": "owner/repo", "pull_request_number": 1,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review_id"], str(review_id))
        self.assertEqual(response.headers["Location"], f"/api/v1/reviews/{review_id}")
        with patch.object(review_service, "ingest_pull_request", AsyncMock(side_effect=ValueError("Malformed repository"))):
            response = await self.client.post("/api/v1/reviews", json={
                "repository": "owner/repo", "pull_request_number": 1,
            })
        self.assertEqual(response.status_code, 400)
        app.state.review_runner.store.create.assert_awaited_once()


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a disposable PostgreSQL database.")
class PostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    loop_factory = staticmethod(asyncio.SelectorEventLoop) if sys.platform == "win32" else None

    async def test_evidence_findings_and_execution_survive_recovery(self):
        fixture = test_review_graph.ReviewGraphTests()
        fixture.setUp()
        context = AsyncMock(return_value=ContextRequest(needs_context=False, reason="Self-contained"))
        hypotheses = AsyncMock(return_value=HypothesisResult(hypotheses=[fixture.hypothesis()]))
        evaluation = AsyncMock(side_effect=[RuntimeError("interruption"), fixture.evaluation()])
        reviewer = AsyncMock(side_effect=[AIMessage(content="", tool_calls=[
            {"id": "read", "name": "read_file", "args": {"path": "helper.py", "revision": "head"}},
            {"id": "test", "name": "run_test", "args": {"path": "test_helper.py", "revision": "head"}},
        ]), AIMessage(content="Enough evidence")])
        read = AsyncMock(return_value="return value")
        execute = AsyncMock(return_value=ExecutionResult(output="1 failed", exit_code=1, completed=True))
        url = os.environ["TEST_DATABASE_URL"]
        with patch.object(nodes.context_service, "analyse_context_need", context), patch.object(
            nodes.review_service, "form_hypotheses", hypotheses,
        ), patch.object(nodes.review_service, "request_verification_tools", reviewer), patch.object(
            nodes.review_service, "evaluate_evidence", evaluation,
        ), patch.object(nodes.github_client, "read_file", read), patch.object(SandboxService, "execute", execute):
            async with database_pool(url) as pool:
                await initialize_database(pool)
                store = ReviewStore(pool)
                review_id = await store.create(fixture.pull_request)
                runner = PersistentReviewRunner(store, build_review_graph(AsyncPostgresSaver(pool, serde=checkpoint_serializer())))
                with self.assertRaises(RuntimeError):
                    await runner.run(review_id)
                failed = await store.get(review_id)
                self.assertEqual(len(failed.tool_calls), 2)
                self.assertTrue(all(call.status == "completed" for call in failed.tool_calls))
            async with database_pool(url) as pool:
                store = ReviewStore(pool)
                runner = PersistentReviewRunner(store, build_review_graph(AsyncPostgresSaver(pool, serde=checkpoint_serializer())))
                result = await runner.run(review_id)
                self.assertEqual(result.status, "reviewed")
                self.assertEqual(len(result.findings), 1)
                self.assertEqual(len(result.tool_calls), 2)
                self.assertEqual(result.tool_calls[1].evidence.exit_code, 1)
                read.assert_awaited_once()
                execute.assert_awaited_once()
                context.assert_awaited_once()
                hypotheses.assert_awaited_once()
                async with pool.connection() as conn:
                    cursor = await conn.execute("SELECT payload FROM executions WHERE review_id = %s", (review_id,))
                    rows = await cursor.fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["payload"]["exit_code"], 1)
                    self.assertFalse(rows[0]["payload"]["timed_out"])

    async def test_database_checkpoint_recovery_and_lock(self):
        url = os.environ["TEST_DATABASE_URL"]
        async with database_pool(url) as pool:
            await initialize_database(pool)
            await initialize_database(pool)
            store = ReviewStore(pool)
            review_id = await store.create(pull_request())
            context = AsyncMock(return_value=ContextRequest(needs_context=False, reason="Self-contained"))
            hypotheses = AsyncMock(side_effect=[RuntimeError("test failure"), HypothesisResult(hypotheses=[])])
            with patch.object(nodes.context_service, "analyse_context_need", context), patch.object(
                nodes.review_service, "form_hypotheses", hypotheses,
            ):
                runner = PersistentReviewRunner(store, build_review_graph(AsyncPostgresSaver(pool, serde=checkpoint_serializer())))
                with self.assertRaises(RuntimeError):
                    await runner.run(review_id)
                self.assertEqual((await store.get(review_id)).status, "failed")
                async with store.lock(review_id):
                    with self.assertRaises(ReviewBusyError):
                        await runner.run(review_id)
                # Close all connections, then load from a completely new pool/saver.
        async with database_pool(url) as pool:
            store = ReviewStore(pool)
            runner = PersistentReviewRunner(store, build_review_graph(AsyncPostgresSaver(pool, serde=checkpoint_serializer())))
            with patch.object(nodes.context_service, "analyse_context_need", context), patch.object(
                nodes.review_service, "form_hypotheses", hypotheses,
            ):
                result = await runner.run(review_id)
                self.assertEqual(result.status, "reviewed")
                context.assert_awaited_once()
                self.assertEqual(hypotheses.await_count, 2)
                await runner.run(review_id)
                self.assertEqual(hypotheses.await_count, 2)


if __name__ == "__main__":
    unittest.main()
