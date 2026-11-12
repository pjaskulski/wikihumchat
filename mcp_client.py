"""Jedna sesja Streamable HTTP MCP na obsługiwane pytanie."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import logging
import json
import re

import httpx
from jsonschema.validators import validator_for
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from errors import ChatError, upstream_error


# SDK może dołączać surowe odpowiedzi i wyjątki do logów transportu.
# Błędy aplikacji raportujemy osobno, wyłącznie bezpiecznym kodem.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)


class MCPClient:
    def __init__(self, session, settings, instructions=None):
        self.session = session
        self.settings = settings
        self.instructions = instructions or ""

    async def list_tools(self):
        try:
            async with asyncio.timeout(self.settings.mcp_timeout_seconds):
                cursor = None
                seen_cursors = set()
                tools = {}
                for _ in range(50):
                    page = await self.session.list_tools(cursor=cursor)
                    for tool in page.tools:
                        if (
                            not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tool.name)
                            or tool.name in tools
                        ):
                            raise ValueError
                        schema = tool.inputSchema
                        validator_for(schema).check_schema(schema)
                        # Zewnętrzne odwołania schematów nie mogą inicjować połączeń HTTP.
                        self._check_refs(schema)
                        tools[tool.name] = tool
                    cursor = page.nextCursor
                    if not cursor:
                        if not tools:
                            raise ValueError
                        return list(tools.values())
                    if cursor in seen_cursors:
                        raise ValueError
                    seen_cursors.add(cursor)
                raise ValueError
        except Exception as error:
            raise upstream_error(error, "mcp") from None

    @staticmethod
    def _check_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"}:
                    if not isinstance(child, str) or not child.startswith("#"):
                        raise ValueError
                MCPClient._check_refs(child)
        elif isinstance(value, list):
            for child in value:
                MCPClient._check_refs(child)

    async def call_tool(self, name, arguments):
        try:
            async with asyncio.timeout(self.settings.mcp_timeout_seconds):
                result = await self.session.call_tool(name, arguments=arguments)
            # Nie przesyłaj dalej binarnych zasobów ani metadanych transportu.
            content = {
                "isError": bool(result.isError),
                "content": [{"type": "text", "text": block.text}
                            for block in result.content if block.type == "text"],
            }
            if result.structuredContent is not None:
                structured = result.structuredContent
                texts = [block["text"] for block in content["content"]]
                duplicate = isinstance(structured, dict) and set(structured) == {"result"} and (
                    len(texts) == 1 and structured["result"] == texts[0]
                )
                if not duplicate and len(texts) == 1:
                    try:
                        duplicate = json.dumps(json.loads(texts[0]), sort_keys=True) == json.dumps(
                            structured, sort_keys=True,
                        )
                    except (ValueError, RecursionError):
                        pass
                if not duplicate:
                    content["structuredContent"] = structured
            return content
        except Exception as error:
            raise upstream_error(error, "mcp") from None


@asynccontextmanager
async def connect_mcp(settings):
    try:
        async with httpx.AsyncClient(timeout=settings.mcp_timeout_seconds) as http:
            async with streamable_http_client(settings.mcp_url, http_client=http) as streams:
                async with ClientSession(
                    streams[0], streams[1],
                    read_timeout_seconds=timedelta(seconds=settings.mcp_timeout_seconds),
                ) as session:
                    async with asyncio.timeout(settings.mcp_timeout_seconds):
                        initialized = await session.initialize()
                    yield MCPClient(session, settings, initialized.instructions)
    except Exception as error:
        raise upstream_error(error, "mcp") from None
