"""Tool audit records collected while the reviewer gathers evidence."""

from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.hypothesis import VerificationEvidence
from app.schemas.specialist import Specialist


class ToolCallRecord(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any]
    status: Literal["pending", "completed"] = "pending"
    result: str | None = None
    evidence: VerificationEvidence | None = None
    # None for shared context retrieval that runs before the specialists.
    specialist: Specialist | None = None
