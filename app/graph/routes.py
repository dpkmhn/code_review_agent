from langgraph.prebuilt import tools_condition
from langgraph.types import Send

from app.graph.nodes import specialist_tasks
from app.graph.state import ReviewState, SpecialistState


def route_after_context_analysis(state: ReviewState) -> str:
    if state["context_request"].needs_context:
        return "retrieve_context"
    return "supervisor"


def route_to_specialists(state: ReviewState) -> list[Send]:
    # Fan out: every selected specialist runs in parallel with its own state.
    return [Send("specialist", task) for task in specialist_tasks(state)]


def route_next_hypothesis(state: SpecialistState) -> str:
    if state["hypothesis_index"] < len(state["hypotheses"]):
        return "select_hypothesis"
    return "finish"


def route_after_evaluation(state: SpecialistState) -> str:
    if state["evaluation"].status == "confirmed":
        return "store_finding"
    return "discard"


def route_after_reviewer(state: SpecialistState) -> str:
    # Requests have already been budgeted by reviewer_node. If it skipped the
    # model, the last message is a tool result, which tools_condition ends on.
    return "tools" if tools_condition(state) == "tools" else "evaluate_evidence"
