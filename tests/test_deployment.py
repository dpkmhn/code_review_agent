import asyncio
import io
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import SecretStr, ValidationError

from app.config import Settings, settings
from app.main import app
from app.sandbox_worker import app as worker, WorkerSettings
from app.services.sandbox_runtime import ContainerRunner, ExecutionResult
from app.services.sandbox_service import SandboxService


TOKEN = "a" * 64


def snapshot(name="root/tests/test_a.py"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, "assert True")
    return stream.getvalue()


class ConfigurationTests(unittest.TestCase):
    def test_production_fails_closed(self):
        config = dict(github_token="test", openai_api_key="test", environment="production",
                      _env_file=None, api_access_token=TOKEN, public_origin="https://reviews.example.com",
                      database_url="postgresql://test/test")
        Settings(**config)
        for key, value in (("api_access_token", None), ("api_access_token", "short"),
                           ("database_url", None), ("public_origin", "http://example.com"),
                           ("public_origin", "https://example.com/path")):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                Settings(**(config | {key: value}))
        with self.assertRaises(ValidationError):
            Settings(**(config | {"sandbox_enabled": True}))
        with self.assertRaises(ValidationError):
            WorkerSettings(worker_token="short", _env_file=None)


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_review_routes_require_auth_before_work(self):
        with patch.object(settings, "api_access_token", SecretStr(TOKEN)), patch(
            "app.api.reviews.review_service.ingest_pull_request", AsyncMock()
        ) as ingest:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
                for method, path in (("POST", "/api/v1/reviews"),
                                     ("GET", "/api/v1/reviews/00000000-0000-0000-0000-000000000001"),
                                     ("POST", "/api/v1/reviews/00000000-0000-0000-0000-000000000001/resume")):
                    response = await client.request(method, path, json={"repository": "o/r", "pull_request_number": 1})
                    self.assertEqual(response.status_code, 401)
                self.assertEqual((await client.get("/health")).status_code, 200)
            ingest.assert_not_awaited()

    async def test_cross_site_submission_rejected_and_authorized_lookup_allowed(self):
        app.state.review_runner = None
        with patch.object(settings, "api_access_token", SecretStr(TOKEN)), patch.object(
            settings, "public_origin", "https://reviews.example.com"
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api",
                                        headers={"Authorization": "Bearer " + TOKEN}) as client:
                response = await client.post("/api/v1/reviews", headers={"Origin": "https://evil.example"},
                                             json={"repository": "o/r", "pull_request_number": 1})
                self.assertEqual(response.status_code, 403)
                response = await client.get("/api/v1/reviews/00000000-0000-0000-0000-000000000001")
                self.assertEqual(response.status_code, 503)  # Passed auth; persistence absent.

    async def test_readiness_reflects_database_failure(self):
        with patch.object(settings, "database_url", SecretStr("postgresql://test/test")):
            app.state.database_pool = None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
                self.assertEqual((await client.get("/ready")).status_code, 503)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        worker.state.settings = SimpleNamespace(worker_token=SecretStr(TOKEN), image="trusted-image")
        worker.state.slots = asyncio.Semaphore(2)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=worker), base_url="http://worker")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def execute(self, archive, **kwargs):
        return await self.client.post("/execute", params={"source": "run_test", "path": "tests/test_a.py"},
                                      content=archive, headers={"Authorization": "Bearer " + TOKEN}, **kwargs)

    async def test_worker_auth_and_unsafe_snapshots_do_not_execute(self):
        with patch.object(ContainerRunner, "_execute_container", AsyncMock()) as run:
            response = await self.client.post("/execute", params={"source": "run_test", "path": "tests/test_a.py"},
                                              content=snapshot())
            self.assertEqual(response.status_code, 401)
            response = await self.execute(snapshot("root/../bad"))
            self.assertFalse(response.json()["completed"])
            run.assert_not_awaited()

    async def test_worker_bounds_archive_and_returns_execution_evidence(self):
        with patch("app.sandbox_worker.ARCHIVE_LIMIT", 1):
            self.assertEqual((await self.execute(snapshot())).status_code, 413)
        with patch.object(ContainerRunner, "_execute_container", AsyncMock(
            return_value=ExecutionResult("1 failed", 1)
        )) as run:
            response = await self.execute(snapshot())
            self.assertTrue(response.json()["completed"])
            self.assertEqual(response.json()["exit_code"], 1)
            run.assert_awaited_once()

    async def test_busy_worker_does_not_execute(self):
        worker.state.slots = asyncio.Semaphore(0)
        self.assertEqual((await self.execute(snapshot())).status_code, 503)

    async def test_remote_dispatch_never_executes_docker_locally(self):
        github = SimpleNamespace(download_archive=AsyncMock(return_value=snapshot()))
        pr = SimpleNamespace(owner="o", repository="r", head_sha="exact-head", base_sha="base")
        def handle(request):
            self.assertEqual(request.headers["authorization"], "Bearer " + TOKEN)
            self.assertEqual(request.url.params["path"], "tests/test_a.py")
            self.assertEqual(request.content, snapshot())
            return httpx.Response(200, json={"output": "failure", "exit_code": 1, "timed_out": False, "completed": True})
        remote = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(settings, "sandbox_enabled", True), patch.object(settings, "sandbox_worker_url", "http://worker"), \
             patch.object(settings, "sandbox_worker_token", SecretStr(TOKEN)), \
             patch("app.services.sandbox_service.httpx.AsyncClient", return_value=remote), \
             patch("app.services.sandbox_service.docker_command", AsyncMock()) as docker:
            result = await SandboxService(github).execute(pr, "run_test", "tests/test_a.py", "head")
            self.assertTrue(result.completed)
            github.download_archive.assert_awaited_once_with("o", "r", "exact-head")
            docker.assert_not_awaited()

    async def test_remote_outage_is_incomplete_evidence(self):
        github = SimpleNamespace(download_archive=AsyncMock(return_value=snapshot()))
        pr = SimpleNamespace(owner="o", repository="r", head_sha="head", base_sha="base")
        remote = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
        with patch.object(settings, "sandbox_enabled", True), patch.object(settings, "sandbox_worker_url", "http://worker"), \
             patch.object(settings, "sandbox_worker_token", SecretStr(TOKEN)), \
             patch("app.services.sandbox_service.httpx.AsyncClient", return_value=remote):
            result = await SandboxService(github).execute(pr, "run_test", "tests/test_a.py", "head")
            self.assertFalse(result.completed)

    async def test_invalid_or_timed_out_remote_results_cannot_confirm(self):
        github = SimpleNamespace(download_archive=AsyncMock(return_value=snapshot()))
        pr = SimpleNamespace(owner="o", repository="r", head_sha="head", base_sha="base")
        responses = [
            httpx.Response(200, content=b"x" * 100_001),
            httpx.Response(200, json={"completed": True}),
            httpx.Response(200, json={"output": "partial", "exit_code": 1, "completed": True, "timed_out": True}),
            httpx.Response(200, json={"output": "collection error", "exit_code": 2, "completed": True, "timed_out": False}),
        ]
        for response in responses:
            remote = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
            with patch.object(settings, "sandbox_enabled", True), patch.object(settings, "sandbox_worker_url", "http://worker"), \
                 patch.object(settings, "sandbox_worker_token", SecretStr(TOKEN)), \
                 patch("app.services.sandbox_service.httpx.AsyncClient", return_value=remote):
                result = await SandboxService(github).execute(pr, "run_test", "tests/test_a.py", "head")
                self.assertFalse(result.completed)


if __name__ == "__main__":
    unittest.main()
