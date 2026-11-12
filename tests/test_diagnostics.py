"""Testy pętli diagnostycznej bez sieci i bez bibliotek zewnętrznych."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.check_connections import conversation, describe_error


def tool_call(name="search", identifier="call_1", arguments='{"name":"Kostka"}'):
    return {"id": identifier, "type": "function",
            "function": {"name": name, "arguments": arguments}}


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tools = [SimpleNamespace(name="search", description="Search", inputSchema={})]
        self.result = SimpleNamespace(
            isError=False, model_dump_json=lambda **_: '{"content":[{"text":"Q123"}]}',
        )
        self.session = SimpleNamespace(call_tool=AsyncMock(return_value=self.result))
        self.config = {"mcp_timeout": 1}

    async def run_conversation(self, responses):
        with patch("scripts.check_connections.complete", new_callable=AsyncMock) as model:
            model.side_effect = responses
            answer = await conversation(
                None, self.config, self.session, self.tools, "Pytanie", [],
            )
            return answer, model

    async def test_multiple_calls_retain_ids_in_model_context(self):
        answer, model = await self.run_conversation([
            {"role": "assistant", "content": None,
             "tool_calls": [tool_call(), tool_call(identifier="call_2")]},
            {"role": "assistant", "content": "Znaleziono Q123."},
        ])
        self.assertEqual(answer, "Znaleziono Q123.")
        self.assertEqual(self.session.call_tool.await_count, 2)
        messages = model.await_args.args[2]
        self.assertEqual([item["tool_call_id"] for item in messages if item["role"] == "tool"],
                         ["call_1", "call_2"])

    async def test_unknown_tool_is_never_executed(self):
        with self.assertRaises(ValueError):
            await self.run_conversation([{"tool_calls": [tool_call(name="unknown")]}])
        self.session.call_tool.assert_not_awaited()

    async def test_invalid_arguments_are_never_executed(self):
        with self.assertRaises(ValueError):
            await self.run_conversation([{"tool_calls": [tool_call(arguments="[]")]}])
        self.session.call_tool.assert_not_awaited()

    async def test_answer_without_tool_is_not_integration_success(self):
        with self.assertRaises(ValueError):
            await self.run_conversation([{"content": "Odpowiedź z pamięci"}])

    async def test_failed_tool_is_not_integration_success(self):
        self.result.isError = True
        with self.assertRaises(ValueError):
            await self.run_conversation([
                {"tool_calls": [tool_call()]}, {"content": "Nie znaleziono"},
            ])

    async def test_round_limit_stops_further_tool_execution(self):
        with self.assertRaises(ValueError):
            await self.run_conversation([{"tool_calls": [tool_call()]}] * 11)
        self.assertEqual(self.session.call_tool.await_count, 10)


class ErrorTests(unittest.TestCase):
    def test_exception_text_is_not_reported(self):
        error = ExceptionGroup("secret", [ValueError("secret")])
        self.assertNotIn("secret", str(describe_error(error)))


if __name__ == "__main__":
    unittest.main()
