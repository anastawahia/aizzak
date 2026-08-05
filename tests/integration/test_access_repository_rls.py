"""Live-Postgres tests for ``SqlRoleAssignmentRepository`` + RLS
(09-testing-strategy §3).

Runs against the real local Compose PostgreSQL 16 (see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. An assignment builder mirrors
``tests/unit/test_access_use_cases.py``, except every id is a *real*
``new_uuid7()`` string rather than an arbitrary label. ``access.role_assignments``
carries no physical FK to ``workspace.users`` (``user_id`` is a logical
reference only, 01 §2.2), so tests here need no parent workspace/user rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.modules.access.adapters.sql_repository import SqlRoleAssignmentRepository
from app.modules.access.domain.entities import RoleAssignment
from app.modules.access.domain.value_objects import RoleName

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


def _assignment(
    *,
    workspace_id: str,
    assignment_id: str | None = None,
    user_id: str | None = None,
    role: RoleName = RoleName.MEMBER,
    granted_by: str | None = None,
    now: datetime | None = None,
) -> RoleAssignment:
    return RoleAssignment(
        id=assignment_id or new_uuid7(),
        workspace_id=workspace_id,
        user_id=user_id or new_uuid7(),
        role=role,
        granted_by=granted_by or new_uuid7(),
        created_at=now or utc_now(),
    )


# --------------------------------------------------------------------------- #
# (1) round-trip add/find                                                    #
# --------------------------------------------------------------------------- #
async def test_add_then_find_round_trips(repo_access: SqlRoleAssignmentRepository) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    user_id = new_uuid7()
    assignment = _assignment(workspace_id=ws, user_id=user_id, role=RoleName.MEMBER)

    await repo_access.add(ctx, assignment)
    found = await repo_access.find(ctx, user_id, RoleName.MEMBER)

    assert found is not None
    assert found.id == assignment.id
    assert found.workspace_id == ws
    assert found.user_id == user_id
    assert found.role is RoleName.MEMBER
    assert found.granted_by == assignment.granted_by
    assert found.created_at == assignment.created_at


# --------------------------------------------------------------------------- #
# (2) find missing -> None                                                   #
# --------------------------------------------------------------------------- #
async def test_find_missing_returns_none(repo_access: SqlRoleAssignmentRepository) -> None:
    assert await repo_access.find(_ctx(new_uuid7()), new_uuid7(), RoleName.MEMBER) is None


# --------------------------------------------------------------------------- #
# (3) list_for_user -- only this tenant's rows, ordered by created_at        #
# --------------------------------------------------------------------------- #
async def test_list_for_user_returns_only_this_tenants_assignments_ordered(
    repo_access: SqlRoleAssignmentRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    user_id = new_uuid7()
    now = utc_now()
    first = _assignment(workspace_id=ws_a, user_id=user_id, role=RoleName.VIEWER, now=now)
    second = _assignment(
        workspace_id=ws_a, user_id=user_id, role=RoleName.MEMBER, now=now + timedelta(seconds=1)
    )
    await repo_access.add(_ctx(ws_a), first)
    await repo_access.add(_ctx(ws_a), second)
    # A same-user assignment under a DIFFERENT tenant must never show up here.
    await repo_access.add(
        _ctx(ws_b), _assignment(workspace_id=ws_b, user_id=user_id, role=RoleName.OWNER)
    )

    found = await repo_access.list_for_user(_ctx(ws_a), user_id)

    assert [a.id for a in found] == [first.id, second.id]


# --------------------------------------------------------------------------- #
# (4) count_by_role -- scoped per tenant, exactly-1 owner case               #
# --------------------------------------------------------------------------- #
async def test_count_by_role_counts_within_tenant_only(
    repo_access: SqlRoleAssignmentRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    await repo_access.add(_ctx(ws_a), _assignment(workspace_id=ws_a, role=RoleName.OWNER))
    assert await repo_access.count_by_role(_ctx(ws_a), RoleName.OWNER) == 1

    await repo_access.add(_ctx(ws_a), _assignment(workspace_id=ws_a, role=RoleName.MEMBER))
    await repo_access.add(_ctx(ws_b), _assignment(workspace_id=ws_b, role=RoleName.OWNER))

    assert await repo_access.count_by_role(_ctx(ws_a), RoleName.OWNER) == 1
    assert await repo_access.count_by_role(_ctx(ws_a), RoleName.MEMBER) == 1
    assert await repo_access.count_by_role(_ctx(ws_b), RoleName.OWNER) == 1


# --------------------------------------------------------------------------- #
# (5) remove + idempotent second remove                                     #
# --------------------------------------------------------------------------- #
async def test_remove_then_idempotent_second_remove(
    repo_access: SqlRoleAssignmentRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    assignment = _assignment(workspace_id=ws)
    await repo_access.add(ctx, assignment)

    await repo_access.remove(ctx, assignment.id)
    assert await repo_access.find(ctx, assignment.user_id, assignment.role) is None

    await repo_access.remove(ctx, assignment.id)  # idempotent: 0 rows, no error
    assert await repo_access.find(ctx, assignment.user_id, assignment.role) is None


# --------------------------------------------------------------------------- #
# (6) duplicate add -> ConflictError (uq_assignment)                         #
# --------------------------------------------------------------------------- #
async def test_duplicate_add_raises_conflict(repo_access: SqlRoleAssignmentRepository) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    user_id = new_uuid7()
    await repo_access.add(ctx, _assignment(workspace_id=ws, user_id=user_id, role=RoleName.MEMBER))

    with pytest.raises(ConflictError):
        await repo_access.add(
            ctx, _assignment(workspace_id=ws, user_id=user_id, role=RoleName.MEMBER)
        )


# --------------------------------------------------------------------------- #
# (7) two-tenant isolation                                                   #
# --------------------------------------------------------------------------- #
async def test_two_tenant_isolation(repo_access: SqlRoleAssignmentRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    user_id = new_uuid7()
    await repo_access.add(
        _ctx(ws_a), _assignment(workspace_id=ws_a, user_id=user_id, role=RoleName.MEMBER)
    )

    assert await repo_access.find(_ctx(ws_b), user_id, RoleName.MEMBER) is None
    assert await repo_access.find(_ctx(ws_a), user_id, RoleName.MEMBER) is not None
    assert await repo_access.list_for_user(_ctx(ws_b), user_id) == []


# --------------------------------------------------------------------------- #
# (8) forged cross-tenant write -> RLS WITH CHECK rejects it                 #
# --------------------------------------------------------------------------- #
async def test_add_with_forged_workspace_id_is_rejected_by_rls_with_check(
    repo_access: SqlRoleAssignmentRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    forged = _assignment(workspace_id=ws_b)  # claims tenant B

    with pytest.raises(AppError) as exc_info:
        await repo_access.add(_ctx(ws_a), forged)  # added under tenant A's context

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)
    assert await repo_access.find(_ctx(ws_b), forged.user_id, forged.role) is None


# --------------------------------------------------------------------------- #
# (9) RLS: no context set at all -> zero rows, no exception                  #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_set_returns_zero_rows(
    repo_access: SqlRoleAssignmentRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws = new_uuid7()
    await repo_access.add(_ctx(ws), _assignment(workspace_id=ws))

    async with sessionmaker_app() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM access.role_assignments"))
        ).scalar_one()

    assert count == 0


# --------------------------------------------------------------------------- #
# (10) empty-string GUC discriminator -- what the NULLIF fix buys            #
# --------------------------------------------------------------------------- #
async def test_empty_string_guc_yields_zero_rows_not_an_exception(
    repo_access: SqlRoleAssignmentRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws = new_uuid7()
    await repo_access.add(_ctx(ws), _assignment(workspace_id=ws))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        count = (
            await session.execute(text("SELECT count(*) FROM access.role_assignments"))
        ).scalar_one()
        assert count == 0

        await session.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws})
        count = (
            await session.execute(text("SELECT count(*) FROM access.role_assignments"))
        ).scalar_one()
        assert count == 1
