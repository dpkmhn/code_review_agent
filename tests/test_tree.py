import unittest

import httpx

from app.github_client import GitHubClient
from app.graph import nodes
from app.schemas.github import PullRequestData, PullRequestFile
from app.tools.tree import tree_evidence, tree_prompt


class TreeTests(unittest.IsolatedAsyncioTestCase):
    def pull_request(self):
        return PullRequestData(
            owner="o", repository="r", number=1, title="t", body=None,
            base_branch="master", head_branch="b", base_sha="base", head_sha="head",
            files=[PullRequestFile(filename="frontend/index.html", status="modified",
                                   additions=1, deletions=1, changes=2, patch="@@")],
        )

    async def test_client_lists_blobs_only(self):
        payload = {"truncated": False, "tree": [
            {"path": "frontend", "type": "tree"},
            {"path": "frontend/src/main.tsx", "type": "blob"},
            {"path": "frontend/index.html", "type": "blob"},
        ]}
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=payload)
        client = GitHubClient(transport=httpx.MockTransport(handle))
        result = await client.list_tree(owner="o", repository="r", ref="head")
        self.assertEqual(result["paths"], ["frontend/index.html", "frontend/src/main.tsx"])
        self.assertEqual(requests[0].url.params["recursive"], "1")
        self.assertTrue(requests[0].url.path.endswith("/git/trees/head"))

    def test_evidence_filters_directory_and_records_absence(self):
        result = {"paths": ["frontend/src/main.tsx", "backend/app.py"], "truncated": False}
        evidence = tree_evidence(result, "head", "frontend/src")
        self.assertTrue(evidence.succeeded)
        self.assertIn("frontend/src/main.tsx", evidence.content)
        self.assertNotIn("main.py", evidence.content)
        self.assertNotIn("backend", evidence.content)

    def test_truncated_tree_is_flagged(self):
        evidence = tree_evidence({"paths": ["a"], "truncated": True}, "head")
        self.assertIn("not proven absent", evidence.content)

    def test_prompt_puts_nearby_paths_first_and_bounds_size(self):
        paths = [f"lib/f{i}.py" for i in range(400)] + ["frontend/src/main.tsx"]
        text = tree_prompt({"paths": paths, "truncated": False}, "head", ["frontend/index.html"])
        self.assertIn("frontend/src/main.tsx", text)
        self.assertIn("Partial view", text)
        self.assertLessEqual(len(text.splitlines()), 303)

    async def test_load_tree_node_feeds_prompt_and_evidence(self):
        class Repo:
            async def list_tree(self, *, owner, repository, ref):
                return {"paths": ["frontend/src/main.tsx"], "truncated": False}
        state = {"pull_request": self.pull_request(), "changed_files": ["frontend/index.html"]}
        result = await nodes.load_tree_node(state, repository=Repo())
        self.assertEqual(result["tree_evidence"].source, "list_tree")
        self.assertIn("frontend/src/main.tsx", result["tree_context"])

    async def test_failed_tree_load_records_nothing(self):
        class Repo:
            async def list_tree(self, *, owner, repository, ref):
                raise httpx.ConnectError("down")
        state = {"pull_request": self.pull_request(), "changed_files": []}
        result = await nodes.load_tree_node(state, repository=Repo())
        self.assertEqual(result, {"tree_evidence": None, "tree_context": ""})

    async def test_selected_hypothesis_includes_tree_evidence(self):
        from app.schemas.hypothesis import BugHypothesis
        hypothesis = BugHypothesis(description="d", file="frontend/index.html", line=1,
                                   confidence="high", verification_plan=[])
        tree = tree_evidence({"paths": ["frontend/src/main.tsx"], "truncated": False}, "head")
        state = {"pull_request": self.pull_request(), "hypotheses": [hypothesis],
                 "hypothesis_index": 0, "relevant_context": [], "tool_calls": 0, "tool_budget": 30,
                 "tree_evidence": tree}
        result = await nodes.select_hypothesis_node(state)
        self.assertEqual([item.source for item in result["evidence"]], ["diff", "list_tree"])


if __name__ == "__main__":
    unittest.main()
