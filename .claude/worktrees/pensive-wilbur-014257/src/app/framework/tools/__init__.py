"""Tool system — the unified ``BaseTool`` contract (02 §3.1), the static
``ToolRegistry`` (02 §3.4), and the ``ToolResolver`` (FR-51/52) that merges it
with the per-workspace dynamic catalog behind one by-name interface."""

from __future__ import annotations

from app.framework.tools.base_tool import BaseTool, ToolSpec
from app.framework.tools.registry import InMemoryToolRegistry, ToolRegistry
from app.framework.tools.resolver import DiscoveredTool, DynamicToolCatalog, ToolResolver
from app.framework.tools.web_search import WebSearchTool

__all__ = [
    "BaseTool",
    "DiscoveredTool",
    "DynamicToolCatalog",
    "InMemoryToolRegistry",
    "ToolRegistry",
    "ToolResolver",
    "ToolSpec",
    "WebSearchTool",
]
