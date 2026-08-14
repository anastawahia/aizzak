"""PostgreSQL adapter for audited platform-account status transitions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import cast

from sqlalchemy import Column, DateTime, MetaData, Table, Text, Uuid, func, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.clock import utc_now
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.types import Uuid as AppUuid
from app.modules.admin.ports.accounts import (
    AccountStatus,
    PlatformAccountDeletion,
    PlatformAccountManager,
    PlatformAccountStatusChange,
)

_metadata = MetaData()
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

users = Table(
    "users",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("firebase_uid", Text, nullable=False),
    Column("email", Text, nullable=False),
    Column("display_name", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("deleted_at", _timestamptz, nullable=True),
    schema="workspace",
)
role_assignments = Table(
    "role_assignments",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("user_id", _uuid_col, nullable=False),
    Column("role", Text, nullable=False),
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
    Column("previous_status", Text, nullable=False),
    Column("new_status", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema="platform",
)

_PLATFORM_ADMIN_ROLE = "platform_admin"


def _redacted_email(user_id: AppUuid) -> str:
    """The address a tombstone carries instead of a person's.

    ``.invalid`` is reserved by RFC 2606, so this can never be delivered to,
    and the id keeps two tombstones distinguishable in an operator's eyes
    without either of them naming anyone. It still parses as an address
    because the identity row is hydrated through the ``Email`` value object
    on every login attempt that meets this tombstone.
    """
    return f"deleted+{user_id}@removed.invalid"


PlatformAdminWriteSessionProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class SqlPlatformAccountManager:
    """Lock, transition and audit one account in one admin-write transaction."""

    def __init__(self, platform_admin_write_session: PlatformAdminWriteSessionProvider) -> None:
        self._platform_admin_write_session = platform_admin_write_session

    async def set_status(
        self,
        *,
        actor_user_id: AppUuid,
        target_user_id: AppUuid,
        status: AccountStatus,
        reason: str,
    ) -> PlatformAccountStatusChange:
        async with self._platform_admin_write_session() as session:
            row = (
                (
                    await session.execute(
                        select(
                            users.c.id,
                            users.c.workspace_id,
                            users.c.firebase_uid,
                            users.c.status,
                        )
                        .where(users.c.id == target_user_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise AppError("platform user not found", code="common.not_found")
            previous_status = cast(AccountStatus, row["status"])
            if previous_status == status:
                return PlatformAccountStatusChange(
                    user_id=row["id"],
                    workspace_id=row["workspace_id"],
                    firebase_uid=row["firebase_uid"],
                    previous_status=previous_status,
                    status=status,
                    changed=False,
                    audit_id=None,
                    changed_at=None,
                )

            changed = (
                await session.execute(
                    update(users)
                    .where(users.c.id == target_user_id)
                    .values(status=status)
                    .returning(users.c.updated_at)
                )
            ).scalar_one()
            audit_id = new_uuid7()
            await session.execute(
                insert(admin_audit_log).values(
                    id=audit_id,
                    actor_user_id=actor_user_id,
                    target_user_id=target_user_id,
                    workspace_id=row["workspace_id"],
                    action="account.status_changed",
                    previous_status=previous_status,
                    new_status=status,
                    reason=reason,
                    created_at=changed,
                )
            )
            return PlatformAccountStatusChange(
                user_id=row["id"],
                workspace_id=row["workspace_id"],
                firebase_uid=row["firebase_uid"],
                previous_status=previous_status,
                status=status,
                changed=True,
                audit_id=audit_id,
                changed_at=changed,
            )

    async def delete(
        self, *, actor_user_id: AppUuid, target_user_id: AppUuid, reason: str
    ) -> PlatformAccountDeletion:
        """Retire one account for good: erase the person, keep the tombstone.

        The row survives only because the audit ledger references it (see
        ``migrations/versions/workspace/0007_platform_admin_delete.py``); the
        identity fields on it do not. Nothing this deletes can be restored by
        re-enabling, which is precisely what separates it from ``set_status``.
        """
        async with self._platform_admin_write_session() as session:
            row = (
                (
                    await session.execute(
                        select(
                            users.c.id,
                            users.c.workspace_id,
                            users.c.firebase_uid,
                            users.c.status,
                            users.c.deleted_at,
                        )
                        .where(users.c.id == target_user_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise AppError("platform user not found", code="common.not_found")
            if row["status"] == "deleted":
                # Repeating a deletion is not a second deletion: the tombstone
                # already says what happened and when, and a fresh audit row
                # would date an event that is over.
                return PlatformAccountDeletion(
                    user_id=row["id"],
                    workspace_id=row["workspace_id"],
                    firebase_uid=row["firebase_uid"],
                    deleted=False,
                    roles_revoked=(),
                    audit_id=None,
                    deleted_at=row["deleted_at"],
                )
            previous_status = cast(AccountStatus, row["status"])

            # Lock every platform-admin assignment before deciding, exactly as
            # the role manager locks every owner: two administrators deleting
            # each other at the same moment would otherwise each see the other
            # as a survivor and leave the platform with no administrator at
            # all. Serialized here, the loser reads a set the winner has
            # already emptied of its target and is refused.
            admin_ids = (
                (
                    await session.execute(
                        select(role_assignments.c.user_id)
                        .where(role_assignments.c.role == _PLATFORM_ADMIN_ROLE)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if target_user_id in admin_ids:
                survivors = (
                    await session.execute(
                        select(func.count(users.c.id)).where(
                            users.c.id.in_([uid for uid in admin_ids if uid != target_user_id]),
                            users.c.status == "active",
                        )
                    )
                ).scalar_one()
                if survivors == 0:
                    raise ConflictError("cannot delete the last platform administrator")

            deleted_at = utc_now()
            revoked = (
                (
                    await session.execute(
                        role_assignments.delete()
                        .where(role_assignments.c.user_id == target_user_id)
                        .returning(role_assignments.c.role)
                    )
                )
                .scalars()
                .all()
            )
            await session.execute(
                update(users)
                .where(users.c.id == target_user_id)
                .values(
                    status="deleted",
                    deleted_at=deleted_at,
                    email=_redacted_email(target_user_id),
                    display_name=None,
                )
            )
            audit_id = new_uuid7()
            await session.execute(
                insert(admin_audit_log).values(
                    id=audit_id,
                    actor_user_id=actor_user_id,
                    target_user_id=target_user_id,
                    workspace_id=row["workspace_id"],
                    action="account.deleted",
                    previous_status=previous_status,
                    new_status="deleted",
                    reason=reason,
                    # The erased address is deliberately absent: an audit row
                    # that quotes what it certifies was erased has not erased it.
                    details={"redacted": True, "roles_revoked": sorted(revoked)},
                    created_at=deleted_at,
                )
            )
            return PlatformAccountDeletion(
                user_id=row["id"],
                workspace_id=row["workspace_id"],
                firebase_uid=row["firebase_uid"],
                deleted=True,
                roles_revoked=tuple(sorted(revoked)),
                audit_id=audit_id,
                deleted_at=deleted_at,
            )


def _conforms(manager: SqlPlatformAccountManager) -> PlatformAccountManager:
    """Static structural proof for the driven port."""
    return manager
