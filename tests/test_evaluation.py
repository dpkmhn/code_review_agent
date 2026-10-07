import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.evaluation.dataset import FixtureRepository, load_dataset
from app.evaluation.metrics import UsageMeter, findings_hash, summarize
from app.evaluation.runner import MODES, OfflineReviewer, run_suite
from app.graph import nodes
from app.schemas.ai_review import ReviewFinding, ReviewResult
from app.schemas.context import ContextRequest
from app.schemas.hypothesis import BugHypothesis, EvidenceEvaluation, HypothesisResult

DATASET = Path(__file__).resolve().parents[1] / "evaluation/dataset.json"


class DatasetTests(unittest.IsolatedAsyncioTestCase):
    async def test_dataset_has_20_buggy_10_clean_pinned_valid_snapshots(self):
        cases, digest = load_dataset(DATASET)
        self.assertEqual(len(digest), 64)
        self.assertEqual(sum(bool(case.expected_bugs) for case in cases), 20)
        self.assertEqual(sum(not case.expected_bugs for case in cases), 10)
        for index, case in enumerate(cases, 1):
            pr = case.pull_request(index)
            self.assertNotEqual(pr.head_sha, pr.base_sha)
            self.assertTrue(all(file.patch for file in pr.files))
            self.assertNotIn("expected_bugs", pr.model_dump())
            for snapshot in (case.base, case.head):
                for path, source in snapshot.items():
                    compile(source, path, "exec")  # Check syntax without executing fixture code.
            repository = FixtureRepository(case)
            self.assertEqual(await repository.read_file(owner=pr.owner, repository=pr.repository,
                             path="subject.py", ref=pr.head_sha), case.head["subject.py"])
            with self.assertRaises(KeyError):
                await repository.read_file(owner=pr.owner, repository=pr.repository,
                                           path="subject.py", ref="main")
            with self.assertRaises(ValueError):
                await repository.search_code(owner="other", repository=pr.repository, query="def")


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.cases, _ = load_dataset(DATASET)

    def run_record(self, case, findings, **updates):
        run = dict(case_id=case.id, mode="verification", status="completed", findings=findings,
                   verified_findings=len(findings), tool_calls=2, latency_seconds=1,
                   usage=UsageMeter().report())
        run.update(updates)
        return run

    def test_semantic_adjudication_duplicate_and_clean_false_positives(self):
        bug, clean = self.cases[0], self.cases[-1]
        findings = [{"file": "subject.py", "line": 2}] * 2
        runs = [self.run_record(bug, findings), self.run_record(clean, findings[:1])]
        initial = summarize(self.cases, runs)["verification"]
        self.assertIsNone(initial["bug_recall"])
        self.assertEqual(initial["unadjudicated_findings"], 2)
        labels = {f"{bug.id}/verification": {"findings_sha256": findings_hash(findings),
                                            "matches": [bug.expected_bugs[0].id] * 2}}
        scored = summarize(self.cases, runs, labels)["verification"]
        self.assertEqual(scored["bug_recall"], 1)
        self.assertEqual(scored["false_positive_findings"], 2)
        self.assertEqual(scored["false_positive_rate"], 1)
        self.assertEqual(scored["clean_pr_accuracy"], 0)
        labels[f"{bug.id}/verification"]["findings_sha256"] = "stale"
        with self.assertRaisesRegex(ValueError, "Stale"):
            summarize(self.cases, runs, labels)

    def test_failed_clean_pr_is_not_a_correct_empty_review(self):
        runs = [self.run_record(self.cases[-1], [], status="failed", tool_calls=None)]
        result = summarize(self.cases, runs)["verification"]
        self.assertEqual(result["failed"], 1)
        self.assertIsNone(result["clean_pr_accuracy"])
        self.assertIsNone(result["mean_tool_calls"])

    def test_chat_and_responses_usage_cached_cost_and_missing_usage(self):
        meter = UsageMeter()
        meter(SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                    input_tokens_details=SimpleNamespace(cached_tokens=40))))
        meter(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=10))))
        usage = meter.report([2, 1, 4])
        self.assertEqual(usage["input_tokens"], 150)
        self.assertEqual(usage["output_tokens"], 30)
        self.assertAlmostEqual(usage["estimated_cost_usd"], 0.00037)
        self.assertIsNone(meter.report()["estimated_cost_usd"])
        meter(SimpleNamespace(usage=None))
        self.assertIsNone(meter.report([2, 1, 4])["estimated_cost_usd"])
        self.assertIsNone(meter.report()["input_tokens"])


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_mode_passes_pinned_local_content_to_review(self):
        cases, _ = load_dataset(DATASET)
        case = next(case for case in cases if case.id == "caller-contract")
        captured = []

        class ContextReviewer(OfflineReviewer):
            async def analyse_context_need(self, pull_request):
                return ContextRequest(needs_context=True, reason="Inspect caller", queries=["customer_name"])

            async def review_pull_request(self, pull_request, relevant_context=None):
                captured.extend(relevant_context)
                return ReviewResult(findings=[])

        with patch("httpx.AsyncClient.send", side_effect=AssertionError("No network allowed")):
            runs = await run_suite([case], ["context"], offline=True,
                                   service_factory=lambda meter: (ContextReviewer(), ContextReviewer()))
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["tool_calls"], 2)
        self.assertTrue(any(case.head["caller.py"] in text for text in captured))
        self.assertTrue(any(case.pull_request(1).head_sha in text for text in captured))

    async def test_all_modes_work_without_network_and_keep_api_dependencies(self):
        cases, _ = load_dataset(DATASET)
        original = (nodes.review_service, nodes.context_service, nodes.github_client)
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("No network allowed")):
            runs = await run_suite(cases, MODES, offline=True)
        self.assertEqual(len(runs), 90)
        self.assertTrue(all(run["status"] == "completed" for run in runs))
        self.assertTrue(all(run["usage"]["model_calls"] == 0 for run in runs))
        self.assertEqual(original, (nodes.review_service, nodes.context_service, nodes.github_client))

    async def test_verification_uses_local_toolnode_and_evidence_gate(self):
        cases, _ = load_dataset(DATASET)
        case = cases[0]
        finding = ReviewFinding(file="subject.py", line=2, severity="high", title="Empty list division",
                                explanation="An empty list triggers division by zero.")

        class ScriptedReviewer(OfflineReviewer):
            async def form_hypotheses(self, pull_request, relevant_context):
                return HypothesisResult(hypotheses=[BugHypothesis(
                    description=finding.explanation, file=finding.file, line=2,
                    confidence="high", verification_plan=[])])

            async def request_verification_tools(self, messages, tools):
                if any(isinstance(message, ToolMessage) for message in messages):
                    return AIMessage(content="Enough evidence")
                return AIMessage(content="", tool_calls=[{
                    "id": "read-head", "name": "read_file",
                    "args": {"path": "subject.py", "revision": "head"}}])

            async def evaluate_evidence(self, hypothesis, evidence):
                # Evidence order: diff, repository tree, then tool results.
                if ([item.source for item in evidence] != ["diff", "list_tree", "read_file"]
                        or evidence[2].content != case.head["subject.py"]):
                    raise AssertionError("Local repository inspection did not reach evaluation")
                return EvidenceEvaluation(status="confirmed", explanation="Inspected head and diff",
                                          evidence_indices=[0, 2], finding=finding)

        with patch("httpx.AsyncClient.send", side_effect=AssertionError("No network allowed")):
            runs = await run_suite([case], ["verification"], offline=True,
                                   service_factory=lambda meter: (ScriptedReviewer(), OfflineReviewer()))
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["verified_findings"], 1)
        self.assertEqual(runs[0]["tool_calls"], 1)
        self.assertEqual(runs[0]["tool_records"][0]["evidence"]["source"], "read_file")

    async def test_errors_do_not_abort_suite_or_leak_exception_text(self):
        cases, _ = load_dataset(DATASET)

        class BrokenReviewer(OfflineReviewer):
            async def review_pull_request(self, *args, **kwargs):
                raise ValueError("secret token")

        runs = await run_suite(cases[:2], ["diff_only"], offline=True,
                               service_factory=lambda meter: (BrokenReviewer(), OfflineReviewer()))
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(run["status"] == "failed" for run in runs))
        self.assertNotIn("secret token", json.dumps(runs))
        self.assertIsNone(runs[0]["usage"]["input_tokens"])


if __name__ == "__main__":
    unittest.main()
