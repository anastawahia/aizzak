"""Live-Postgres tests for ``SqlConnectionRepository`` +
``SqlMcpServerRepository`` + RLS (09-testing-strategy §3).

Runs against a real, local PostgreSQL 16 (no Docker/testcontainers -- see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. The module-specific behaviour under test beyond the standard
pattern: ``update_tokens`` is a NARROW hot-path write (INV-I3 lazy refresh,
last-writer-wins) -- it must persist fresh token material WITHOUT a version
predicate or bump, and must still work when the caller's in-memory aggregate
is stale (exactly the concurrent-refresh scenario the port docstring
protects), while the trigger-owned ``updated_at`` still advances. ``save``
by contrast is the full optimistic-lock write.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.modules.integrations.adapters.sql_repository import (
    SqlConnectionRepository,
    SqlMcpServerRepository,
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
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


def _connection(
    *,
    workspace_id: str,
    connector_key: str = "github",
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
    scopes: tuple[str, ...] = ("repo", "read:user"),
    token_ref: CipherRef | None = None,
    expires_at: datetime | None = None,
) -> Connection:
    now = utc_now()
    return Connection(
        id=new_uuid7(),
        workspace_id=workspace_id,
        connector_key=ConnectorKey(connector_key),
        display_name="GitHub",
        status=status,
        scopes=scopes,
        token_ref=token_ref
        if token_ref is not None
        else CipherRef(ciphertext="vault:v1:abc", key_name="tenant-secrets"),
        expires_at=expires_at if expires_at is not None else now + timedelta(hours=1),
        last_error=None,
        created_by=new_uuid7(),
        created_at=now,
        updated_at=now,
        version=1,
    )


def _mcp_server(*, workspace_id: str, name: str = "docs-mcp") -> McpServer:
    now = utc_now()
    return McpServer(
        id=new_uuid7(),
        workspace_id=workspace_id,
        name=McpServerName(name),
        endpoint=McpEndpoint(url="https://mcp.example.com/sse", transport="sse"),
        auth_ref=CipherRef(ciphertext="vault:v1:mcp", key_name="tenant-secrets"),
        status=McpStatus.ACTIVE,
        created_by=new_uuid7(),
        created_at=now,
        updated_at=now,
        version=1,
    )


async def _connection_row_as_owner(
    owner_dsn: str, workspace_id: str, conn_id: str
) -> RowMapping | None:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text(
                    "SELECT token_ref, key_id, expires_at, version, updated_at"
                    " FROM integrations.connections WHERE id = :id"
                ),
                {"id": conn_id},
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1)-(2) connection round-trip                                               #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips_the_connection(
    repo_connections: SqlConnectionRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conn = _connection(workspace_id=ws)

    await repo_connections.add(ctx, conn)
    loaded = await repo_connections.get(ctx, conn.id)

    assert loaded is not None
    assert loaded.id == conn.id
    assert loaded.workspace_id == ws
    assert loaded.connector_key == ConnectorKey("github")
    assert loaded.status is ConnectionStatus.CONNECTED
    assert loaded.scopes == ("repo", "read:user")
    assert loaded.token_ref == CipherRef(ciphertext="vault:v1:abc", key_name="tenant-secrets")
    assert loaded.expires_at == conn.expires_at
    assert loaded.version == 1


async def test_get_missing_connection_returns_none(
    repo_connections: SqlConnectionRepository,
) -> None:
    assert await repo_connections.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (3)-(4) save: optimistic lock + write-back                                  #
# --------------------------------------------------------------------------- #
async def test_save_advances_version_and_writes_back(
    repo_connections: SqlConnectionRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conn = _connection(workspace_id=ws)
    await repo_connections.add(ctx, conn)

    conn.revoke(utc_now())
    await repo_connections.save(ctx, conn)

    assert conn.version == 2  # written back onto the aggregate
    row = await _connection_row_as_owner(live_db.owner, ws, conn.id)
    assert row is not None and row["version"] == 2
    reloaded = await repo_connections.get(ctx, conn.id)
    assert reloaded is not None and reloaded.status is ConnectionStatus.REVOKED


async def test_stale_save_raises_conflict_and_leaves_row_unchanged(
    repo_connections: SqlConnectionRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conn = _connection(workspace_id=ws)
    await repo_connections.add(ctx, conn)
    fresh = await repo_connections.get(ctx, conn.id)
    stale = await repo_connections.get(ctx, conn.id)
    assert fresh is not None and stale is not None

    fresh.revoke(utc_now())
    await repo_connections.save(ctx, fresh)  # bumps to version 2

    stale.revoke(utc_now())
    with pytest.raises(ConflictError):
        await repo_connections.save(ctx, stale)  # still claims version 1

    reloaded = await repo_connections.get(ctx, conn.id)
    assert reloaded is not None and reloaded.version == 2


# --------------------------------------------------------------------------- #
# (5) update_tokens: THE narrow lazy-refresh write (INV-I3)                   #
# --------------------------------------------------------------------------- #
async def test_update_tokens_is_a_narrow_write_that_ignores_staleness(
    repo_connections: SqlConnectionRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conn = _connection(workspace_id=ws)
    await repo_connections.add(ctx, conn)
    before = await _connection_row_as_owner(live_db.owner, ws, conn.id)
    assert before is not None
    new_expiry = utc_now() + timedelta(hours=6)

    # Deliberately NOT re-reading the aggregate first: a concurrent lazy
    # refresh must not need (or fight over) the optimistic version.
    await repo_connections.update_tokens(
        ctx, conn.id, token_ref="vault:v1:rotated", key_id="tenant-secrets", expires_at=new_expiry
    )

    after = await _connection_row_as_owner(live_db.owner, ws, conn.id)
    assert after is not None
    assert after["token_ref"] == "vault:v1:rotated"
    assert after["expires_at"] == new_expiry
    assert after["version"] == before["version"]  # NO version bump (last-writer-wins)
    assert after["updated_at"] > before["updated_at"]  # trigger-owned column still moves


# --------------------------------------------------------------------------- #
# (6) find_by_connector + (7) mcp server repo                                 #
# --------------------------------------------------------------------------- #
async def test_find_by_connector_returns_the_workspace_row(
    repo_connections: SqlConnectionRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conn = _connection(workspace_id=ws, connector_key="slack")
    await repo_connections.add(ctx, conn)

    found = await repo_connections.find_by_connector(ctx, "slack")
    assert found is not None and found.id == conn.id
    assert await repo_connections.find_by_connector(ctx, "github") is None


async def test_mcp_server_round_trip_and_active_filter(
    repo_mcp_servers: SqlMcpServerRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    server = _mcp_server(workspace_id=ws, name="docs-mcp")
    await repo_mcp_servers.add(ctx, server)

    found = await repo_mcp_servers.find_by_name(ctx, "docs-mcp")
    assert found is not None
    assert found.id == server.id
    assert found.name == McpServerName("docs-mcp")
    assert found.endpoint == McpEndpoint(url="https://mcp.example.com/sse", transport="sse")
    assert found.auth_ref == CipherRef(ciphertext="vault:v1:mcp", key_name="tenant-secrets")
    assert found.status is McpStatus.ACTIVE

    active = await repo_mcp_servers.list_active(ctx)
    assert [s.id for s in active] == [server.id]


# --------------------------------------------------------------------------- #
# (8)-(10) RLS: no context / empty-string GUC / tenant isolation              #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_sees_zero_rows(
    repo_connections: SqlConnectionRepository,
    repo_mcp_servers: SqlMcpServerRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    await repo_connections.add(ctx, _connection(workspace_id=ws))
    await repo_mcp_servers.add(ctx, _mcp_server(workspace_id=ws))

    async with sessionmaker_app() as session:  # no GUC ever set
        for table in ("integrations.connections", "integrations.mcp_servers"):
            result = await session.execute(text(f"SELECT count(*) AS n FROM {table}"))
            assert result.scalar_one() == 0


async def test_empty_string_guc_sees_zero_rows_without_error(
    repo_connections: SqlConnectionRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ctx = _ctx(new_uuid7())
    await repo_connections.add(ctx, _connection(workspace_id=ctx.workspace_id))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        result = await session.execute(text("SELECT count(*) AS n FROM integrations.connections"))
        assert result.scalar_one() == 0


async def test_two_tenant_isolation_on_connections(
    repo_connections: SqlConnectionRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a, ctx_b = _ctx(ws_a), _ctx(ws_b)
    conn_a = _connection(workspace_id=ws_a, connector_key="github")
    await repo_connections.add(ctx_a, conn_a)
    await repo_connections.add(ctx_b, _connection(workspace_id=ws_b, connector_key="slack"))

    assert await repo_connections.get(ctx_b, conn_a.id) is None
    assert await repo_connections.find_by_connector(ctx_b, "github") is None
    assert {c.workspace_id for c in await repo_connections.list_connected(ctx_a)} == {ws_a}


# --------------------------------------------------------------------------- #
# (11)-(12) forged cross-tenant writes -> RLS WITH CHECK rejects              #
# --------------------------------------------------------------------------- #
async def test_forged_cross_tenant_connection_add_is_rejected(
    repo_connections: SqlConnectionRepository,
) -> None:
    ws_victim, ws_attacker = new_uuid7(), new_uuid7()
    forged = _connection(workspace_id=ws_victim)

    with pytest.raises(AppError) as excinfo:
        await repo_connections.add(_ctx(ws_attacker), forged)

    assert not isinstance(excinfo.value, ConflictError)
    assert excinfo.value.code == "common.internal"
    assert await repo_connections.get(_ctx(ws_victim), forged.id) is None


async def test_forged_cross_tenant_mcp_server_add_is_rejected(
    repo_mcp_servers: SqlMcpServerRepository,
) -> None:
    ws_victim, ws_attacker = new_uuid7(), new_uuid7()
    forged = _mcp_server(workspace_id=ws_victim)

    with pytest.raises(AppError) as excinfo:
        await repo_mcp_servers.add(_ctx(ws_attacker), forged)

    assert excinfo.value.code == "common.internal"
    assert await repo_mcp_servers.find_by_name(_ctx(ws_victim), "docs-mcp") is None


# --------------------------------------------------------------------------- #
# cursor pagination: newest first, every status, tenant-scoped (6.3-ب)        #
# --------------------------------------------------------------------------- #
async def test_list_pages_newest_first_over_every_status(
    repo_connections: SqlConnectionRepository,
) -> None:
    """``listConnections`` became paginated in 6.3-ب.

    Seeded mostly with NON-connected rows on purpose: that is the population
    ``Limits.max_connectors`` never counts ("pending rows are handshake
    debris, not capacity"), and therefore the reason this listing had no
    ceiling at all despite documenting one.
    """
    ws, other_ws = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    created = []
    for index, status in enumerate(
        (
            ConnectionStatus.PENDING,
            ConnectionStatus.ERROR,
            ConnectionStatus.PENDING,
            ConnectionStatus.REVOKED,
            ConnectionStatus.CONNECTED,
        )
    ):
        conn = _connection(workspace_id=ws, connector_key=f"conn-{index}", status=status)
        await repo_connections.add(ctx, conn)
        created.append(conn)
    await repo_connections.add(_ctx(other_ws), _connection(workspace_id=other_ws))

    expected = [c.id for c in reversed(created)]  # UUIDv7 monotonic ⇒ reversed insertion

    page1 = await repo_connections.list(ctx, limit=2, cursor=None)
    assert [c.id for c in page1.data] == expected[:2]
    assert page1.next_cursor is not None

    page2 = await repo_connections.list(ctx, limit=2, cursor=page1.next_cursor)
    assert [c.id for c in page2.data] == expected[2:4]

    page3 = await repo_connections.list(ctx, limit=2, cursor=page2.next_cursor)
    assert [c.id for c in page3.data] == expected[4:]
    assert page3.next_cursor is None  # last page, and the other tenant never appears
