from typing import Literal
from pydantic import BaseModel, Field

from app.schemas.specialist import Specialist

# Represents an individual code defect or improvement suggestion detected during a review
class ReviewFinding(BaseModel):
    file: str = Field(description="Repository path of the file containing the issue.")
    line: int | None = Field(default=None, description="Relevant line number extracted from diff context.")
    severity: Literal["low", "medium", "high", "critical"] # Enum enforcing strict risk categories
    title: str = Field(description="Short description of the problem.")
    explanation: str = Field(description="Why this is a bug or correctness issue.")
    category: Specialist | None = Field(default=None, description="Review area that reported the issue.")

# Encapsulates a collection of review metrics and individual codebase findings
class ReviewResult(BaseModel):
    findings: list[ReviewFinding] # Nested list holding every structured issue identified
