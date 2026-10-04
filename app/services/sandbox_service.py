"""Download pinned snapshots and dispatch local or remote sandbox execution."""

import asyncio
import tempfile
import zipfile
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.services.sandbox_runtime import (
    ARCHIVE_LIMIT, OUTPUT_LIMIT, ContainerRunner, ExecutionResult,
    SandboxCleanupError, docker_command, extract_snapshot, relative_path,
)


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    output: str
    exit_code: int | None
    timed_out: bool
    completed: bool


class SandboxService:
    def __init__(self, github_client):
        self.github_client = github_client
        self._slots = asyncio.Semaphore(2)

    async def execute(self, pr, source: str, path: str, revision: str) -> ExecutionResult:
        path = relative_path(path)
        if source not in ("run_test", "run_static_analysis") or revision not in ("head", "base"):
            raise ValueError("Unsupported sandbox operation.")
        if not settings.sandbox_enabled:
            return ExecutionResult("Sandbox disabled; execution did not run.", None, completed=False)
        ref = pr.head_sha if revision == "head" else pr.base_sha
        async with self._slots:
            try:
                archive = await asyncio.wait_for(
                    self.github_client.download_archive(pr.owner, pr.repository, ref), 45,
                )
                if settings.sandbox_worker_url:
                    return await self._execute_remote(archive, source, path)
                with tempfile.TemporaryDirectory(prefix="review-sandbox-") as temporary:
                    root = Path(temporary)
                    extraction = asyncio.create_task(asyncio.to_thread(extract_snapshot, archive, root))
                    try:
                        await asyncio.shield(extraction)
                    except asyncio.CancelledError:
                        await extraction
                        raise
                    if not (root / path).is_file():
                        raise ValueError("Select an existing file in the snapshot.")
                    return await self._execute_container(root, source, path)
            except SandboxCleanupError:
                return ExecutionResult("Sandbox cleanup failed; a review-sandbox container may remain. "
                                       "Operator cleanup is required; execution evidence is incomplete.",
                                       None, completed=False)
            except (OSError, ValueError, TimeoutError, zipfile.BadZipFile, httpx.HTTPError):
                return ExecutionResult("Sandbox unavailable or snapshot invalid; execution did not complete.",
                                       None, completed=False)

    async def _execute_container(self, root, source, path):
        return await ContainerRunner(settings.sandbox_image, docker_command)._execute_container(root, source, path)

    async def _execute_remote(self, archive, source, path):
        if len(archive) > ARCHIVE_LIMIT:
            raise ValueError("Snapshot exceeds transfer limit.")
        async with httpx.AsyncClient(timeout=240, follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST",
                settings.sandbox_worker_url.rstrip("/") + "/execute",
                params={"source": source, "path": path}, content=archive,
                headers={"Authorization": "Bearer " + settings.sandbox_worker_token.get_secret_value(),
                         "Content-Type": "application/zip"},
            ) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(payload) + len(chunk) > 100_000:
                        raise ValueError("Worker response exceeds limit.")
                    payload.extend(chunk)
            result = WorkerResult.model_validate_json(payload)
            if len(result.output) > OUTPUT_LIMIT * 2 + 100:
                raise ValueError("Worker output exceeds limit.")
            completed = result.completed and not result.timed_out and result.exit_code in (0, 1)
            return ExecutionResult(result.output, result.exit_code, result.timed_out, completed)
