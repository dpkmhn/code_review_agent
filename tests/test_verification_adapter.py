import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.graph import nodes


class VerificationAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_schemas_hide_repository_identity_and_injected_state(self):
        for tool in nodes.verification_tools:
            schema = convert_to_openai_tool(tool)["function"]["parameters"]["properties"]
            self.assertNotIn("state", schema)
            self.assertNotIn("repository", schema)
            self.assertNotIn("owner", schema)
            self.assertNotIn("ref", schema)

    async def test_openai_calls_round_trip_with_tool_results(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[SimpleNamespace(id="next", function=SimpleNamespace(
                name="read_file", arguments='{"path":"helper.py","revision":"base"}',
            ))],
        ))])
        messages = [HumanMessage(content="Investigate"), AIMessage(content="", tool_calls=[{
            "id": "first", "name": "search_code", "args": {"query": "helper"},
        }]), ToolMessage(content='{"candidate_paths_default_branch": []}', tool_call_id="first")]
        with patch.object(nodes.review_service.client.chat.completions, "create",
                          AsyncMock(return_value=response)) as create:
            result = await nodes.review_service.request_verification_tools(messages, nodes.verification_tools)
        sent = create.await_args.kwargs
        self.assertEqual(sent["model"], "gpt-6-luna")
        self.assertEqual(sent["reasoning_effort"], "none")
        self.assertEqual([item["role"] for item in sent["messages"]],
                         ["system", "user", "assistant", "tool"])
        self.assertEqual(json.loads(sent["messages"][2]["tool_calls"][0]["function"]["arguments"]),
                         {"query": "helper"})
        self.assertEqual(sent["messages"][3]["tool_call_id"], "first")
        self.assertEqual(result.tool_calls[0]["args"], {"path": "helper.py", "revision": "base"})

    async def test_malformed_arguments_become_a_non_executable_schema_error(self):
        for arguments in ("{", "[]"):
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=None, tool_calls=[SimpleNamespace(id="bad", function=SimpleNamespace(
                    name="read_file", arguments=arguments,
                ))],
            ))])
            with patch.object(nodes.review_service.client.chat.completions, "create",
                              AsyncMock(return_value=response)):
                result = await nodes.review_service.request_verification_tools([], nodes.verification_tools)
            self.assertEqual(result.tool_calls[0]["args"], {"_invalid_arguments": arguments})


if __name__ == "__main__":
    unittest.main()
