from pydantic import BaseModel, Field
from app.schemas.ai_review import ReviewFinding
from app.schemas.github import PullRequestData
from uuid import UUID

# Incoming API payload validation schema
class ReviewRequest(BaseModel):
    repository: str = Field(min_length=3)      # Repository identifier string
    pull_request_number: int = Field(gt=0)     # Positive integer PR identifier

# Outgoing API response serialization schema
class ReviewResponse(BaseModel):
    review_id: UUID | None = None
    status: str                                # Operation state status string
    pull_request: PullRequestData              # Nested structural PR content payload
    findings: list[ReviewFinding]              # Array of detected code vulnerabilities/bugs
