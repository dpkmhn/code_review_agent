import time

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from app.evaluation.dataset import FixtureRepository
from app.evaluation.metrics import UsageMeter, findings_hash
from app.graph import nodes
from app.graph.review_graph import build_review_graph
from app.graph.state import ReviewState
from app.schemas.context import ContextRequest
from app.schemas.hypothesis import HypothesisResult
from app.schemas.ai_review import ReviewResult
from app.schemas.specialist import SpecialistSelection
from app.services.context_service import ContextService
from app.services.llm_review_service import LLMReviewService
from app.tools.verification_tools import build_verification_tools

MODES = ("diff_only", "context", "verification")


class OfflineReviewer:
    """Exercise wiring only. No model predictions or ground-truth access."""

    async def analyse_context_need(self, pull_request):
        return ContextRequest(needs_context=False, reason="Offline wiring check")

    async def review_pull_request(self, pull_request, relevant_context=None):
        return ReviewResult(findings=[])

    async def select_specialists(self, pull_request):
        return SpecialistSelection(specialists=["correctness"], reason="Offline wiring check")

    async def form_hypotheses(self, pull_request, relevant_context, specialist="correctness"):
        return HypothesisResult(hypotheses=[])


async def execute_case(case, number, mode, llm, context, repository):
    pr = case.pull_request(number)
    if mode == "diff_only":
        return {"review": await llm.review_pull_request(pr), "tool_calls": 0}
    if mode == "context":
        # Same context nodes and budgets as the verification graph, with direct review.
        graph = StateGraph(ReviewState)
        graph.add_node("analyse_diff", nodes.analyse_diff_node)

        async def analyse(state):
            return await nodes.analyse_context_node(state, context=context)

        async def retrieve(state):
            return await nodes.retrieve_context_node(state, repository=repository)

        async def review(state):
            return {"review": await llm.review_pull_request(pr, state["relevant_context"])}

        graph.add_node("analyse_context", analyse)
        graph.add_node("retrieve_context", retrieve)
        graph.add_node("review", review)
        graph.add_edge(START, "analyse_diff")
        graph.add_edge("analyse_diff", "analyse_context")
        graph.add_conditional_edges("analyse_context", lambda state:
                                    "retrieve_context" if state["context_request"].needs_context else "review")
        graph.add_edge("retrieve_context", "review")
        graph.add_edge("review", END)
        return await graph.compile().ainvoke({"pull_request": pr})
    if mode != "verification":
        raise ValueError(f"Unknown mode: {mode}")
    # Synthetic fixtures cannot be sent to Docker/GitHub archive tools.
    tools = [tool for tool in build_verification_tools(repository)
             if tool.name in {"read_file", "list_tree", "search_code", "find_references"}]
    graph = build_review_graph(llm=llm, context=context, repository=repository, tools=tools)
    return await graph.ainvoke({"pull_request": pr})


async def run_suite(cases, modes, *, offline, rates=None, progress=None, service_factory=None):
    runs = []
    for number, case in enumerate(cases, 1):
        for mode in modes:
            meter = UsageMeter()
            llm = context = None
            started = time.perf_counter()
            run = {"case_id": case.id, "mode": mode, "status": "failed", "findings": [],
                   "tool_calls": 0, "verified_findings": 0, "error_type": None}
            try:
                if service_factory is not None:
                    llm, context = service_factory(meter)
                elif offline:
                    llm = context = OfflineReviewer()
                else:
                    llm, context = LLMReviewService(meter), ContextService(meter)
                # Groups each case's model calls under filterable LangSmith metadata.
                with tracing_context(tags=["evaluation", mode], metadata={"case_id": case.id, "mode": mode}):
                    result = await execute_case(case, number, mode, llm, context, FixtureRepository(case))
                findings = [finding.model_dump() for finding in result["review"].findings]
                run.update(status="completed", findings=findings, tool_calls=result.get("tool_calls", 0),
                           verified_findings=len(findings) if mode == "verification" else 0)
                run["tool_records"] = [record.model_dump() for record in result.get("tool_records", [])]
                run["evaluations"] = [item.model_dump() for item in result.get("evaluations", [])]
            except Exception as error:
                # No exception text: SDK errors can contain credentials or request content.
                run["error_type"] = type(error).__name__
                meter.missing_usage += 1  # Failed attempts may have incurred unreported usage.
                # Partial graph tool counts are unknown rather than fabricated zeroes.
                run["tool_calls"] = None
            finally:
                for client in {id(getattr(service, "client", None)): getattr(service, "client", None)
                               for service in (llm, context)}.values():
                    if client is not None:
                        await client.close()
            run["latency_seconds"] = time.perf_counter() - started
            run["usage"] = meter.report(rates)
            run["findings_sha256"] = findings_hash(run["findings"])
            run["location_candidates"] = [
                [bug.id for bug in case.expected_bugs if finding["file"] == bug.file
                 and finding["line"] is not None and bug.start_line <= finding["line"] <= bug.end_line]
                for finding in run["findings"]]
            runs.append(run)
            if progress is not None:
                progress(run)
    return runs
