import asyncio
import json

import httpx
import pytest

from config import Settings
from errors import ChatError
from model_client import ModelClient


def run_completion(handler, *, allow_tools=True, require_tools=False, thinking=False, effort=""):
    async def run():
        async with ModelClient(Settings(model_api_key="private-secret", model_diagnostics_file="",
                                        model_enable_thinking=thinking, model_reasoning_effort=effort),
                               transport=httpx.MockTransport(handler)) as client:
            return await client.complete(
                [{"role": "user", "content": "Pytanie"}], [], allow_tools=allow_tools,
                require_tools=require_tools,
            )
    return asyncio.run(run())


@pytest.mark.parametrize("effort", ["low", "medium", "xhigh"])
@pytest.mark.parametrize("allow_tools", [True, False])
def test_reasoning_effort_is_sent_to_qwen_template_in_all_rounds(effort, allow_tools):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": effort}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Odpowiedź",
        }}]})
    run_completion(handler, thinking=True, effort=effort, allow_tools=allow_tools)


@pytest.mark.parametrize("thinking,effort", [(True, ""), (False, "medium")])
def test_unspecified_or_disabled_reasoning_does_not_send_effort(thinking, effort):
    def handler(request):
        assert json.loads(request.content)["chat_template_kwargs"] == {"enable_thinking": thinking}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Odpowiedź",
        }}]})
    run_completion(handler, thinking=thinking, effort=effort)


def test_wire_format_and_filtering_of_reasoning():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer private-secret"
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert "tool_choice" not in payload
        assert "tools" not in payload
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Odpowiedź", "reasoning_content": "Ukryte",
        }}]})
    assert run_completion(handler, allow_tools=False) == {
        "role": "assistant", "content": "Odpowiedź",
    }


@pytest.mark.parametrize("response,code,status", [
    (httpx.Response(401, text="private-secret"), "model_auth_error", 502),
    (httpx.Response(400, text="private-secret"), "model_request_rejected", 502),
    (httpx.Response(500, text="private-secret"), "model_server_error", 502),
    (httpx.Response(200, text="not json"), "model_invalid_response", 502),
    (httpx.Response(200, json={"choices": []}), "model_invalid_response", 502),
    (httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": None, "reasoning_content": "private-secret",
    }}]}), "model_empty_response", 502),
    (httpx.Response(200, json={"choices": [{"finish_reason": "length"}]}), "model_incomplete", 502),
])
def test_invalid_and_failed_responses_are_safe(response, code, status):
    with pytest.raises(ChatError) as captured:
        run_completion(lambda _: response)
    assert captured.value.code == code
    assert captured.value.status == status
    assert "private-secret" not in str(captured.value)


def test_model_timeout_is_reported_without_original_exception():
    def handler(request):
        raise httpx.ReadTimeout("private-secret", request=request)
    with pytest.raises(ChatError) as captured:
        run_completion(handler)
    assert captured.value.code == "model_timeout"
    assert captured.value.status == 504
    assert "private-secret" not in str(captured.value)


def test_tool_required_is_sent_to_model():
    def handler(request):
        assert json.loads(request.content)["tool_choice"] == "required"
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}],
        }}]})
    run_completion(handler, require_tools=True)


def test_large_model_response_is_rejected_before_parsing():
    with pytest.raises(ChatError) as captured:
        run_completion(lambda _: httpx.Response(200, content=b"x" * 240_001))
    assert captured.value.code == "model_response_too_large"
