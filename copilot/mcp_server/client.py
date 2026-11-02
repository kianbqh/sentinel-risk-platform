"""Agent-facing MCP-only async read interface."""
from contextlib import AsyncExitStack
from typing import Protocol

from .contracts import failure, validate_call


class ToolClient(Protocol):
    async def call(self, name: str, args: dict) -> dict: ...


class ToolTransportError(RuntimeError):
    pass


class MCPToolClient:
    def __init__(self, endpoint, token, timeout=30):
        self.endpoint, self.token, self.timeout = endpoint, token, timeout
        self._stack = None
        self._client = None

    async def __aenter__(self):
        # MCP v2.3 uses httpx2, including in its public streamable transport.
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            http = await self._stack.enter_async_context(httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + self.token}, timeout=self.timeout, trust_env=False))
            self._client = await self._stack.enter_async_context(Client(
                streamable_http_client(self.endpoint, http_client=http), raise_exceptions=False))
        except Exception:
            await self._stack.aclose()
            raise ToolTransportError("MCP HTTP connection failed") from None
        return self

    async def __aexit__(self, *exc):
        await self._stack.__aexit__(*exc)
        self._client = None

    async def call(self, name, args):
        validate_call(name, args)
        if self._client is None:
            raise ToolTransportError("MCP client is not connected")
        try:
            result = await self._client.call_tool(name, args)
            if result.is_error:
                return failure("mcp_rejected", "MCP server rejected the tool call")
            data = result.structured_content
            if not isinstance(data, dict) or type(data.get("ok")) is not bool:
                raise ToolTransportError("MCP response envelope is invalid")
            return data
        except ToolTransportError:
            raise
        except Exception:
            raise ToolTransportError("MCP tool transport failed") from None
