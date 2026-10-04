from fastapi import FastAPI
from contextlib import asynccontextmanager
import sys
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.api.reviews import router as reviews_router
from app.config import settings
from app.graph.review_graph import build_review_graph
from app.persistence.database import database_pool, checkpoint_serializer
from app.persistence.store import ReviewStore
from app.services.persistent_review import PersistentReviewRunner


@asynccontextmanager
async def lifespan(app):
    app.state.review_runner = None
    app.state.database_pool = None
    if settings.database_url is None:
        yield
    else:
        if sys.platform == "win32" and settings.sandbox_enabled:
            raise RuntimeError("Run PostgreSQL with sandbox execution on Linux; Windows requires incompatible event loops.")
        async with database_pool(settings.database_url.get_secret_value()) as pool:
            app.state.database_pool = pool
            saver = AsyncPostgresSaver(pool, serde=checkpoint_serializer())
            app.state.review_runner = PersistentReviewRunner(ReviewStore(pool), build_review_graph(saver))
            yield


app = FastAPI(
    title="AI Code Review Agent",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)


@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }


app.include_router(reviews_router)


@app.get("/ready")
async def readiness():
    from fastapi.responses import JSONResponse
    pool = getattr(app.state, "database_pool", None)
    if settings.database_url is not None:
        if pool is None:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        try:
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}
