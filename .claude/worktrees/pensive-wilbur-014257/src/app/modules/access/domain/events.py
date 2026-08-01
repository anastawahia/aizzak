"""Access domain events (pure — 06-domain-models §2, 04-event-catalog §5).

In-memory domain events; ``role`` is carried as its string value for stable
serialization when these are later dispatched to the event bus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RoleAssigned:
    """A role was granted to a user in a workspace."""

    assignment_id: str
    workspace_id: str
    user_id: str
    role: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RoleRevoked:
    """A role was revoked from a user in a workspace."""

    assignment_id: str
    workspace_id: str
    user_id: str
    role: str
    occurred_at: datetime


AccessEvent = RoleAssigned | RoleRevoked
