"""``AgentRegistry`` — per-request agent creation from registered factories
(02-port-contracts §3.4; the Registries family of ARC-06's Platform
Components).

The registry is a kernel-pure in-memory map: it holds ``(metadata, factory)``
pairs keyed by ``metadata.key``, creates ONE agent instance per request
(FR-10/11 — ``create`` never caches instances, only factories), and lists
metadata deterministically. It is rebuilt from a ``PluginLoader`` scan at
every boot (11 §5), which is what makes deleting an agent folder deregister
it (AC-04): a key that vanished from the scan is simply absent here, and
``create`` answers 404 — exactly what ``/agents/{key}/invoke`` must return
for a removed agent.

**Strictness split with the loader.** ``register`` REFUSES a duplicate key
(two folders claiming ``rag_agent`` is an operator error; last-one-wins would
silently swap which code serves the key), and refuses non-``AgentMetadata``
/ non-callable arguments — the R6 family this repo shipped in 2.5/2.6/2.7
(hostile garbage surfacing later as a raw ``AttributeError`` from a boot
path). The registry raises; the LOADER is the resilient boundary that
catches, isolates the offending plugin, and keeps booting. Both behaviors
compose: strict core, forgiving edge.

``deps`` stays the opaque ``AgentDependencies`` seam (4.2/4.7 grow it); the
factory contract is positional ``factory(ctx, deps)`` — and per D-13 the
factory IS normally the agent class itself. No logging here (deterministic
map operations, nothing to observe — the resolver precedent); discovery
events belong to the loader.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from app.framework.agent_runtime.base_agent import AgentDependencies, BaseAgent
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError


class AgentRegistry(Protocol):
    """02 §3.4 verbatim. ``factory`` is invoked as ``factory(ctx, deps)``."""

    def register(self, metadata: AgentMetadata, factory: Callable[..., BaseAgent]) -> None: ...

    def create(self, key: str, ctx: ExecutionContext, deps: AgentDependencies) -> BaseAgent: ...

    # `builtins.list`: a method named `list` shadows the builtin inside this
    # class body, so the annotation must reach around it.
    def list(self) -> builtins.list[AgentMetadata]: ...


class InMemoryAgentRegistry:
    """The concrete registry — a strict, deterministic, in-memory map."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[AgentMetadata, Callable[..., BaseAgent]]] = {}

    def register(self, metadata: AgentMetadata, factory: Callable[..., BaseAgent]) -> None:
        if not isinstance(metadata, AgentMetadata):
            raise ValidationError(
                f"metadata must be an AgentMetadata (got {type(metadata).__name__})"
            )
        # The key is this map's whole coordinate system, and AgentMetadata is
        # a deliberately dumb carrier (nothing validates its fields at
        # construction) — an int key would register fine here and detonate
        # far away as a str/int sort TypeError in list(): the R6
        # action-at-a-distance family, refused at the boundary instead.
        if not isinstance(metadata.key, str) or not metadata.key.strip():
            raise ValidationError(
                f"metadata.key must be a non-empty string (got {type(metadata.key).__name__})"
            )
        if not callable(factory):
            raise ValidationError(
                f"factory for agent {metadata.key!r} is not callable (got {type(factory).__name__})"
            )
        if metadata.key in self._entries:
            raise ValidationError(f"agent key {metadata.key!r} is already registered")
        self._entries[metadata.key] = (metadata, factory)

    def create(self, key: str, ctx: ExecutionContext, deps: AgentDependencies) -> BaseAgent:
        # The `or` keeps the raise type-reachable (blank str) under
        # warn_unreachable while still catching runtime non-str garbage: an
        # unhashable key would otherwise surface as a raw TypeError from the
        # dict lookup (2.9 verifier RC-C's exact family) — a caller bug, 422,
        # distinct from an honest unknown key, 404.
        if not isinstance(key, str) or not key.strip():
            raise ValidationError(
                f"agent key must be a non-empty string (got {type(key).__name__})"
            )
        entry = self._entries.get(key)
        if entry is None:
            # `agent.unknown` verbatim from 03 §4 ("`agent_key` غير مُسجّل"),
            # the exact move `WorkflowRegistry.get` already made for
            # `workflow.unknown` (4.7-e-1): the status was always right, only
            # the inherited `common.not_found` was too coarse for a client
            # that must tell "no such agent" from "no such conversation" on
            # one router. 6.2 closes the asymmetry — an entry that had no
            # site had to get one or leave the catalog.
            raise NotFoundError(f"unknown agent {key!r}", code="agent.unknown")
        _metadata, factory = entry
        return factory(ctx, deps)

    def list(self) -> builtins.list[AgentMetadata]:
        # Sorted by key: deterministic for GET /agents and for tests,
        # independent of filesystem scan order.
        return [metadata for _key, (metadata, _factory) in sorted(self._entries.items())]


if TYPE_CHECKING:

    def _conforms(registry: InMemoryAgentRegistry) -> AgentRegistry:
        """mypy-gate structural proof that the concrete class satisfies the
        §3.4 port (the resolver's ``_conforms`` precedent — the consuming
        wiring site arrives only in 4.7/م6)."""
        return registry
