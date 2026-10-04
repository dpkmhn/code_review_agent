"""Private Linux worker. This module never imports API/model credentials."""

import asyncio
import secrets
import tempfile
import zipfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.sandbox_runtime import (
    ARCHIVE_LIMIT, ContainerRunner, ExecutionResult, SandboxCleanupError,
    extract_snapshot, relative_path,
)


class WorkerSettings(BaseSettings):
    worker_token: SecretStr = Field(min_length=32)
    image: str = "code-review-sandbox:stage9"
    model_config = SettingsConfigDict(env_prefix="sandbox_", env_file=None)


@asynccontextmanager
async def lifespan(app):
    app.state.settings = WorkerSettings()
    app.state.slots = asyncio.Semaphore(2)
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


async def authenticate(request: Request):
    expected = "Bearer " + request.app.state.settings.worker_token.get_secret_value()
    if not secrets.compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Worker authentication required")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/execute", dependencies=[Depends(authenticate)])
async def execute(request: Request, source: Literal["run_test", "run_static_analysis"], path: str):
    try:
        path = relative_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid snapshot path") from exc
    # Reject excess work before buffering archives. Run one Uvicorn worker so the
    # semaphore bounds the entire service; busy calls are incomplete evidence.
    slots = request.app.state.slots
    if slots.locked():
        raise HTTPException(status_code=503, detail="Worker busy")
    async with slots:
        archive = bytearray()
        try:
            async with asyncio.timeout(45):
                async for chunk in request.stream():
                    if len(archive) + len(chunk) > ARCHIVE_LIMIT:
                        raise HTTPException(status_code=413, detail="Snapshot too large")
                    archive.extend(chunk)
            with tempfile.TemporaryDirectory(prefix="review-sandbox-") as folder:
                root = Path(folder)
                extraction = asyncio.create_task(asyncio.to_thread(extract_snapshot, archive, root))
                try:
                    await asyncio.shield(extraction)
                except asyncio.CancelledError:
                    await extraction
                    raise
                if not (root / path).is_file():
                    raise ValueError("Missing selected file")
                result = await ContainerRunner(request.app.state.settings.image)._execute_container(root, source, path)
        except SandboxCleanupError:
            result = ExecutionResult("Sandbox cleanup failed; operator cleanup required.", None, completed=False)
        except (OSError, ValueError, TimeoutError, zipfile.BadZipFile):
            result = ExecutionResult("Sandbox unavailable or snapshot invalid.", None, completed=False)
        return asdict(result)
