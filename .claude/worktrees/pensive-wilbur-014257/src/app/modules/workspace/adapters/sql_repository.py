"""SQL adapters for ``WorkspaceRepository``/``UserRepository`` (02-port-contracts
§2; 01-data-model §2.1; ``migrations/versions/workspace/0001_workspace.py``).

Declares its own local Core ``Table``\\ s against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as
plain callables, so this adapter never even imports ``app.infrastructure``.

Two classes, two tables, two different isolation stories:

* ``SqlWorkspaceRepository`` / ``workspace.workspaces`` — the tenant ROOT
  (R2, 01 §2.1). It carries NO ``workspace_id`` column and NO RLS: there is
  nothing to isolate rows *by*. ``get``/``save``'s ``id = :ctx_ws`` clause IS
  the entire isolation boundary for reading/mutating an EXISTING row — a
  caller can only ever read or write the one workspace matching its own
  ``ExecutionContext``, regardless of whether some other workspace row
  exists. This is AuthZ-by-query, not defence in depth alongside RLS.
  ``add`` needs no such guard: it INSERTs a brand new row, and the only
  caller that mints one (``ProvisionUserOnFirstLogin``) always mints
  ``ctx.workspace_id`` and ``workspace.id`` as the SAME fresh UUIDv7.
* ``SqlUserRepository`` / ``workspace.users`` — an ordinary tenant-scoped
  table (RLS ``tenant_isolation`` + ``WHERE workspace_id``, DD-04), EXCEPT
  for ``get_by_firebase_uid``, which is unscoped by design (the port's own
  docstring): it resolves a user by the external Firebase UID — a global
  unique natural key — before any workspace/RLS context can exist (JIT
  login). That one method runs over a SEPARATE ``PlatformSessionProvider``
  seam (R1) backed by the ``users_platform_read`` sentinel policy
  (``migrations/versions/workspace/0001_workspace.py``); it is the SOLE
  sanctioned consumer of that seam (see also
  ``infrastructure/persistence/rls.py::PlatformSessionFactory``).
"""

from __future__ import annotations

from collections.abc import Callable
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
from app.framework.types import Uuid as UuidStr
from app.modules.workspace.domain.entities import User, Workspace
from app.modules.workspace.domain.value_objects import (
    Email,
    UserStatus,
    WorkspaceName,
    WorkspaceStatus,
)

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

workspaces = Table(
    "workspaces",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("owner_user_id", _uuid_col, nullable=False),
    Column("name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="workspace",
)

users = Table(
    "users",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("firebase_uid", Text, nullable=False),
    Column("email", Text, nullable=False),
    Column("display_name", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="workspace",
)

# Request-scoped session-provider seams (structurally satisfy whatever
# ``TenantSessionFactory``/``PlatformSessionFactory.__call__`` return): the
# adapter depends only on these narrow shapes, never on
# ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]
PlatformSessionProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class SqlWorkspaceRepository:
    """SQL ``WorkspaceRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    ``workspace.workspaces`` has NO RLS (R2): ``get``/``save`` guard tenant
    access themselves via ``id = :ctx_ws`` — read the class docstring above
    for why that predicate, not a policy, is the isolation boundary (``add``
    needs no such guard; see the same docstring). Each method opens its own
    tenant-scoped transaction (one round trip per call, R2 media precedent)
    — there is no cross-call unit of work yet.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(self, ctx: ExecutionContext, workspace_id: UuidStr) -> Workspace | None:
        # `id == ctx.workspace_id` is NOT defence in depth here (there is no
        # RLS policy on this table to defend alongside) -- it is the WHOLE
        # guard: a caller only ever gets back the one workspace matching its
        # own context, never an arbitrary id, even if that other row exists.
        stmt = select(workspaces).where(
            workspaces.c.id == workspace_id, workspaces.c.id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc, "workspace") from exc
        return None if row is None else _hydrate_workspace(row)

    async def add(self, ctx: ExecutionContext, workspace: Workspace) -> None:
        stmt = insert(workspaces).values(
            id=workspace.id,
            owner_user_id=workspace.owner_user_id,
            name=workspace.name.value,
            status=workspace.status.value,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
            version=workspace.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc, "workspace") from exc

    async def save(self, ctx: ExecutionContext, workspace: Workspace) -> None:
        # Optimistic lock: only a row still at `workspace.version` is
        # updated; the `id == ctx.workspace_id` clause is the same AuthZ
        # guard as `get` (no RLS on this table, R2).
        stmt = (
            update(workspaces)
            .where(
                workspaces.c.id == workspace.id,
                workspaces.c.id == ctx.workspace_id,
                workspaces.c.version == workspace.version,
            )
            .values(
                name=workspace.name.value,
                status=workspace.status.value,
                version=workspaces.c.version + 1,
            )
            .returning(workspaces.c.version, workspaces.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc, "workspace") from exc
        if row is None:
            raise ConflictError(
                f"workspace {workspace.id} was modified concurrently (stale version)"
            )
        # `version`/`updated_at` are repository-owned (02-port-contracts §2)
        # -- write the fresh values back onto the aggregate, media precedent.
        workspace.version, workspace.updated_at = row


class SqlUserRepository:
    """SQL ``UserRepository`` adapter (structural Protocol match).

    ``get_by_firebase_uid`` is the SOLE sanctioned consumer of
    ``platform_session`` (R1,
    ``infrastructure/persistence/rls.py::PlatformSessionFactory``): it never
    sets ``app.workspace_id`` and applies NO workspace filter — the port's
    own docstring binds this as the unscoped-by-design identity lookup used
    before any tenant context exists (JIT login/provisioning). ``add`` is
    ordinarily tenant-scoped (RLS ``tenant_isolation`` + ``WHERE
    workspace_id``, DD-04). There is no ``save``/update method: v1 never
    mutates a provisioned user.
    """

    def __init__(
        self, tenant_session: TenantSessionProvider, platform_session: PlatformSessionProvider
    ) -> None:
        self._tenant_session = tenant_session
        self._platform_session = platform_session

    async def get_by_firebase_uid(self, firebase_uid: str) -> User | None:
        stmt = select(users).where(users.c.firebase_uid == firebase_uid)
        try:
            async with self._platform_session() as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc, "user") from exc
        return None if row is None else _hydrate_user(row)

    async def add(self, ctx: ExecutionContext, user: User) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched user.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's tenant
        # (media precedent).
        stmt = insert(users).values(
            id=user.id,
            workspace_id=user.workspace_id,
            firebase_uid=user.firebase_uid,
            email=user.email.value,
            display_name=user.display_name,
            status=user.status.value,
            created_at=user.created_at,
            updated_at=user.updated_at,
            version=user.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc, "user") from exc


def _hydrate_workspace(row: RowMapping) -> Workspace:
    return Workspace(
        id=row["id"],
        owner_user_id=row["owner_user_id"],
        name=WorkspaceName(row["name"]),
        status=WorkspaceStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _hydrate_user(row: RowMapping) -> User:
    return User(
        id=row["id"],
        workspace_id=row["workspace_id"],
        firebase_uid=row["firebase_uid"],
        email=Email(row["email"]),
        display_name=row["display_name"] or "",  # R8: column nullable, entity is non-optional str
        status=UserStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _translate(exc: DBAPIError, entity: str) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race (a duplicate
    ``id``, ``firebase_uid``, or the ``uq_users_owner`` partial unique index)
    -- ``ConflictError`` (409, ``common.conflict``).
    ``42501`` (``insufficient_privilege``) -- rejected by the database (e.g.
    the ``workspace.users`` RLS ``WITH CHECK`` clause on a forged
    cross-tenant ``user.workspace_id``, or a missing GRANT) -- an
    internal/500-class error (``common.internal``): a well-behaved caller can
    never trigger this, so it is not a normal 4xx. Anything else is an
    unexpected database failure, folded into the same 500-class error rather
    than leaking the driver exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError(f"{entity} write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError(
            f"{entity} write rejected by insufficient database privilege", code="common.internal"
        )
    return AppError(
        f"unexpected database error while persisting a {entity}", code="common.internal"
    )
