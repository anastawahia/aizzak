"""Unit tests for access use-cases over an in-memory fake repository.
Pure: the repository is faked. Covers INV-A1 (last owner) and INV-A3 (unique)."""

from __future__ import annotations

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ForbiddenError
from app.modules.access.application.use_cases import AssignRole, Authorization, RevokeRole
from app.modules.access.domain.entities import RoleAssignment
from app.modules.access.domain.value_objects import RoleName


class _FakeAssignments:
    """In-memory ``RoleAssignmentRepository``; queries respect ``ctx.workspace_id``."""

    def __init__(self) -> None:
        self.rows: dict[str, RoleAssignment] = {}

    async def list_for_user(self, ctx: ExecutionContext, user_id: str) -> list[RoleAssignment]:
        return [
            a
            for a in self.rows.values()
            if a.user_id == user_id and a.workspace_id == ctx.workspace_id
        ]

    async def find(
        self, ctx: ExecutionContext, user_id: str, role: RoleName
    ) -> RoleAssignment | None:
        for a in self.rows.values():
            if a.workspace_id == ctx.workspace_id and a.user_id == user_id and a.role is role:
                return a
        return None

    async def add(self, ctx: ExecutionContext, assignment: RoleAssignment) -> None:
        self.rows[assignment.id] = assignment

    async def remove(self, ctx: ExecutionContext, assignment_id: str) -> None:
        self.rows.pop(assignment_id, None)

    async def count_by_role(self, ctx: ExecutionContext, role: RoleName) -> int:
        return sum(
            1 for a in self.rows.values() if a.workspace_id == ctx.workspace_id and a.role is role
        )


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="admin1",
        correlation_id="corr",
        roles=frozenset({"owner"}),
    )


# --------------------------------------------------------------------------- #
# AssignRole                                                                   #
# --------------------------------------------------------------------------- #
async def test_assign_role_creates_and_emits_event() -> None:
    repo = _FakeAssignments()
    assignment, events = await AssignRole(repo).execute(
        _ctx(), user_id="u2", role=RoleName.MEMBER, granted_by="admin1"
    )
    assert assignment.role is RoleName.MEMBER
    assert assignment.workspace_id == "w1"
    assert len(events) == 1
    assert assignment.id in repo.rows


async def test_assign_role_is_idempotent() -> None:
    repo = _FakeAssignments()
    assign = AssignRole(repo)
    first, _ = await assign.execute(_ctx(), user_id="u2", role=RoleName.MEMBER, granted_by="a")
    second, events = await assign.execute(
        _ctx(), user_id="u2", role=RoleName.MEMBER, granted_by="a"
    )
    assert second.id == first.id
    assert events == ()
    assert len(repo.rows) == 1


async def test_assign_platform_admin_is_rejected() -> None:
    with pytest.raises(ForbiddenError):
        await AssignRole(_FakeAssignments()).execute(
            _ctx(), user_id="u2", role=RoleName.PLATFORM_ADMIN, granted_by="a"
        )


# --------------------------------------------------------------------------- #
# RevokeRole                                                                   #
# --------------------------------------------------------------------------- #
async def test_revoke_last_owner_is_blocked() -> None:
    repo = _FakeAssignments()
    await AssignRole(repo).execute(_ctx(), user_id="u1", role=RoleName.OWNER, granted_by="seed")
    with pytest.raises(ConflictError):
        await RevokeRole(repo).execute(_ctx(), user_id="u1", role=RoleName.OWNER)


async def test_revoke_owner_allowed_when_another_exists() -> None:
    repo = _FakeAssignments()
    await AssignRole(repo).execute(_ctx(), user_id="u1", role=RoleName.OWNER, granted_by="s")
    await AssignRole(repo).execute(_ctx(), user_id="u2", role=RoleName.OWNER, granted_by="s")
    revoked, events = await RevokeRole(repo).execute(_ctx(), user_id="u1", role=RoleName.OWNER)
    assert revoked is not None
    assert len(events) == 1
    assert len(repo.rows) == 1


async def test_revoke_missing_is_noop() -> None:
    revoked, events = await RevokeRole(_FakeAssignments()).execute(
        _ctx(), user_id="ux", role=RoleName.MEMBER
    )
    assert revoked is None
    assert events == ()


# --------------------------------------------------------------------------- #
# Authorization (inbound service)                                             #
# --------------------------------------------------------------------------- #
async def test_authorization_roles_of_and_is_allowed() -> None:
    repo = _FakeAssignments()
    await AssignRole(repo).execute(_ctx(), user_id="u2", role=RoleName.MEMBER, granted_by="a")
    authz = Authorization(repo)
    roles = await authz.roles_of(_ctx(), "u2")
    assert roles == frozenset({"member"})
    assert authz.is_allowed(roles, "agents:invoke") is True
    assert authz.is_allowed(roles, "files:delete") is False
    assert authz.is_allowed(roles, "bogus:permission") is False


async def test_authorize_raises_when_permission_missing() -> None:
    repo = _FakeAssignments()
    await AssignRole(repo).execute(_ctx(), user_id="u2", role=RoleName.VIEWER, granted_by="a")
    authz = Authorization(repo)
    await authz.authorize(_ctx(), "u2", "files:read")  # allowed → no raise
    with pytest.raises(ForbiddenError):
        await authz.authorize(_ctx(), "u2", "files:write")
