"""Access aggregate — role assignments (pure — 06-domain-models §2).

``RoleAssignment`` binds a user to a role within a workspace. It is create-or-
delete only (no in-place mutation, hence no ``version``); uniqueness on
``(workspace_id, user_id, role)`` (INV-A3) and the last-owner rule (INV-A1) are
enforced by the application use-cases, which need repository queries.
Identifiers are UUIDv7 text; timestamps are UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.access.domain.value_objects import RoleName


@dataclass(slots=True)
class RoleAssignment:
    """A user's role within one workspace."""

    id: str
    workspace_id: str
    user_id: str
    role: RoleName
    granted_by: str
    created_at: datetime
