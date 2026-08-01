"""Memory domain events (pure — 06-domain-models §5, 04-event-catalog §5).

In-memory domain events: plain frozen records with no shared base class (the
domain imports no framework code). Dispatch to the event bus / outbox happens
in the application and infrastructure layers, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MemoryStored:
    """A memory item was recorded (possibly still pending vector indexing)."""

    memory_id: str
    workspace_id: str
    agent_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryForgotten:
    """A memory item was soft-deleted; its Qdrant point is cleaned up eventually (INV-M2)."""

    memory_id: str
    occurred_at: datetime


MemoryEvent = MemoryStored | MemoryForgotten
