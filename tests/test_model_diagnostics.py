import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import pytest

from config import Settings
from errors import ChatError
from model_client import ModelClient
from model_diagnostics import response_metadata, write_diagnostic


def test_empty_response_log_records_shape_usage_and_shared_correlation(tmp_path):
    path = tmp_path / "model.jsonl"
    settings = Settings(model_api_key="private-key", model_diagnostics_file=str(path),
                        model_reasoning_effort="medium", model_temperature=0.5)

    def handler(request):
        return httpx.Response(200, json={
            "id": "private-response-id", "extra": "private-extra",
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": None,
                "reasoning_content": "private-reasoning", "refusal": "private-refusal",
            }}],
            "usage": {"prompt_tokens": 1700, "completion_tokens": 19, "total_tokens": 1719,
                      "completion_tokens_details": {"reasoning_tokens": 19}},
        })

    async def run():
        async with ModelClient(settings, transport=httpx.MockTransport(handler)) as client:
            for _ in range(2):
                with pytest.raises(ChatError) as captured:
                    await client.complete([
                        {"role": "system", "content": "private-system"},
                        {"role": "user", "content": "private-question"},
                        {"role": "tool", "content": "private-result"},
                    ], [], allow_tools=False)
                assert client.diagnostic_id in captured.value.message
    asyncio.run(run())
    raw = path.read_text()
    assert "private" not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    assert len({item["diagnostic_id"] for item in records}) == 1
    assert [item["call_index"] for item in records] == [1, 2]
    for record in records:
        assert record["outcome"] == "model_empty_response"
        assert record["http_status"] == 200
        assert record["duration_ms"] >= 0
        assert record["request"]["tools_allowed"] is False
        assert record["request"]["reasoning_effort"] == "medium"
        assert record["request"]["temperature"] == 0.5
        assert record["request"]["role_counts"]["tool"] == 1
        assert record["response"]["content_type"] == "NoneType"
        assert record["response"]["reasoning_content_length"] == len("private-reasoning")
        assert record["response"]["usage"]["completion_tokens_details"]["reasoning_tokens"] == 19


@pytest.mark.parametrize("response,code", [
    (httpx.Response(200, text="private-body"), "model_invalid_response"),
    (httpx.Response(500, text="private-body"), "model_server_error"),
])
def test_invalid_json_and_http_errors_are_logged_without_raw_body(tmp_path, response, code):
    path = tmp_path / "model.jsonl"

    async def run():
        async with ModelClient(Settings(model_api_key="private-key", model_diagnostics_file=str(path)),
                               transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises(ChatError):
                await client.complete([], [])
    asyncio.run(run())
    assert "private" not in path.read_text()
    record = json.loads(path.read_text())
    assert record["outcome"] == code
    assert record["http_status"] == response.status_code


def test_unexpected_metadata_values_are_not_copied():
    result = response_metadata({"choices": [{"finish_reason": "private-reason", "message": {
        "content": {"private-key": "private-value"},
    }}], "usage": {"prompt_tokens": "private-value", "total_tokens": True}})
    assert "private" not in json.dumps(result)
    assert result["finish_reason"] == "other"
    assert result["usage"] == {}


def test_diagnostic_file_failure_does_not_break_request(tmp_path, caplog):
    write_diagnostic({"outcome": "ok"}, str(tmp_path))
    assert "model_diagnostics_write_failed" in caplog.text


def test_parallel_file_records_remain_separate_json_lines(tmp_path):
    path = tmp_path / "model.jsonl"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: write_diagnostic({"outcome": "ok", "call_index": index},
                                                    str(path)), range(20)))
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert sorted(item["call_index"] for item in records) == list(range(20))
