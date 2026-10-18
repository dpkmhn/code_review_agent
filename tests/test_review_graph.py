import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from app.graph import nodes
from app.graph.review_graph import review_graph
from app.schemas.ai_review import ReviewFinding
from app.schemas.context import ContextRequest
from app.schemas.github import PullRequestData, PullRequestFile
from app.schemas.hypothesis import (
    BugHypothesis, EvidenceEvaluation, HypothesisResult, VerificationStep,
)
from app.schemas.specialist import SpecialistSelection
from app.services.context_service import ContextService
from app.services.sandbox_service import ExecutionResult, SandboxService


class ReviewGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pull_request = PullRequestData(
            owner="owner", repository="repo", number=1, title="Change validation",
            body=None, base_branch="main", head_branch="fix",
            base_sha="base", head_sha="head", files=[PullRequestFile(
                filename="helper.py", status="modified", additions=1, deletions=1,
                changes=2, patch="@@ -1 +1 @@\n-return value or 0\n+return value",
            )],
        )

    def hypothesis(self, line=1):
        return BugHypothesis(
            description="None may reach the caller", file="helper.py", line=line,
            confidence="medium", verification_plan=[VerificationStep(
                path="helper.py", revision="head", reason="Inspect implementation",
            ), VerificationStep(path="helper.py", revision="base", reason="Check regression")],
        )

    def evaluation(self, status="confirmed", line=1, indices=None):
        return EvidenceEvaluation(
            status=status, explanation="Checked behavior", evidence_indices=indices or [0, 1],
            finding=ReviewFinding(
                file="helper.py", line=line, severity="high", title=f"None reaches caller {line}",
                explanation="A missing value causes a runtime error in the caller.",
            ) if status == "confirmed" else None,
        )

    async def invoke(self, hypotheses=(), evaluations=(), context=None, read=None, reviewer=None,
                     search=None, specialists=()):
        with ExitStack() as stack:
            self.supervisor_mock = stack.enter_context(patch.object(
                nodes.review_service, "select_specialists", AsyncMock(return_value=SpecialistSelection(
                    specialists=list(specialists), reason="Scoped change")),
            ))
            self.context_mock = stack.enter_context(patch.object(
                nodes.context_service, "analyse_context_need", AsyncMock(return_value=context or
                    ContextRequest(needs_context=False, reason="Self-contained")),
            ))
            self.hypotheses_mock = stack.enter_context(patch.object(
                nodes.review_service, "form_hypotheses", hypotheses if callable(hypotheses)
                else AsyncMock(return_value=HypothesisResult(hypotheses=list(hypotheses))),
            ))
            self.evaluate_mock = stack.enter_context(patch.object(
                nodes.review_service, "evaluate_evidence", evaluations if callable(evaluations)
                else AsyncMock(side_effect=list(evaluations)),
            ))
            self.read_mock = stack.enter_context(patch.object(
                nodes.github_client, "read_file", read or AsyncMock(return_value="return value"),
            ))
            self.search_mock = stack.enter_context(patch.object(
                nodes.github_client, "search_code", search or AsyncMock(return_value=[{"path": "caller.py"}]),
            ))
            self.baseline_mock = stack.enter_context(patch.object(
                nodes.review_service, "review_pull_request", AsyncMock(),
            ))
            self.reviewer_mock = stack.enter_context(patch.object(
                nodes.review_service, "request_verification_tools", reviewer or AsyncMock(
                    side_effect=[message for index, hypothesis in enumerate(hypotheses)
                                 for message in (AIMessage(content="", tool_calls=[{
                                     "id": f"call-{index}-{step_index}", "name": "read_file",
                                     "args": {"path": step.path, "revision": step.revision},
                                 } for step_index, step in enumerate(hypothesis.verification_plan)]),
                                     AIMessage(content="Enough evidence"))]),
            ))
            result = await review_graph.ainvoke({"pull_request": self.pull_request})
        self.runs = {run["specialist"]: run for run in result["specialist_runs"]}
        self.run = self.runs.get("correctness")
        return result

    async def test_no_hypotheses_generates_empty_review(self):
        result = await self.invoke()
        self.assertEqual(result["review"].findings, [])
        self.search_mock.assert_not_awaited()
        self.read_mock.assert_not_awaited()
        self.evaluate_mock.assert_not_awaited()
        self.baseline_mock.assert_not_awaited()

    async def test_context_is_read_at_head_and_reaches_hypotheses(self):
        result = await self.invoke(context=ContextRequest(
            needs_context=True, reason="Check callers", queries=["helper"],
        ))
        self.search_mock.assert_awaited_once_with(owner="owner", repository="repo", query="helper")
        self.read_mock.assert_awaited_once_with(
            owner="owner", repository="repo", path="caller.py", ref="head",
        )
        self.assertIn("return value", result["relevant_context"][1])
        self.hypotheses_mock.assert_awaited_once_with(
            pull_request=self.pull_request, relevant_context=result["relevant_context"],
            specialist="correctness",
        )
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual([record.name for record in result["tool_records"]], ["search_code", "read_file"])
        self.assertEqual(result["tool_records"][1].evidence.ref, "head")

    async def test_failed_context_search_is_recorded_and_review_continues(self):
        request = httpx.Request("GET", "https://api.github.com/search/code")
        rate_limited = httpx.HTTPStatusError("rate limited", request=request,
                                             response=httpx.Response(403, request=request))
        result = await self.invoke(context=ContextRequest(
            needs_context=True, reason="Check callers", queries=["helper", "caller"],
        ), search=AsyncMock(side_effect=[rate_limited, [{"path": "caller.py"}]]))
        self.assertEqual(result["review"].findings, [])
        self.assertEqual([record.name for record in result["tool_records"]],
                         ["search_code", "search_code", "read_file"])
        self.assertIn("Search failed", result["tool_records"][0].result)
        self.assertIn("not evidence", result["relevant_context"][0])
        self.assertEqual(result["tool_calls"], 3)
        self.hypotheses_mock.assert_awaited_once()

    async def test_confirmed_finding_uses_head_and_base_evidence(self):
        result = await self.invoke([self.hypothesis()], [self.evaluation()])
        self.assertEqual(len(result["review"].findings), 1)
        self.assertEqual([item.ref for item in self.run["evidence"]], ["head", "head", "base"])
        self.assertEqual(self.run["hypothesis_index"], 1)
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual([record.evidence.ref for record in result["tool_records"]], ["head", "base"])
        self.assertTrue(all(record.status == "completed" for record in result["tool_records"]))
        self.assertEqual(self.evaluate_mock.await_args.kwargs["evidence"], self.run["evidence"])

    async def test_mixed_outcomes_advance_and_only_store_confirmed(self):
        result = await self.invoke(
            [self.hypothesis(line) for line in range(1, 4)],
            [self.evaluation("rejected"), self.evaluation(line=2), self.evaluation("inconclusive")],
        )
        self.assertEqual([finding.line for finding in result["review"].findings], [2])
        self.assertEqual([item.status for item in result["evaluations"]],
                         ["rejected", "confirmed", "inconclusive"])
        self.assertEqual(self.run["hypothesis_index"], 3)
        self.assertEqual(self.evaluate_mock.await_count, 3)

    async def test_five_hypotheses_terminate_without_recursion_error(self):
        result = await self.invoke(
            [self.hypothesis(line) for line in range(1, 6)],
            [self.evaluation(line=line) for line in range(1, 6)],
        )
        self.assertEqual(len(result["review"].findings), 5)
        self.assertEqual(self.run["hypothesis_index"], 5)
        records = result["tool_records"]
        self.assertEqual(len(records), 10)
        self.assertEqual(len({record.call_id for record in records}), 10)

    async def test_failed_inspection_cannot_support_confirmation(self):
        error = httpx.HTTPStatusError("Missing", request=httpx.Request("GET", "https://example.test"),
                                    response=httpx.Response(404))
        # A directory or oversized file raises ValueError from GitHubClient.read_file.
        for failure in (error, ValueError("Path is not a regular file.")):
            with self.subTest(failure=type(failure).__name__):
                result = await self.invoke([self.hypothesis()], [self.evaluation(indices=[1])],
                                           read=AsyncMock(side_effect=failure))
                self.assertFalse(self.run["evidence"][1].succeeded)
                self.assertEqual(result["evaluations"][0].status, "inconclusive")
                self.assertEqual(result["review"].findings, [])

    async def test_incomplete_sandbox_execution_cannot_support_confirmation(self):
        reviewer = AsyncMock(side_effect=[AIMessage(content="", tool_calls=[{
            "id": "test", "name": "run_test",
            "args": {"path": "tests/test_helper.py", "revision": "head"},
        }]), AIMessage(content="Done")])
        with patch.object(SandboxService, "execute", AsyncMock(
            return_value=ExecutionResult("collection error", 2, completed=False)
        )):
            result = await self.invoke([self.hypothesis()], [self.evaluation(indices=[1])],
                                       reviewer=reviewer)
        self.assertEqual(self.run["evidence"][1].source, "run_test")
        self.assertFalse(self.run["evidence"][1].succeeded)
        self.assertEqual(result["review"].findings, [])

    async def test_invalid_evidence_index_is_discarded(self):
        result = await self.invoke([self.hypothesis()], [self.evaluation(indices=[99])])
        self.assertEqual(result["review"].findings, [])

    async def test_missing_evidence_or_finding_is_discarded(self):
        for update in ({"evidence_indices": []}, {"finding": None}):
            result = await self.invoke([self.hypothesis()], [self.evaluation().model_copy(update=update)])
            self.assertEqual(result["review"].findings, [])

    async def test_finding_outside_hypothesis_location_is_discarded(self):
        result = await self.invoke([self.hypothesis()], [self.evaluation(line=99)])
        self.assertEqual(result["review"].findings, [])

    async def test_hypothesis_outside_changed_files_is_filtered(self):
        hypothesis = self.hypothesis().model_copy(update={"file": "unrelated.py"})
        result = await self.invoke([hypothesis])
        self.assertEqual(result["review"].findings, [])
        self.evaluate_mock.assert_not_awaited()

    async def test_duplicate_findings_are_stored_once(self):
        result = await self.invoke([self.hypothesis(), self.hypothesis()],
                                   [self.evaluation(), self.evaluation()])
        self.assertEqual(len(result["review"].findings), 1)

    async def test_graph_runs_do_not_share_accumulators(self):
        await self.invoke([self.hypothesis()], [self.evaluation()])
        result = await self.invoke()
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["evaluations"], [])
        self.assertEqual(result["tool_calls"], 0)

    async def test_context_queries_and_reads_are_bounded(self):
        result = await self.invoke(context=ContextRequest(
            needs_context=True, reason="Investigate", queries=[f"query{i}" for i in range(20)],
        ))
        self.assertEqual(self.search_mock.await_count, nodes.MAX_CONTEXT_QUERIES)
        self.assertEqual(self.read_mock.await_count, 1)  # Deduplicated candidate path.
        self.assertEqual(result["tool_calls"], 4)

    async def test_tool_budget_prevents_further_reads(self):
        with patch.object(nodes.review_service, "request_verification_tools", AsyncMock()) as reviewer:
            result = await nodes.reviewer_node({
                "specialist": "correctness", "tool_budget": 10, "tool_calls": 10,
                "verification_tool_calls": 0, "verification_rounds": 0,
            })
        reviewer.assert_not_awaited()
        self.assertEqual(result, {})

    async def test_reviewer_without_tools_goes_directly_to_evaluation(self):
        result = await self.invoke([self.hypothesis()], [self.evaluation(indices=[0])],
                                   reviewer=AsyncMock(return_value=AIMessage(content="Diff is enough")))
        self.read_mock.assert_not_awaited()
        self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(len(self.run["evidence"]), 1)

    async def test_search_then_read_uses_tool_results_in_next_round(self):
        reviewer = AsyncMock(side_effect=[
            AIMessage(content="", tool_calls=[{"id": "search", "name": "search_code",
                                               "args": {"query": "helper"}}]),
            AIMessage(content="", tool_calls=[{"id": "read", "name": "read_file",
                                               "args": {"path": "caller.py", "revision": "head"}}]),
            AIMessage(content="Done"),
        ])
        result = await self.invoke([self.hypothesis()], [self.evaluation(indices=[1])], reviewer=reviewer)
        self.assertIn("candidate_paths_default_branch", reviewer.await_args_list[1].kwargs["messages"][-1].content)
        self.assertEqual([item.source for item in self.run["evidence"]], ["diff", "read_file"])
        self.assertEqual(result["tool_calls"], 2)

    async def test_unknown_and_invalid_tools_do_not_execute_or_create_evidence(self):
        reviewer = AsyncMock(side_effect=[AIMessage(content="", tool_calls=[
            {"id": "unknown", "name": "arbitrary_command", "args": {}},
            {"id": "invalid", "name": "read_file", "args": {"path": "helper.py", "revision": "main"}},
            {"id": "traversal", "name": "read_file", "args": {"path": "../secret", "revision": "head"}},
            {"id": "scope", "name": "search_code", "args": {"query": "helper repo:other/repo"}},
        ]), AIMessage(content="Done")])
        result = await self.invoke([self.hypothesis()], [self.evaluation(indices=[1])], reviewer=reviewer)
        self.read_mock.assert_not_awaited()
        self.search_mock.assert_not_awaited()
        self.assertEqual(result["tool_calls"], 4)
        self.assertEqual(len(self.run["evidence"]), 1)
        self.assertEqual(result["review"].findings, [])

    async def test_oversized_batch_is_reserved_before_tool_execution(self):
        reviewer = AsyncMock(return_value=AIMessage(content="", tool_calls=[{
            "id": f"read-{index}", "name": "read_file",
            "args": {"path": "helper.py", "revision": "head"},
        } for index in range(20)]))
        result = await self.invoke([self.hypothesis()], [self.evaluation()], reviewer=reviewer)
        self.assertEqual(self.read_mock.await_count, nodes.MAX_VERIFICATION_TOOL_CALLS)
        self.assertEqual(reviewer.await_count, 1)
        self.assertEqual(result["tool_calls"], nodes.MAX_VERIFICATION_TOOL_CALLS)

    async def test_repeated_calls_stop_at_round_limit(self):
        count = 0
        async def request(**kwargs):
            nonlocal count
            count += 1
            return AIMessage(content="", tool_calls=[{
                "id": f"read-{count}", "name": "read_file",
                "args": {"path": "helper.py", "revision": "head"},
            }])
        await self.invoke([self.hypothesis()], [self.evaluation()],
                          reviewer=AsyncMock(side_effect=request))
        self.assertEqual(count, nodes.MAX_VERIFICATION_ROUNDS)
        self.assertEqual(len(self.run["evidence"]), 1 + count)

    async def test_global_budget_caps_batches_across_five_hypotheses(self):
        reviewer = AsyncMock(return_value=AIMessage(content="", tool_calls=[{
            "id": f"read-{index}", "name": "read_file",
            "args": {"path": "helper.py", "revision": "head"},
        } for index in range(6)]))
        result = await self.invoke(
            [self.hypothesis(line) for line in range(1, 6)],
            [self.evaluation(line=line) for line in range(1, 6)],
            context=ContextRequest(needs_context=True, reason="Check caller", queries=["helper"]),
            reviewer=reviewer,
        )
        self.assertEqual(result["tool_calls"], nodes.MAX_TOOL_CALLS)
        self.assertEqual(self.read_mock.await_count, 29)  # One context read + 28 verification reads.
        self.assertEqual(len(self.run["evidence"]), 5)  # Last hypothesis has four remaining calls.
        for call in reviewer.await_args_list:
            self.assertEqual(len(call.kwargs["messages"]), 1)  # Fresh per hypothesis.

    async def test_supervisor_always_runs_correctness_and_adds_selected_specialists(self):
        result = await self.invoke(specialists=["performance"])
        self.assertEqual(result["specialists"], ["correctness", "performance"])
        self.assertEqual(sorted(call.kwargs["specialist"] for call in self.hypotheses_mock.await_args_list),
                         ["correctness", "performance"])

    async def test_remaining_tool_budget_is_split_across_specialists(self):
        result = await self.invoke(specialists=["security", "performance"], context=ContextRequest(
            needs_context=True, reason="Check callers", queries=["helper"],
        ))
        self.assertEqual(result["tool_calls"], 2)
        budgets = {name: run["tool_budget"] for name, run in self.runs.items()}
        self.assertEqual(budgets, {"correctness": 10, "security": 9, "performance": 9})

    async def test_parallel_specialists_merge_verified_findings(self):
        by_specialist = {
            "correctness": [self.hypothesis(1)],
            "security": [self.hypothesis(1), self.hypothesis(2)],
            "performance": [self.hypothesis(3)],
        }
        severities = {"correctness": "medium", "security": "critical", "performance": "low"}

        async def hypotheses(*, pull_request, relevant_context, specialist):
            return HypothesisResult(hypotheses=by_specialist[specialist])

        async def evaluate(*, hypothesis, evidence, specialist):
            evaluation = self.evaluation(line=hypothesis.line, indices=[1])
            return evaluation.model_copy(update={"finding": evaluation.finding.model_copy(update={
                "severity": severities[specialist], "title": f"{specialist} {hypothesis.line}"})})

        async def reviewer(*, messages, tools, specialist):
            # Read once, then stop: deterministic regardless of parallel scheduling.
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="Enough evidence")
            return AIMessage(content="", tool_calls=[{"id": f"{specialist}-read", "name": "read_file",
                                                      "args": {"path": "helper.py", "revision": "head"}}])

        result = await self.invoke(AsyncMock(side_effect=hypotheses), AsyncMock(side_effect=evaluate),
                                   reviewer=AsyncMock(side_effect=reviewer),
                                   specialists=["security", "performance"])
        findings = result["review"].findings
        # Line 1 was confirmed twice; the more severe security finding wins.
        self.assertEqual([(item.line, item.category, item.severity) for item in findings],
                         [(1, "security", "critical"), (2, "security", "critical"), (3, "performance", "low")])
        self.assertEqual(len(result["evaluations"]), 4)
        self.assertEqual(result["tool_calls"], 4)
        call_ids = [record.call_id for record in result["tool_records"]]
        self.assertEqual(len(set(call_ids)), 4)
        self.assertEqual(sorted(record.specialist for record in result["tool_records"]),
                         ["correctness", "performance", "security", "security"])
        self.assertTrue(all(record.status == "completed" for record in result["tool_records"]))

    async def test_missing_structured_outputs_have_clear_errors(self):
        with patch.object(nodes.review_service.client.responses, "parse",
                          AsyncMock(return_value=SimpleNamespace(output_parsed=None))):
            with self.assertRaisesRegex(ValueError, "structured bug hypotheses"):
                await nodes.review_service.form_hypotheses(self.pull_request, [])
            with self.assertRaisesRegex(ValueError, "structured evidence evaluation"):
                await nodes.review_service.evaluate_evidence(self.hypothesis(), [])
            with self.assertRaisesRegex(ValueError, "structured specialist selection"):
                await nodes.review_service.select_specialists(self.pull_request)
        with patch.object(nodes.context_service.client.responses, "parse",
                          AsyncMock(return_value=SimpleNamespace(output_parsed=None))):
            with self.assertRaisesRegex(ValueError, "structured context request"):
                await nodes.context_service.analyse_context_need(self.pull_request)

    def test_hypothesis_and_verification_limits_are_enforced(self):
        with self.assertRaises(ValidationError):
            HypothesisResult(hypotheses=[self.hypothesis()] * 6)
        with self.assertRaises(ValidationError):
            BugHypothesis(**{**self.hypothesis().model_dump(),
                             "verification_plan": self.hypothesis().verification_plan * 2})

    def test_context_service_uses_configured_key(self):
        with (
            patch("app.services.openai_client.settings", SimpleNamespace(openai_api_key="test-key", langsmith_tracing=False)),
            patch("app.services.openai_client.AsyncOpenAI") as client,
        ):
            ContextService()
        client.assert_called_once_with(api_key="test-key")


if __name__ == "__main__":
    unittest.main()
