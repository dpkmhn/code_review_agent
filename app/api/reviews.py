from fastapi import APIRouter, HTTPException
import httpx

from app.schemas.review import ReviewRequest, ReviewResponse
from app.services.review_service import ReviewService

# Define API router with path prefix and documentation tags
router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

# Global service layer instance
review_service = ReviewService()


@router.post("", response_model=ReviewResponse)
async def create_review(request: ReviewRequest) -> ReviewResponse:
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

    # Return structured success status along with data payload
    return ReviewResponse(
        status="ingested",
        pull_request=pull_request,
        findings=[],
    )
