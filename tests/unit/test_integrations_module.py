"""Unit tests for the integrations module: value objects (INV-I2), the
``Connection``/``McpServer`` aggregates (INV-I1/I3), and the six use-cases
over local fakes -- none imported from other test modules (the
``test_media_module.py`` precedent).

Unlike files/knowledge/media there is **no** JSON-Schema section here: the
four integrations events are internal, in-memory only (04-event-catalog §5 --
they never cross Redis Streams and have no wire schema in v1), so the events
group asserts the 06 §9 field sets verbatim instead.

The two deliberate alpha bug-fixes are pinned by tests: the OAuth ``state``
is random/single-use/server-bound (CSRF), and a lazy refresh *persists* the
renewed tokens (including a provider-rotated refresh token) and emits
``TokenRefreshed``.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.connector_provider import OAuthTokens
from app.framework.ports.mcp_client import McpTarget, McpTool
from app.framework.settings.settings import IntegrationsSettings, Limits
from app.framework.types import Json
from app.modules.integrations.application.use_cases import (
    BeginConnection,
    CompleteOAuth,
    DisableMcpServer,
    ListMcpServers,
    RefreshConnectionToken,
    RegisterMcpServer,
    RevokeConnection,
    WorkspaceToolCatalog,
)
from app.modules.integrations.domain.entities import Connection, McpServer
from app.modules.integrations.domain.errors import (
    ConnectionStateError,
    InvalidIntegrationInput,
    UnsupportedMcpTransport,
)
from app.modules.integrations.domain.events import (
    ConnectionEstablished,
    ConnectionRevoked,
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
from app.modules.integrations.ports.toolset import ConnectorAccess, ConnectorToolSpec

_KEY = "tenant-secrets"
_BASE = "https://app.example/api/v1/integrations/oauth"
_REDIRECT = f"{_BASE}/callback"


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str = "ws1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _settings(**over: object) -> IntegrationsSettings:
    defaults: dict[str, object] = {"oauth_redirect_base_url": _BASE}
    defaults.update(over)
    return IntegrationsSettings(**defaults)  # type: ignore[arg-type]


def _cipher(plain_access: str = "at-0", plain_refresh: str | None = "rt-0") -> CipherRef:
    payload = json.dumps({"access_token": plain_access, "refresh_token": plain_refresh})
    return CipherRef(f"enc:{payload}", _KEY)


def _connection(
    *,
    conn_id: str = "conn-1",
    workspace_id: str = "ws1",
    connector_key: str = "gmail",
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
    token_ref: CipherRef | None = None,
    expires_in_s: int | None = 3600,
) -> Connection:
    now = utc_now()
    return Connection(
        id=conn_id,
        workspace_id=workspace_id,
        connector_key=ConnectorKey(connector_key),
        display_name=None,
        status=status,
        scopes=("s1",),
        token_ref=token_ref if token_ref is not None else _cipher(),
        expires_at=None if expires_in_s is None else now + timedelta(seconds=expires_in_s),
        last_error=None,
        created_by="u1",
        created_at=now,
        updated_at=now,
        version=1,
    )


def _server(
    *,
    server_id: str = "srv-1",
    workspace_id: str = "ws1",
    name: str = "notes",
    url: str = "https://mcp.example/sse",
    transport: str = "sse",
    auth_ref: CipherRef | None = None,
    status: McpStatus = McpStatus.ACTIVE,
) -> McpServer:
    now = utc_now()
    return McpServer(
        id=server_id,
        workspace_id=workspace_id,
        name=McpServerName(name),
        endpoint=McpEndpoint(url, transport),
        auth_ref=auth_ref,
        status=status,
        created_by="u1",
        created_at=now,
        updated_at=now,
        version=1,
    )


# --------------------------------------------------------------------------- #
# Fakes -- local to this module.                                              #
# --------------------------------------------------------------------------- #
class _FakeConnections:
    """In-memory ``ConnectionRepository``; mirrors the adapter semantics
    pinned in the port docstrings (``update_tokens`` = narrow write applied
    to the row without a version bump)."""

    def __init__(self) -> None:
        self.rows: dict[str, Connection] = {}
        self.get_calls = 0
        self.save_calls: list[str] = []
        self.update_tokens_calls: list[tuple[str, str, str]] = []

    async def get(self, ctx: ExecutionContext, conn_id: str) -> Connection | None:
        self.get_calls += 1
        row = self.rows.get(conn_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, conn: Connection) -> None:
        self.rows[conn.id] = conn

    async def list_connected(self, ctx: ExecutionContext) -> list[Connection]:
        return [
            c
            for c in self.rows.values()
            if c.workspace_id == ctx.workspace_id and c.status is ConnectionStatus.CONNECTED
        ]

    async def update_tokens(
        self, ctx: ExecutionContext, conn_id: str, token_ref: str, key_id: str, expires_at: object
    ) -> None:
        self.update_tokens_calls.append((conn_id, token_ref, key_id))
        row = self.rows[conn_id]
        row.token_ref = CipherRef(token_ref, key_id)
        row.expires_at = expires_at  # type: ignore[assignment]

    async def find_by_connector(
        self, ctx: ExecutionContext, connector_key: str
    ) -> Connection | None:
        for row in self.rows.values():
            if row.workspace_id == ctx.workspace_id and row.connector_key.value == connector_key:
                return row
        return None

    async def save(self, ctx: ExecutionContext, conn: Connection) -> None:
        self.rows[conn.id] = conn
        self.save_calls.append(conn.id)


class _FakeMcpServers:
    def __init__(self) -> None:
        self.rows: dict[str, McpServer] = {}
        self.saves: list[str] = []

    async def add(self, ctx: ExecutionContext, server: McpServer) -> None:
        self.rows[server.id] = server

    async def list_active(self, ctx: ExecutionContext) -> list[McpServer]:
        return [
            s
            for s in self.rows.values()
            if s.workspace_id == ctx.workspace_id and s.status is McpStatus.ACTIVE
        ]

    async def find_by_name(self, ctx: ExecutionContext, name: str) -> McpServer | None:
        for row in self.rows.values():
            if row.workspace_id == ctx.workspace_id and row.name.value == name:
                return row
        return None

    async def get(self, ctx: ExecutionContext, server_id: str) -> McpServer | None:
        row = self.rows.get(server_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def list(self, ctx: ExecutionContext) -> list[McpServer]:
        return sorted(
            (s for s in self.rows.values() if s.workspace_id == ctx.workspace_id),
            key=lambda s: s.id,
            reverse=True,
        )

    async def save(self, ctx: ExecutionContext, server: McpServer) -> None:
        self.saves.append(server.id)
        self.rows[server.id] = server


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _FakeSecrets:
    """Reversible fake Transit: ``encrypt`` records the exact plaintext it was
    handed (so tests can assert raw tokens DID pass through encryption and
    only ciphertext got stored) and prefixes it with ``enc:``."""

    def __init__(self) -> None:
        self.encrypt_calls: list[tuple[str, bytes]] = []

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        self.encrypt_calls.append((key_name, plaintext))
        return f"enc:{plaintext.decode()}"

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        assert ciphertext.startswith("enc:"), "decrypting something never encrypted"
        return ciphertext[len("enc:") :].encode()


class _FakeConnector:
    """Configurable ``ConnectorProvider`` fake."""

    def __init__(
        self,
        connector: str = "gmail",
        *,
        fail_exchange: bool = False,
        fail_refresh: bool = False,
        rotated_refresh: str | None = "rt-2",
        expires_in: int = 3600,
        granted_scopes: tuple[str, ...] = ("s1", "s2"),
    ) -> None:
        self.connector = connector
        self.fail_exchange = fail_exchange
        self.fail_refresh = fail_refresh
        self.rotated_refresh = rotated_refresh
        self.expires_in = expires_in
        self.granted_scopes = granted_scopes
        self.exchange_calls: list[tuple[str, str]] = []
        self.refresh_calls: list[str] = []

    def authorize_url(self, redirect_uri: str, state: str, scopes: object) -> str:
        return f"https://provider.example/authorize?state={state}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        self.exchange_calls.append((code, redirect_uri))
        if self.fail_exchange:
            raise RuntimeError("provider is down")
        return OAuthTokens("at-1", "rt-1", self.expires_in, self.granted_scopes)

    async def refresh(self, refresh_token: str) -> OAuthTokens:
        self.refresh_calls.append(refresh_token)
        if self.fail_refresh:
            raise RuntimeError("provider is down")
        return OAuthTokens("at-2", self.rotated_refresh, self.expires_in, self.granted_scopes)


class _FakeToolset:
    def __init__(self, specs: dict[str, list[ConnectorToolSpec]] | None = None) -> None:
        self.specs = specs or {}
        self.invocations: list[tuple[ConnectorAccess, str, Json]] = []

    def list_tools(self, connector_key: str) -> list[ConnectorToolSpec]:
        return self.specs.get(connector_key, [])

    async def invoke(
        self, ctx: ExecutionContext, *, access: ConnectorAccess, tool_name: str, args: Json
    ) -> Json:
        self.invocations.append((access, tool_name, args))
        return {"ok": tool_name}


class _FakeMcpClient:
    def __init__(
        self,
        tools: dict[str, list[McpTool]] | None = None,
        unreachable: frozenset[str] = frozenset(),
    ) -> None:
        self.tools = tools or {}  # keyed by endpoint_url
        self.unreachable = unreachable
        self.call_tool_calls: list[tuple[McpTarget, str, Json]] = []

    async def list_tools(self, target: McpTarget) -> list[McpTool]:
        if target.endpoint_url in self.unreachable:
            raise RuntimeError("unreachable")
        return self.tools.get(target.endpoint_url, [])

    async def call_tool(self, target: McpTarget, name: str, args: Json) -> Json:
        if target.endpoint_url in self.unreachable:
            raise RuntimeError("unreachable")
        self.call_tool_calls.append((target, name, args))
        return {"mcp": name}


def _begin(
    connections: _FakeConnections | None = None,
    cache: _FakeCache | None = None,
    provider: _FakeConnector | None = None,
    limits: Limits | None = None,
) -> tuple[BeginConnection, _FakeConnections, _FakeCache, _FakeConnector]:
    connections = connections or _FakeConnections()
    cache = cache or _FakeCache()
    provider = provider or _FakeConnector()
    use_case = BeginConnection(
        connections,  # type: ignore[arg-type]
        {provider.connector: provider},
        cache,  # type: ignore[arg-type]
        _settings(),
        limits or Limits(),
    )
    return use_case, connections, cache, provider


def _state_from(cache: _FakeCache) -> str:
    (key,) = cache.store.keys()
    return key.removeprefix("integrations:oauth:state:")


# --------------------------------------------------------------------------- #
# Value objects (INV-I2)                                                      #
# --------------------------------------------------------------------------- #
class TestValueObjects:
    def test_connector_key_normalizes(self) -> None:
        assert ConnectorKey("  GitHub ").value == "github"

    @pytest.mark.parametrize("raw", ["", "a.b", "-bad", "bad!", "x" * 65])
    def test_connector_key_rejects_bad_shapes(self, raw: str) -> None:
        with pytest.raises(InvalidIntegrationInput):
            ConnectorKey(raw)

    def test_mcp_server_name_rejects_dots(self) -> None:
        # No dots: the name is the `<prefix>.<tool>` routing prefix.
        with pytest.raises(InvalidIntegrationInput):
            McpServerName("my.server")

    def test_cipher_ref_rejects_empty_parts(self) -> None:
        with pytest.raises(InvalidIntegrationInput):
            CipherRef("", _KEY)
        with pytest.raises(InvalidIntegrationInput):
            CipherRef("vault:v1:abc", "")

    @pytest.mark.parametrize("transport", ["http", "sse"])
    def test_mcp_endpoint_accepts_remote_transports(self, transport: str) -> None:
        endpoint = McpEndpoint("https://mcp.example", transport)
        assert endpoint.transport == transport

    def test_mcp_endpoint_rejects_stdio_transport(self) -> None:
        with pytest.raises(UnsupportedMcpTransport):
            McpEndpoint("https://mcp.example", "stdio")

    @pytest.mark.parametrize("url", ["", "stdio://local", "file:///tmp/x", "ftp://x"])
    def test_mcp_endpoint_rejects_non_http_urls(self, url: str) -> None:
        with pytest.raises(InvalidIntegrationInput):
            McpEndpoint(url, "http")

    def test_status_enum_values_match_ddl(self) -> None:
        assert {s.value for s in ConnectionStatus} == {"pending", "connected", "revoked", "error"}
        assert {s.value for s in McpStatus} == {"active", "disabled", "error"}


# --------------------------------------------------------------------------- #
# Connection aggregate (INV-I1 / INV-I3)                                      #
# --------------------------------------------------------------------------- #
class TestConnectionAggregate:
    def test_begin_restarts_a_failed_handshake(self) -> None:
        conn = _connection(status=ConnectionStatus.ERROR)
        conn.last_error = "boom"
        conn.begin(("s9",), utc_now())
        assert conn.status is ConnectionStatus.PENDING
        assert conn.scopes == ("s9",)
        assert conn.last_error is None

    def test_begin_on_connected_is_illegal(self) -> None:
        conn = _connection(status=ConnectionStatus.CONNECTED)
        with pytest.raises(ConnectionStateError):
            conn.begin(("s1",), utc_now())

    def test_connect_stores_cipher_ref_only(self) -> None:
        conn = _connection(status=ConnectionStatus.PENDING, token_ref=None, expires_in_s=None)
        conn.token_ref = None
        now = utc_now()
        ref = _cipher("at-9")
        conn.connect(ref, now + timedelta(seconds=60), ("granted",), now)
        assert conn.status is ConnectionStatus.CONNECTED
        assert conn.token_ref is ref  # opaque CipherRef, never raw material
        assert conn.scopes == ("granted",)
        assert conn.last_error is None

    def test_connect_from_revoked_is_illegal(self) -> None:
        conn = _connection(status=ConnectionStatus.REVOKED)
        with pytest.raises(ConnectionStateError):
            conn.connect(_cipher(), utc_now(), ("s1",), utc_now())

    def test_apply_refreshed_tokens_requires_connected(self) -> None:
        conn = _connection(status=ConnectionStatus.PENDING)
        with pytest.raises(ConnectionStateError):
            conn.apply_refreshed_tokens(_cipher(), utc_now(), utc_now())

    def test_revoke_drops_secret_and_is_idempotent(self) -> None:
        conn = _connection()
        conn.revoke(utc_now())
        assert conn.status is ConnectionStatus.REVOKED
        assert conn.token_ref is None and conn.expires_at is None
        stamp = conn.updated_at
        conn.revoke(utc_now())  # second call: nothing changes
        assert conn.updated_at == stamp

    def test_needs_refresh_boundaries(self) -> None:
        now = utc_now()
        fresh = _connection(expires_in_s=3600)
        expired = _connection(expires_in_s=-10)
        unknown = _connection(expires_in_s=None)
        at_skew = _connection()
        at_skew.expires_at = now + timedelta(seconds=60)
        assert fresh.needs_refresh(now, 60) is False
        assert expired.needs_refresh(now, 60) is True
        assert unknown.needs_refresh(now, 60) is True  # fail-closed toward renewal
        assert at_skew.needs_refresh(now, 60) is True  # boundary: <= is inclusive


# --------------------------------------------------------------------------- #
# BeginConnection                                                             #
# --------------------------------------------------------------------------- #
class TestBeginConnection:
    async def test_happy_path_creates_pending_row_and_server_bound_state(self) -> None:
        use_case, _connections, cache, _provider = _begin()
        ctx = _ctx()
        out = await use_case.execute(
            ctx, connector_key="gmail", redirect_uri=_REDIRECT, scopes=("s1",)
        )
        assert out.connection.status is ConnectionStatus.PENDING
        assert out.connection.token_ref is None
        state = _state_from(cache)
        assert out.authorize_url.endswith(f"state={state}")
        payload = json.loads(cache.store[f"integrations:oauth:state:{state}"])
        assert payload == {
            "workspace_id": "ws1",
            "connection_id": out.connection.id,
            "connector_key": "gmail",
            "created_by": "u1",
            "redirect_uri": _REDIRECT,
        }

    async def test_state_is_random_not_identity(self) -> None:
        # The CSRF fix: state is unguessable, never user/workspace identity.
        use_case, _, cache, _ = _begin()
        await use_case.execute(
            _ctx(), connector_key="gmail", redirect_uri=_REDIRECT, scopes=("s1",)
        )
        state = _state_from(cache)
        assert state not in {"u1", "ws1"}
        assert len(state) >= 32

    async def test_unknown_connector_rejected(self) -> None:
        use_case, _, _, _ = _begin()
        with pytest.raises(ValidationError) as exc_info:
            await use_case.execute(
                _ctx(), connector_key="notion", redirect_uri=_REDIRECT, scopes=()
            )
        assert exc_info.value.code == "integrations.connector_unknown"

    async def test_redirect_outside_configured_base_rejected(self) -> None:
        use_case, _, _, _ = _begin()
        with pytest.raises(ValidationError):
            await use_case.execute(
                _ctx(), connector_key="gmail", redirect_uri="https://evil.example/cb", scopes=()
            )

    async def test_connector_cap_enforced_for_new_rows(self) -> None:
        connections = _FakeConnections()
        connections.rows["other"] = _connection(conn_id="other", connector_key="slack")
        use_case, _, _, _ = _begin(connections=connections, limits=Limits(max_connectors=1))
        with pytest.raises(ConflictError) as exc_info:
            await use_case.execute(_ctx(), connector_key="gmail", redirect_uri=_REDIRECT, scopes=())
        assert exc_info.value.code == "integrations.too_many"

    async def test_restart_reuses_existing_non_connected_row(self) -> None:
        connections = _FakeConnections()
        existing = _connection(status=ConnectionStatus.ERROR)
        connections.rows[existing.id] = existing
        use_case, connections, _, _ = _begin(connections=connections)
        out = await use_case.execute(
            _ctx(), connector_key="gmail", redirect_uri=_REDIRECT, scopes=("s2",)
        )
        assert out.connection.id == existing.id
        assert out.connection.status is ConnectionStatus.PENDING
        assert connections.save_calls == [existing.id]

    async def test_reconsent_leaves_connected_row_untouched(self) -> None:
        connections = _FakeConnections()
        live = _connection(status=ConnectionStatus.CONNECTED)
        connections.rows[live.id] = live
        use_case, connections, cache, _ = _begin(connections=connections)
        out = await use_case.execute(
            _ctx(), connector_key="gmail", redirect_uri=_REDIRECT, scopes=("s1",)
        )
        assert out.connection.status is ConnectionStatus.CONNECTED
        assert connections.save_calls == []  # working row untouched
        assert len(cache.store) == 1  # but a fresh state was still issued


# --------------------------------------------------------------------------- #
# CompleteOAuth (public callback -- CSRF closed server-side)                  #
# --------------------------------------------------------------------------- #
class TestCompleteOAuth:
    @staticmethod
    def _wire(
        connections: _FakeConnections,
        cache: _FakeCache,
        provider: _FakeConnector,
        secrets: _FakeSecrets,
    ) -> CompleteOAuth:
        return CompleteOAuth(
            connections,  # type: ignore[arg-type]
            {provider.connector: provider},
            cache,  # type: ignore[arg-type]
            secrets,  # type: ignore[arg-type]
        )

    async def _handshake(
        self, provider: _FakeConnector | None = None
    ) -> tuple[CompleteOAuth, _FakeConnections, _FakeCache, _FakeConnector, _FakeSecrets, str]:
        begin, connections, cache, prov = _begin(provider=provider)
        await begin.execute(_ctx(), connector_key="gmail", redirect_uri=_REDIRECT, scopes=("s1",))
        secrets = _FakeSecrets()
        complete = self._wire(connections, cache, prov, secrets)
        return complete, connections, cache, prov, secrets, _state_from(cache)

    async def test_happy_path_encrypts_tokens_and_connects(self) -> None:
        complete, _connections, cache, provider, secrets, state = await self._handshake()
        conn, events = await complete.execute(state=state, code="code-1")

        assert provider.exchange_calls == [("code-1", _REDIRECT)]  # stored redirect, not caller's
        assert conn.status is ConnectionStatus.CONNECTED
        # Raw tokens passed through Transit exactly once; only ciphertext stored.
        (key_name, plaintext) = secrets.encrypt_calls[0]
        assert key_name == _KEY
        assert json.loads(plaintext) == {"access_token": "at-1", "refresh_token": "rt-1"}
        assert conn.token_ref is not None and conn.token_ref.ciphertext.startswith("enc:")
        assert conn.scopes == ("s1", "s2")  # granted scopes win over requested
        assert conn.expires_at is not None
        assert cache.store == {}  # state consumed
        (event,) = events
        assert isinstance(event, ConnectionEstablished)
        assert (event.connection_id, event.workspace_id, event.connector_key) == (
            conn.id,
            "ws1",
            "gmail",
        )

    async def test_workspace_identity_comes_from_state_binding(self) -> None:
        complete, _, _, _, _, state = await self._handshake()
        conn, (event,) = await complete.execute(state=state, code="c")
        assert conn.workspace_id == "ws1"
        assert isinstance(event, ConnectionEstablished) and event.workspace_id == "ws1"

    async def test_missing_state_fails_closed_without_db_touch(self) -> None:
        connections = _FakeConnections()
        complete = self._wire(connections, _FakeCache(), _FakeConnector(), _FakeSecrets())
        with pytest.raises(ValidationError):
            await complete.execute(state="forged", code="c")
        assert connections.get_calls == 0

    async def test_state_is_single_use(self) -> None:
        complete, _, _, _, _, state = await self._handshake()
        await complete.execute(state=state, code="c")
        with pytest.raises(ValidationError):
            await complete.execute(state=state, code="c")  # replay loses

    async def test_exchange_failure_marks_error_and_maps_to_502(self) -> None:
        complete, connections, _, _, _, state = await self._handshake(
            provider=_FakeConnector(fail_exchange=True)
        )
        with pytest.raises(AppError) as exc_info:
            await complete.execute(state=state, code="bad")
        assert exc_info.value.code == "integrations.oauth_failed"
        assert exc_info.value.status == 502
        (row,) = connections.rows.values()
        assert row.status is ConnectionStatus.ERROR
        assert row.last_error is not None

    async def test_vanished_row_is_not_found(self) -> None:
        complete, connections, _, _, _, state = await self._handshake()
        connections.rows.clear()
        with pytest.raises(NotFoundError):
            await complete.execute(state=state, code="c")


# --------------------------------------------------------------------------- #
# RefreshConnectionToken (INV-I3 lazy renewal + the alpha persistence fix)    #
# --------------------------------------------------------------------------- #
class TestRefreshConnectionToken:
    @staticmethod
    def _wire(
        connections: _FakeConnections,
        provider: _FakeConnector,
        secrets: _FakeSecrets | None = None,
    ) -> RefreshConnectionToken:
        return RefreshConnectionToken(
            connections,  # type: ignore[arg-type]
            {provider.connector: provider},
            secrets or _FakeSecrets(),  # type: ignore[arg-type]
            _settings(),
        )

    async def test_fresh_token_is_a_no_op(self) -> None:
        connections, provider = _FakeConnections(), _FakeConnector()
        conn = _connection(expires_in_s=3600)
        connections.rows[conn.id] = conn
        refresher = self._wire(connections, provider)
        out, events = await refresher.ensure_fresh(_ctx(), conn)
        assert out is conn and events == ()
        assert provider.refresh_calls == [] and connections.update_tokens_calls == []

    async def test_expired_token_is_renewed_and_persisted(self) -> None:
        # The alpha bug fix: the renewal is WRITTEN BACK and announced.
        connections, provider = _FakeConnections(), _FakeConnector(rotated_refresh="rt-2")
        secrets = _FakeSecrets()
        conn = _connection(expires_in_s=-5, token_ref=_cipher("at-old", "rt-1"))
        connections.rows[conn.id] = conn
        refresher = self._wire(connections, provider, secrets)
        out, (event,) = await refresher.ensure_fresh(_ctx(), conn)

        assert provider.refresh_calls == ["rt-1"]
        assert len(connections.update_tokens_calls) == 1  # persisted, not just in-memory
        (_, plaintext) = secrets.encrypt_calls[0]
        assert json.loads(plaintext) == {"access_token": "at-2", "refresh_token": "rt-2"}
        assert isinstance(event, TokenRefreshed)
        assert event.connection_id == conn.id and event.expires_at == out.expires_at

    async def test_unrotated_refresh_token_is_kept(self) -> None:
        connections, provider = _FakeConnections(), _FakeConnector(rotated_refresh=None)
        secrets = _FakeSecrets()
        conn = _connection(expires_in_s=-5, token_ref=_cipher("at-old", "rt-1"))
        connections.rows[conn.id] = conn
        refresher = self._wire(connections, provider, secrets)
        await refresher.ensure_fresh(_ctx(), conn)
        (_, plaintext) = secrets.encrypt_calls[0]
        assert json.loads(plaintext)["refresh_token"] == "rt-1"  # never dropped

    async def test_missing_refresh_token_fails_as_not_connected(self) -> None:
        connections, provider = _FakeConnections(), _FakeConnector()
        conn = _connection(expires_in_s=-5, token_ref=_cipher("at-old", None))
        connections.rows[conn.id] = conn
        refresher = self._wire(connections, provider)
        with pytest.raises(ConflictError) as exc_info:
            await refresher.ensure_fresh(_ctx(), conn)
        assert exc_info.value.code == "integrations.not_connected"
        assert conn.status is ConnectionStatus.ERROR
        assert connections.save_calls == [conn.id]

    async def test_provider_failure_maps_to_502_and_marks_error(self) -> None:
        connections, provider = _FakeConnections(), _FakeConnector(fail_refresh=True)
        conn = _connection(expires_in_s=-5)
        connections.rows[conn.id] = conn
        refresher = self._wire(connections, provider)
        with pytest.raises(AppError) as exc_info:
            await refresher.ensure_fresh(_ctx(), conn)
        assert exc_info.value.code == "integrations.oauth_failed"
        assert exc_info.value.status == 502
        assert conn.status is ConnectionStatus.ERROR

    async def test_connection_without_material_is_not_connected(self) -> None:
        connections, provider = _FakeConnections(), _FakeConnector()
        conn = _connection(status=ConnectionStatus.PENDING, expires_in_s=None)
        conn.token_ref = None
        refresher = self._wire(connections, provider)
        with pytest.raises(ConflictError) as exc_info:
            await refresher.ensure_fresh(_ctx(), conn)
        assert exc_info.value.code == "integrations.not_connected"


# --------------------------------------------------------------------------- #
# RevokeConnection                                                            #
# --------------------------------------------------------------------------- #
class TestRevokeConnection:
    async def test_revoke_clears_secret_and_emits_once(self) -> None:
        connections = _FakeConnections()
        conn = _connection()
        connections.rows[conn.id] = conn
        use_case = RevokeConnection(connections)  # type: ignore[arg-type]
        out, (event,) = await use_case.execute(_ctx(), connection_id=conn.id)
        assert out.status is ConnectionStatus.REVOKED and out.token_ref is None
        assert isinstance(event, ConnectionRevoked) and event.connection_id == conn.id

        _again, events = await use_case.execute(_ctx(), connection_id=conn.id)
        assert events == ()  # idempotent: no second event

    async def test_missing_connection_is_not_found(self) -> None:
        use_case = RevokeConnection(_FakeConnections())  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            await use_case.execute(_ctx(), connection_id="nope")


# --------------------------------------------------------------------------- #
# RegisterMcpServer (INV-I2)                                                  #
# --------------------------------------------------------------------------- #
class TestRegisterMcpServer:
    @staticmethod
    def _wire(
        servers: _FakeMcpServers | None = None,
        secrets: _FakeSecrets | None = None,
        limits: Limits | None = None,
        connector_keys: frozenset[str] = frozenset({"gmail"}),
    ) -> tuple[RegisterMcpServer, _FakeMcpServers, _FakeSecrets]:
        servers = servers or _FakeMcpServers()
        secrets = secrets or _FakeSecrets()
        use_case = RegisterMcpServer(
            servers,  # type: ignore[arg-type]
            secrets,  # type: ignore[arg-type]
            _settings(),
            limits or Limits(),
            connector_keys,
        )
        return use_case, servers, secrets

    async def test_happy_path_registers_active_server(self) -> None:
        use_case, servers, _ = self._wire()
        server, (event,) = await use_case.execute(
            _ctx(), name="notes", endpoint_url="https://mcp.example", transport="http"
        )
        assert server.status is McpStatus.ACTIVE and server.auth_ref is None
        assert servers.rows[server.id] is server
        assert isinstance(event, McpServerRegistered)
        assert (event.server_id, event.workspace_id) == (server.id, "ws1")

    async def test_auth_material_is_encrypted(self) -> None:
        use_case, _, secrets = self._wire()
        server, _ = await use_case.execute(
            _ctx(),
            name="notes",
            endpoint_url="https://mcp.example",
            transport="sse",
            auth={"bearer": "raw-secret"},
        )
        (key_name, plaintext) = secrets.encrypt_calls[0]
        assert key_name == _KEY and json.loads(plaintext) == {"bearer": "raw-secret"}
        assert server.auth_ref is not None and server.auth_ref.ciphertext.startswith("enc:")

    async def test_stdio_transport_rejected_with_catalog_code(self) -> None:
        use_case, _, _ = self._wire()
        with pytest.raises(ValidationError) as exc_info:
            await use_case.execute(
                _ctx(), name="notes", endpoint_url="https://mcp.example", transport="stdio"
            )
        assert exc_info.value.code == "integrations.mcp_transport_unsupported"

    async def test_non_http_url_rejected(self) -> None:
        use_case, _, _ = self._wire()
        with pytest.raises(ValidationError):
            await use_case.execute(
                _ctx(), name="notes", endpoint_url="stdio://local", transport="http"
            )

    async def test_duplicate_name_conflicts(self) -> None:
        use_case, servers, _ = self._wire()
        existing = _server(name="notes")
        servers.rows[existing.id] = existing
        with pytest.raises(ConflictError):
            await use_case.execute(_ctx(), name="notes", endpoint_url="https://x.example")

    async def test_name_shadowing_a_connector_key_rejected(self) -> None:
        use_case, _, _ = self._wire(connector_keys=frozenset({"gmail"}))
        with pytest.raises(ValidationError):
            await use_case.execute(_ctx(), name="gmail", endpoint_url="https://x.example")

    async def test_server_cap_enforced(self) -> None:
        use_case, servers, _ = self._wire(limits=Limits(max_mcp_servers=1))
        existing = _server(name="other")
        servers.rows[existing.id] = existing
        with pytest.raises(ConflictError) as exc_info:
            await use_case.execute(_ctx(), name="notes", endpoint_url="https://x.example")
        assert exc_info.value.code == "integrations.too_many"

    @pytest.mark.parametrize("status", [McpStatus.DISABLED, McpStatus.ERROR])
    async def test_a_non_active_name_is_revived_in_place(self, status: McpStatus) -> None:
        """Both recoverable states re-register onto the SAME row, so a
        DELETE (or a server that errored out) never burns its name."""
        use_case, servers, _ = self._wire()
        existing = _server(name="notes", url="https://old.example/sse", status=status)
        servers.rows[existing.id] = existing
        server, events = await use_case.execute(
            _ctx(), name="notes", endpoint_url="https://new.example/sse", transport="sse"
        )
        assert server.id == existing.id
        assert server.status is McpStatus.ACTIVE
        assert server.endpoint.url == "https://new.example/sse"
        assert len(servers.rows) == 1 and servers.saves == [existing.id]
        assert isinstance(events[0], McpServerRegistered)

    async def test_reviving_ignores_the_cap(self) -> None:
        use_case, servers, _ = self._wire(limits=Limits(max_mcp_servers=1))
        existing = _server(name="notes", status=McpStatus.DISABLED)
        servers.rows[existing.id] = existing
        server, _events = await use_case.execute(
            _ctx(), name="notes", endpoint_url="https://x.example"
        )
        assert server.id == existing.id

    async def test_reviving_replaces_the_auth_material(self) -> None:
        use_case, servers, secrets = self._wire()
        existing = _server(name="notes", status=McpStatus.DISABLED, auth_ref=CipherRef("old", "k"))
        servers.rows[existing.id] = existing
        server, _events = await use_case.execute(
            _ctx(), name="notes", endpoint_url="https://x.example", auth={"header": "new"}
        )
        assert server.auth_ref is not None and server.auth_ref.ciphertext != "old"
        assert secrets.encrypt_calls == [("tenant-secrets", b'{"header": "new"}')]


# --------------------------------------------------------------------------- #
# ListMcpServers / DisableMcpServer (6.1-و-4-3)                               #
# --------------------------------------------------------------------------- #
class TestMcpServerLifecycle:
    async def test_list_returns_every_status_newest_first(self) -> None:
        servers = _FakeMcpServers()
        for row in (
            _server(server_id="srv-1", name="alpha"),
            _server(server_id="srv-2", name="beta", status=McpStatus.DISABLED),
            _server(server_id="srv-3", name="other", workspace_id="ws2"),
        ):
            servers.rows[row.id] = row
        listed = await ListMcpServers(servers).execute(_ctx())  # type: ignore[arg-type]
        assert [s.id for s in listed] == ["srv-2", "srv-1"]

    async def test_disable_flips_the_status_and_keeps_the_auth_ref(self) -> None:
        servers = _FakeMcpServers()
        row = _server(auth_ref=CipherRef("c", "tenant-secrets"))
        servers.rows[row.id] = row
        disabled = await DisableMcpServer(servers).execute(  # type: ignore[arg-type]
            _ctx(), server_id=row.id
        )
        assert disabled.status is McpStatus.DISABLED
        assert disabled.auth_ref is not None  # kept — `reactivate` needs it
        assert servers.saves == [row.id]

    async def test_disable_is_idempotent_without_a_second_write(self) -> None:
        servers = _FakeMcpServers()
        row = _server(status=McpStatus.DISABLED)
        servers.rows[row.id] = row
        await DisableMcpServer(servers).execute(_ctx(), server_id=row.id)  # type: ignore[arg-type]
        assert servers.saves == []

    async def test_disable_refuses_an_unknown_or_foreign_id(self) -> None:
        servers = _FakeMcpServers()
        foreign = _server(server_id="srv-9", workspace_id="ws2")
        servers.rows[foreign.id] = foreign
        use_case = DisableMcpServer(servers)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            await use_case.execute(_ctx(), server_id="missing")
        with pytest.raises(NotFoundError):
            await use_case.execute(_ctx(), server_id="srv-9")

    def test_reactivating_an_active_server_is_an_illegal_transition(self) -> None:
        """The entity keeps the rule even though `RegisterMcpServer` refuses
        first: a duplicate active name must stay a 409, never a silent
        re-initialisation of a working server."""
        row = _server()
        with pytest.raises(ConnectionStateError):
            row.reactivate(McpEndpoint("https://x.example", "http"), None, utc_now())


# --------------------------------------------------------------------------- #
# WorkspaceToolCatalog (ToolCatalog inbound -- INV-I4)                        #
# --------------------------------------------------------------------------- #
class TestWorkspaceToolCatalog:
    @staticmethod
    def _wire(
        *,
        connections: _FakeConnections | None = None,
        servers: _FakeMcpServers | None = None,
        toolset: _FakeToolset | None = None,
        mcp_client: _FakeMcpClient | None = None,
        provider: _FakeConnector | None = None,
        limits: Limits | None = None,
    ) -> tuple[
        WorkspaceToolCatalog, _FakeConnections, _FakeMcpServers, _FakeToolset, _FakeMcpClient
    ]:
        connections = connections or _FakeConnections()
        servers = servers or _FakeMcpServers()
        toolset = toolset or _FakeToolset()
        mcp_client = mcp_client or _FakeMcpClient()
        provider = provider or _FakeConnector()
        secrets = _FakeSecrets()
        refresher = RefreshConnectionToken(
            connections,  # type: ignore[arg-type]
            {provider.connector: provider},
            secrets,  # type: ignore[arg-type]
            _settings(),
        )
        catalog = WorkspaceToolCatalog(
            connections,  # type: ignore[arg-type]
            servers,  # type: ignore[arg-type]
            toolset,  # type: ignore[arg-type]
            mcp_client,  # type: ignore[arg-type]
            secrets,  # type: ignore[arg-type]
            refresher,
            limits or Limits(),
        )
        return catalog, connections, servers, toolset, mcp_client

    @staticmethod
    def _gmail_specs() -> dict[str, list[ConnectorToolSpec]]:
        return {
            "gmail": [
                ConnectorToolSpec("read", "Read mail", {"type": "object"}),
                ConnectorToolSpec("send", "Send mail", {"type": "object"}),
            ]
        }

    async def test_list_tools_merges_connectors_then_mcp(self) -> None:
        connections = _FakeConnections()
        conn = _connection()
        connections.rows[conn.id] = conn
        servers = _FakeMcpServers()
        srv = _server(name="notes", url="https://mcp.example/sse")
        servers.rows[srv.id] = srv
        mcp_client = _FakeMcpClient(
            tools={"https://mcp.example/sse": [McpTool("search", "Search notes", {})]}
        )
        catalog, *_ = self._wire(
            connections=connections,
            servers=servers,
            toolset=_FakeToolset(self._gmail_specs()),
            mcp_client=mcp_client,
        )
        tools = await catalog.list_tools(_ctx())
        assert [t.name for t in tools] == ["gmail.read", "gmail.send", "notes.search"]
        assert tools[0].source == f"connector:{conn.id}"
        assert tools[2].source == "mcp:notes"
        assert tools[0].parameters == {"type": "object"}

    async def test_list_tools_skips_unreachable_mcp_servers(self) -> None:
        servers = _FakeMcpServers()
        dead = _server(server_id="dead", name="dead", url="https://dead.example")
        live = _server(server_id="live", name="live", url="https://live.example")
        servers.rows.update({dead.id: dead, live.id: live})
        mcp_client = _FakeMcpClient(
            tools={"https://live.example": [McpTool("ping", "", {})]},
            unreachable=frozenset({"https://dead.example"}),
        )
        catalog, *_ = self._wire(servers=servers, mcp_client=mcp_client)
        tools = await catalog.list_tools(_ctx())
        assert [t.name for t in tools] == ["live.ping"]  # dead server skipped, catalog survives

    async def test_list_tools_caps_at_max_discovered_tools(self) -> None:
        connections = _FakeConnections()
        conn = _connection()
        connections.rows[conn.id] = conn
        catalog, *_ = self._wire(
            connections=connections,
            toolset=_FakeToolset(self._gmail_specs()),
            limits=Limits(max_discovered_tools=1),
        )
        tools = await catalog.list_tools(_ctx())
        assert [t.name for t in tools] == ["gmail.read"]

    async def test_list_tools_empty_workspace(self) -> None:
        catalog, *_ = self._wire()
        assert await catalog.list_tools(_ctx()) == []

    async def test_invoke_connector_tool_passes_resolved_access(self) -> None:
        connections = _FakeConnections()
        conn = _connection(token_ref=_cipher("at-live", "rt-1"), expires_in_s=3600)
        connections.rows[conn.id] = conn
        toolset = _FakeToolset(self._gmail_specs())
        catalog, _, _, toolset, _ = self._wire(connections=connections, toolset=toolset)
        result = await catalog.invoke_tool(_ctx(), "gmail.send", {"to": "x@y"})
        assert result == {"ok": "send"}
        (invocation,) = toolset.invocations  # exactly one call went through
        access, tool_name, args = invocation
        assert access.connector_key == "gmail" and access.access_token == "at-live"
        assert access.scopes == conn.scopes
        assert tool_name == "send" and args == {"to": "x@y"}

    async def test_invoke_refreshes_expired_token_before_use(self) -> None:
        connections = _FakeConnections()
        conn = _connection(token_ref=_cipher("at-old", "rt-1"), expires_in_s=-5)
        connections.rows[conn.id] = conn
        provider = _FakeConnector()
        toolset = _FakeToolset(self._gmail_specs())
        catalog, connections, _, toolset, _ = self._wire(
            connections=connections, toolset=toolset, provider=provider
        )
        await catalog.invoke_tool(_ctx(), "gmail.read", {})
        assert provider.refresh_calls == ["rt-1"]  # INV-I3: renewed before use
        assert len(connections.update_tokens_calls) == 1  # and persisted
        access, _, _ = toolset.invocations[0]
        assert access.access_token == "at-2"  # the renewed token is what got used

    async def test_invoke_non_connected_connector_conflicts(self) -> None:
        connections = _FakeConnections()
        conn = _connection(status=ConnectionStatus.PENDING)
        connections.rows[conn.id] = conn
        catalog, *_ = self._wire(connections=connections)
        with pytest.raises(ConflictError) as exc_info:
            await catalog.invoke_tool(_ctx(), "gmail.read", {})
        assert exc_info.value.code == "integrations.not_connected"

    async def test_invoke_rejects_unprefixed_names(self) -> None:
        catalog, *_ = self._wire()
        with pytest.raises(ValidationError):
            await catalog.invoke_tool(_ctx(), "noprefix", {})

    async def test_invoke_unknown_prefix_is_not_found(self) -> None:
        catalog, *_ = self._wire()
        with pytest.raises(NotFoundError):
            await catalog.invoke_tool(_ctx(), "ghost.tool", {})

    async def test_invoke_mcp_tool_builds_target_with_decrypted_auth(self) -> None:
        servers = _FakeMcpServers()
        auth_ref = CipherRef(f"enc:{json.dumps({'bearer': 'tok'})}", _KEY)
        srv = _server(name="notes", url="https://mcp.example/sse", auth_ref=auth_ref)
        servers.rows[srv.id] = srv
        mcp_client = _FakeMcpClient()
        catalog, *_ = self._wire(servers=servers, mcp_client=mcp_client)
        result = await catalog.invoke_tool(_ctx(), "notes.search", {"q": "x"})
        assert result == {"mcp": "search"}
        (target, name, args) = mcp_client.call_tool_calls[0]
        assert target.endpoint_url == "https://mcp.example/sse" and target.transport == "sse"
        assert target.auth == {"bearer": "tok"}  # decrypted transiently for the call
        assert (name, args) == ("search", {"q": "x"})

    async def test_invoke_mcp_without_auth_passes_none(self) -> None:
        servers = _FakeMcpServers()
        srv = _server(name="notes", url="https://mcp.example/sse", auth_ref=None)
        servers.rows[srv.id] = srv
        mcp_client = _FakeMcpClient()
        catalog, *_ = self._wire(servers=servers, mcp_client=mcp_client)
        await catalog.invoke_tool(_ctx(), "notes.search", {})
        assert mcp_client.call_tool_calls[0][0].auth is None

    async def test_invoke_unreachable_mcp_maps_to_502(self) -> None:
        servers = _FakeMcpServers()
        srv = _server(name="notes", url="https://dead.example")
        servers.rows[srv.id] = srv
        catalog, *_ = self._wire(
            servers=servers,
            mcp_client=_FakeMcpClient(unreachable=frozenset({"https://dead.example"})),
        )
        with pytest.raises(AppError) as exc_info:
            await catalog.invoke_tool(_ctx(), "notes.search", {})
        assert exc_info.value.code == "integrations.mcp_unreachable"
        assert exc_info.value.status == 502

    async def test_invoke_disabled_mcp_server_conflicts(self) -> None:
        servers = _FakeMcpServers()
        srv = _server(name="notes", status=McpStatus.DISABLED)
        servers.rows[srv.id] = srv
        catalog, *_ = self._wire(servers=servers)
        with pytest.raises(ConflictError) as exc_info:
            await catalog.invoke_tool(_ctx(), "notes.search", {})
        assert exc_info.value.code == "integrations.not_connected"

    async def test_connector_wins_prefix_lookup_over_mcp(self) -> None:
        connections = _FakeConnections()
        conn = _connection(connector_key="dup")
        connections.rows[conn.id] = conn
        servers = _FakeMcpServers()
        srv = _server(name="dup", url="https://mcp.example")
        servers.rows[srv.id] = srv
        toolset = _FakeToolset({"dup": [ConnectorToolSpec("t", "", {})]})
        mcp_client = _FakeMcpClient()
        provider = _FakeConnector(connector="dup")
        catalog, *_ = self._wire(
            connections=connections,
            servers=servers,
            toolset=toolset,
            mcp_client=mcp_client,
            provider=provider,
        )
        result = await catalog.invoke_tool(_ctx(), "dup.t", {})
        assert result == {"ok": "t"}  # routed to the connector
        assert mcp_client.call_tool_calls == []


# --------------------------------------------------------------------------- #
# Events -- 06 §9 field sets verbatim (internal, no wire schema by design)    #
# --------------------------------------------------------------------------- #
class TestEvents:
    def test_event_field_sets_match_06_section_9(self) -> None:
        fields = {
            ConnectionEstablished: {
                "connection_id",
                "workspace_id",
                "connector_key",
                "occurred_at",
            },
            ConnectionRevoked: {"connection_id", "occurred_at"},
            TokenRefreshed: {"connection_id", "expires_at", "occurred_at"},
            McpServerRegistered: {"server_id", "workspace_id", "occurred_at"},
        }
        for event_type, expected in fields.items():
            actual = {f.name for f in dataclasses.fields(event_type)}
            assert actual == expected, event_type.__name__

    def test_events_are_frozen(self) -> None:
        event = ConnectionRevoked("c1", utc_now())
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.connection_id = "c2"  # type: ignore[misc]


def test_new_uuid7_used_for_identifiers_smoke() -> None:
    """Guard the identity doctrine transitively: ids minted in the app layer
    are framework UUIDv7 strings (DD-02); a quick shape smoke keeps the
    helper wired."""
    value = new_uuid7()
    assert isinstance(value, str) and len(value) == 36
