import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import Settings
from errors import ChatError, upstream_error
from mcp_client import MCPClient


def test_tool_inventory_handles_pagination():
    tools = [SimpleNamespace(name=name, inputSchema={}) for name in ("first", "second")]
    session = SimpleNamespace(list_tools=AsyncMock(side_effect=[
        SimpleNamespace(tools=tools[:1], nextCursor="next"),
        SimpleNamespace(tools=tools[1:], nextCursor=None),
    ]))
    result = asyncio.run(MCPClient(session, Settings(model_api_key="secret")).list_tools())
    assert result == tools
    assert session.list_tools.await_args.kwargs["cursor"] == "next"


def test_external_schema_refs_are_rejected_without_network():
    session = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(
        tools=[SimpleNamespace(name="search", inputSchema={"$ref": "https://untrusted.test/schema"})],
        nextCursor=None,
    )))
    with pytest.raises(ChatError) as captured:
        asyncio.run(MCPClient(session, Settings(model_api_key="secret")).list_tools())
    assert captured.value.code == "mcp_unavailable"


def test_mcp_timeout_and_exception_groups_keep_safe_business_error():
    error = ChatError("limit", "Przekroczono limit.", 413)
    assert upstream_error(ExceptionGroup("private", [error]), "mcp") is error
    normalized = upstream_error(ExceptionGroup("private", [TimeoutError("private")]), "mcp")
    assert normalized.status == 504
    assert normalized.code == "mcp_timeout"
    assert "private" not in str(normalized)


def test_tool_result_filters_non_text_blocks():
    session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
        isError=False, structuredContent={"result": "Q1"}, content=[
            SimpleNamespace(type="text", text="Q1"), SimpleNamespace(type="image", data="private"),
        ],
    )))
    result = asyncio.run(MCPClient(session, Settings(model_api_key="secret")).call_tool("search", {}))
    assert result == {"isError": False, "content": [{"type": "text", "text": "Q1"}]}


@pytest.mark.parametrize("text,structured,retained", [
    ('{"x": 1}', {"x": 1}, False),
    ('Q1', {"result": "Q1", "total": 100}, True),
    ('Q1', {"result": "Q2"}, True),
    ('Informacja', {"x": 1}, True),
    ('{"x": true}', {"x": 1}, True),
])
def test_only_exact_duplicate_structured_content_is_removed(text, structured, retained):
    session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
        isError=True, structuredContent=structured,
        content=[SimpleNamespace(type="text", text=text)],
    )))
    client = MCPClient(session, Settings(model_api_key="secret"), "Instrukcje MCP")
    result = asyncio.run(client.call_tool("search", {}))
    assert client.instructions == "Instrukcje MCP"
    assert result["isError"] is True
    assert ("structuredContent" in result) is retained
    assert result["content"][0]["text"] == text


def test_mcp_call_timeout_becomes_safe_api_error():
    session = SimpleNamespace(call_tool=AsyncMock(side_effect=TimeoutError("private-secret")))
    with pytest.raises(ChatError) as captured:
        asyncio.run(MCPClient(session, Settings(model_api_key="secret")).call_tool("search", {}))
    assert captured.value.code == "mcp_timeout"
    assert captured.value.status == 504
    assert "private-secret" not in str(captured.value)
