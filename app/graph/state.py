from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.schemas.github import PullRequestData
from app.schemas.ai_review import ReviewResult
from app.schemas.context import ContextRequest
from app.schemas.ai_review import ReviewFinding
from app.schemas.hypothesis import BugHypothesis, EvidenceEvaluation, VerificationEvidence
from app.schemas.persistence import ToolCallRecord


class ReviewState(TypedDict, total=False):
    pull_request: PullRequestData
    diff: str
    changed_files: list[str]

    context_request: ContextRequest
    relevant_context: list[str]
    hypotheses: list[BugHypothesis]
    hypothesis_index: int
    current_hypothesis: BugHypothesis
    evidence: list[VerificationEvidence]
    evaluation: EvidenceEvaluation
    evaluations: list[EvidenceEvaluation]
    findings: list[ReviewFinding]
    tool_calls: int
    tool_records: list[ToolCallRecord]
    messages: Annotated[list[AnyMessage], add_messages]
    verification_tool_calls: int
    verification_rounds: int

    review: ReviewResult
