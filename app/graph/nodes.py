import json
import httpx
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.graph.state import ReviewState, SpecialistState
from app.github_client import GitHubClient
from app.schemas.ai_review import ReviewResult
from app.schemas.hypothesis import EvidenceEvaluation, VerificationEvidence
from app.schemas.specialist import SPECIALISTS
from app.schemas.tool_call import ToolCallRecord
from app.services.context_service import ContextService
from app.services.llm_review_service import LLMReviewService
from app.tools.executor import execute_repository_tool
from app.tools.tree import tree_evidence, tree_prompt
from app.tools.verification_tools import bounded_content, build_verification_tools

context_service = ContextService()
review_service = LLMReviewService()
github_client = GitHubClient()
verification_tools = build_verification_tools(github_client)

MAX_CONTEXT_QUERIES = 3
MAX_CONTEXT_FILES = 3
MAX_TOOL_CALLS = 30
MAX_VERIFICATION_ROUNDS = 3
MAX_VERIFICATION_TOOL_CALLS = 6
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


async def analyse_diff_node(state: ReviewState) -> dict:
    """Start each run with independent accumulators, including on graph reuse."""
    pull_request = state["pull_request"]
    return {
        "diff": review_service._build_diff(pull_request),
        "changed_files": [file.filename for file in pull_request.files],
        "relevant_context": [],
        "tree_evidence": None,
        "tree_context": "",
        "specialists": [],
        "evaluations": [],
        "findings": [],
        "tool_calls": 0,
        "tool_records": [],
    }


async def load_tree_node(state: ReviewState, *, repository=None) -> dict:
    """Fetch the PR head file tree once; it feeds the prompt and the evidence list."""
    pull_request = state["pull_request"]
    try:
        result = await execute_repository_tool(
            github_client=repository or github_client, tool_name="list_tree", arguments={},
            owner=pull_request.owner, repository=pull_request.repository,
            ref=pull_request.head_sha,
        )
    except (httpx.HTTPError, KeyError, ValueError, AttributeError):
        # A failed listing proves nothing about which files exist, so record none.
        return {"tree_evidence": None, "tree_context": ""}
    return {
        "tree_evidence": tree_evidence(result, pull_request.head_sha),
        "tree_context": tree_prompt(result, pull_request.head_sha, state["changed_files"]),
    }


async def analyse_context_node(state: ReviewState, *, context=None) -> dict:
    request = await (context or context_service).analyse_context_need(state["pull_request"])
    return {"context_request": request}


async def retrieve_context_node(state: ReviewState, *, repository=None) -> dict:
    client = repository or github_client
    pull_request = state["pull_request"]
    context: list[str] = []
    tool_calls = state["tool_calls"]
    inspected_paths: set[str] = set()
    records = list(state.get("tool_records", []))

    for query in state["context_request"].queries[:MAX_CONTEXT_QUERIES]:
        tool_calls += 1
        try:
            result = await execute_repository_tool(
                github_client=client,
                tool_name="search_code",
                arguments={"query": query},
                owner=pull_request.owner,
                repository=pull_request.repository,
                ref=pull_request.head_sha,
            )
        except (httpx.HTTPError, KeyError, ValueError):
            # Code search is heavily rate limited; one failed query must not
            # abort the review, and the model must not read it as "no matches".
            records.append(ToolCallRecord(
                call_id=f"context-{tool_calls}", name="search_code",
                arguments={"query": query}, status="completed",
                result="Search failed; no candidate paths were inspected.",
            ))
            context.append(f"Search query: {query}\nSearch failed; absence of results is not evidence.")
            continue
        records.append(ToolCallRecord(
            call_id=f"context-{tool_calls}", name="search_code",
            arguments={"query": query}, status="completed",
            result=bounded_content(json.dumps(result)),
        ))
        # GitHub search finds paths on the default branch. Read each candidate
        # at the PR head before using its contents as review context.
        context.append(f"Search query: {query}\nCandidate paths (default branch): {result}")
        for match in result:
            path = match["path"]
            if path in inspected_paths or len(inspected_paths) >= MAX_CONTEXT_FILES:
                continue
            inspected_paths.add(path)
            tool_calls += 1
            succeeded = True
            try:
                content = await client.read_file(
                    owner=pull_request.owner, repository=pull_request.repository,
                    path=path, ref=pull_request.head_sha,
                )
                context.append(f"File: {path}\nRef: {pull_request.head_sha}\n{bounded_content(content)}")
            except (httpx.HTTPError, UnicodeDecodeError, KeyError, ValueError):
                succeeded = False
                content = "File unavailable at PR head; no contents inspected."
                context.append(f"File: {path}\nUnavailable at PR head; no contents inspected.")
            evidence = VerificationEvidence(
                source="read_file", path=path, ref=pull_request.head_sha,
                content=bounded_content(content), succeeded=succeeded,
            )
            records.append(ToolCallRecord(
                call_id=f"context-{tool_calls}", name="read_file",
                arguments={"path": path, "revision": "head"}, status="completed",
                result=evidence.content, evidence=evidence,
            ))

    return {"relevant_context": context, "tool_calls": tool_calls, "tool_records": records}


async def supervisor_node(state: ReviewState, *, llm=None) -> dict:
    selection = await (llm or review_service).select_specialists(pull_request=state["pull_request"])
    # Correctness is the baseline review; the supervisor can only add specialists.
    chosen = {"correctness", *selection.specialists}
    return {"specialists": [name for name in SPECIALISTS if name in chosen]}


def specialist_tasks(state: ReviewState) -> list[dict]:
    """Initial state per specialist, splitting what remains of the global tool budget."""
    specialists = state["specialists"]
    share, extra = divmod(max(0, MAX_TOOL_CALLS - state["tool_calls"]), len(specialists))
    return [{
        "pull_request": state["pull_request"], "relevant_context": state["relevant_context"],
        "tree_evidence": state.get("tree_evidence"), "tree_context": state.get("tree_context", ""),
        "specialist": specialist, "tool_budget": share + (index < extra),
        "hypotheses": [], "hypothesis_index": 0, "evidence": [], "evaluations": [],
        "findings": [], "tool_calls": 0, "tool_records": [], "messages": [],
        "verification_rounds": 0, "verification_tool_calls": 0,
    } for index, specialist in enumerate(specialists)]


def build_specialist_node(specialist_graph):
    async def specialist_node(task: SpecialistState) -> dict:
        # Each specialist is its own agent graph; only its outcome reaches the parent.
        result = await specialist_graph.ainvoke(task, {
            "run_name": f"{task['specialist']}_specialist", "tags": [task["specialist"]],
        })
        run = {key: value for key, value in result.items() if key not in ("messages", "pull_request")}
        return {"specialist_runs": [run]}
    return specialist_node


async def form_hypotheses_node(state: SpecialistState, *, llm=None) -> dict:
    tree_context = state.get("tree_context")
    result = await (llm or review_service).form_hypotheses(
        pull_request=state["pull_request"],
        relevant_context=([tree_context] if tree_context else []) + state["relevant_context"],
        specialist=state["specialist"],
    )
    patched_files = {file.filename for file in state["pull_request"].files if file.patch}
    return {"hypotheses": [item for item in result.hypotheses if item.file in patched_files]}


async def select_hypothesis_node(state: SpecialistState) -> dict:
    hypothesis = state["hypotheses"][state["hypothesis_index"]]
    pr = state["pull_request"]
    evidence = [VerificationEvidence(
        source="diff", path=file.filename, ref=pr.head_sha,
        content=file.patch, succeeded=True,
    ) for file in pr.files if file.filename == hypothesis.file and file.patch]
    if state.get("tree_evidence") is not None:
        evidence.append(state["tree_evidence"])
    return {
        "current_hypothesis": hypothesis,
        "evidence": evidence,
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content=(
            f"Hypothesis: {hypothesis.model_dump_json()}\n"
            f"Diff evidence: {[item.model_dump() for item in evidence]}\n"
            f"Earlier repository context: {state['relevant_context']}\n"
            f"Limits: {MAX_VERIFICATION_ROUNDS} tool rounds, "
            f"{MAX_VERIFICATION_TOOL_CALLS} calls per hypothesis, "
            f"{state['tool_budget'] - state['tool_calls']} remaining repository calls."
        ))],
        "verification_rounds": 0,
        "verification_tool_calls": 0,
    }


def _call_id(state: SpecialistState, number: int) -> str:
    # Prefixed so records stay unique once parallel specialists are merged.
    return f"{state['specialist']}-verification-{number}"


async def reviewer_node(state: SpecialistState, *, llm=None, tools=None) -> dict:
    remaining = min(state["tool_budget"] - state["tool_calls"],
                    MAX_VERIFICATION_TOOL_CALLS - state["verification_tool_calls"])
    if remaining <= 0 or state["verification_rounds"] >= MAX_VERIFICATION_ROUNDS:
        return {}  # Conditional routing sends existing evidence to evaluation.
    message = await (llm or review_service).request_verification_tools(
        messages=state["messages"], tools=verification_tools if tools is None else tools,
        specialist=state["specialist"],
    )
    # Reserve the entire batch before ToolNode starts parallel execution. Count
    # invalid requests too, so repeated errors cannot bypass the budget.
    calls = message.tool_calls[:remaining]
    message = message.model_copy(update={"tool_calls": calls})
    records = list(state.get("tool_records", []))
    for index, call in enumerate(calls):
        records.append(ToolCallRecord(
            call_id=_call_id(state, state["tool_calls"] + index + 1),
            name=call["name"], arguments=call["args"], specialist=state["specialist"],
        ))
    return {"messages": [message], "tool_calls": state["tool_calls"] + len(calls),
            "tool_records": records,
            "verification_tool_calls": state["verification_tool_calls"] + len(calls),
            "verification_rounds": state["verification_rounds"] + 1}


async def collect_evidence_node(state: SpecialistState) -> dict:
    evidence = list(state["evidence"])
    records = list(state.get("tool_records", []))
    # Only the newly completed batch is appended, not previous tool results.
    batch = []
    for message in reversed(state["messages"]):
        if not isinstance(message, ToolMessage):
            break
        batch.append(message)
    for message in reversed(batch):
        if isinstance(message.artifact, VerificationEvidence):
            evidence.append(message.artifact)
    # Match by call ID, since ToolNode may execute a batch concurrently.
    calls = state["messages"][-len(batch) - 1].tool_calls if batch else []
    messages_by_id = {message.tool_call_id: message for message in batch}
    first_call = state["tool_calls"] - len(calls) + 1
    record_indices = {record.call_id: index for index, record in enumerate(records)}
    for offset, call in enumerate(calls):
        message = messages_by_id.get(call["id"])
        record_index = record_indices.get(_call_id(state, first_call + offset))
        if message is not None and record_index is not None:
            records[record_index] = records[record_index].model_copy(update={
                "status": "completed", "result": bounded_content(str(message.content)),
                "evidence": message.artifact if isinstance(message.artifact, VerificationEvidence) else None,
            })
    return {"evidence": evidence, "tool_records": records}


async def evaluate_evidence_node(state: SpecialistState, *, llm=None) -> dict:
    evaluation = await (llm or review_service).evaluate_evidence(
        hypothesis=state["current_hypothesis"], evidence=state["evidence"],
        specialist=state["specialist"],
    )
    if evaluation.status == "confirmed":
        finding = evaluation.finding
        hypothesis = state["current_hypothesis"]
        valid_evidence = bool(evaluation.evidence_indices) and all(
            0 <= index < len(state["evidence"]) and state["evidence"][index].succeeded
            for index in evaluation.evidence_indices
        )
        if (not valid_evidence or finding is None
                or finding.file != hypothesis.file or finding.line != hypothesis.line):
            evaluation = EvidenceEvaluation(
                status="inconclusive", explanation="Confirmation lacked valid evidence or location.",
                evidence_indices=[], finding=None,
            )
    return {"evaluation": evaluation, "evaluations": state["evaluations"] + [evaluation]}


async def store_finding_node(state: SpecialistState) -> dict:
    finding = state["evaluation"].finding
    findings = list(state["findings"])
    if finding is not None:
        finding = finding.model_copy(update={"category": state["specialist"]})
    if finding is not None and not any(
        (existing.file, existing.line, existing.title) == (finding.file, finding.line, finding.title)
        for existing in findings
    ):
        findings.append(finding)
    return {"findings": findings}


async def discard_node(state: SpecialistState) -> dict:
    return {}


async def advance_hypothesis_node(state: SpecialistState) -> dict:
    return {"hypothesis_index": state["hypothesis_index"] + 1}


async def generate_review_node(state: ReviewState) -> dict:
    # Assemble only verified findings; a final free-form model call could add
    # claims that have never passed through the verification loop.
    runs = sorted(state["specialist_runs"], key=lambda run: SPECIALISTS.index(run["specialist"]))
    # Specialists may confirm the same defect; keep one per location, the most severe.
    merged = {}
    for finding in (finding for run in runs for finding in run["findings"]):
        existing = merged.get((finding.file, finding.line))
        if existing is None or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[existing.severity]:
            merged[(finding.file, finding.line)] = finding
    findings = list(merged.values())
    return {
        "review": ReviewResult(findings=findings), "findings": findings,
        "evaluations": [item for run in runs for item in run["evaluations"]],
        "tool_calls": state["tool_calls"] + sum(run["tool_calls"] for run in runs),
        "tool_records": state["tool_records"] + [record for run in runs for record in run["tool_records"]],
    }
