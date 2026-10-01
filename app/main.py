from fastapi import FastAPI

from app.api.reviews import router as reviews_router


app = FastAPI(
    title="AI Code Review Agent",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }


app.include_router(reviews_router)