"""Workspace aggregates (pure — 06-domain-models §1).

``Workspace`` is the tenant root; ``User`` is the identity mirror created
just-in-time on first Firebase login. Behaviour lives on the aggregate;
mutations touch only state + ``updated_at``. The optimistic ``version`` is
advanced by the repository on ``save`` (02-port-contracts §2). Identifiers are
UUIDv7 text (``str``); timestamps are timezone-aware UTC (DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.workspace.domain.errors import WorkspaceArchivedError
from app.modules.workspace.domain.value_objects import (
    Email,
    UserStatus,
    WorkspaceName,
    WorkspaceStatus,
)


@dataclass(slots=True)
class Workspace:
    """Tenant root. Owns exactly one owner user (INV-W1)."""

    id: str
    owner_user_id: str
    name: WorkspaceName
    status: WorkspaceStatus
    created_at: datetime
    updated_at: datetime
    version: int

    def rename(self, name: WorkspaceName, now: datetime) -> None:
        """Change the display name. Rejected on an archived workspace (INV-W2)."""
        self._guard_not_archived()
        self.name = name
        self.updated_at = now

    def archive(self, now: datetime) -> None:
        """Archive the workspace. Idempotent: archiving an archived one is a no-op."""
        if self.status is WorkspaceStatus.ARCHIVED:
            return
        self.status = WorkspaceStatus.ARCHIVED
        self.updated_at = now

    def _guard_not_archived(self) -> None:
        if self.status is WorkspaceStatus.ARCHIVED:
            raise WorkspaceArchivedError("workspace is archived")


@dataclass(slots=True)
class User:
    """A workspace member. ``id`` is a platform UUIDv7 (DD-02); ``firebase_uid``
    is the unique external identity it maps to (01-data-model §2.1)."""

    id: str
    workspace_id: str
    firebase_uid: str
    email: Email
    display_name: str
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    version: int
