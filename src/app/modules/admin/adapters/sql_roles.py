"""PostgreSQL adapter for atomic, audited platform role changes."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import Column, DateTime, MetaData, Table, Text, Uuid, insert, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.clock import utc_now
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.types import Uuid as AppUuid
from app.modules.admin.ports.roles import (
    PlatformRoleChange,
    PlatformRoleManager,
    WorkspaceRole,
)

_metadata = MetaData()
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

users = Table(
    "users",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    # capacity-plan 1.1 — read for the SAME reason `sql_accounts.py` already
    # reads it: it is what the caller needs to invalidate the sessions this
    # change makes stale. The platform-admin write role already holds SELECT
    # on it (`app.ops.provision`), so this adds no grant.
    Column("firebase_uid", Text, nullable=False),
    schema="workspace",
)
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
admin_audit_log = Table(
    "admin_audit_log",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("actor_user_id", _uuid_col, nullable=False),
    Column("target_user_id", _uuid_col, nullable=False),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("action", Text, nullable=False),
    Column("previous_status", Text, nullable=True),
    Column("new_status", Text, nullable=True),
    Column("reason", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema="platform",
)

PlatformAdminWriteSessionProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class SqlPlatformRoleManager:
    """Write an assignment and its audit row under the admin-write sentinel."""

    def __init__(self, platform_admin_write_session: PlatformAdminWriteSessionProvider) -> None:
        self._platform_admin_write_session = platform_admin_write_session

    async def set_workspace_role(
        self,
        *,
        actor_user_id: AppUuid,
        target_user_id: AppUuid,
        role: WorkspaceRole,
        enabled: bool,
        reason: str,
    ) -> PlatformRoleChange:
        return await self._set_role(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            role=role,
            enabled=enabled,
            reason=reason,
            action="role.workspace.changed",
            scope="workspace",
        )

    async def set_platform_admin(
        self,
        *,
        actor_user_id: AppUuid,
        target_user_id: AppUuid,
        enabled: bool,
        reason: str,
    ) -> PlatformRoleChange:
        return await self._set_role(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            role="platform_admin",
            enabled=enabled,
            reason=reason,
            action="role.platform_admin.changed",
            scope="platform",
        )

    async def _set_role(
        self,
        *,
        actor_user_id: AppUuid,
        target_user_id: AppUuid,
        role: str,
        enabled: bool,
        reason: str,
        action: str,
        scope: str,
    ) -> PlatformRoleChange:
        async with self._platform_admin_write_session() as session:
            target = (
                (
                    await session.execute(
                        select(users.c.id, users.c.workspace_id, users.c.firebase_uid)
                        .where(users.c.id == target_user_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if target is None:
                raise AppError("platform user not found", code="common.not_found")
            workspace_id = target["workspace_id"]
            firebase_uid = target["firebase_uid"]
            existing = (
                (
                    await session.execute(
                        select(role_assignments.c.id)
                        .where(
                            role_assignments.c.workspace_id == workspace_id,
                            role_assignments.c.user_id == target_user_id,
                            role_assignments.c.role == role,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            currently_enabled = existing is not None
            if currently_enabled == enabled:
                return PlatformRoleChange(
                    user_id=target_user_id,
                    workspace_id=workspace_id,
                    firebase_uid=firebase_uid,
                    role=role,
                    enabled=enabled,
                    changed=False,
                    audit_id=None,
                    changed_at=None,
                )

            if role == "owner" and not enabled:
                # Lock every current owner row before deciding. Concurrent owner
                # removals serialize here, so the workspace cannot be left ownerless.
                owner_ids = (
                    (
                        await session.execute(
                            select(role_assignments.c.id)
                            .where(
                                role_assignments.c.workspace_id == workspace_id,
                                role_assignments.c.role == "owner",
                            )
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(owner_ids) <= 1:
                    raise ConflictError("cannot revoke the last owner")

            changed_at = utc_now()
            if enabled:
                await session.execute(
                    insert(role_assignments).values(
                        id=new_uuid7(),
                        workspace_id=workspace_id,
                        user_id=target_user_id,
                        role=role,
                        granted_by=actor_user_id,
                        created_at=changed_at,
                    )
                )
            else:
                assert existing is not None  # established by the idempotency branch above
                await session.execute(
                    role_assignments.delete().where(role_assignments.c.id == existing["id"])
                )

            audit_id = new_uuid7()
            await session.execute(
                insert(admin_audit_log).values(
                    id=audit_id,
                    actor_user_id=actor_user_id,
                    target_user_id=target_user_id,
                    workspace_id=workspace_id,
                    action=action,
                    reason=reason,
                    details={"scope": scope, "role": role, "enabled": enabled},
                    created_at=changed_at,
                )
            )
            return PlatformRoleChange(
                user_id=target_user_id,
                workspace_id=workspace_id,
                firebase_uid=firebase_uid,
                role=role,
                enabled=enabled,
                changed=True,
                audit_id=audit_id,
                changed_at=changed_at,
            )


def _conforms(manager: SqlPlatformRoleManager) -> PlatformRoleManager:
    """Static structural proof for the driven port."""
    return manager
