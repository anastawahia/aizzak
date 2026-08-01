"""Workspace domain events (pure — 06-domain-models §1, 04-event-catalog §5).

In-memory domain events: plain frozen records with no shared base class (the
domain imports no framework code). Dispatch to the event bus / outbox happens
in the application and infrastructure layers, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WorkspaceCreated:
    """A new workspace was provisioned for its owner (JIT first login)."""

    workspace_id: str
    owner_user_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceArchived:
    """A workspace was archived."""

    workspace_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class UserProvisioned:
    """A user identity was mirrored into a workspace on first token verify."""

    user_id: str
    workspace_id: str
    email: str
    occurred_at: datetime


WorkspaceEvent = WorkspaceCreated | WorkspaceArchived | UserProvisioned
