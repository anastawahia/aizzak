"""SQL adapter for ``RoleAssignmentRepository`` (02-port-contracts §2;
01-data-model §2.2; ``migrations/versions/access/0001_access.py``).

Declares its own local Core ``Table`` against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly in every method below. ``access.role_assignments`` has no
``version``/``updated_at`` columns (create-or-delete only, 06 §2) -- there
is no ``save``/touch trigger, unlike ``media``/``workspace``/``credentials``.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    Uuid,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.types import Uuid as UuidStr
from app.modules.access.domain.entities import RoleAssignment
from app.modules.access.domain.value_objects import RoleName

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

role_assignments = Table(
    "role_assignments",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("user_id", _uuid_col, nullable=False),
    Column("role", Text, nullable=False),
    Column("granted_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    schema="access",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlRoleAssignmentRepository:
    """SQL ``RoleAssignmentRepository`` adapter (structural Protocol match —
    no inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — there is no cross-call unit of work yet. Every
    method carries an explicit ``workspace_id = :ws`` filter (DD-04 defence
    in depth) alongside the RLS ``tenant_isolation`` policy.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def list_for_user(self, ctx: ExecutionContext, user_id: UuidStr) -> list[RoleAssignment]:
        stmt = (
            select(role_assignments)
            .where(
                role_assignments.c.workspace_id == ctx.workspace_id,
                role_assignments.c.user_id == user_id,
            )
            .order_by(role_assignments.c.created_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate(row) for row in rows]

    async def find(
        self, ctx: ExecutionContext, user_id: UuidStr, role: RoleName
    ) -> RoleAssignment | None:
        stmt = (
            select(role_assignments)
            .where(
                role_assignments.c.workspace_id == ctx.workspace_id,
                role_assignments.c.user_id == user_id,
                role_assignments.c.role == role.value,
            )
            .limit(1)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate(row)

    async def add(self, ctx: ExecutionContext, assignment: RoleAssignment) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched assignment.workspace_id is then rejected by the
        # RLS WITH CHECK clause rather than silently persisted under ctx's
        # tenant (media precedent).
        stmt = insert(role_assignments).values(
            id=assignment.id,
            workspace_id=assignment.workspace_id,
            user_id=assignment.user_id,
            role=assignment.role.value,
            granted_by=assignment.granted_by,
            created_at=assignment.created_at,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def remove(self, ctx: ExecutionContext, assignment_id: UuidStr) -> None:
        stmt = delete(role_assignments).where(
            role_assignments.c.workspace_id == ctx.workspace_id,
            role_assignments.c.id == assignment_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)  # 0 rows deleted is a silent no-op (idempotent)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def count_by_role(self, ctx: ExecutionContext, role: RoleName) -> int:
        stmt = (
            select(func.count())
            .select_from(role_assignments)
            .where(
                role_assignments.c.workspace_id == ctx.workspace_id,
                role_assignments.c.role == role.value,
            )
        )
        try:
            async with self._tenant_session(ctx) as session:
                count = (await session.execute(stmt)).scalar_one()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return count


def _hydrate(row: RowMapping) -> RoleAssignment:
    return RoleAssignment(
        id=row["id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        role=RoleName(row["role"]),
        granted_by=row["granted_by"] or "",  # R8: column nullable, entity is non-optional str
        created_at=row["created_at"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race on
    ``uq_assignment`` (``workspace_id, user_id, role``) -- ``ConflictError``
    (409, ``common.conflict``); ``AssignRole`` already treats a duplicate as
    idempotent via its own ``find`` pre-check, so this is only reachable
    under real concurrency.
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant
    ``assignment.workspace_id`` on ``add``) -- an internal/500-class error
    (``common.internal``): a well-behaved caller can never trigger this, so
    it is not a normal 4xx. Anything else is an unexpected database failure,
    folded into the same 500-class error rather than leaking the driver
    exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("role assignment write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError(
            "role assignment write rejected by row-level security", code="common.internal"
        )
    return AppError(
        "unexpected database error while persisting a role assignment", code="common.internal"
    )
