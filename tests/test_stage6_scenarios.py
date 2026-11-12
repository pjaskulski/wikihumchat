import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import create_app
from chat_service import ChatService
from config import Settings
from model_client import ModelClient


TOOL = SimpleNamespace(name="get_statements", description="Statements", inputSchema={
    "type": "object", "properties": {"entity_id": {"type": "string"}},
    "required": ["entity_id"], "additionalProperties": False,
})


def test_concurrent_conversations_do_not_share_context_or_sources():
    models = []

    class Model:
        def __init__(self, settings):
            self.called = False
            models.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def complete(self, messages, tools, **kwargs):
            question = next(item["content"] for item in reversed(messages) if item["role"] == "user")
            qid = "Q101" if question == "sesja A" else "Q202"
            await asyncio.sleep(0)
            if not self.called:
                self.called = True
                return {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "same_call_id_in_each_session", "type": "function",
                    "function": {"name": "get_statements", "arguments": '{"entity_id":"' + qid + '"}'},
                }]}
            return {"role": "assistant", "content": f"{question}: rekord {qid}"}

    class MCP:
        async def list_tools(self):
            return [TOOL]

        async def call_tool(self, name, arguments):
            await asyncio.sleep(0)
            return {"isError": False, "content": [{"text": arguments["entity_id"]}]}

    @asynccontextmanager
    async def mcp_factory(_):
        yield MCP()

    service = ChatService(Settings(model_api_key="secret"), model_factory=Model,
                          mcp_factory=mcp_factory)

    async def run():
        return await asyncio.gather(service.reply({"message": "sesja A"}),
                                    service.reply({"message": "sesja B"}))

    first, second = asyncio.run(run())
    assert len(models) == 2
    assert first["sources"][0]["id"] == "Q101"
    assert second["sources"][0]["id"] == "Q202"
    assert "sesja B" not in str(first)
    assert "Q202" not in str(first)
    assert "sesja A" not in str(second)
    assert "Q101" not in str(second)


@pytest.mark.parametrize("failure,code,status", [
    (httpx.ConnectError("private-upstream-error"), "model_unavailable", 502),
    (httpx.ReadTimeout("private-upstream-error"), "model_timeout", 504),
])
def test_model_network_failure_through_real_service_and_api(failure, code, status, caplog):
    def handler(_):
        raise failure

    @asynccontextmanager
    async def mcp_factory(_):
        yield SimpleNamespace(list_tools=AsyncMock(return_value=[TOOL]))

    settings = Settings(model_api_key="private-key")
    service = ChatService(settings, mcp_factory=mcp_factory,
                          model_factory=lambda config: ModelClient(
                              config, transport=httpx.MockTransport(handler),
                          ))
    app = create_app(settings, chat_service=service)
    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "private-question"})
    assert response.status_code == status
    assert response.json["error"]["code"] == code
    assert "private" not in response.get_data(as_text=True)
    assert "private" not in caplog.text


def test_mcp_connection_failure_through_actual_context_manager(monkeypatch, caplog):
    @asynccontextmanager
    async def failed_transport(*args, **kwargs):
        raise httpx.ConnectError("private-transport-error")
        yield  # pragma: no cover

    monkeypatch.setattr("mcp_client.streamable_http_client", failed_transport)
    model = SimpleNamespace(complete=AsyncMock())

    @asynccontextmanager
    async def model_factory(_):
        yield model

    settings = Settings(model_api_key="private-key")
    app = create_app(settings, chat_service=ChatService(settings, model_factory=model_factory))
    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "private-question"})
    assert response.status_code == 502
    assert response.json["error"]["code"] == "mcp_unavailable"
    model.complete.assert_not_awaited()
    assert "private" not in response.get_data(as_text=True)
    assert "private" not in caplog.text
