"""Live-Postgres tests for ``SqlWorkspaceRepository``/``SqlUserRepository`` +
RLS (09-testing-strategy §3), plus the R1 platform-read sentinel and R2's
no-RLS AuthZ guard on ``workspace.workspaces``.

Runs against a real, local PostgreSQL 16 (no Docker/testcontainers -- see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. Builders mirror ``tests/unit/test_workspace_use_cases.py``,
except every id is a *real* ``new_uuid7()`` string rather than an arbitrary
label -- the underlying Postgres columns are actually typed ``uuid``.
"""

from __future__ import annotations

from datetime import datetime

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
from app.modules.workspace.adapters.sql_repository import SqlUserRepository, SqlWorkspaceRepository
from app.modules.workspace.domain.entities import User, Workspace
from app.modules.workspace.domain.value_objects import (
    Email,
    UserStatus,
    WorkspaceName,
    WorkspaceStatus,
)
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str, *, user_id: str | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id or new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


def _workspace(
    *,
    workspace_id: str | None = None,
    owner_user_id: str | None = None,
    name: str = "Acme",
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
    now: datetime | None = None,
) -> Workspace:
    at = now or utc_now()
    return Workspace(
        id=workspace_id or new_uuid7(),
        owner_user_id=owner_user_id or new_uuid7(),
        name=WorkspaceName(name),
        status=status,
        created_at=at,
        updated_at=at,
        version=1,
    )


def _user(
    *,
    workspace_id: str,
    user_id: str | None = None,
    firebase_uid: str | None = None,
    email: str = "zaid@example.com",
    display_name: str = "Zaid",
    status: UserStatus = UserStatus.ACTIVE,
    now: datetime | None = None,
) -> User:
    at = now or utc_now()
    return User(
        id=user_id or new_uuid7(),
        workspace_id=workspace_id,
        firebase_uid=firebase_uid or f"fb-{new_uuid7()}",
        email=Email(email),
        display_name=display_name,
        status=status,
        created_at=at,
        updated_at=at,
        version=1,
    )


async def _seed_workspace(repo_workspace: SqlWorkspaceRepository, workspace_id: str) -> None:
    """``workspace.users.workspace_id`` carries an intra-schema FK
    (``fk_user_ws``) to ``workspace.workspaces(id)`` -- every ``SqlUserRepository``
    test below must create the parent workspace row first."""
    await repo_workspace.add(_ctx(workspace_id), _workspace(workspace_id=workspace_id))


async def _fetch_workspace_row_as_owner(owner_dsn: str, workspace_id: str) -> RowMapping | None:
    """Bypass the repository: read the raw row as the table owner. No GUC to
    set first -- ``workspace.workspaces`` carries NO RLS at all (R2)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT * FROM workspace.workspaces WHERE id = :id"), {"id": workspace_id}
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


async def _fetch_user_row_as_owner(
    owner_dsn: str, workspace_id: str, user_id: str
) -> RowMapping | None:
    """Bypass the repository: read the raw row as the table owner.
    ``workspace.users`` IS ``FORCE ROW LEVEL SECURITY`` (01 §3), so even the
    owner needs the tenant GUC set first, on the same connection/transaction.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT * FROM workspace.users WHERE id = :id"), {"id": user_id}
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# SqlWorkspaceRepository -- (1) round-trip                                    #
# --------------------------------------------------------------------------- #
async def test_workspace_add_then_get_round_trips(
    repo_workspace: SqlWorkspaceRepository,
) -> None:
    ws_id = new_uuid7()
    ctx = _ctx(ws_id)
    ws = _workspace(workspace_id=ws_id, owner_user_id=ctx.user_id or new_uuid7())

    await repo_workspace.add(ctx, ws)
    fetched = await repo_workspace.get(ctx, ws_id)

    assert fetched is not None
    assert fetched.id == ws.id
    assert fetched.owner_user_id == ws.owner_user_id
    assert fetched.name.value == ws.name.value
    assert fetched.status is WorkspaceStatus.ACTIVE
    assert fetched.created_at == ws.created_at
    assert fetched.updated_at == ws.updated_at
    assert fetched.version == 1


# --------------------------------------------------------------------------- #
# (2) missing -> None                                                         #
# --------------------------------------------------------------------------- #
async def test_workspace_get_missing_returns_none(
    repo_workspace: SqlWorkspaceRepository,
) -> None:
    ws_id = new_uuid7()
    assert await repo_workspace.get(_ctx(ws_id), ws_id) is None


# --------------------------------------------------------------------------- #
# (3) save() rename: version/updated_at write-back, owner-verified            #
# --------------------------------------------------------------------------- #
async def test_workspace_save_rename_advances_version_and_writes_back(
    repo_workspace: SqlWorkspaceRepository, live_db: LiveDbDsns
) -> None:
    ws_id = new_uuid7()
    ctx = _ctx(ws_id)
    ws = _workspace(workspace_id=ws_id)
    await repo_workspace.add(ctx, ws)
    created_at = ws.created_at

    ws.rename(WorkspaceName("Renamed"), utc_now())
    await repo_workspace.save(ctx, ws)

    assert ws.version == 2
    assert ws.updated_at >= created_at
    row = await _fetch_workspace_row_as_owner(live_db.owner, ws_id)
    assert row is not None
    assert row["name"] == "Renamed"
    assert row["version"] == 2

    fetched = await repo_workspace.get(ctx, ws_id)
    assert fetched is not None
    assert fetched.name.value == "Renamed"
    assert fetched.version == 2


# --------------------------------------------------------------------------- #
# (4) optimistic conflict                                                     #
# --------------------------------------------------------------------------- #
async def test_workspace_save_with_stale_version_raises_conflict_and_leaves_row_unchanged(
    repo_workspace: SqlWorkspaceRepository, live_db: LiveDbDsns
) -> None:
    ws_id = new_uuid7()
    ctx = _ctx(ws_id)
    await repo_workspace.add(ctx, _workspace(workspace_id=ws_id))

    reader_a = await repo_workspace.get(ctx, ws_id)
    reader_b = await repo_workspace.get(ctx, ws_id)
    assert reader_a is not None
    assert reader_b is not None
    assert reader_a.version == reader_b.version == 1

    reader_a.rename(WorkspaceName("First"), utc_now())
    await repo_workspace.save(ctx, reader_a)
    assert reader_a.version == 2

    reader_b.rename(WorkspaceName("Second"), utc_now())  # reader_b stuck at version==1
    with pytest.raises(ConflictError):
        await repo_workspace.save(ctx, reader_b)

    row = await _fetch_workspace_row_as_owner(live_db.owner, ws_id)
    assert row is not None
    assert row["version"] == 2
    assert row["name"] == "First"


# --------------------------------------------------------------------------- #
# (5) AuthZ guard -- no RLS on workspace.workspaces (R2)                      #
# --------------------------------------------------------------------------- #
async def test_workspace_get_authz_guard_rejects_mismatched_context(
    repo_workspace: SqlWorkspaceRepository,
) -> None:
    """``workspace.workspaces`` carries NO RLS policy (R2): the adapter's own
    ``id == ctx.workspace_id`` predicate is the WHOLE isolation boundary --
    querying a real id under a context for a DIFFERENT id must yield
    ``None``, even though the row genuinely exists."""
    ws_id = new_uuid7()
    other_ctx_id = new_uuid7()
    await repo_workspace.add(_ctx(ws_id), _workspace(workspace_id=ws_id))

    assert await repo_workspace.get(_ctx(other_ctx_id), ws_id) is None
    assert await repo_workspace.get(_ctx(ws_id), ws_id) is not None


# --------------------------------------------------------------------------- #
# (6) two-tenant isolation                                                    #
# --------------------------------------------------------------------------- #
async def test_workspace_two_tenant_isolation(repo_workspace: SqlWorkspaceRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    await repo_workspace.add(_ctx(ws_a), _workspace(workspace_id=ws_a))
    await repo_workspace.add(_ctx(ws_b), _workspace(workspace_id=ws_b))

    assert await repo_workspace.get(_ctx(ws_a), ws_a) is not None
    assert await repo_workspace.get(_ctx(ws_b), ws_b) is not None
    assert await repo_workspace.get(_ctx(ws_a), ws_b) is None
    assert await repo_workspace.get(_ctx(ws_b), ws_a) is None


# --------------------------------------------------------------------------- #
# SqlUserRepository -- (7) add -> get_by_firebase_uid round-trip              #
# --------------------------------------------------------------------------- #
async def test_user_add_then_get_by_firebase_uid_round_trips(
    repo_user: SqlUserRepository, repo_workspace: SqlWorkspaceRepository
) -> None:
    """``get_by_firebase_uid`` always runs over the R1 platform sentinel
    session (``repo_user`` is built with both providers) -- no
    ``ExecutionContext``/tenant scope is involved on the read side at all."""
    ws_id = new_uuid7()
    await _seed_workspace(repo_workspace, ws_id)
    firebase_uid = f"fb-{new_uuid7()}"
    user = _user(workspace_id=ws_id, firebase_uid=firebase_uid)

    await repo_user.add(_ctx(ws_id), user)
    fetched = await repo_user.get_by_firebase_uid(firebase_uid)

    assert fetched is not None
    assert fetched.id == user.id
    assert fetched.workspace_id == ws_id
    assert fetched.firebase_uid == firebase_uid
    assert fetched.email.value == user.email.value
    assert fetched.display_name == user.display_name
    assert fetched.status is UserStatus.ACTIVE
    assert fetched.created_at == user.created_at
    assert fetched.updated_at == user.updated_at
    assert fetched.version == 1


# --------------------------------------------------------------------------- #
# (8) missing -> None                                                        #
# --------------------------------------------------------------------------- #
async def test_user_get_by_firebase_uid_missing_returns_none(
    repo_user: SqlUserRepository,
) -> None:
    assert await repo_user.get_by_firebase_uid(f"fb-{new_uuid7()}") is None


# --------------------------------------------------------------------------- #
# (9) tenant_isolation holds even for a raw owner SELECT                      #
# --------------------------------------------------------------------------- #
async def test_user_tenant_isolation_raw_select_cross_tenant_yields_zero_rows(
    repo_user: SqlUserRepository, repo_workspace: SqlWorkspaceRepository, live_db: LiveDbDsns
) -> None:
    ws_a = new_uuid7()
    await _seed_workspace(repo_workspace, ws_a)
    user = _user(workspace_id=ws_a)
    await repo_user.add(_ctx(ws_a), user)

    ws_b = new_uuid7()  # a foreign tenant that never wrote anything
    # Even a raw owner SELECT (not the repository) obeys `tenant_isolation`
    # once the GUC names a DIFFERENT tenant than the row's own workspace_id.
    row = await _fetch_user_row_as_owner(live_db.owner, ws_b, user.id)
    assert row is None


# --------------------------------------------------------------------------- #
# (10) forged cross-tenant write -> RLS WITH CHECK rejects it                 #
# --------------------------------------------------------------------------- #
async def test_user_add_with_forged_workspace_id_is_rejected_by_rls_with_check(
    repo_user: SqlUserRepository, repo_workspace: SqlWorkspaceRepository
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    # Both must be real workspace rows (fk_user_ws) so the write is rejected
    # SPECIFICALLY by the RLS WITH CHECK clause, not incidentally by the FK.
    await _seed_workspace(repo_workspace, ws_a)
    await _seed_workspace(repo_workspace, ws_b)
    forged_user = _user(workspace_id=ws_b)  # claims tenant B

    with pytest.raises(AppError) as exc_info:
        await repo_user.add(_ctx(ws_a), forged_user)  # added under tenant A's context

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)
    assert await repo_user.get_by_firebase_uid(forged_user.firebase_uid) is None


# --------------------------------------------------------------------------- #
# (11) CRITICAL fail-safe -- empty-string GUC, no sentinel -> zero rows       #
# --------------------------------------------------------------------------- #
async def test_user_empty_string_guc_with_no_sentinel_yields_zero_rows_not_an_exception(
    repo_user: SqlUserRepository,
    repo_workspace: SqlWorkspaceRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """A session with ``app.workspace_id`` reset to ``''`` AND the R1
    ``app.platform_read`` sentinel never set must see zero rows -- never an
    exception (the ``NULLIF`` hardening) and never every tenant's rows (R1's
    sentinel-gated ``users_platform_read`` stays closed by default; there is
    no ambient/implicit platform visibility)."""
    ws_a = new_uuid7()
    await _seed_workspace(repo_workspace, ws_a)
    await repo_user.add(_ctx(ws_a), _user(workspace_id=ws_a))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        count = (await session.execute(text("SELECT count(*) FROM workspace.users"))).scalar_one()

    assert count == 0


# --------------------------------------------------------------------------- #
# (12) JIT race -- a second add() for the same firebase_uid conflicts         #
# --------------------------------------------------------------------------- #
async def test_user_add_same_firebase_uid_twice_raises_conflict(
    repo_user: SqlUserRepository, repo_workspace: SqlWorkspaceRepository
) -> None:
    """Backs R5's ``ProvisionUserOnFirstLogin`` race handling: the SQL adapter
    translates the ``firebase_uid`` unique-constraint violation into
    ``ConflictError`` so the application layer can catch it and re-read the
    winner."""
    ws_a, ws_b = new_uuid7(), new_uuid7()
    await _seed_workspace(repo_workspace, ws_a)
    await _seed_workspace(repo_workspace, ws_b)
    firebase_uid = f"fb-{new_uuid7()}"
    await repo_user.add(_ctx(ws_a), _user(workspace_id=ws_a, firebase_uid=firebase_uid))

    with pytest.raises(ConflictError):
        await repo_user.add(_ctx(ws_b), _user(workspace_id=ws_b, firebase_uid=firebase_uid))
