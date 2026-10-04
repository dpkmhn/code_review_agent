import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.config import settings
from app.graph import nodes


class VerificationAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_schemas_hide_repository_identity_and_injected_state(self):
        for tool in nodes.verification_tools:
            schema = convert_to_openai_tool(tool)["function"]["parameters"]["properties"]
            self.assertNotIn("state", schema)
            self.assertNotIn("repository", schema)
            self.assertNotIn("owner", schema)
            self.assertNotIn("ref", schema)

    @staticmethod
    def response(*items, text=""):
        return SimpleNamespace(output=list(items), output_text=text, usage=None)

    @staticmethod
    def call(call_id, name, arguments):
        return SimpleNamespace(type="function_call", call_id=call_id, name=name, arguments=arguments)

    async def test_openai_calls_round_trip_with_tool_results(self):
        response = self.response(self.call("next", "read_file", '{"path":"helper.py","revision":"base"}'))
        messages = [HumanMessage(content="Investigate"), AIMessage(content="", tool_calls=[{
            "id": "first", "name": "search_code", "args": {"query": "helper"},
        }]), ToolMessage(content='{"candidate_paths_default_branch": []}', tool_call_id="first")]
        with patch.object(nodes.review_service.client.responses, "create",
                          AsyncMock(return_value=response)) as create:
            result = await nodes.review_service.request_verification_tools(messages, nodes.verification_tools)
        sent = create.await_args.kwargs
        self.assertEqual(sent["model"], "gpt-6-luna")
        self.assertEqual(sent["reasoning"], {"effort": "none"})
        self.assertNotIn("store", sent)
        self.assertIn("untrusted data", sent["instructions"])
        self.assertEqual([item.get("role", item.get("type")) for item in sent["input"]],
                         ["user", "function_call", "function_call_output"])
        self.assertEqual(json.loads(sent["input"][1]["arguments"]), {"query": "helper"})
        self.assertEqual(sent["input"][2]["call_id"], "first")
        self.assertEqual(sent["tools"][0]["type"], "function")
        self.assertEqual(sent["tools"][0]["name"], "read_file")
        self.assertEqual(result.tool_calls[0]["id"], "next")
        self.assertEqual(result.tool_calls[0]["args"], {"path": "helper.py", "revision": "base"})

    async def test_reasoning_items_are_requested_and_replayed_before_calls(self):
        reasoning = SimpleNamespace(type="reasoning", model_dump=lambda **_: {
            "type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque",
        })
        response = self.response(reasoning, self.call("next", "read_file",
                                                      '{"path":"helper.py","revision":"head"}'))
        with patch.object(settings, "verification_reasoning_effort", "medium"), \
                patch.object(nodes.review_service.client.responses, "create",
                             AsyncMock(return_value=response)) as create:
            first = await nodes.review_service.request_verification_tools(
                [HumanMessage(content="Investigate")], nodes.verification_tools)
            sent = create.await_args.kwargs
            self.assertEqual(sent["reasoning"], {"effort": "medium"})
            self.assertFalse(sent["store"])
            self.assertEqual(sent["include"], ["reasoning.encrypted_content"])
            await nodes.review_service.request_verification_tools([
                HumanMessage(content="Investigate"), first,
                ToolMessage(content="contents", tool_call_id="next"),
            ], nodes.verification_tools)
        replayed = create.await_args.kwargs["input"]
        self.assertEqual([item.get("role", item.get("type")) for item in replayed],
                         ["user", "reasoning", "function_call", "function_call_output"])
        self.assertEqual(replayed[1]["encrypted_content"], "opaque")

    async def test_malformed_arguments_become_a_non_executable_schema_error(self):
        for arguments in ("{", "[]"):
            response = self.response(self.call("bad", "read_file", arguments))
            with patch.object(nodes.review_service.client.responses, "create",
                              AsyncMock(return_value=response)):
                result = await nodes.review_service.request_verification_tools([], nodes.verification_tools)
            self.assertEqual(result.tool_calls[0]["args"], {"_invalid_arguments": arguments})


if __name__ == "__main__":
    unittest.main()
