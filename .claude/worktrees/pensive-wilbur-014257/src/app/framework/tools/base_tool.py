"""``BaseTool`` + ``ToolSpec`` — the unified tool contract (02-port-contracts
§3.1, D-08).

A tool is a thin adapter over injected ports; an agent uses it BY NAME through
the ``ToolResolver`` without knowing its implementation (FR-51) or its source
(static code, connector, or MCP — FR-52).

**``run`` stays ``async def`` — no deviation from 02 §3.1.** Unlike
``BaseAgent.run`` (a ``def`` returning an ``AsyncIterator`` so the guide's
async-generator override type-checks), a tool returns a SINGLE ``Json`` value,
not a stream, so the plain ``async def run(...) -> Json`` of the contract is
exactly right.

**``__init__(self, deps)`` — a conscious extension of 02 §3.1** (which lists
only ``spec`` + ``run``), justified by the authoring guide (11 §4 constructs a
tool as ``def __init__(self, deps): self._retrieval = deps.knowledge``) and by
the ``BaseAgent.__init__(ctx, deps)`` precedent. The uniform constructor is
what lets ``ToolResolver`` instantiate ANY registered tool as
``tool_cls(deps)``; a dep-free tool (a calculator) simply inherits this and
ignores ``deps``. Tools and agents share the SAME injected-ports bundle
(``AgentDependencies``) — the ``deps.knowledge`` / ``deps.tools`` shape of 11
§4 (doc-sync recommendation to 02 §3.1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from app.framework.agent_runtime.base_agent import AgentDependencies
from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Name, description and JSON-Schema parameters of one tool (02 §3.1)."""

    name: str
    description: str
    parameters: Json  # a JSON Schema object for the tool's arguments


class BaseTool(ABC):
    """Contract every static tool inherits (02 §3.1; authoring guide 11 §4).

    Subclasses MUST set ``spec = ToolSpec(...)`` and implement ``run``. They are
    constructed once per call with the injected-ports bundle
    (``tool_cls(deps)``) and driven via ``run(ctx, args)`` — stateless per
    call, the ``BaseAgent`` discipline applied to tools.
    """

    spec: ClassVar[ToolSpec]

    def __init__(self, deps: AgentDependencies) -> None:
        # The signature IS the uniform construction contract ToolResolver
        # relies on; a tool that needs no ports inherits this and ignores deps.
        self.deps = deps

    @abstractmethod
    async def run(self, ctx: ExecutionContext, args: Json) -> Json:
        """Execute the tool for one call and return a JSON result."""
