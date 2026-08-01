"""Integrations aggregates (pure — 06-domain-models §9).

``Connection`` tracks one third-party OAuth connection per (workspace,
connector); ``McpServer`` one registered remote MCP server. Behaviour lives
on the aggregates; mutations touch only state + ``updated_at``. The
optimistic ``version`` is advanced by the repository on ``save`` — never
here, matching every other aggregate in this codebase. Identifiers are
UUIDv7 text (``str``); timestamps are timezone-aware UTC (DD-03).

INV-I1 is visible here structurally: the only token/auth material an
aggregate ever holds is an opaque ``CipherRef`` — raw OAuth tokens exist
transiently in the application layer alone and are never assigned to domain
state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.modules.integrations.domain.errors import ConnectionStateError
from app.modules.integrations.domain.value_objects import (
    CipherRef,
    ConnectionStatus,
    ConnectorKey,
    McpEndpoint,
    McpServerName,
    McpStatus,
)


@dataclass(slots=True)
class Connection:
    """One third-party OAuth connection (06 §9 AR ``Connection``)."""

    id: str
    workspace_id: str
    connector_key: ConnectorKey
    display_name: str | None
    status: ConnectionStatus
    scopes: tuple[str, ...]
    token_ref: CipherRef | None
    expires_at: datetime | None
    last_error: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    def begin(self, scopes: tuple[str, ...], now: datetime) -> None:
        """Restart the OAuth handshake on an existing row: any non-connected
        status → ``pending`` with the newly requested scopes.

        Re-consenting on a live ``connected`` row is a *use-case*-level
        no-op (``BeginConnection`` never calls this on one) — at the entity
        level it is an illegal transition, so a working connection can never
        be knocked back to ``pending`` by a stray call.
        """
        if self.status is ConnectionStatus.CONNECTED:
            raise ConnectionStateError("cannot restart the handshake on a connected connection")
        self.status = ConnectionStatus.PENDING
        self.scopes = scopes
        self.last_error = None
        self.updated_at = now

    def connect(
        self,
        token_ref: CipherRef,
        expires_at: datetime,
        scopes: tuple[str, ...],
        now: datetime,
    ) -> None:
        """Successful code exchange: → ``connected`` with the encrypted token
        reference (INV-I1 — a ``CipherRef``, never raw material), its expiry
        and the *granted* scopes. Allowed from ``pending``/``error`` (and
        idempotently from ``connected`` itself); a ``revoked`` connection
        stays revoked — reconnecting one means a fresh ``BeginConnection``
        handshake first, which is only legal via ``begin`` on a non-revoked
        row or a brand-new row.
        """
        if self.status is ConnectionStatus.REVOKED:
            raise ConnectionStateError("cannot connect a revoked connection")
        self.status = ConnectionStatus.CONNECTED
        self.token_ref = token_ref
        self.expires_at = expires_at
        self.scopes = scopes
        self.last_error = None
        self.updated_at = now

    def apply_refreshed_tokens(
        self, token_ref: CipherRef, expires_at: datetime, now: datetime
    ) -> None:
        """Mirror a successful lazy refresh (INV-I3) in memory: stays
        ``connected``, only the token reference + expiry move. The durable
        write happens through ``ConnectionRepository.update_tokens`` — this
        keeps the in-memory aggregate consistent for the caller that goes on
        using it (e.g. ``invoke_tool``)."""
        if self.status is not ConnectionStatus.CONNECTED:
            raise ConnectionStateError(
                f"cannot apply refreshed tokens from status {self.status.value!r}"
            )
        self.token_ref = token_ref
        self.expires_at = expires_at
        self.updated_at = now

    def mark_error(self, reason: str, now: datetime) -> None:
        """Record a failed exchange/refresh: → ``error`` with the reason.
        Reachable from any non-terminal point of the lifecycle; a later
        ``begin`` (re-handshake) or successful ``connect`` clears it."""
        self.status = ConnectionStatus.ERROR
        self.last_error = reason
        self.updated_at = now

    def revoke(self, now: datetime) -> None:
        """→ ``revoked`` and drop the stored secret reference (INV-I1
        hygiene: a revoked row keeps no token material at all). Idempotent —
        revoking a revoked connection changes nothing."""
        if self.status is ConnectionStatus.REVOKED:
            return
        self.status = ConnectionStatus.REVOKED
        self.token_ref = None
        self.expires_at = None
        self.updated_at = now

    def needs_refresh(self, now: datetime, skew_s: int) -> bool:
        """Pure INV-I3 check (FR-124 lazy renewal): the access token is
        treated as expired ``skew_s`` seconds *before* its actual expiry, and
        an unknown expiry (``None``) always refreshes — fail-closed toward
        renewing rather than calling a connector with a dead token."""
        if self.expires_at is None:
            return True
        return self.expires_at <= now + timedelta(seconds=skew_s)


@dataclass(slots=True)
class McpServer:
    """One registered remote MCP server (06 §9 AR ``McpServer``).

    Two lifecycle methods, both added in 6.1-و-4-3 when ``DELETE
    /mcp-servers/{id}`` first needed one — and they are a *pair* on purpose.
    ``disable`` alone would make deletion irreversible in practice: the
    row keeps the workspace-unique name, ``RegisterMcpServer``'s duplicate
    guard sees it, and that name could never be registered again (03 defines
    no re-enable route). So re-registering a disabled name revives the row
    instead, exactly as ``Connection.begin`` revives a revoked connection.
    """

    id: str
    workspace_id: str
    name: McpServerName
    endpoint: McpEndpoint
    auth_ref: CipherRef | None
    status: McpStatus
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    def disable(self, now: datetime) -> None:
        """→ ``disabled``: the server stops being discovered and stops being
        routable. Idempotent, like ``Connection.revoke``.

        The ``auth_ref`` is deliberately KEPT, which is where this parts
        company with ``Connection.revoke``'s secret-dropping hygiene. A
        connection's token is a live third-party grant that revocation
        should destroy; an MCP server's ``auth_ref`` is the credential the
        operator configured for an endpoint they still own, and dropping it
        would turn ``reactivate`` into "register it all over again". Nothing
        reads it while the status is not ``active``: discovery walks
        ``list_active`` and routing refuses a non-active server.
        """
        if self.status is McpStatus.DISABLED:
            return
        self.status = McpStatus.DISABLED
        self.updated_at = now

    def reactivate(self, endpoint: McpEndpoint, auth_ref: CipherRef | None, now: datetime) -> None:
        """Re-register a name that already has a row: → ``active`` with the
        newly supplied endpoint and auth material.

        Refused on an ``active`` row — that is a genuine duplicate name and
        stays a 409 — but allowed from ``disabled`` and from ``error``, the
        two states a registration is *meant* to be able to recover from. The
        ``ConnectionStatus.CONNECTED`` guard on ``Connection.begin`` is the
        same rule at the same place: only the working state is protected
        from being re-initialised under it.
        """
        if self.status is McpStatus.ACTIVE:
            raise ConnectionStateError("cannot re-register an active MCP server")
        self.status = McpStatus.ACTIVE
        self.endpoint = endpoint
        self.auth_ref = auth_ref
        self.updated_at = now
