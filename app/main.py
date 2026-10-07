from fastapi import FastAPI

from app.api.reviews import router as reviews_router
from app.config import settings


app = FastAPI(
    title="AI Code Review Agent",
    version="0.1.0",
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
    return {"status": "ready"}
