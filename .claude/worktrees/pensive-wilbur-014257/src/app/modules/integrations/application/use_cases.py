"""Integrations use-cases (06-domain-models §9, FR-120…124).

Thin application services over the pure domain + injected ports. They own
identity/time (framework ``new_uuid7``/``utc_now``), translate domain-rule
violations into the shared framework error hierarchy at this boundary, and
are the *only* place raw OAuth material ever exists — transiently, between a
``ConnectorProvider`` response and ``SecretsProvider.encrypt`` (INV-I1; the
unified ``tenant-secrets`` Transit key is shared with ``credentials``,
SEC-07).

Two alpha bugs are deliberately fixed here (docs/migration/refs/
credentials-oauth-jobs.md §5):

- **CSRF (state==user_id):** the OAuth ``state`` is a random 256-bit,
  single-use token bound *server-side* (via ``CacheProvider``) to the
  workspace/connection that initiated the handshake. ``CompleteOAuth`` — the
  public callback — derives its entire tenant identity from that stored
  binding and accepts none of it from the caller.
- **Refresh never persisted:** a lazy renewal (FR-124/INV-I3) always writes
  the re-encrypted tokens back through ``ConnectionRepository.update_tokens``
  and emits ``TokenRefreshed`` — including a provider-rotated
  ``refresh_token`` (and keeping the old one when the provider omits it).

One-time-use ``state`` is get-then-delete over ``CacheProvider`` (no atomic
GETDEL on the port): the sub-millisecond race window does not weaken CSRF —
the state is unguessable and server-bound either way — and a duplicated
callback loses at the provider anyway, since the authorization ``code`` is
single-use there (the second ``exchange_code`` fails → ``oauth_failed``).
PKCE is not threaded through the v1 ``ConnectorProvider`` port: v1 connectors
are confidential clients (client secret in Vault KV) + state-CSRF; a
public-client connector would need the port extended (documented extension
point, escalated at design time).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from secrets import token_urlsafe
from typing import Any, Protocol

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.ports.cache_provider import CacheProvider
from app.framework.ports.connector_provider import ConnectorProvider, OAuthTokens
from app.framework.ports.mcp_client import MCPClient, McpTarget, McpTool
from app.framework.ports.secrets_provider import SecretsProvider
from app.framework.settings.settings import IntegrationsSettings, Limits
from app.framework.types import Json, Uuid
from app.modules.integrations.domain.entities import Connection, McpServer
from app.modules.integrations.domain.errors import ConnectionStateError, IntegrationError
from app.modules.integrations.domain.events import (
    ConnectionEstablished,
    ConnectionRevoked,
    IntegrationsEvent,
    McpServerRegistered,
    TokenRefreshed,
)
from app.modules.integrations.domain.value_objects import (
    CipherRef,
    ConnectionStatus,
    ConnectorKey,
    McpEndpoint,
    McpServerName,
    McpStatus,
)
from app.modules.integrations.ports.inbound import DiscoveredTool
from app.modules.integrations.ports.repository import ConnectionRepository, McpServerRepository
from app.modules.integrations.ports.toolset import ConnectorAccess, ConnectorToolset

# Unified Transit key shared with `credentials` (05 §3.2, SEC-07).
_TENANT_SECRETS_KEY = "tenant-secrets"
# One-time OAuth state: TTL matches alpha's PKCE-verifier window (refs §2).
_OAUTH_STATE_TTL_S = 600


def _state_key(state: str) -> str:
    return f"integrations:oauth:state:{state}"


def _encode_tokens(access_token: str, refresh_token: str | None) -> bytes:
    """The exact plaintext handed to Transit: access+refresh only. ``scopes``
    and ``expires_at`` stay as *plain columns* (01 §2.9) so the lazy-refresh
    check never needs a decrypt round-trip."""
    return json.dumps({"access_token": access_token, "refresh_token": refresh_token}).encode()


def _decode_tokens(raw: bytes) -> dict[str, str | None]:
    data: dict[str, Any] = json.loads(raw.decode())
    return {"access_token": data.get("access_token"), "refresh_token": data.get("refresh_token")}


@dataclass(frozen=True, slots=True)
class ConnectorDescriptor:
    """One entry of the connector catalog ``GET /connectors`` renders
    (03 §2 ``ConnectorOut``) — Phase 6.1-و-4-1.

    The catalog is *configuration*, not state: which connectors a deployment
    offers is decided by which ``ConnectorProvider`` adapters it builds, so
    the Composition Root hands this list down rather than a repository
    reading it. ``auth_type`` is a fixed ``'oauth2'`` in v1 — every connector
    in this module's design is an OAuth2 confidential client (module
    docstring) — and it is carried explicitly anyway, because the day an
    API-key connector appears the catalog must be able to say so.

    ``scopes`` are the connector's *offered* scopes, which is a different
    list from the ones a given ``Connection`` was actually granted.
    """

    key: str
    name: str
    scopes: tuple[str, ...]
    auth_type: str = "oauth2"


class ListConnectors:
    """The deployment's connector catalog (6.1-و-4-1).

    Takes no ``ExecutionContext`` argument beyond the caller's, and reads
    nothing: the catalog is the same for every workspace in v1. An **empty**
    catalog is a legitimate answer, not a failure — a deployment that has
    built no connector adapter offers no connectors, and saying so with an
    empty collection is the truth. It is also the answer a client needs in
    order to make sense of ``POST /connections`` refusing every key with
    ``integrations.connector_unknown``.
    """

    def __init__(self, catalog: Sequence[ConnectorDescriptor]) -> None:
        self._catalog = tuple(catalog)

    async def execute(self, ctx: ExecutionContext) -> tuple[ConnectorDescriptor, ...]:
        return self._catalog


class ListConnections:
    """This workspace's connections, newest first, every status (6.1-و-4-1),
    one page at a time (6.3-ب — the listing's documented ceiling counts only
    connected rows, so it never bounded this read)."""

    def __init__(self, connections: ConnectionRepository) -> None:
        self._connections = connections

    async def execute(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[Connection]:
        return await self._connections.list(ctx, limit=limit, cursor=cursor)


@dataclass(frozen=True, slots=True)
class ConnectionInitiation:
    """``BeginConnection``'s result: the (pending) connection row plus the
    provider authorize URL the client must redirect the user to, and the
    one-time ``state`` bound to that URL. No domain event — nothing has been
    established yet.

    ``state`` is carried out (6.1-و-4-1) because 03 §2's ``AuthorizeOut``
    returns it: it is a CSRF nonce, not a secret — unguessable, single-use,
    and bound server-side to the workspace that minted it — and the client
    holding it is by definition the one that just asked for it. Handing it
    back lets that client match the callback it later observes to the
    handshake it started.
    """

    connection: Connection
    authorize_url: str
    state: str


class BeginConnection:
    """Start (or restart) a connector's OAuth handshake (06 §9)."""

    def __init__(
        self,
        connections: ConnectionRepository,
        connectors: Mapping[str, ConnectorProvider],
        cache: CacheProvider,
        settings: IntegrationsSettings,
        limits: Limits,
    ) -> None:
        self._connections = connections
        self._connectors = connectors
        self._cache = cache
        self._settings = settings
        self._limits = limits

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        connector_key: str,
        redirect_uri: str,
        scopes: Sequence[str],
        display_name: str | None = None,
    ) -> ConnectionInitiation:
        provider = self._connectors.get(connector_key)
        if provider is None:
            raise ValidationError(
                f"unknown connector: {connector_key!r}", code="integrations.connector_unknown"
            )
        try:
            key = ConnectorKey(connector_key)
        except IntegrationError as exc:
            raise ValidationError(str(exc)) from exc

        # Open-redirect guard: the callback must live under the platform's
        # configured OAuth redirect base (refs §2 — the callback is public).
        base = self._settings.oauth_redirect_base_url
        if not base or not redirect_uri.startswith(base):
            raise ValidationError("redirect_uri is outside the configured OAuth redirect base")

        now = utc_now()
        existing = await self._connections.find_by_connector(ctx, key.value)
        if existing is None:
            # Cap applies to *new* rows only; restarting an existing row adds
            # nothing. Counted against connected rows (pending rows are
            # handshake debris, not capacity).
            connected = await self._connections.list_connected(ctx)
            if len(connected) >= self._limits.max_connectors:
                raise ConflictError(
                    f"workspace already has {self._limits.max_connectors} connected connectors",
                    code="integrations.too_many",
                )
            conn = Connection(
                id=new_uuid7(),
                workspace_id=ctx.workspace_id,
                connector_key=key,
                display_name=display_name,
                status=ConnectionStatus.PENDING,
                scopes=tuple(scopes),
                token_ref=None,
                expires_at=None,
                last_error=None,
                created_by=ctx.user_id,
                created_at=now,
                updated_at=now,
                version=1,
            )
            await self._connections.add(ctx, conn)
        elif existing.status is not ConnectionStatus.CONNECTED:
            existing.begin(tuple(scopes), now)
            await self._connections.save(ctx, existing)
            conn = existing
        else:
            # Re-consent on a live connection: leave the working row alone;
            # only a fresh state (and later a successful exchange) matters.
            conn = existing

        state = token_urlsafe(32)  # 256-bit, unguessable, single-use
        payload = json.dumps(
            {
                "workspace_id": ctx.workspace_id,
                "connection_id": conn.id,
                "connector_key": key.value,
                "created_by": ctx.user_id,
                "redirect_uri": redirect_uri,
            }
        ).encode()
        await self._cache.set(_state_key(state), payload, ttl_s=_OAUTH_STATE_TTL_S)
        authorize_url = provider.authorize_url(redirect_uri, state, list(scopes))
        return ConnectionInitiation(connection=conn, authorize_url=authorize_url, state=state)


class StartConnection:
    """``POST /connections``: begin a handshake and return the row (6.1-و-4-1).

    A thin composition over ``BeginConnection`` whose only additions are the
    two things the API layer must not invent for itself:

    * the **redirect URI**, which is derived from configuration by the
      Composition Root and passed in here once. A router that built its own
      would be choosing where a third party sends a user back to — the very
      thing ``BeginConnection``'s open-redirect guard exists to police.
    * **dropping the authorize URL**, because 03's ``POST /connections``
      answers ``201`` with a bare ``ConnectionOut`` and the URL is
      ``POST /connections/{id}/authorize``'s to return.

    That split means creating a connection mints an OAuth ``state`` that is
    then never used. Harmless by construction — a state is single-use with a
    600 s TTL and is only ever *consumed* by a callback carrying it — and the
    alternative (a second code path that creates the row without a state)
    would duplicate the one use-case that owns the handshake's invariants.
    """

    def __init__(self, begin: BeginConnection, redirect_uri: str) -> None:
        self._begin = begin
        self._redirect_uri = redirect_uri

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        connector_key: str,
        scopes: Sequence[str],
        display_name: str | None = None,
    ) -> Connection:
        initiation = await self._begin.execute(
            ctx,
            connector_key=connector_key,
            redirect_uri=self._redirect_uri,
            scopes=scopes,
            display_name=display_name,
        )
        return initiation.connection


class AuthorizeConnection:
    """``POST /connections/{id}/authorize``: the URL to redirect a user to
    (6.1-و-4-1).

    Addresses an **existing** connection by id and re-runs the handshake's
    opening move for the connector and scopes that row already carries — the
    client does not get to change either at this point, which is why this
    takes no body. A fresh ``state`` is minted per call on purpose: a user
    who abandoned the browser flow and came back must be able to try again,
    and the abandoned state simply expires.

    Unknown or another tenant's id ⇒ ``NotFoundError`` (a 403 would confirm
    the id exists). A ``revoked`` row is *not* special-cased here:
    ``Connection.begin`` moves it back to ``pending``, which is exactly what
    reconnecting a revoked connector should mean — and a live ``connected``
    row is left untouched by ``BeginConnection`` while still yielding a URL,
    so re-consenting to broader scopes cannot knock a working connection out.
    """

    def __init__(
        self, connections: ConnectionRepository, begin: BeginConnection, redirect_uri: str
    ) -> None:
        self._connections = connections
        self._begin = begin
        self._redirect_uri = redirect_uri

    async def execute(self, ctx: ExecutionContext, *, connection_id: Uuid) -> ConnectionInitiation:
        conn = await self._connections.get(ctx, connection_id)
        if conn is None:
            raise NotFoundError("connection not found")
        return await self._begin.execute(
            ctx,
            connector_key=conn.connector_key.value,
            redirect_uri=self._redirect_uri,
            scopes=conn.scopes,
            display_name=conn.display_name,
        )


class CompleteOAuth:
    """Public-callback half of the handshake (06 §9 ``CompleteOAuth``).

    Takes **no** ``ExecutionContext``: the callback is unauthenticated, so
    the tenant identity is derived exclusively from the server-side ``state``
    binding written by ``BeginConnection`` — never from anything the caller
    supplies (the CSRF fix; refs §5-3).
    """

    def __init__(
        self,
        connections: ConnectionRepository,
        connectors: Mapping[str, ConnectorProvider],
        cache: CacheProvider,
        secrets: SecretsProvider,
    ) -> None:
        self._connections = connections
        self._connectors = connectors
        self._cache = cache
        self._secrets = secrets

    async def execute(
        self, *, state: str, code: str
    ) -> tuple[Connection, tuple[IntegrationsEvent, ...]]:
        raw = await self._cache.get(_state_key(state))
        if raw is None:
            # Fail-closed: forged, expired, or replayed state — no DB touch.
            # 6.2 names it. 03 §4 had no entry, so §3.66 shipped the generic
            # 422 rather than invent one; the catalog pass is where inventing
            # is allowed, and this outcome earns its own code because it is
            # the ONE failure on a public, unauthenticated route — an operator
            # reading logs must be able to tell "someone is probing the
            # callback" from "a client sent a malformed body".
            raise ValidationError(
                "invalid or expired OAuth state", code="integrations.oauth_state_invalid"
            )
        await self._cache.delete(_state_key(state))  # single use (module docstring)

        payload: dict[str, Any] = json.loads(raw.decode())
        ctx = ExecutionContext(
            workspace_id=payload["workspace_id"],
            user_id=payload.get("created_by"),
            correlation_id=new_uuid7(),
            roles=frozenset(),
        )
        conn = await self._connections.get(ctx, payload["connection_id"])
        if conn is None:
            raise NotFoundError("connection not found")
        provider = self._connectors.get(payload["connector_key"])
        if provider is None:
            raise ValidationError(
                f"unknown connector: {payload['connector_key']!r}",
                code="integrations.connector_unknown",
            )

        now = utc_now()
        try:
            tokens: OAuthTokens = await provider.exchange_code(code, payload["redirect_uri"])
        except Exception as exc:
            # Bad/expired/replayed code, provider outage, timeout (the
            # adapter enforces Limits.oauth_timeout_s): record and surface a
            # clean 502 instead of a stack trace (refs §2 step B).
            conn.mark_error("oauth code exchange failed", now)
            await self._connections.save(ctx, conn)
            raise AppError("oauth code exchange failed", code="integrations.oauth_failed") from exc

        ciphertext = await self._secrets.encrypt(
            _TENANT_SECRETS_KEY, _encode_tokens(tokens.access_token, tokens.refresh_token)
        )
        expires_at = now + timedelta(seconds=tokens.expires_in)
        try:
            conn.connect(
                CipherRef(ciphertext, _TENANT_SECRETS_KEY), expires_at, tuple(tokens.scopes), now
            )
        except ConnectionStateError as exc:  # revoked mid-handshake
            raise ConflictError(str(exc)) from exc
        await self._connections.save(ctx, conn)
        event = ConnectionEstablished(conn.id, ctx.workspace_id, conn.connector_key.value, now)
        return conn, (event,)


class RefreshConnectionToken:
    """Lazy (on-demand) token renewal — INV-I3 / FR-124.

    ``ensure_fresh`` is the single gate every token use passes through
    (``invoke_tool`` calls it before each connector call). It is a no-op
    while the token is fresh; otherwise it refreshes through the provider
    and — fixing alpha's bug — *persists* the renewed material via
    ``update_tokens`` and emits ``TokenRefreshed``. A provider-rotated
    ``refresh_token`` is stored; if the provider omits one, the previous
    refresh token is kept (never dropped).
    """

    def __init__(
        self,
        connections: ConnectionRepository,
        connectors: Mapping[str, ConnectorProvider],
        secrets: SecretsProvider,
        settings: IntegrationsSettings,
    ) -> None:
        self._connections = connections
        self._connectors = connectors
        self._secrets = secrets
        self._settings = settings

    async def ensure_fresh(
        self, ctx: ExecutionContext, conn: Connection
    ) -> tuple[Connection, tuple[IntegrationsEvent, ...]]:
        now = utc_now()
        if conn.token_ref is None:
            raise ConflictError(
                "connection holds no token material", code="integrations.not_connected"
            )
        if not conn.needs_refresh(now, self._settings.oauth_refresh_skew_s):
            return conn, ()

        stored = _decode_tokens(
            await self._secrets.decrypt(conn.token_ref.key_name, conn.token_ref.ciphertext)
        )
        refresh_token = stored.get("refresh_token")
        if not refresh_token:
            conn.mark_error("access token expired and no refresh_token is stored", now)
            await self._connections.save(ctx, conn)
            raise ConflictError(
                "access token expired and no refresh_token is stored",
                code="integrations.not_connected",
            )
        provider = self._connectors.get(conn.connector_key.value)
        if provider is None:
            raise ValidationError(
                f"unknown connector: {conn.connector_key.value!r}",
                code="integrations.connector_unknown",
            )
        try:
            renewed = await provider.refresh(refresh_token)
        except Exception as exc:
            conn.mark_error("oauth token refresh failed", now)
            await self._connections.save(ctx, conn)
            raise AppError("oauth token refresh failed", code="integrations.oauth_failed") from exc

        effective_refresh = renewed.refresh_token or refresh_token  # keep old if not rotated
        ciphertext = await self._secrets.encrypt(
            _TENANT_SECRETS_KEY, _encode_tokens(renewed.access_token, effective_refresh)
        )
        new_expires = now + timedelta(seconds=renewed.expires_in)
        # Persisting the renewal is the whole point (alpha bug fix): a narrow
        # hot-path write, no version race with concurrent refreshes.
        await self._connections.update_tokens(
            ctx, conn.id, ciphertext, _TENANT_SECRETS_KEY, new_expires
        )
        conn.apply_refreshed_tokens(CipherRef(ciphertext, _TENANT_SECRETS_KEY), new_expires, now)
        return conn, (TokenRefreshed(conn.id, new_expires, now),)


class RevokeConnection:
    """Revoke a connection and drop its stored secret (06 §9)."""

    def __init__(self, connections: ConnectionRepository) -> None:
        self._connections = connections

    async def execute(
        self, ctx: ExecutionContext, *, connection_id: Uuid
    ) -> tuple[Connection, tuple[IntegrationsEvent, ...]]:
        conn = await self._connections.get(ctx, connection_id)
        if conn is None:
            raise NotFoundError("connection not found")
        if conn.status is ConnectionStatus.REVOKED:
            return conn, ()  # idempotent: no second event
        now = utc_now()
        conn.revoke(now)
        await self._connections.save(ctx, conn)
        return conn, (ConnectionRevoked(conn.id, now),)


class RegisterMcpServer:
    """Register a remote MCP server for the workspace (06 §9, INV-I2).

    Registration is by NAME, and a name is workspace-unique
    (``uq_mcp_ws_name``, 01 §2.9). A name already held by an *active* row is
    a duplicate and conflicts; one held by a ``disabled``/``error`` row is
    revived in place (6.1-و-4-3 — see ``McpServer.disable``). That makes
    ``DELETE`` a reversible operation without a re-enable route 03 does not
    define.
    """

    def __init__(
        self,
        servers: McpServerRepository,
        secrets: SecretsProvider,
        settings: IntegrationsSettings,
        limits: Limits,
        connector_keys: frozenset[str],
    ) -> None:
        self._servers = servers
        self._secrets = secrets
        self._settings = settings
        self._limits = limits
        # The composition root passes the configured connector-catalog keys so
        # a server name can never shadow a connector prefix in tool routing.
        self._connector_keys = connector_keys

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        name: str,
        endpoint_url: str,
        transport: str = "http",
        auth: Json | None = None,
    ) -> tuple[McpServer, tuple[IntegrationsEvent, ...]]:
        try:
            server_name = McpServerName(name)
        except IntegrationError as exc:
            raise ValidationError(str(exc)) from exc
        if transport not in self._settings.mcp_allowed_transports:
            raise ValidationError(
                f"MCP transport {transport!r} is not allowed",
                code="integrations.mcp_transport_unsupported",
            )
        try:
            endpoint = McpEndpoint(endpoint_url, transport)
        except IntegrationError as exc:
            # UnsupportedMcpTransport and malformed-URL both surface as 422;
            # the transport case keeps its stable catalog code (03 §4).
            code = (
                "integrations.mcp_transport_unsupported"
                if "transport" in str(exc)
                else "common.validation_error"
            )
            raise ValidationError(str(exc), code=code) from exc

        if server_name.value in self._connector_keys:
            raise ValidationError(
                f"MCP server name {server_name.value!r} collides with a connector key"
            )
        existing = await self._servers.find_by_name(ctx, server_name.value)
        if existing is not None and existing.status is McpStatus.ACTIVE:
            raise ConflictError(f"an MCP server named {server_name.value!r} already exists")
        active = await self._servers.list_active(ctx)
        if len(active) >= self._limits.max_mcp_servers:
            # No carve-out for the revive path, and none is needed: `existing`
            # is non-active by the time we get here (an active name already
            # raised above), so it is never among `active` and reviving it
            # cannot be blocked by a cap it does not currently occupy.
            raise ConflictError(
                f"workspace already has {self._limits.max_mcp_servers} MCP servers",
                code="integrations.too_many",
            )

        auth_ref: CipherRef | None = None
        if auth is not None:
            ciphertext = await self._secrets.encrypt(_TENANT_SECRETS_KEY, json.dumps(auth).encode())
            auth_ref = CipherRef(ciphertext, _TENANT_SECRETS_KEY)  # INV-I1

        now = utc_now()
        if existing is not None:
            # Re-registering a disabled/errored name revives its row rather
            # than minting a second one: `uq_mcp_ws_name` makes a second row
            # impossible anyway, and without this branch a DELETE would burn
            # the name forever (`McpServer.disable`'s docstring). The id is
            # deliberately the OLD one — same server, same identity.
            existing.reactivate(endpoint, auth_ref, now)
            await self._servers.save(ctx, existing)
            return existing, (McpServerRegistered(existing.id, ctx.workspace_id, now),)

        server = McpServer(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            name=server_name,
            endpoint=endpoint,
            auth_ref=auth_ref,
            status=McpStatus.ACTIVE,
            created_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        await self._servers.add(ctx, server)
        return server, (McpServerRegistered(server.id, ctx.workspace_id, now),)


class ListMcpServers:
    """``GET /mcp-servers``: the workspace's registered servers, every status
    (6.1-و-4-3).

    The administrative read, not the routing one — ``list``, never
    ``list_active`` (``McpServerRepository.list``). A ``disabled`` row is the
    single most important thing to show, since deleting a server is what
    produces one and its name stays reserved until it is re-registered.
    """

    def __init__(self, servers: McpServerRepository) -> None:
        self._servers = servers

    async def execute(self, ctx: ExecutionContext) -> tuple[McpServer, ...]:
        return tuple(await self._servers.list(ctx))


class DisableMcpServer:
    """``DELETE /mcp-servers/{id}``: stop using a server (6.1-و-4-3).

    Disables, does not erase — the ``RevokeConnection``/credentials
    precedent. The row survives, so ``GET`` can still explain why a set of
    tools disappeared from the catalog, and the audit trail of what was once
    registered is not rewritten by a DELETE.

    **Emits no event.** 06 §9 lists exactly one MCP event
    (``McpServerRegistered``) and 04 §5 promotes none of this module's events
    to a stream, so inventing ``McpServerDisabled`` would add an unconsumed
    type — the thing 12-module-authoring-guide forbids. It returns the
    server rather than the ``(aggregate, events)`` pair its siblings return,
    which is the same statement in the signature.

    Idempotent: disabling a disabled server is a second 204, not a 409.
    Unknown or another tenant's id ⇒ ``NotFoundError`` (a 403 would confirm
    the id exists).
    """

    def __init__(self, servers: McpServerRepository) -> None:
        self._servers = servers

    async def execute(self, ctx: ExecutionContext, *, server_id: Uuid) -> McpServer:
        server = await self._servers.get(ctx, server_id)
        if server is None:
            raise NotFoundError("MCP server not found")
        if server.status is McpStatus.DISABLED:
            return server  # idempotent: no second write
        server.disable(utc_now())
        await self._servers.save(ctx, server)
        return server


class WorkspaceToolCatalog:
    """Implements the ``ToolCatalog`` inbound port (02 §2): 06 §9's
    ``DiscoverWorkspaceTools`` (``list_tools``) + ``InvokeConnectorTool``
    (``invoke_tool``) behind one object, consumed by the Phase-4 tool system
    (INV-I4 — never a direct connector coupling).

    Routing convention: ``<prefix>.<tool>`` where ``<prefix>`` is a connector
    key or an MCP server name. Prefix ambiguity is prevented structurally
    (slugs contain no dots; ``RegisterMcpServer`` rejects names shadowing a
    connector key), and connectors win the lookup order regardless.
    """

    def __init__(
        self,
        connections: ConnectionRepository,
        servers: McpServerRepository,
        toolset: ConnectorToolset,
        mcp_client: MCPClient,
        secrets: SecretsProvider,
        refresher: RefreshConnectionToken,
        limits: Limits,
    ) -> None:
        self._connections = connections
        self._servers = servers
        self._toolset = toolset
        self._mcp_client = mcp_client
        self._secrets = secrets
        self._refresher = refresher
        self._limits = limits

    async def list_tools(self, ctx: ExecutionContext) -> list[DiscoveredTool]:
        """06 §9 ``DiscoverWorkspaceTools``: connector tools (static
        per-connector catalog in v1) + remote-MCP tools discovered live at
        runtime (FR-52/122 — nothing persisted). MCP discovery is
        best-effort: an unreachable/broken server is skipped, never failing
        the whole catalog. Connectors come first; the combined list is capped
        at ``Limits.max_discovered_tools`` (07 §4).
        """
        tools: list[DiscoveredTool] = []
        for conn in await self._connections.list_connected(ctx):
            key = conn.connector_key.value
            tools.extend(
                DiscoveredTool(
                    name=f"{key}.{spec.name}",
                    description=spec.description,
                    parameters=spec.parameters,
                    source=f"connector:{conn.id}",
                )
                for spec in self._toolset.list_tools(key)
            )

        servers = await self._servers.list_active(ctx)
        discoveries = await asyncio.gather(
            *(self._discover(server) for server in servers), return_exceptions=True
        )
        for server, discovered in zip(servers, discoveries, strict=True):
            if isinstance(discovered, BaseException):
                continue  # best-effort: a dead server must not sink the catalog
            tools.extend(
                DiscoveredTool(
                    name=f"{server.name.value}.{tool.name}",
                    description=tool.description,
                    parameters=tool.parameters,
                    source=f"mcp:{server.name.value}",
                )
                for tool in discovered
            )
        return tools[: self._limits.max_discovered_tools]

    async def invoke_tool(self, ctx: ExecutionContext, name: str, args: Json) -> Json:
        """06 §9 ``InvokeConnectorTool``: route ``<prefix>.<tool>`` to its
        connector — passing the INV-I3 lazy-refresh gate before every use —
        or to its remote MCP server. Connectors win the prefix lookup;
        collisions are prevented structurally at registration (no dots in
        slugs + ``RegisterMcpServer``'s shadow guard)."""
        prefix, sep, tool = name.partition(".")
        if not sep or not prefix or not tool:
            raise ValidationError(f"tool name must look like '<prefix>.<tool>': {name!r}")

        conn = await self._connections.find_by_connector(ctx, prefix)
        if conn is not None:
            return await self._invoke_connector_tool(ctx, conn, prefix, tool, args)

        server = await self._servers.find_by_name(ctx, prefix)
        if server is None:
            raise NotFoundError(f"no connector or MCP server matches prefix {prefix!r}")
        if server.status is not McpStatus.ACTIVE:
            raise ConflictError(
                f"MCP server {prefix!r} is not active", code="integrations.not_connected"
            )
        target = await self._target(server)
        try:
            return await self._mcp_client.call_tool(target, tool, args)
        except Exception as exc:
            raise AppError(
                f"MCP server {prefix!r} is unreachable", code="integrations.mcp_unreachable"
            ) from exc

    async def _invoke_connector_tool(
        self, ctx: ExecutionContext, conn: Connection, prefix: str, tool: str, args: Json
    ) -> Json:
        if conn.status is not ConnectionStatus.CONNECTED:
            raise ConflictError(
                f"connector {prefix!r} is not connected", code="integrations.not_connected"
            )
        conn, _refresh_events = await self._refresher.ensure_fresh(ctx, conn)
        # TokenRefreshed is not surfaced through ToolCatalog's Json contract;
        # the durable `update_tokens` write inside ensure_fresh is the source
        # of truth (module docstring / design risk note).
        if conn.token_ref is None:  # narrow for typing; ensure_fresh guarantees it
            raise ConflictError(
                f"connector {prefix!r} holds no token material",
                code="integrations.not_connected",
            )
        stored = _decode_tokens(
            await self._secrets.decrypt(conn.token_ref.key_name, conn.token_ref.ciphertext)
        )
        access_token = stored.get("access_token")
        if not access_token:
            raise ConflictError(
                f"connector {prefix!r} holds no access token",
                code="integrations.not_connected",
            )
        access = ConnectorAccess(
            connector_key=prefix, access_token=access_token, scopes=conn.scopes
        )
        return await self._toolset.invoke(ctx, access=access, tool_name=tool, args=args)

    async def _discover(self, server: McpServer) -> list[McpTool]:
        return await self._mcp_client.list_tools(await self._target(server))

    async def _target(self, server: McpServer) -> McpTarget:
        """Build the transport target, decrypting the (optional) MCP auth
        material transiently — the INV-I1 pattern: ciphertext at rest, raw
        auth only inside a single call's scope."""
        auth: Json | None = None
        if server.auth_ref is not None:
            raw = await self._secrets.decrypt(server.auth_ref.key_name, server.auth_ref.ciphertext)
            auth = json.loads(raw.decode())
        return McpTarget(server.endpoint.url, server.endpoint.transport, auth)


class ToolDiscovery(Protocol):
    """The **reading half** of ``ToolCatalog``, and the only half the API
    layer is given (6.1-و-4-3).

    ``WorkspaceToolCatalog`` satisfies this structurally, but a field typed
    as this Protocol exposes ``list_tools`` and nothing else: a router that
    tried ``services.integrations.tools.invoke_tool(...)`` would not be a
    review finding, it would be a **type error**. That is what و-4-1's
    docstring promised when it said ``invoke_tool`` is unreachable "by
    construction", and ``GET /tools`` is the first step that would otherwise
    have quietly broken the promise — the object it needs carries both
    halves.

    So the boundary is drawn at the type, not at the object. INV-I4 stays
    intact in the other direction too: the agent runtime still consumes the
    full ``ToolCatalog`` inbound port, unnarrowed.
    """

    async def list_tools(self, ctx: ExecutionContext) -> list[DiscoveredTool]: ...


@dataclass(frozen=True, slots=True)
class IntegrationsUseCases:
    """The module's API-facing bundle — ONE field on ``ApiServices``
    (the ``CredentialUseCases``/``KnowledgeUseCases`` precedent). **Complete
    at 6.1-و-4-3**: ten fields, one per 03 §1 route plus the public callback.

    **Two omissions carry the module's security shape**, and each is
    structural rather than a route nobody wrote:

    * ``WorkspaceToolCatalog.invoke_tool`` — the one face that *uses* a
      connection's decrypted access token against a third party. It belongs
      to the agent runtime through the ``ToolCatalog`` inbound port
      (INV-I4); reaching it from an HTTP request would let a client drive
      arbitrary connector calls with a token it can never see, which is a
      strictly worse hole than leaking the token. و-4-3 needs the *reading*
      half of that same object, so the omission moved from "not in the
      bundle" to "not in the field's TYPE": ``tools`` is a ``ToolDiscovery``,
      which has ``list_tools`` and nothing else.
    * ``RefreshConnectionToken`` — the INV-I3 gate every token use passes
      through. It is an internal invariant, not an operation: a client
      cannot ask for a refresh, and does not need to, because every use
      refreshes itself.

    ``complete`` (و-4-2) is the one field no *authenticated* route may reach.
    It takes no ``ExecutionContext`` at all (it *derives* one from the
    server-side state binding), so it cannot sit behind the same door as the
    others; the public callback router mounts it separately. Being in this
    bundle is wiring, not exposure: what a request can reach is decided by
    which router names a field, and only the unauthenticated callback names
    this one.

    ``tools`` is ``None`` while this deployment has no ``ConnectorToolset``
    and no ``MCPClient`` adapter — the ``knowledge.search`` shape (§3.64),
    optionality declared in the type so the route's 503 branch is something
    mypy knows about rather than a runtime surprise.

    And what no face here returns, at any point: a ``CipherRef``, a token,
    or an ``auth_ref``. INV-I1 holds at the domain level; the DTOs restate
    it at the wire.
    """

    list_connectors: ListConnectors
    list_connections: ListConnections
    start: StartConnection
    authorize: AuthorizeConnection
    revoke: RevokeConnection
    complete: CompleteOAuth
    list_mcp_servers: ListMcpServers
    register_mcp_server: RegisterMcpServer
    disable_mcp_server: DisableMcpServer
    tools: ToolDiscovery | None
