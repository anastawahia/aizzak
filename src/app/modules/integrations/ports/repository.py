"""Integrations persistence ports (02-port-contracts §2).

Outbound repository contracts for the ``Connection`` and ``McpServer``
aggregates. Every method takes ``ExecutionContext`` first so the SQL adapter
can apply the RLS guard (``SET LOCAL app.workspace_id``) and the
``WHERE workspace_id`` filter (DD-04) — the files/knowledge/media precedent.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.integrations.domain.entities import Connection, McpServer


class ConnectionRepository(Protocol):
    """Tenant-scoped persistence for the ``Connection`` aggregate.

    The first four methods are the 02 §2 contract verbatim. Two extensions
    are consumed by the use-cases (no unconsumed surface):

    - ``find_by_connector`` — lookup over ``uq_conn_ws_connector``
      (01 §2.9): ``BeginConnection`` idempotency + ``invoke_tool`` routing.
    - ``save`` — full-aggregate write for lifecycle transitions
      (``connect``/``revoke``/``mark_error``), with the optimistic lock on
      ``version`` advanced by the adapter (every-aggregate pattern).

    ``update_tokens`` deliberately stays a *narrow* hot-path write for the
    lazy-refresh loop (INV-I3): it targets only ``token_ref``/``key_id``/
    ``expires_at`` with **no** ``version`` bump/check — concurrent refreshes
    of the same connection are benign ("last writer wins": every freshly
    renewed token is valid), so they must not fail each other on the
    optimistic lock the way racing lifecycle transitions should.
    """

    async def get(self, ctx: ExecutionContext, conn_id: Uuid) -> Connection | None: ...

    async def add(self, ctx: ExecutionContext, conn: Connection) -> None: ...

    async def list_connected(self, ctx: ExecutionContext) -> list[Connection]: ...

    # Adapter note: reads through the partial index ix_conn_ws
    # (WHERE status = 'connected', 01 §2.9).

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None
    ) -> Page[Connection]:
        """Every connection this workspace has, newest first — **all four
        statuses**, unlike ``list_connected`` (6.1-و-4-1) — cursor-paginated
        (6.3-ب).

        The two reads answer different questions and neither can serve the
        other. ``list_connected`` is the *capability* question the tool
        catalog asks ("what can I call right now?"), and its partial index
        exists precisely so the answer is cheap. This one is the
        *administrative* question ``GET /connections`` asks, and a client that
        just started a handshake needs to see the ``pending`` row it created,
        while one whose token exchange failed needs to see the ``error`` row
        to know why the connector went quiet. Filtering to ``connected``
        would make both invisible — the connection would simply not exist as
        far as the API is concerned.

        And it is precisely that inclusiveness that makes it unbounded, which
        is why 6.3-ب paginated it. ``Limits.max_connectors`` caps CONNECTED
        rows only — ``BeginConnection`` says so in as many words ("pending
        rows are handshake debris, not capacity") — so every abandoned
        handshake and every failed exchange leaves a row that this read
        returns and no cap ever counts. ``openapi.yaml`` declared ``limit``/
        ``cursor`` on ``listConnections`` from the start; the implementation
        was the half that had drifted.
        """
        ...

    async def update_tokens(
        self,
        ctx: ExecutionContext,
        conn_id: Uuid,
        token_ref: str,
        key_id: str,
        expires_at: datetime,
    ) -> None: ...  # lazy renewal write (FR-124) — see class docstring

    async def find_by_connector(
        self, ctx: ExecutionContext, connector_key: str
    ) -> Connection | None: ...

    async def save(self, ctx: ExecutionContext, conn: Connection) -> None: ...


class McpServerRepository(Protocol):
    """Tenant-scoped persistence for the ``McpServer`` aggregate.

    Kept separate from ``ConnectionRepository``: a different aggregate over a
    different table (01 §2.9). Only what the use-cases consume:
    ``RegisterMcpServer`` (add + duplicate/cap guards via ``find_by_name``/
    ``list_active``, and ``save`` for the revive path), the tool catalog
    (``list_active`` + ``find_by_name`` routing), and 6.1-و-4-3's two API
    faces (``list`` for ``GET /mcp-servers``, ``get``+``save`` for
    ``DELETE``). ``list_active`` reads through the partial index ix_mcp_ws
    (WHERE status = 'active').
    """

    async def add(self, ctx: ExecutionContext, server: McpServer) -> None: ...

    async def list_active(self, ctx: ExecutionContext) -> list[McpServer]: ...

    async def find_by_name(self, ctx: ExecutionContext, name: str) -> McpServer | None: ...

    async def get(self, ctx: ExecutionContext, server_id: Uuid) -> McpServer | None: ...

    async def list(self, ctx: ExecutionContext) -> Sequence[McpServer]:
        """Every registered server, newest first — **all three statuses**,
        the ``ConnectionRepository.list`` counterpart and for the same
        reason.

        ``list_active`` answers the capability question the tool catalog
        asks; this one answers the administrative question ``GET
        /mcp-servers`` asks. Since ``DELETE`` disables rather than erases, a
        row a client just deleted would otherwise vanish from the API while
        still occupying its unique name — the one state where a client most
        needs to see it.
        """
        ...

    async def save(self, ctx: ExecutionContext, server: McpServer) -> None:
        """Full-aggregate write for the two lifecycle transitions
        (``disable``/``reactivate``), optimistic lock on ``version`` advanced
        by the adapter — the ``ConnectionRepository.save`` shape."""
        ...
