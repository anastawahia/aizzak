"""Access use-cases (06-domain-models §2).

``AssignRole`` / ``RevokeRole`` mutate assignments while enforcing the RBAC
invariants that need persistence queries (INV-A1 last-owner, INV-A3 uniqueness,
and the v1 rule that ``platform_admin`` is seeded out-of-band, 05 §1.5).
``Authorization`` implements the ``AuthorizationService`` inbound port and adds a
raising ``authorize`` helper (the ``AuthorizeAction`` use-case). Domain enums are
parsed from the string surface at this boundary; unknown values deny by default.
"""

from __future__ import annotations

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ForbiddenError
from app.framework.identifiers import new_uuid7
from app.framework.types import Uuid
from app.modules.access.domain.entities import RoleAssignment
from app.modules.access.domain.events import AccessEvent, RoleAssigned, RoleRevoked
from app.modules.access.domain.value_objects import (
    Permission,
    RoleName,
    permission_granted,
)
from app.modules.access.ports.repository import RoleAssignmentRepository


class AssignRole:
    """Grant a role to a user. Idempotent on ``(user, role)`` (INV-A3)."""

    def __init__(self, assignments: RoleAssignmentRepository) -> None:
        self._assignments = assignments

    async def execute(
        self, ctx: ExecutionContext, *, user_id: Uuid, role: RoleName, granted_by: Uuid
    ) -> tuple[RoleAssignment, tuple[AccessEvent, ...]]:
        if role is RoleName.PLATFORM_ADMIN:
            raise ForbiddenError("platform_admin is seeded out-of-band, not assignable via API")
        existing = await self._assignments.find(ctx, user_id, role)
        if existing is not None:
            return existing, ()
        now = utc_now()
        assignment = RoleAssignment(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            user_id=user_id,
            role=role,
            granted_by=granted_by,
            created_at=now,
        )
        await self._assignments.add(ctx, assignment)
        event = RoleAssigned(assignment.id, ctx.workspace_id, user_id, role.value, now)
        return assignment, (event,)

    async def seed_owner(self, ctx: ExecutionContext, user_id: Uuid) -> None:
        """Grant ``owner`` to the user a workspace was just minted for (05 §1.5).

        The ``RoleSeeding`` inbound port's implementation (structural match, 6.4):
        the authentication path holds THIS method and not ``execute``, so it can
        create the first owner of a fresh workspace and cannot grant any other
        role anywhere. ``granted_by`` is the user itself — at first login there
        is no other actor in the workspace, and recording the platform as grantor
        would mean inventing an id for a user that does not exist.

        The event is dropped on purpose: ``RoleAssigned`` is an INTERNAL domain
        event (04 §5 promotes no ``access`` event to a stream), and every other
        caller in this codebase drops it the same way.
        """
        await self.execute(ctx, user_id=user_id, role=RoleName.OWNER, granted_by=user_id)


class RevokeRole:
    """Revoke a role from a user. Idempotent, and refuses the last owner (INV-A1)."""

    def __init__(self, assignments: RoleAssignmentRepository) -> None:
        self._assignments = assignments

    async def execute(
        self, ctx: ExecutionContext, *, user_id: Uuid, role: RoleName
    ) -> tuple[RoleAssignment | None, tuple[AccessEvent, ...]]:
        existing = await self._assignments.find(ctx, user_id, role)
        if existing is None:
            return None, ()
        if role is RoleName.OWNER:
            owners = await self._assignments.count_by_role(ctx, RoleName.OWNER)
            if owners <= 1:
                raise ConflictError("cannot revoke the last owner")
        await self._assignments.remove(ctx, existing.id)
        event = RoleRevoked(existing.id, ctx.workspace_id, user_id, role.value, utc_now())
        return existing, (event,)


class Authorization:
    """Implements ``AuthorizationService`` (02 §2) and the ``AuthorizeAction`` helper."""

    def __init__(self, assignments: RoleAssignmentRepository) -> None:
        self._assignments = assignments

    async def roles_of(self, ctx: ExecutionContext, user_id: Uuid) -> frozenset[str]:
        assignments = await self._assignments.list_for_user(ctx, user_id)
        return frozenset(a.role.value for a in assignments)

    def is_allowed(self, roles: frozenset[str], permission: str) -> bool:
        perm = _parse_permission(permission)
        if perm is None:
            return False
        parsed = frozenset(role for role in map(_parse_role, roles) if role is not None)
        return permission_granted(parsed, perm)

    async def authorize(self, ctx: ExecutionContext, user_id: Uuid, permission: str) -> None:
        if not self.is_allowed(await self.roles_of(ctx, user_id), permission):
            raise ForbiddenError(f"missing permission: {permission}")


def _parse_role(value: str) -> RoleName | None:
    try:
        return RoleName(value)
    except ValueError:
        return None


def _parse_permission(value: str) -> Permission | None:
    try:
        return Permission(value)
    except ValueError:
        return None
