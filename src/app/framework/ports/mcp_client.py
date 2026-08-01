"""MCPClient driven port (02-port-contracts §1.12 · integrations, FR-120).

Remote MCP transports only (HTTP/SSE). Running MCP servers as local
subprocesses is out of v1 scope — it belongs to the reserved ``sandbox``
module (ARC-15). Tools are discovered at runtime; nothing is persisted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class McpTool:
    name: str
    description: str
    parameters: Json  # neutral tool schema


@dataclass(frozen=True, slots=True)
class McpTarget:
    endpoint_url: str
    transport: str  # 'http' | 'sse' only
    auth: Json | None


class MCPClient(Protocol):
    async def list_tools(self, target: McpTarget) -> list[McpTool]: ...  # runtime discovery

    async def call_tool(self, target: McpTarget, name: str, args: Json) -> Json: ...
