"""Narrow, audited role-management port for platform administrators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.framework.types import Uuid

WorkspaceRole = Literal["owner", "admin", "member", "viewer"]


@dataclass(frozen=True, slots=True)
class PlatformRoleChange:
    """The durable result of one platform-admin role transition."""

    user_id: Uuid
    workspace_id: Uuid
    role: str
    enabled: bool
    changed: bool
    audit_id: Uuid | None
    changed_at: datetime | None


class PlatformRoleManager(Protocol):
    """Change one role and append its audit record in the same transaction."""

    async def set_workspace_role(
        self,
        *,
        actor_user_id: Uuid,
        target_user_id: Uuid,
        role: WorkspaceRole,
        enabled: bool,
        reason: str,
    ) -> PlatformRoleChange: ...

    async def set_platform_admin(
        self,
        *,
        actor_user_id: Uuid,
        target_user_id: Uuid,
        enabled: bool,
        reason: str,
    ) -> PlatformRoleChange: ...
