from fastapi import APIRouter, Depends, HTTPException
import httpx

from app.schemas.review import ReviewRequest, ReviewResponse
from app.services.review_service import ReviewService
from app.graph.review_graph import review_graph
from app.security import require_access

# Define API router with path prefix and documentation tags
router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"], dependencies=[Depends(require_access)])

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

    # Execute the LangGraph processing workflow asynchronously with the initial state dictionary
    result = await review_graph.ainvoke(
        {
            "pull_request": pull_request,
        },
        # Names and labels the LangSmith trace when tracing is enabled.
        config={
            "run_name": "pull_request_review",
            "tags": ["api"],
            "metadata": {
                "repository": request.repository,
                "pull_request_number": request.pull_request_number,
            },
        },
    )

    # Extract the populated review findings from the terminal graph workspace state object
    return ReviewResponse(
        status="reviewed",
        pull_request=pull_request,
        findings=result["review"].findings,
        tool_calls=result.get("tool_records", []),
        specialists=result.get("specialists", []),
    )

