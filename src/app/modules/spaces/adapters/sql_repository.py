"""SQL adapter for ``SpaceRepository`` (02-port-contracts §2;
``docs/spaces-backend-plan.md`` §3.2; ``migrations/versions/spaces/0001_spaces.py``).

Declares its own local Core ``Table`` against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another module
or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery are built in ``infrastructure/persistence/``
and handed in by the Composition Root as a plain callable.

Two-layer tenant isolation (DD-04), the ``files`` adapter verbatim: Layer 1
(the RLS GUC) is set by the injected ``tenant_session`` provider before this
code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied explicitly in
every method below. ``list`` additionally filters ``deleted_at IS NULL``;
``get`` does not, so a caller holding an id can re-``get`` it after a
soft-delete (idempotent ``DeleteSpace``).

**The table this adapter targets does not exist yet.** Step 2 of the plan
writes the migration. That ordering is the module's, not an accident: the DDL
in §3.2 is fixed and this file is written against it, so the migration has one
job — to make the database agree with a shape that is already reviewed. Every
test that exercises this adapter against a real table is therefore step 2's
(``tests/integration/test_spaces_repository_rls.py``), the same way every
other module's RLS proof arrived with its migration.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

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
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.types import Uuid as UuidStr
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

spaces = Table(
    "spaces",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("name", Text, nullable=False),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("deleted_at", _timestamptz, nullable=True),
    Column("version", Integer, nullable=False),
    schema="spaces",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlSpaceRepository:
    """SQL ``SpaceRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, the files/media precedent).
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(self, ctx: ExecutionContext, space_id: UuidStr) -> Space | None:
        stmt = select(spaces).where(
            spaces.c.id == space_id, spaces.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate(row)

    async def add(self, ctx: ExecutionContext, space: Space) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched space.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's tenant
        # (media/files precedent).
        stmt = insert(spaces).values(
            id=space.id,
            workspace_id=space.workspace_id,
            name=space.name.value,
            created_by=space.created_by,
            created_at=space.created_at,
            updated_at=space.updated_at,
            deleted_at=space.deleted_at,
            version=space.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def save(self, ctx: ExecutionContext, space: Space) -> None:
        # Optimistic lock: only a row still at `space.version` is updated.
        # `workspace_id`/`created_by`/`created_at` never change (no domain
        # mutator touches them), and leaving them out of the UPDATE is what
        # enforces that at the adapter.
        stmt = (
            update(spaces)
            .where(
                spaces.c.id == space.id,
                spaces.c.workspace_id == ctx.workspace_id,
                spaces.c.version == space.version,
            )
            .values(
                name=space.name.value,
                deleted_at=space.deleted_at,
                version=spaces.c.version + 1,
            )
            .returning(spaces.c.version, spaces.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(f"space {space.id} was modified concurrently (stale version)")
        # `version`/`updated_at` are repository-owned (02-port-contracts §2) --
        # write the fresh values back onto the aggregate (`updated_at` comes
        # from the `trg_touch` trigger, §3.2), media precedent.
        space.version, space.updated_at = row

    async def list(self, ctx: ExecutionContext, *, limit: int, cursor: str | None) -> Page[Space]:
        # Active spaces only. NEWEST FIRST (`framework/pagination`, 6.3-ب):
        # `id DESC` with an `id <` predicate, UUIDv7 being time-ordered.
        conditions = [spaces.c.workspace_id == ctx.workspace_id, spaces.c.deleted_at.is_(None)]
        if cursor is not None:
            conditions.append(spaces.c.id < decode_id_cursor(cursor))
        stmt = select(spaces).where(*conditions).order_by(spaces.c.id.desc()).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate(rows, limit)


def _paginate(rows: Sequence[RowMapping], limit: int) -> Page[Space]:
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = encode_id_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
    return Page(data=[_hydrate(row) for row in page_rows], next_cursor=next_cursor, limit=limit)


def _hydrate(row: RowMapping) -> Space:
    return Space(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=SpaceName(row["name"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
        version=row["version"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` types never escape this
    adapter (R6 media precedent).

    ``23505`` is NAMED here rather than generic, and that is the one deviation
    from the ``files`` translator. This table has exactly two unique
    constraints: the primary key on a freshly minted UUIDv7, which no caller
    can collide with, and ``ux_spaces_ws_name`` (§3.2), which a caller
    collides with every time they reuse a name. So a ``23505`` reaching here
    means "that name is taken" with a certainty ``files`` cannot claim about
    its own -- and ``spaces.duplicate_name`` says so, instead of making a
    client guess at ``common.conflict``.

    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``space.workspace_id``) --
    an internal/500-class error: a well-behaved caller can never trigger it.
    Anything else is an unexpected database failure, folded into the same
    500-class error rather than leaking the driver exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("a space with this name already exists", code="spaces.duplicate_name")
    if sqlstate == "42501":
        return AppError("space write rejected by row-level security", code="common.internal")
    return AppError("unexpected database error while persisting a space", code="common.internal")
