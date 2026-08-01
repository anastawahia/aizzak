"""SQL adapters for ``ConnectionRepository``/``McpServerRepository``
(02-port-contracts §2; 01-data-model §2.9;
``migrations/versions/integrations/0001_integrations.py``).

Declares its own local Core ``Table``\\ s against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly in every method below, on both ``connections`` and
``mcp_servers``.

``update_tokens`` is deliberately a NARROW write (``ConnectionRepository``'s
own class docstring, binding): it writes only ``token_ref``/``key_id``/
``expires_at`` by ``id`` + ``workspace_id``, with **no** ``version``
predicate and no ``version`` bump -- concurrent lazy refreshes of the same
connection are benign (every freshly renewed token is valid: last-writer-
wins), so they must never fail each other the way racing lifecycle
transitions (``save``) should. ``updated_at`` still moves, via
``platform.touch_updated_at()`` (the migration's trigger fires on any
``UPDATE``, whether or not this statement's own ``SET`` clause names that
column).

``CipherRef`` maps onto its module's two cipher columns exactly like
``credentials`` does: ``token_ref``/``key_id`` for ``Connection``,
``auth_ref``/``key_id`` for ``McpServer``.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.types import Uuid as UuidStr
from app.modules.integrations.domain.entities import Connection, McpServer
from app.modules.integrations.domain.value_objects import (
    CipherRef,
    ConnectionStatus,
    ConnectorKey,
    McpEndpoint,
    McpServerName,
    McpStatus,
)

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

connections = Table(
    "connections",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("connector_key", Text, nullable=False),
    Column("display_name", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("scopes", ARRAY(Text), nullable=False),
    Column("token_ref", Text, nullable=True),
    Column("key_id", Text, nullable=True),
    Column("expires_at", _timestamptz, nullable=True),
    Column("last_error", Text, nullable=True),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="integrations",
)

mcp_servers = Table(
    "mcp_servers",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("name", Text, nullable=False),
    Column("endpoint_url", Text, nullable=False),
    Column("transport", Text, nullable=False),
    Column("auth_ref", Text, nullable=True),
    Column("key_id", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="integrations",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlConnectionRepository:
    """SQL ``ConnectionRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — there is no cross-call unit of work yet.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(self, ctx: ExecutionContext, conn_id: UuidStr) -> Connection | None:
        stmt = select(connections).where(
            connections.c.id == conn_id, connections.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_connection(row)

    async def add(self, ctx: ExecutionContext, conn: Connection) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched conn.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's
        # tenant (media precedent).
        stmt = insert(connections).values(
            id=conn.id,
            workspace_id=conn.workspace_id,
            connector_key=conn.connector_key.value,
            display_name=conn.display_name,
            status=conn.status.value,
            scopes=list(conn.scopes),
            token_ref=conn.token_ref.ciphertext if conn.token_ref else None,
            key_id=conn.token_ref.key_name if conn.token_ref else None,
            expires_at=conn.expires_at,
            last_error=conn.last_error,
            created_by=conn.created_by,
            created_at=conn.created_at,
            updated_at=conn.updated_at,
            version=conn.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def list_connected(self, ctx: ExecutionContext) -> list[Connection]:
        # Backed by the partial index ix_conn_ws (WHERE status = 'connected',
        # 01 §2.9 -- port adapter note).
        stmt = select(connections).where(
            connections.c.workspace_id == ctx.workspace_id,
            connections.c.status == ConnectionStatus.CONNECTED.value,
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate_connection(row) for row in rows]

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None
    ) -> Page[Connection]:
        # Every status, deliberately -- see the port's docstring. No partial
        # index applies here (that one is `status = 'connected'`).
        #
        # PAGINATED (6.3-ب), unlike what this method's own comment used to
        # claim: `Limits.max_connectors` caps CONNECTED rows only ("pending
        # rows are handshake debris, not capacity", `BeginConnection`), so
        # every abandoned or failed handshake leaves a row here that no cap
        # counts. The ceiling this read was said to have does not exist.
        #
        # Ordered by `id` rather than `created_at` because UUIDv7 is
        # time-ordered -- the same sort, but total; NEWEST FIRST with a
        # matching `id <` predicate (`framework/pagination`).
        conditions = [connections.c.workspace_id == ctx.workspace_id]
        if cursor is not None:
            conditions.append(connections.c.id < decode_id_cursor(cursor))
        stmt = (
            select(connections)
            .where(*conditions)
            .order_by(connections.c.id.desc())
            .limit(limit + 1)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = encode_id_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
        return Page(
            data=[_hydrate_connection(row) for row in page_rows],
            next_cursor=next_cursor,
            limit=limit,
        )

    async def update_tokens(
        self,
        ctx: ExecutionContext,
        conn_id: UuidStr,
        token_ref: str,
        key_id: str,
        expires_at: datetime,
    ) -> None:
        # Narrow hot-path write -- no version predicate/bump (class
        # docstring, INV-I3 last-writer-wins).
        stmt = (
            update(connections)
            .where(connections.c.id == conn_id, connections.c.workspace_id == ctx.workspace_id)
            .values(token_ref=token_ref, key_id=key_id, expires_at=expires_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def find_by_connector(
        self, ctx: ExecutionContext, connector_key: str
    ) -> Connection | None:
        stmt = (
            select(connections)
            .where(
                connections.c.workspace_id == ctx.workspace_id,
                connections.c.connector_key == connector_key,
            )
            .limit(1)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_connection(row)

    async def save(self, ctx: ExecutionContext, conn: Connection) -> None:
        # Optimistic lock: only a row still at `conn.version` is updated --
        # every field a lifecycle mutator (`begin`/`connect`/`mark_error`/
        # `revoke`) can change is written; `apply_refreshed_tokens`' durable
        # write instead goes through `update_tokens` above.
        stmt = (
            update(connections)
            .where(
                connections.c.id == conn.id,
                connections.c.workspace_id == ctx.workspace_id,
                connections.c.version == conn.version,
            )
            .values(
                status=conn.status.value,
                scopes=list(conn.scopes),
                token_ref=conn.token_ref.ciphertext if conn.token_ref else None,
                key_id=conn.token_ref.key_name if conn.token_ref else None,
                expires_at=conn.expires_at,
                last_error=conn.last_error,
                version=connections.c.version + 1,
            )
            .returning(connections.c.version, connections.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(f"connection {conn.id} was modified concurrently (stale version)")
        # `version`/`updated_at` are repository-owned (02-port-contracts §2)
        # -- write the fresh values back onto the aggregate, media precedent.
        conn.version, conn.updated_at = row


class SqlMcpServerRepository:
    """SQL ``McpServerRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports)."""

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def add(self, ctx: ExecutionContext, server: McpServer) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id)
        # -- same forged-write guard as `SqlConnectionRepository.add`.
        stmt = insert(mcp_servers).values(
            id=server.id,
            workspace_id=server.workspace_id,
            name=server.name.value,
            endpoint_url=server.endpoint.url,
            transport=server.endpoint.transport,
            auth_ref=server.auth_ref.ciphertext if server.auth_ref else None,
            key_id=server.auth_ref.key_name if server.auth_ref else None,
            status=server.status.value,
            created_by=server.created_by,
            created_at=server.created_at,
            updated_at=server.updated_at,
            version=server.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def list_active(self, ctx: ExecutionContext) -> list[McpServer]:
        # Backed by the partial index ix_mcp_ws (WHERE status = 'active', 01
        # §2.9 -- port class docstring).
        stmt = select(mcp_servers).where(
            mcp_servers.c.workspace_id == ctx.workspace_id,
            mcp_servers.c.status == McpStatus.ACTIVE.value,
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate_mcp_server(row) for row in rows]

    async def find_by_name(self, ctx: ExecutionContext, name: str) -> McpServer | None:
        stmt = (
            select(mcp_servers)
            .where(mcp_servers.c.workspace_id == ctx.workspace_id, mcp_servers.c.name == name)
            .limit(1)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_mcp_server(row)

    async def get(self, ctx: ExecutionContext, server_id: UuidStr) -> McpServer | None:
        stmt = select(mcp_servers).where(
            mcp_servers.c.id == server_id, mcp_servers.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_mcp_server(row)

    async def list(self, ctx: ExecutionContext) -> list[McpServer]:
        # Every status, newest first -- UUIDv7 ids sort by creation time, so
        # `ORDER BY id DESC` needs no extra index (connections precedent).
        stmt = (
            select(mcp_servers)
            .where(mcp_servers.c.workspace_id == ctx.workspace_id)
            .order_by(mcp_servers.c.id.desc())
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate_mcp_server(row) for row in rows]

    async def save(self, ctx: ExecutionContext, server: McpServer) -> None:
        # Optimistic lock: every field `disable`/`reactivate` can change is
        # written, and only a row still at `server.version` is updated.
        stmt = (
            update(mcp_servers)
            .where(
                mcp_servers.c.id == server.id,
                mcp_servers.c.workspace_id == ctx.workspace_id,
                mcp_servers.c.version == server.version,
            )
            .values(
                endpoint_url=server.endpoint.url,
                transport=server.endpoint.transport,
                auth_ref=server.auth_ref.ciphertext if server.auth_ref else None,
                key_id=server.auth_ref.key_name if server.auth_ref else None,
                status=server.status.value,
                version=mcp_servers.c.version + 1,
            )
            .returning(mcp_servers.c.version, mcp_servers.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(f"MCP server {server.id} was modified concurrently (stale version)")
        server.version, server.updated_at = row


def _hydrate_connection(row: RowMapping) -> Connection:
    token_ref: CipherRef | None = None
    if row["token_ref"] is not None and row["key_id"] is not None:
        token_ref = CipherRef(ciphertext=row["token_ref"], key_name=row["key_id"])
    return Connection(
        id=row["id"],
        workspace_id=row["workspace_id"],
        connector_key=ConnectorKey(row["connector_key"]),
        display_name=row["display_name"],
        status=ConnectionStatus(row["status"]),
        scopes=tuple(row["scopes"]),
        token_ref=token_ref,
        expires_at=row["expires_at"],
        last_error=row["last_error"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _hydrate_mcp_server(row: RowMapping) -> McpServer:
    auth_ref: CipherRef | None = None
    if row["auth_ref"] is not None and row["key_id"] is not None:
        auth_ref = CipherRef(ciphertext=row["auth_ref"], key_name=row["key_id"])
    return McpServer(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=McpServerName(row["name"]),
        endpoint=McpEndpoint(row["endpoint_url"], row["transport"]),
        auth_ref=auth_ref,
        status=McpStatus(row["status"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race: a duplicate
    ``id``, or ``uq_conn_ws_connector``/``uq_mcp_ws_name`` under real
    concurrency (``BeginConnection``/``RegisterMcpServer`` already guard
    these with their own pre-checks, so this is only a TOCTOU backstop) --
    ``ConflictError`` (409, ``common.conflict``).
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``workspace_id`` on
    ``add``) -- an internal/500-class error (``common.internal``): a
    well-behaved caller can never trigger this, so it is not a normal 4xx.
    Anything else is an unexpected database failure, folded into the same
    500-class error rather than leaking the driver exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("integrations write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError("integrations write rejected by row-level security", code="common.internal")
    return AppError(
        "unexpected database error while persisting integrations data", code="common.internal"
    )
