"""Connector-toolset seam (module port — the ``media.MediaGenerator`` /
``knowledge.EmbeddingResolver`` precedent).

Isolates the *concrete* connector tool implementations (a Gmail adapter over
its REST API, a GitHub adapter, … — infrastructure, built in the adapter
phase) from this module's use-cases, which stay fully testable with fakes.

The seam never sees a ``CipherRef``: decryption happens exactly once, inside
the application layer, and the adapter receives a transient, already-resolved
``ConnectorAccess`` for a single call (the ``credentials.ResolvedKey``
precedent — INV-I1/INV-C2 family).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class ConnectorToolSpec:
    """One tool a connector offers, unprefixed (the catalog prepends
    ``<connector_key>.``)."""

    name: str
    description: str
    parameters: Json


@dataclass(frozen=True, slots=True)
class ConnectorAccess:
    """Transient access material for one connector call — never persisted,
    never returned through any API."""

    connector_key: str
    access_token: str
    scopes: tuple[str, ...]


class ConnectorToolset(Protocol):
    """v1 connector tools are a *static* per-connector catalog (Gmail being
    the first), so ``list_tools`` is pure/synchronous; only ``invoke``
    performs I/O. The adapter enforces its own per-call timeout
    (``Limits.oauth_timeout_s``-family knobs are its concern, not the
    use-case's)."""

    def list_tools(self, connector_key: str) -> list[ConnectorToolSpec]: ...

    async def invoke(
        self, ctx: ExecutionContext, *, access: ConnectorAccess, tool_name: str, args: Json
    ) -> Json: ...
