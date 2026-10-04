from fastapi import APIRouter, Depends, HTTPException, Request, Response
from uuid import UUID
import httpx

from app.schemas.review import ReviewRequest, ReviewResponse
from app.services.review_service import ReviewService
from app.graph.review_graph import review_graph
from app.schemas.persistence import StoredReview
from app.persistence.store import ReviewBusyError, ReviewNotFoundError
from app.security import require_access

# Define API router with path prefix and documentation tags
router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"], dependencies=[Depends(require_access)])

# Global service layer instance
review_service = ReviewService()


@router.post("", response_model=ReviewResponse)
async def create_review(request: ReviewRequest, http_request: Request, response: Response) -> ReviewResponse:
    try:
        # Ingest and validate PR metadata via service layer
        pull_request = await review_service.ingest_pull_request(
            repository=request.repository,
            pull_request_number=request.pull_request_number,
        )

    except ValueError as exc:
        # Catch invalid repository format (missing '/' or malformed)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except httpx.HTTPStatusError as exc:
        # Map GitHub 404 error code cleanly
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Pull request or repository not found") from exc
        
        # Map all other GitHub upstream server failures
        raise HTTPException(status_code=502, detail="GitHub API request failed") from exc

    runner = getattr(http_request.app.state, "review_runner", None)
    if runner is not None:
        try:
            review_id = await runner.store.create(pull_request)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Review database unavailable") from exc
        response.headers["Location"] = f"/api/v1/reviews/{review_id}"
        stored = await execute_persisted_review(runner, review_id)
        return ReviewResponse(review_id=review_id, status=stored.status,
                              pull_request=stored.pull_request, findings=stored.findings)

    # Execute the LangGraph processing workflow asynchronously with the initial state dictionary
    result = await review_graph.ainvoke(
        {
            "pull_request": pull_request,
        }
    )

    # Extract the populated review findings from the terminal graph workspace state object
    return ReviewResponse(
        status="reviewed",
        pull_request=pull_request,
        findings=result["review"].findings,
    )


def persistent_runner(request):
    runner = getattr(request.app.state, "review_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="Persistence requires database_url configuration")
    return runner


async def execute_persisted_review(runner, review_id):
    try:
        return await runner.run(review_id)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Review not found") from exc
    except ReviewBusyError as exc:
        raise HTTPException(status_code=409, detail="Review is already running") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail={
            "message": "Review failed; retry the resume endpoint.", "review_id": str(review_id),
        }, headers={"Location": f"/api/v1/reviews/{review_id}"}) from exc


@router.get("/{review_id}", response_model=StoredReview)
async def get_review(review_id: UUID, request: Request):
    runner = persistent_runner(request)
    try:
        return await runner.store.get(review_id)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Review not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Review database unavailable") from exc


@router.post("/{review_id}/resume", response_model=StoredReview)
async def resume_review(review_id: UUID, request: Request):
    return await execute_persisted_review(persistent_runner(request), review_id)
