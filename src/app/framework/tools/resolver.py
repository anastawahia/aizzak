"""``ToolResolver`` — unifies static tools + the per-workspace dynamic catalog
behind ONE by-name interface (FR-51/52; the tool-system binding of Phase 4.3).

This is what makes an agent source-blind: it resolves a tool BY NAME and never
learns whether the tool is static platform code, a connector, or an MCP server
(FR-51/52; 11 §4). It is the object 4.7 puts on ``deps.tools``.

**Structural injection for the dynamic catalog (the 2.9 ``ProviderResolver``
precedent).** ``integrations.ToolCatalog`` is a MODULE inbound port, and the
``framework-kernel`` contract forbids ``app.framework → app.modules``. So this
file declares framework-local twins — ``DynamicToolCatalog`` (the Protocol
shape) and ``DiscoveredTool`` (the record) — that the real
``integrations.ToolCatalog`` / ``integrations.DiscoveredTool`` satisfy
structurally. The composition root adapts and wires (4.7); the ``_conforms``
proof that the real port satisfies ``DynamicToolCatalog`` lives at that wiring
site (it would need the forbidden import here), exactly as 2.9 deferred its
``CredentialResolver`` conformance proof.

**Per-request view.** 4.7 constructs one ``ToolResolver(registry, catalog,
deps)`` per request and hands it to the agent as ``deps.tools``. ⚠️ 4.7 must
untangle the ``deps.tools = resolver`` / ``resolver`` -needs-``deps``
back-reference (a resolver needs the ports bundle to build static tools) — via
two-phase construction, or by giving tools a deps bundle that omits ``.tools``.
4.3 assumes nothing about ``AgentDependencies``' future mutability/frozenness.

**Name resolution (status §3.28 decision, user-gated).** Dynamic tools are
namespaced ``<prefix>.<tool>`` (the ``integrations`` catalog enforces this and
refuses an MCP name shadowing a connector) and static tools have bare names,
so the two namespaces are disjoint by construction. ``invoke`` resolves the
STATIC registry FIRST (platform code wins) and otherwise delegates to the
catalog; a collision (which should not occur) resolves to the static tool and
is logged, and ``list`` drops the shadowed dynamic entry with a warning.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Protocol

from app.framework.agent_runtime.base_agent import AgentDependencies
from app.framework.context.execution_context import ExecutionContext
from app.framework.observability import get_logger
from app.framework.tools.base_tool import ToolSpec
from app.framework.tools.registry import ToolRegistry
from app.framework.types import Json

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveredTool:
    """A dynamic (connector/MCP) tool visible to a workspace — the framework's
    local twin of ``integrations.DiscoveredTool`` (structural injection).

    ``name`` is the invocation handle ``<prefix>.<tool>``; ``source`` pins
    provenance (``connector:<id>`` / ``mcp:<name>``).
    """

    name: str
    description: str
    parameters: Json
    source: str


class DynamicToolCatalog(Protocol):
    """The shape ``ToolResolver`` needs from ``integrations.ToolCatalog`` — a
    framework-local Protocol so ``framework-kernel`` stays 8/0."""

    async def list_tools(self, ctx: ExecutionContext) -> builtins.list[DiscoveredTool]: ...

    async def invoke_tool(self, ctx: ExecutionContext, name: str, args: Json) -> Json: ...


class ToolResolver:
    """One request's merged, by-name tool view over static + dynamic tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        catalog: DynamicToolCatalog,
        deps: AgentDependencies,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._deps = deps

    async def list(self, ctx: ExecutionContext) -> builtins.list[ToolSpec]:
        """Every tool available to this workspace, static + dynamic, as one
        list of ``ToolSpec`` (the shape an LLM tools payload / ``GET /tools``
        wants). A dynamic tool whose name collides with a static one is dropped
        and logged (static wins — decision Q2-A)."""
        specs = self._registry.specs()
        static_names = {spec.name for spec in specs}
        for tool in await self._catalog.list_tools(ctx):
            # Catalog names ultimately come from external MCP servers — untrusted
            # input at this boundary. A non-string (or unhashable) name would
            # otherwise blow up as a raw TypeError on the set-membership below, or
            # leak a non-str name into a ToolSpec: the R6 family, refused here
            # exactly as `registry.py` guards its static keys.
            if not isinstance(tool.name, str) or not tool.name.strip():
                _logger.warning(
                    "tool_resolver.dynamic_tool_malformed_name",
                    extra={"name_type": type(tool.name).__name__, "source": tool.source},
                )
                continue
            if tool.name in static_names:
                _logger.warning(
                    "tool_resolver.dynamic_shadowed_by_static",
                    extra={"tool": tool.name, "source": tool.source},
                )
                continue
            specs.append(
                ToolSpec(name=tool.name, description=tool.description, parameters=tool.parameters)
            )
        return specs

    async def invoke(self, ctx: ExecutionContext, name: str, args: Json) -> Json:
        """Resolve ``name`` and run it. Static registry FIRST (platform code
        wins), otherwise the workspace's dynamic catalog. An unknown name
        surfaces as the catalog's ``NotFoundError`` (404)."""
        if any(spec.name == name for spec in self._registry.specs()):
            tool = self._registry.get(name)(self._deps)
            return await tool.run(ctx, args)
        return await self._catalog.invoke_tool(ctx, name, args)
