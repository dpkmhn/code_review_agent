from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.routes import (
    route_after_context_analysis, route_after_evaluation, route_next_hypothesis,
    route_after_reviewer, route_to_specialists,
)
from app.graph.state import ReviewState, SpecialistState
from app.tools.verification_tools import build_tool_node


def build_specialist_graph(*, llm=None, tools=None):
    """One specialist agent: form hypotheses in its area, verify each with tools."""
    selected_tools = nodes.verification_tools if tools is None else tools
    overrides = {
        "form_hypotheses": partial(nodes.form_hypotheses_node, llm=llm),
        "reviewer": partial(nodes.reviewer_node, llm=llm, tools=selected_tools),
        # The shared judge: every specialist's claims pass the same evidence gate.
        "evaluate_evidence": partial(nodes.evaluate_evidence_node, llm=llm),
    }
    builder = StateGraph(SpecialistState)

    for name in (
        "form_hypotheses", "select_hypothesis", "reviewer", "collect_evidence",
        "evaluate_evidence", "store_finding", "discard", "advance_hypothesis",
    ):
        builder.add_node(name, overrides.get(name, getattr(nodes, f"{name}_node")))
    builder.add_node("tools", build_tool_node(selected_tools))

    next_hypothesis = {"select_hypothesis": "select_hypothesis", "finish": END}
    builder.add_edge(START, "form_hypotheses")
    builder.add_conditional_edges("form_hypotheses", route_next_hypothesis, next_hypothesis)
    builder.add_edge("select_hypothesis", "reviewer")
    builder.add_conditional_edges("reviewer", route_after_reviewer, {
        "tools": "tools", "evaluate_evidence": "evaluate_evidence",
    })
    builder.add_edge("tools", "collect_evidence")
    builder.add_edge("collect_evidence", "reviewer")
    builder.add_conditional_edges("evaluate_evidence", route_after_evaluation, {
        "store_finding": "store_finding", "discard": "discard",
    })
    builder.add_edge("store_finding", "advance_hypothesis")
    builder.add_edge("discard", "advance_hypothesis")
    builder.add_conditional_edges("advance_hypothesis", route_next_hypothesis, next_hypothesis)
    return builder.compile(name="specialist")


def build_review_graph(*, llm=None, context=None, repository=None, tools=None):
    """Build an isolated graph; omitted dependencies retain API behavior."""
    specialist_graph = build_specialist_graph(llm=llm, tools=tools)
    overrides = {
        "load_tree": partial(nodes.load_tree_node, repository=repository),
        "analyse_context": partial(nodes.analyse_context_node, context=context),
        "retrieve_context": partial(nodes.retrieve_context_node, repository=repository),
        "supervisor": partial(nodes.supervisor_node, llm=llm),
        "specialist": nodes.build_specialist_node(specialist_graph),
    }
    builder = StateGraph(ReviewState)

    for name in (
        "analyse_diff", "load_tree", "analyse_context", "retrieve_context", "supervisor",
        "specialist", "generate_review",
    ):
        builder.add_node(name, overrides.get(name, getattr(nodes, f"{name}_node", None)))

    # PR ingestion stays at the API boundary so GitHub errors retain HTTP mappings.
    builder.add_edge(START, "analyse_diff")
    builder.add_edge("analyse_diff", "load_tree")
    builder.add_edge("load_tree", "analyse_context")
    builder.add_conditional_edges("analyse_context", route_after_context_analysis, {
        "retrieve_context": "retrieve_context", "supervisor": "supervisor",
    })
    builder.add_edge("retrieve_context", "supervisor")
    builder.add_conditional_edges("supervisor", route_to_specialists, ["specialist"])
    builder.add_edge("specialist", "generate_review")
    builder.add_edge("generate_review", END)

    # Five hypotheses per specialist need more than LangGraph's default 25 supersteps.
    return builder.compile().with_config({"recursion_limit": 200})


review_graph = build_review_graph()
