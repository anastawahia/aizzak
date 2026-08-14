"""Read-only, platform-wide directory port for platform administrators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PlatformUser:
    """The safe identity fields the platform directory may expose."""

    id: str
    workspace_id: str
    email: str
    display_name: str
    status: str
    roles: tuple[str, ...]
    last_seen_at: datetime | None
    online: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlatformUserPage:
    """One page plus the counts of the set it was drawn from.

    Under a search the four counts describe the *matching* users, not the whole
    platform: ``total`` is what the caller pages through, so an unfiltered total
    beside a filtered page would hand the caller a next offset for rows it can
    never receive.
    """

    items: tuple[PlatformUser, ...]
    total: int
    active: int
    disabled: int
    online: int


class PlatformUserDirectory(Protocol):
    """A read-only cross-tenant directory, callable only after RBAC."""

    async def list_users(
        self, *, limit: int, offset: int, search: str | None = None
    ) -> PlatformUserPage: ...
