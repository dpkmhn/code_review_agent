"""Durable review metadata and checkpointed tool audit records."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ai_review import ReviewFinding
from app.schemas.github import PullRequestData
from app.schemas.hypothesis import VerificationEvidence


class ToolCallRecord(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any]
    status: Literal["pending", "completed"] = "pending"
    result: str | None = None
    evidence: VerificationEvidence | None = None


class StoredReview(BaseModel):
    review_id: UUID
    status: Literal["pending", "running", "reviewed", "failed"]
    pull_request: PullRequestData
    findings: list[ReviewFinding] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
