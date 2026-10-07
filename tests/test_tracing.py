import os
import unittest
from unittest.mock import MagicMock, patch

import httpx
import langsmith
from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from typing_extensions import TypedDict

from app.config import Settings, export_langsmith_environment, settings
from app.schemas.github import PullRequestData
from app.services import openai_client
from app.services.context_service import ContextService

RESPONSE = {
    "id": "resp_1", "object": "response", "created_at": 0, "status": "completed",
    "model": "gpt-6-luna", "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    "output": [{
        "id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "annotations": [],
                     "text": '{"needs_context": false, "reason": "Self-contained", "queries": []}'}],
    }],
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
              "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
}


class State(TypedDict, total=False):
    pull_request: PullRequestData
    needs_context: bool


class TracingConfigTests(unittest.TestCase):
    def test_tracing_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "langsmith_api_key"):
            Settings(github_token="t", openai_api_key="t", langsmith_tracing=True, _env_file=None)

    def test_export_sets_sdk_environment_only_when_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            export_langsmith_environment(Settings(github_token="t", openai_api_key="t", _env_file=None))
            self.assertNotIn("LANGSMITH_API_KEY", os.environ)
            export_langsmith_environment(Settings(
                github_token="t", openai_api_key="t", langsmith_tracing=True,
                langsmith_api_key="ls-key", langsmith_project="reviews", _env_file=None))
            self.assertEqual(os.environ["LANGSMITH_TRACING"], "true")
            self.assertEqual(os.environ["LANGSMITH_API_KEY"], "ls-key")
            self.assertEqual(os.environ["LANGSMITH_PROJECT"], "reviews")


class TracedReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_call_is_traced_under_graph_node(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=RESPONSE))

        def client():
            return AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(transport=transport))

        ls_client = MagicMock(spec=langsmith.Client)
        with patch.object(settings, "langsmith_tracing", True), \
                patch.object(openai_client, "AsyncOpenAI", side_effect=lambda **_: client()):
            context = ContextService()

            async def analyse(state):
                request = await context.analyse_context_need(state["pull_request"])
                return {"needs_context": request.needs_context}

            builder = StateGraph(State)
            builder.add_node("analyse_context", analyse)
            builder.add_edge(START, "analyse_context")
            builder.add_edge("analyse_context", END)
            pr = PullRequestData(
                owner="octo", repository="repo", number=1, title="Fix", body=None,
                base_branch="main", head_branch="fix", base_sha="a", head_sha="b", files=[])
            with langsmith.tracing_context(enabled=True, client=ls_client, project_name="test"):
                result = await builder.compile().ainvoke(
                    {"pull_request": pr}, config={"metadata": {"repository": "octo/repo"}})
            wait_for_all_tracers()
            await context.client.close()

        self.assertFalse(result["needs_context"])
        runs = {call.kwargs["name"]: call.kwargs for call in ls_client.create_run.call_args_list}
        llm_run = next(run for run in runs.values() if run["run_type"] == "llm")
        node_run = runs["analyse_context"]
        self.assertEqual(llm_run["parent_run_id"], node_run["id"])
