"""Memory aggregate (pure — 06-domain-models §5).

``MemoryItem`` is per-agent (INV-M1): semantic/episodic content plus an
optional pointer to its indexed Qdrant point. The backing table carries no
``version``/``updated_at`` (01-data-model §2.5) — it is append + soft-delete
only, so behaviour here is limited to attaching the vector once a (possibly
deferred) indexing completes, and forgetting (soft-delete). INV-M2 (deleting
the matching Qdrant point) is eventual consistency driven by the
``MemoryForgotten`` event emitted at the application boundary, not by this
aggregate. Identifiers are UUIDv7 text; timestamps are timezone-aware UTC
(DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.memory.domain.errors import MemoryError
from app.modules.memory.domain.value_objects import AgentKey, MemoryKind, VectorRef


@dataclass(slots=True)
class MemoryItem:
    """A single memory item, always bound to one agent (INV-M1)."""

    id: str
    workspace_id: str
    agent_key: AgentKey
    kind: MemoryKind
    content: str
    vector_ref: VectorRef | None
    salience: float
    created_at: datetime
    deleted_at: datetime | None

    def attach_vector(self, vector_ref: VectorRef) -> None:
        """Record the Qdrant point once the (possibly deferred) indexing completes."""
        if self.deleted_at is not None:
            raise MemoryError("cannot attach a vector to a forgotten memory item")
        self.vector_ref = vector_ref

    def forget(self, now: datetime) -> None:
        """Soft-delete. Idempotent: forgetting an already-forgotten item is a no-op."""
        if self.deleted_at is not None:
            return
        self.deleted_at = now
