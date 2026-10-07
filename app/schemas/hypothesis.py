from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai_review import ReviewFinding


MAX_HYPOTHESES = 5
MAX_VERIFICATION_STEPS = 3


class VerificationStep(BaseModel):
    path: str = Field(min_length=1, description="Repository-relative file to inspect.")
    revision: Literal["head", "base"]
    reason: str = Field(description="What this inspection should establish.")


class BugHypothesis(BaseModel):
    description: str
    file: str
    line: int = Field(gt=0, description="Relevant line in the supplied diff.")
    confidence: Literal["low", "medium", "high"]
    verification_plan: list[VerificationStep] = Field(max_length=MAX_VERIFICATION_STEPS)


class HypothesisResult(BaseModel):
    hypotheses: list[BugHypothesis] = Field(max_length=MAX_HYPOTHESES)


class VerificationEvidence(BaseModel):
    source: Literal["diff", "read_file", "list_tree", "run_test", "run_static_analysis"]
    path: str
    ref: str
    content: str
    succeeded: bool
    exit_code: int | None = None
    timed_out: bool = False


class EvidenceEvaluation(BaseModel):
    status: Literal["confirmed", "rejected", "inconclusive"]
    explanation: str
    evidence_indices: list[int] = Field(description="Zero-based indices supporting the decision.")
    finding: ReviewFinding | None
