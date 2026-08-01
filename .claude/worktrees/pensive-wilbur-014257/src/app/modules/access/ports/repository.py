"""Access persistence port (02-port-contracts §2).

Outbound repository for role assignments. Every method is workspace-scoped via
``ctx`` (RLS + ``WHERE workspace_id``). ``count_by_role`` backs the last-owner
invariant (INV-A1); ``find`` backs the uniqueness invariant (INV-A3).
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.access.domain.entities import RoleAssignment
from app.modules.access.domain.value_objects import RoleName


class RoleAssignmentRepository(Protocol):
    """Tenant-scoped persistence for role assignments."""

    async def list_for_user(self, ctx: ExecutionContext, user_id: Uuid) -> list[RoleAssignment]: ...

    async def find(
        self, ctx: ExecutionContext, user_id: Uuid, role: RoleName
    ) -> RoleAssignment | None: ...

    async def add(self, ctx: ExecutionContext, assignment: RoleAssignment) -> None: ...

    async def remove(self, ctx: ExecutionContext, assignment_id: Uuid) -> None: ...

    async def count_by_role(self, ctx: ExecutionContext, role: RoleName) -> int: ...
