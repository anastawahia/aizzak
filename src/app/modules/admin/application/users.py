"""Read-only platform user-directory use case."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ForbiddenError, ValidationError
from app.framework.types import Uuid
from app.modules.admin.ports.accounts import (
    AccountStatus,
    PlatformAccountDeletion,
    PlatformAccountManager,
    PlatformAccountStatusChange,
)
from app.modules.admin.ports.directory import PlatformUserDirectory, PlatformUserPage
from app.modules.admin.ports.roles import PlatformRoleChange, PlatformRoleManager, WorkspaceRole

_MIN_REASON_LENGTH = 3
_MAX_REASON_LENGTH = 500


class ListPlatformUsers:
    """Delegate the bounded page to the deliberately platform-scoped port."""

    def __init__(self, directory: PlatformUserDirectory) -> None:
        self._directory = directory

    async def execute(
        self, *, limit: int, offset: int, search: str | None = None
    ) -> PlatformUserPage:
        # A box the operator typed into and cleared arrives as "" or "   "; that
        # is the unfiltered directory, not a search for the empty string.
        cleaned = (search or "").strip()
        return await self._directory.list_users(limit=limit, offset=offset, search=cleaned or None)


class SetPlatformAccountStatus:
    """Perform one guarded, auditable platform-account state transition."""

    def __init__(self, accounts: PlatformAccountManager) -> None:
        self._accounts = accounts

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        target_user_id: Uuid,
        status: AccountStatus,
        reason: str,
    ) -> PlatformAccountStatusChange:
        if ctx.user_id is None:
            raise ForbiddenError("authenticated user is required")
        if target_user_id == ctx.user_id:
            raise ForbiddenError("an administrator cannot change their own account status")
        cleaned_reason = reason.strip()
        if not _MIN_REASON_LENGTH <= len(cleaned_reason) <= _MAX_REASON_LENGTH:
            raise ValidationError(
                f"reason must be between {_MIN_REASON_LENGTH} and {_MAX_REASON_LENGTH} characters"
            )
        return await self._accounts.set_status(
            actor_user_id=ctx.user_id,
            target_user_id=target_user_id,
            status=status,
            reason=cleaned_reason,
        )


class DeletePlatformUser:
    """Retire one account irreversibly, never the caller's own."""

    def __init__(self, accounts: PlatformAccountManager) -> None:
        self._accounts = accounts

    async def execute(
        self, ctx: ExecutionContext, *, target_user_id: Uuid, reason: str
    ) -> PlatformAccountDeletion:
        if ctx.user_id is None:
            raise ForbiddenError("authenticated user is required")
        if target_user_id == ctx.user_id:
            # Stricter than the status route's version of this rule, and for a
            # reason the wording hides: a self-disable can be undone by any
            # other administrator, while this cannot be undone by anyone.
            raise ForbiddenError("an administrator cannot delete their own account")
        cleaned_reason = reason.strip()
        if not _MIN_REASON_LENGTH <= len(cleaned_reason) <= _MAX_REASON_LENGTH:
            raise ValidationError(
                f"reason must be between {_MIN_REASON_LENGTH} and {_MAX_REASON_LENGTH} characters"
            )
        return await self._accounts.delete(
            actor_user_id=ctx.user_id,
            target_user_id=target_user_id,
            reason=cleaned_reason,
        )


class SetPlatformWorkspaceRole:
    """Grant or revoke one workspace-local role through the platform surface."""

    def __init__(self, roles: PlatformRoleManager) -> None:
        self._roles = roles

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        target_user_id: Uuid,
        role: WorkspaceRole,
        enabled: bool,
        reason: str,
    ) -> PlatformRoleChange:
        return await _set_platform_role(
            ctx,
            target_user_id=target_user_id,
            enabled=enabled,
            reason=reason,
            mutate=lambda actor, cleaned: self._roles.set_workspace_role(
                actor_user_id=actor,
                target_user_id=target_user_id,
                role=role,
                enabled=enabled,
                reason=cleaned,
            ),
        )


class SetPlatformAdminRole:
    """Grant or revoke only the cross-tenant ``platform_admin`` role."""

    def __init__(self, roles: PlatformRoleManager) -> None:
        self._roles = roles

    async def execute(
        self, ctx: ExecutionContext, *, target_user_id: Uuid, enabled: bool, reason: str
    ) -> PlatformRoleChange:
        return await _set_platform_role(
            ctx,
            target_user_id=target_user_id,
            enabled=enabled,
            reason=reason,
            mutate=lambda actor, cleaned: self._roles.set_platform_admin(
                actor_user_id=actor,
                target_user_id=target_user_id,
                enabled=enabled,
                reason=cleaned,
            ),
        )


RoleMutation = Callable[[Uuid, str], Awaitable[PlatformRoleChange]]


async def _set_platform_role(
    ctx: ExecutionContext,
    *,
    target_user_id: Uuid,
    enabled: bool,
    reason: str,
    mutate: RoleMutation,
) -> PlatformRoleChange:
    if ctx.user_id is None:
        raise ForbiddenError("authenticated user is required")
    if target_user_id == ctx.user_id:
        raise ForbiddenError("an administrator cannot change their own roles")
    cleaned_reason = reason.strip()
    if not _MIN_REASON_LENGTH <= len(cleaned_reason) <= _MAX_REASON_LENGTH:
        raise ValidationError(
            f"reason must be between {_MIN_REASON_LENGTH} and {_MAX_REASON_LENGTH} characters"
        )
    return await mutate(ctx.user_id, cleaned_reason)


@dataclass(frozen=True, slots=True)
class PlatformAdminUseCases:
    """The only administration face available to the HTTP API."""

    users: ListPlatformUsers
    accounts: SetPlatformAccountStatus
    deletions: DeletePlatformUser
    workspace_roles: SetPlatformWorkspaceRole
    platform_roles: SetPlatformAdminRole
