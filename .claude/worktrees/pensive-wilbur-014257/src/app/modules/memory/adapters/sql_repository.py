"""SQL adapter for ``MemoryRepository`` (02-port-contracts §2; 01-data-model
§2.5; ``migrations/versions/memory/0001_memory.py``).

Declares its own local Core ``Table`` against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly in every method below.

``memory.memory_items`` carries no ``version``/``updated_at`` column
(01-data-model §2.5, access precedent): ``save`` is therefore a PLAIN
update-by-id (+ ``WHERE workspace_id`` defence in depth) with no optimistic
lock and nothing written back onto the aggregate — unlike every other
module in this wave. ``get`` applies no ``deleted_at`` filter (mirrors
``conversations.get``): ``ForgetMemory`` re-``get``s an already
soft-deleted item to stay idempotent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

from sqlalchemy import Column, DateTime, Float, MetaData, Table, Text, Uuid, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.types import Uuid as UuidStr
from app.modules.memory.domain.entities import MemoryItem
from app.modules.memory.domain.value_objects import AgentKey, MemoryKind, VectorRef

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

memory_items = Table(
    "memory_items",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("agent_key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("collection", Text, nullable=True),
    Column("point_id", _uuid_col, nullable=True),
    Column("salience", Float, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    Column("deleted_at", _timestamptz, nullable=True),
    schema="memory",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlMemoryRepository:
    """SQL ``MemoryRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — there is no cross-call unit of work yet.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def add(self, ctx: ExecutionContext, item: MemoryItem) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched item.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's
        # tenant (media precedent).
        stmt = insert(memory_items).values(
            id=item.id,
            workspace_id=item.workspace_id,
            agent_key=item.agent_key.value,
            kind=item.kind.value,
            content=item.content,
            collection=item.vector_ref.collection if item.vector_ref else None,
            point_id=item.vector_ref.point_id if item.vector_ref else None,
            salience=item.salience,
            created_at=item.created_at,
            deleted_at=item.deleted_at,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def get(self, ctx: ExecutionContext, memory_id: UuidStr) -> MemoryItem | None:
        # No `deleted_at` filter: `ForgetMemory` re-`get`s an already
        # soft-deleted item to stay idempotent (mirrors `conversations.get`).
        stmt = select(memory_items).where(
            memory_items.c.id == memory_id, memory_items.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate(row)

    async def list_by_agent(
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None
    ) -> Page[MemoryItem]:
        # `deleted_at IS NULL`: mirrors the partial index `ix_mem_ws_agent`
        # (01 §2.5) and the same "active only" semantics as
        # `ListConversationsByAgent` -- a forgotten item drops out of
        # listing even though `get` still resolves it directly by id.
        conditions = [
            memory_items.c.workspace_id == ctx.workspace_id,
            memory_items.c.agent_key == agent_key,
            memory_items.c.deleted_at.is_(None),
        ]
        if cursor is not None:
            conditions.append(memory_items.c.id > decode_id_cursor(cursor))
        stmt = select(memory_items).where(*conditions).order_by(memory_items.c.id).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate(rows, limit, _hydrate)

    async def save(self, ctx: ExecutionContext, item: MemoryItem) -> None:
        # No `version` column (01 §2.5) -- a plain update-by-id, no
        # optimistic lock and nothing to write back onto the aggregate.
        # Only the fields the two domain mutators can change are written:
        # `forget` (deleted_at) and `attach_vector` (collection/point_id).
        stmt = (
            update(memory_items)
            .where(memory_items.c.id == item.id, memory_items.c.workspace_id == ctx.workspace_id)
            .values(
                collection=item.vector_ref.collection if item.vector_ref else None,
                point_id=item.vector_ref.point_id if item.vector_ref else None,
                deleted_at=item.deleted_at,
            )
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc


def _paginate[T](
    rows: Sequence[RowMapping], limit: int, hydrate: Callable[[RowMapping], T]
) -> Page[T]:
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = encode_id_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
    return Page(data=[hydrate(row) for row in page_rows], next_cursor=next_cursor, limit=limit)


def _hydrate(row: RowMapping) -> MemoryItem:
    vector_ref: VectorRef | None = None
    if row["collection"] is not None and row["point_id"] is not None:
        vector_ref = VectorRef(collection=row["collection"], point_id=row["point_id"])
    return MemoryItem(
        id=row["id"],
        workspace_id=row["workspace_id"],
        agent_key=AgentKey(row["agent_key"]),
        kind=MemoryKind(row["kind"]),
        content=row["content"],
        vector_ref=vector_ref,
        salience=row["salience"],
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race: a duplicate
    ``id`` on ``add`` -- ``ConflictError`` (409, ``common.conflict``).
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``item.workspace_id`` on
    ``add``) -- an internal/500-class error (``common.internal``): a
    well-behaved caller can never trigger this, so it is not a normal 4xx.
    Anything else is an unexpected database failure, folded into the same
    500-class error rather than leaking the driver exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("memory item write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError("memory item write rejected by row-level security", code="common.internal")
    return AppError(
        "unexpected database error while persisting a memory item", code="common.internal"
    )
