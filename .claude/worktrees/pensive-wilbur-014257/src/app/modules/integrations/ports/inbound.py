"""Integrations inbound port (02-port-contracts §2, verbatim).

``ToolCatalog`` is how the *tool system* (Phase 4, FR-52/122) consumes this
module: a dynamic per-workspace catalog of connector + remote-MCP tools,
discovered at runtime — never a direct coupling to a concrete connector
(INV-I4). Wired by the composition root only (ARC-07/08).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class DiscoveredTool:
    """One tool visible to the workspace (02 §2 verbatim).

    ``name`` is the invocation handle ``<prefix>.<tool>`` where ``<prefix>``
    is the connector key or MCP server name; ``source`` pins provenance as
    ``'connector:<id>'`` or ``'mcp:<name>'``.
    """

    name: str
    description: str
    parameters: Json
    source: str


class ToolCatalog(Protocol):
    async def list_tools(self, ctx: ExecutionContext) -> list[DiscoveredTool]: ...

    async def invoke_tool(self, ctx: ExecutionContext, name: str, args: Json) -> Json: ...
