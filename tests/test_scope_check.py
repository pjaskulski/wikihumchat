import asyncio
import json

import httpx
import pytest

from config import Settings
from errors import ChatError
from model_client import ModelClient
from scope_policy import scope_messages


def document(arguments, name="classify_wikihum_scope"):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "scope", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }}]}


@pytest.mark.parametrize("decision", [False, True])
def test_scope_call_is_short_non_thinking_and_does_not_mutate_query_settings(decision):
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(200, json=document({"in_scope": decision}))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Odpowiedź",
        }}]})

    async def run():
        settings = Settings(model_api_key="test", model_enable_thinking=True, model_reasoning_effort="low",
                            model_max_output_tokens=15000, model_temperature=1.0, model_diagnostics_file="")
        async with ModelClient(settings, transport=httpx.MockTransport(handler)) as client:
            assert await client.check_scope("Pytanie", []) is decision
            await client.complete([{"role": "user", "content": "Pytanie"}], [], allow_tools=False)

    asyncio.run(run())
    assert payloads[0]["max_tokens"] == 256
    assert payloads[0]["temperature"] == 0.0
    assert payloads[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert len(payloads[0]["tools"]) == 1 and payloads[0]["tool_choice"] == "required"
    assert payloads[1]["max_tokens"] == 15000 and payloads[1]["temperature"] == 1.0
    assert payloads[1]["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}


@pytest.mark.parametrize("response", [
    document({"in_scope": "false"}), document({"in_scope": 1}), document({}),
    document({"in_scope": True, "extra": "nieobsługiwane"}),
    document({"in_scope": True}, name="search_items"),
    {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "true"}}]},
])
def test_scope_check_requires_a_valid_typed_decision(response):
    async def run():
        async with ModelClient(Settings(model_api_key="test", model_diagnostics_file=""),
                               transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as client:
            with pytest.raises(ChatError) as captured:
                await client.check_scope("Pytanie", [])
            assert captured.value.code == "scope_invalid_response"
    asyncio.run(run())


def test_scope_context_is_bounded_and_keeps_followup_and_recent_history():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i) + "x" * 5000}
               for i in range(8)]
    result = json.loads(scope_messages("Który z nich?", history)[1]["content"])
    assert result["question"] == "Który z nich?"
    assert len(result["history"]) == 4
    assert all(len(item["content"]) == 1000 for item in result["history"])
    assert result["history"][0]["content"].startswith("4")
    assert len(history[0]["content"]) == 5001
