"""Narrow, audited account-state management port for platform administrators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.framework.types import Uuid

AccountStatus = Literal["active", "disabled"]


@dataclass(frozen=True, slots=True)
class PlatformAccountStatusChange:
    """The non-secret result needed to invalidate an account's sessions."""

    user_id: Uuid
    workspace_id: Uuid
    firebase_uid: str
    previous_status: AccountStatus
    status: AccountStatus
    changed: bool
    audit_id: Uuid | None
    changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlatformAccountDeletion:
    """What a completed deletion leaves behind, and what it erased.

    ``firebase_uid`` outlives the identity fields on purpose: it is what the
    caller needs to invalidate the tokens already in the deleted user's hands,
    and keeping it is what makes the deletion terminal rather than a reset —
    the same external identity signing in again meets its own tombstone
    instead of being provisioned a fresh workspace.
    """

    user_id: Uuid
    workspace_id: Uuid
    firebase_uid: str
    deleted: bool
    roles_revoked: tuple[str, ...]
    audit_id: Uuid | None
    deleted_at: datetime | None


class PlatformAccountManager(Protocol):
    """Change exactly one account state and append its audit row atomically."""

    async def set_status(
        self,
        *,
        actor_user_id: Uuid,
        target_user_id: Uuid,
        status: AccountStatus,
        reason: str,
    ) -> PlatformAccountStatusChange: ...

    async def delete(
        self, *, actor_user_id: Uuid, target_user_id: Uuid, reason: str
    ) -> PlatformAccountDeletion: ...
