from app.graph.state import ReviewState
from langgraph.prebuilt import tools_condition


def route_after_context_analysis(state: ReviewState) -> str:
    if state["context_request"].needs_context:
        return "retrieve_context"
    return "form_hypotheses"


def route_next_hypothesis(state: ReviewState) -> str:
    if state["hypothesis_index"] < len(state["hypotheses"]):
        return "select_hypothesis"
    return "generate_review"


def route_after_evaluation(state: ReviewState) -> str:
    if state["evaluation"].status == "confirmed":
        return "store_finding"
    return "discard"


def route_after_reviewer(state: ReviewState) -> str:
    # Requests have already been budgeted by reviewer_node. If it skipped the
    # model, the last message is a tool result, which tools_condition ends on.
    return "tools" if tools_condition(state) == "tools" else "evaluate_evidence"
