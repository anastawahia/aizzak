"""In-memory integrations wiring shared by the API router tests (6.1-و-4-1,
extended by و-4-2).

Not a ``test_*`` module, so pytest never collects it — the
``support_knowledge``/``support_credentials`` precedent: every router test
file constructs ``ApiServices``, and a per-file copy of these fakes is how
copies drift.

Four fakes, each faithful on exactly what the routes ride on:

* ``InMemoryConnectionRepository.list`` returns the caller's OWN rows only,
  newest first and **in every status** — the tenant scoping and ordering the
  SQL adapter gets from ``WHERE workspace_id`` plus a descending UUIDv7 sort,
  and the deliberate absence of the ``status = 'connected'`` filter that
  ``list_connected`` has.
* ``RecordingConnector`` is a structural ``ConnectorProvider`` that builds a
  deterministic authorize URL and LOGS the ``(redirect_uri, state, scopes)``
  it was handed. That log is what lets a test prove the redirect URI came
  from configuration rather than from the request body — the open-redirect
  property the router's docstring claims. ``exchanges`` is the same log for
  the callback's half, and ``exchange_error`` makes a provider failure a
  first-class scenario rather than a mocked-out edge.
* ``DictCache`` is a structural ``CacheProvider`` over one dict, so the
  one-time OAuth ``state`` a handshake mints is inspectable — and so a test
  can prove the callback DELETED it.
* ``RecordingSecrets`` (و-4-2) is a structural ``SecretsProvider`` with
  base64 standing in for Transit, logging every plaintext it was asked to
  encrypt. That log is how a test proves the callback's tokens went through
  encryption on their way to storage (INV-I1) instead of being stored raw.

و-4-3 adds two more:

* ``InMemoryMcpServerRepository``, whose ``list`` returns every status
  (newest first) while ``list_active`` filters — the pair the routes depend
  on, since ``DELETE`` produces a ``disabled`` row that ``GET`` must still
  show.
* ``StubToolDiscovery``, a structural ``ToolDiscovery`` returning a fixed
  catalog. It has ``list_tools`` and **no** ``invoke_tool`` at all, which is
  how a test can show that ``GET /tools`` is served by the narrow half of
  the port rather than by the full ``WorkspaceToolCatalog``.

``build_integrations`` wires ONE repository behind every face, exactly as the
Composition Root does — which is why a connection created through ``POST`` is
immediately the one ``GET`` reports. ``tools`` defaults to ``None``:
production's state today, and the one a test needs in order to prove the 503
is real.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.ports.connector_provider import ConnectorProvider, OAuthTokens
from app.framework.settings.settings import IntegrationsSettings, Limits
from app.framework.types import Json
from app.modules.integrations.application.use_cases import (
    AuthorizeConnection,
    BeginConnection,
    CompleteOAuth,
    ConnectorDescriptor,
    DisableMcpServer,
    IntegrationsUseCases,
    ListConnections,
    ListConnectors,
    ListMcpServers,
    RegisterMcpServer,
    RevokeConnection,
    StartConnection,
)
from app.modules.integrations.domain.entities import Connection, McpServer
from app.modules.integrations.domain.value_objects import (
    CipherRef,
    ConnectionStatus,
    ConnectorKey,
    McpEndpoint,
    McpServerName,
    McpStatus,
)
from app.modules.integrations.ports.inbound import DiscoveredTool

SEEDED_CREATED_AT = datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC)
SEEDED_EXPIRES_AT = datetime(2026, 5, 6, 8, 8, 9, tzinfo=UTC)

# The base a deployment configures, and the callback the Composition Root
# derives under it — mirrored here so a test can assert the exact string the
# connector was handed.
REDIRECT_BASE = "https://app.example.test"
REDIRECT_URI = f"{REDIRECT_BASE}/api/v1/integrations/connections/oauth/callback"


@dataclass
class RecordingConnector:
    """A structural ``ConnectorProvider`` that records every authorize call."""

    connector: str = "github"
    calls: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    exchanges: list[tuple[str, str]] = field(default_factory=list)
    # When set, `exchange_code` raises it — the provider-side failure the
    # callback must turn into `integrations.oauth_failed`/502 (و-4-2).
    exchange_error: str | None = None

    def authorize_url(self, redirect_uri: str, state: str, scopes: Sequence[str]) -> str:
        self.calls.append((redirect_uri, state, tuple(scopes)))
        return f"https://{self.connector}.test/authorize?state={state}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        self.exchanges.append((code, redirect_uri))
        if self.exchange_error is not None:
            raise RuntimeError(self.exchange_error)
        return OAuthTokens(access_token="at", refresh_token="rt", expires_in=3600, scopes=("repo",))

    async def refresh(self, refresh_token: str) -> OAuthTokens:
        return OAuthTokens(
            access_token="at2", refresh_token="rt2", expires_in=3600, scopes=("repo",)
        )


@dataclass
class DictCache:
    """A structural ``CacheProvider`` over one dict (no TTL simulation: the
    handshake's expiry is Redis's job, not a use-case rule under test)."""

    values: dict[str, bytes] = field(default_factory=dict)

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def incr(self, key: str, amount: int = 1) -> int:
        return amount

    async def expire(self, key: str, ttl_s: int) -> None:
        return None


@dataclass
class RecordingSecrets:
    """A structural ``SecretsProvider``: base64 in place of Transit, plus a
    log of every plaintext encrypted (the ``support_credentials`` fake, و-4-2).
    """

    encrypted: list[bytes] = field(default_factory=list)

    async def get_secret(self, path: str) -> Json:
        return {}

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        self.encrypted.append(plaintext)
        return f"vault:v1:{base64.b64encode(plaintext).decode('ascii')}"

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        return base64.b64decode(ciphertext.split(":", 2)[2])


@dataclass
class InMemoryConnectionRepository:
    """A structural ``ConnectionRepository`` over one dict."""

    rows: dict[str, Connection] = field(default_factory=dict)

    async def get(self, ctx: ExecutionContext, conn_id: str) -> Connection | None:
        row = self.rows.get(conn_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, conn: Connection) -> None:
        self.rows[conn.id] = conn

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[Connection]:
        # Newest-first keyset on `id` through the REAL codec (6.3-ب).
        items = sorted(
            (row for row in self.rows.values() if row.workspace_id == ctx.workspace_id),
            key=lambda row: row.id,
            reverse=True,
        )
        if cursor is not None:
            after = decode_id_cursor(cursor)
            items = [row for row in items if row.id < after]
        page, has_more = items[:limit], len(items) > limit
        next_cursor = encode_id_cursor(page[-1].id) if has_more and page else None
        return Page(data=page, next_cursor=next_cursor, limit=limit)

    async def list_connected(self, ctx: ExecutionContext) -> list[Connection]:
        return [
            row
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.status is ConnectionStatus.CONNECTED
        ]

    async def update_tokens(
        self,
        ctx: ExecutionContext,
        conn_id: str,
        token_ref: str,
        key_id: str,
        expires_at: datetime,
    ) -> None:
        # The API bundle holds no refreshing face (INV-I3 is an internal
        # gate), so a router test reaching this is a wiring mistake.
        raise AssertionError("the API bundle must never drive a token refresh")

    async def find_by_connector(
        self, ctx: ExecutionContext, connector_key: str
    ) -> Connection | None:
        for row in self.rows.values():
            if row.workspace_id == ctx.workspace_id and row.connector_key.value == connector_key:
                return row
        return None

    async def save(self, ctx: ExecutionContext, conn: Connection) -> None:
        self.rows[conn.id] = conn


@dataclass
class InMemoryMcpServerRepository:
    """A structural ``McpServerRepository`` over one dict."""

    rows: dict[str, McpServer] = field(default_factory=dict)

    async def add(self, ctx: ExecutionContext, server: McpServer) -> None:
        self.rows[server.id] = server

    async def get(self, ctx: ExecutionContext, server_id: str) -> McpServer | None:
        row = self.rows.get(server_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def list(self, ctx: ExecutionContext) -> list[McpServer]:
        return sorted(
            (row for row in self.rows.values() if row.workspace_id == ctx.workspace_id),
            key=lambda row: row.id,
            reverse=True,
        )

    async def list_active(self, ctx: ExecutionContext) -> list[McpServer]:
        return [
            row
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.status is McpStatus.ACTIVE
        ]

    async def find_by_name(self, ctx: ExecutionContext, name: str) -> McpServer | None:
        for row in self.rows.values():
            if row.workspace_id == ctx.workspace_id and row.name.value == name:
                return row
        return None

    async def save(self, ctx: ExecutionContext, server: McpServer) -> None:
        self.rows[server.id] = server


@dataclass
class StubToolDiscovery:
    """A structural ``ToolDiscovery``: ``list_tools`` and nothing else."""

    tools: list[DiscoveredTool] = field(default_factory=list)
    calls: int = 0

    async def list_tools(self, ctx: ExecutionContext) -> list[DiscoveredTool]:
        self.calls += 1
        return list(self.tools)


@dataclass(frozen=True, slots=True)
class IntegrationsStack:
    """The bundle plus the fakes a test asserts against."""

    integrations: IntegrationsUseCases
    repository: InMemoryConnectionRepository
    cache: DictCache
    connector: RecordingConnector
    secrets: RecordingSecrets
    servers: InMemoryMcpServerRepository


def seed_connection(
    *,
    connection_id: str,
    workspace_id: str,
    connector_key: str = "github",
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
    scopes: tuple[str, ...] = ("repo",),
    display_name: str | None = "GitHub",
    last_error: str | None = None,
) -> Connection:
    connected = status is ConnectionStatus.CONNECTED
    return Connection(
        id=connection_id,
        workspace_id=workspace_id,
        connector_key=ConnectorKey(connector_key),
        display_name=display_name,
        status=status,
        scopes=scopes,
        token_ref=CipherRef("vault:v1:c2VlZA==", "tenant-secrets") if connected else None,
        expires_at=SEEDED_EXPIRES_AT if connected else None,
        last_error=last_error,
        created_by="seeder",
        created_at=SEEDED_CREATED_AT,
        updated_at=SEEDED_CREATED_AT,
        version=1,
    )


def seed_mcp_server(
    *,
    server_id: str,
    workspace_id: str,
    name: str = "notes",
    url: str = "https://mcp.example.test/sse",
    transport: str = "sse",
    status: McpStatus = McpStatus.ACTIVE,
    auth_ref: CipherRef | None = None,
) -> McpServer:
    return McpServer(
        id=server_id,
        workspace_id=workspace_id,
        name=McpServerName(name),
        endpoint=McpEndpoint(url, transport),
        auth_ref=auth_ref,
        status=status,
        created_by="seeder",
        created_at=SEEDED_CREATED_AT,
        updated_at=SEEDED_CREATED_AT,
        version=1,
    )


def build_integrations(
    *,
    catalog: Sequence[ConnectorDescriptor] = (),
    connector: RecordingConnector | None = None,
    redirect_base: str | None = REDIRECT_BASE,
    tools: StubToolDiscovery | None = None,
    limits: Limits | None = None,
) -> IntegrationsStack:
    """The bundle over in-memory fakes.

    Defaults to an EMPTY catalog and no connector provider — production's
    state today, and the one a test needs in order to prove that an
    unconfigured deployment refuses cleanly rather than half-working.
    """
    repository = InMemoryConnectionRepository()
    servers = InMemoryMcpServerRepository()
    cache = DictCache()
    secrets = RecordingSecrets()
    recorder = connector or RecordingConnector()
    providers: dict[str, ConnectorProvider] = (
        {recorder.connector: recorder} if connector is not None else {}
    )
    settings = IntegrationsSettings(oauth_redirect_base_url=redirect_base)
    effective_limits = limits or Limits()
    begin = BeginConnection(repository, providers, cache, settings, effective_limits)
    return IntegrationsStack(
        integrations=IntegrationsUseCases(
            list_connectors=ListConnectors(catalog),
            list_connections=ListConnections(repository),
            start=StartConnection(begin, REDIRECT_URI),
            authorize=AuthorizeConnection(repository, begin, REDIRECT_URI),
            revoke=RevokeConnection(repository),
            complete=CompleteOAuth(repository, providers, cache, secrets),
            list_mcp_servers=ListMcpServers(servers),
            register_mcp_server=RegisterMcpServer(
                servers,
                secrets,
                settings,
                effective_limits,
                frozenset(descriptor.key for descriptor in catalog),
            ),
            disable_mcp_server=DisableMcpServer(servers),
            tools=tools,
        ),
        repository=repository,
        cache=cache,
        connector=recorder,
        secrets=secrets,
        servers=servers,
    )
