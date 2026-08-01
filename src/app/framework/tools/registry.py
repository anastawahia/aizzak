"""``ToolRegistry`` — the static, code-registered tool catalog (02 §3.4; the
Registries family of ARC-06).

A kernel-pure in-memory map keyed by ``spec.name``. It holds the STATIC tools
wired at boot (``register(tool_cls)`` takes the CLASS — D-08); the dynamic,
per-workspace connector/MCP tools are NOT here — they are resolved at runtime
through ``integrations.ToolCatalog`` and merged with these by ``ToolResolver``
(FR-52).

**Strict core (the ``InMemoryAgentRegistry`` precedent, 4.1).** ``register``
REFUSES a duplicate ``spec.name`` (two tools claiming one name is an operator
error, not last-one-wins), and refuses the R6 family this repo shipped in
2.5/2.6/2.7 — a non-class, a non-``BaseTool``, the abstract base itself, or a
class with a missing/mistyped ``spec`` — so hostile garbage cannot register
and detonate far away as a raw ``AttributeError``/``TypeError`` from a boot or
request path. ``get`` on an unknown name is a ``NotFoundError`` (404 — the
tool an agent named does not exist); a non-string name is a caller bug (422).

``specs()`` (deterministic, name-sorted) is an extension over 02 §3.4's
``register``/``get`` — ``ToolResolver.list`` and ``GET /tools`` need to
enumerate the static specs (doc-sync recommendation to 02 §3.4, mirroring the
``AgentRegistry.list`` that WAS in the contract).
"""

from __future__ import annotations

import builtins
import inspect
from typing import TYPE_CHECKING, Protocol

from app.framework.errors import NotFoundError, ValidationError
from app.framework.tools.base_tool import BaseTool, ToolSpec


class ToolRegistry(Protocol):
    """02 §3.4 verbatim, plus ``specs()`` (see module docstring)."""

    def register(self, tool_cls: type[BaseTool]) -> None: ...

    def get(self, name: str) -> type[BaseTool]: ...

    def specs(self) -> builtins.list[ToolSpec]: ...


class InMemoryToolRegistry:
    """The concrete registry — a strict, deterministic, in-memory map."""

    def __init__(self) -> None:
        self._tools: dict[str, type[BaseTool]] = {}

    def register(self, tool_cls: type[BaseTool]) -> None:
        # isinstance(_, type) first: issubclass on a non-class raises a bare
        # TypeError — the R6 action-at-a-distance family, refused legibly here.
        if not isinstance(tool_cls, type) or not issubclass(tool_cls, BaseTool):
            raise ValidationError(
                f"tool must be a BaseTool subclass (got {type(tool_cls).__name__})"
            )
        if tool_cls is BaseTool or inspect.isabstract(tool_cls):
            raise ValidationError(
                f"tool {tool_cls.__qualname__} is abstract — register a concrete BaseTool"
            )
        # Typed `object`, not the attribute `Any`: the 2.9/4.1 pattern — parse
        # the (possibly missing) spec from `object` so isinstance narrows under
        # mypy --strict. A tool that forgot `spec = ToolSpec(...)` lands here.
        spec: object = getattr(tool_cls, "spec", None)
        if not isinstance(spec, ToolSpec):
            raise ValidationError(
                f"tool {tool_cls.__qualname__} has no ToolSpec spec (got {type(spec).__name__})"
            )
        if not isinstance(spec.name, str) or not spec.name.strip():
            raise ValidationError(
                f"tool {tool_cls.__qualname__} spec.name must be a non-empty string "
                f"(got {type(spec.name).__name__})"
            )
        if spec.name in self._tools:
            raise ValidationError(f"tool name {spec.name!r} is already registered")
        self._tools[spec.name] = tool_cls

    def get(self, name: str) -> type[BaseTool]:
        # The `or` keeps the raise type-reachable (blank str) under
        # warn_unreachable while catching runtime non-str garbage: an unhashable
        # name would otherwise surface as a raw TypeError from the dict lookup
        # (a caller bug, 422) — distinct from an honest unknown name (404).
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(
                f"tool name must be a non-empty string (got {type(name).__name__})"
            )
        tool_cls = self._tools.get(name)
        if tool_cls is None:
            raise NotFoundError(f"unknown tool {name!r}")
        return tool_cls

    def specs(self) -> builtins.list[ToolSpec]:
        # Sorted by name: deterministic for GET /tools, for the LLM tools
        # payload, and for tests — independent of registration order.
        return [tool_cls.spec for _name, tool_cls in sorted(self._tools.items())]


if TYPE_CHECKING:

    def _conforms(registry: InMemoryToolRegistry) -> ToolRegistry:
        """mypy-gate structural proof that the concrete class satisfies the
        §3.4 port (the ``InMemoryAgentRegistry`` / resolver precedent)."""
        return registry
