"""Knowledge aggregate + entity (pure — 06-domain-models §7).

``Document`` is the ingestion-lifecycle aggregate root; ``Chunk`` is the
immutable per-window slice produced once indexing completes (persisted
alongside its owning ``Document`` — see ``ports/repository.py``). Status
transitions are one-way and terminal (INV-K2: ``pending -> indexing ->
(indexed | failed)``); INV-K3 forbids any retry/reset back onto a
``failed``/``indexed`` document — reprocessing is always a logically NEW
document, so this aggregate deliberately exposes no such method. Identifiers
are UUIDv7 text; timestamps are timezone-aware UTC (DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.knowledge.domain.errors import DocumentStateError
from app.modules.knowledge.domain.value_objects import IndexStatus, VectorRef

# start_indexing is re-entrant from INDEXING itself -- see its docstring.
_STARTABLE = (IndexStatus.PENDING, IndexStatus.INDEXING)


@dataclass(slots=True)
class Document:
    """A file's ingestion/indexing lifecycle (06 §7 AR ``Document``)."""

    id: str
    workspace_id: str
    file_id: str
    status: IndexStatus
    chunk_count: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    def start_indexing(self, now: datetime) -> None:
        """``pending|indexing -> indexing``.

        Re-entrant from ``indexing`` on purpose: at-least-once event
        redelivery (DD-09) may call this again after a worker crashed
        mid-run, and the restarted worker simply re-runs the pipeline from
        scratch rather than treating that as an illegal transition — safe
        because re-indexing is itself idempotent (INV-K1's
        ``UNIQUE(document_id, seq)`` plus the deterministic Qdrant point ids,
        3.k3).
        """
        if self.status not in _STARTABLE:
            raise DocumentStateError(f"cannot start indexing from status {self.status.value!r}")
        self.status = IndexStatus.INDEXING
        self.updated_at = now

    def complete_indexing(self, chunk_count: int, now: datetime) -> None:
        """``indexing -> indexed`` (INV-K2): records the final chunk count
        and clears any error left over from... nothing, in practice (a
        document only ever reaches here via ``indexing``), but clearing is
        cheap insurance against a future relaxation of that rule."""
        if self.status is not IndexStatus.INDEXING:
            raise DocumentStateError(f"cannot complete indexing from status {self.status.value!r}")
        self.status = IndexStatus.INDEXED
        self.chunk_count = chunk_count
        self.error = None
        self.updated_at = now

    def fail_indexing(self, reason: str, now: datetime) -> None:
        """``indexing -> failed`` (INV-K2) — a terminal state. INV-K3
        forbids any retry/reset back onto this same document; reprocessing
        means registering a brand-new one."""
        if self.status is not IndexStatus.INDEXING:
            raise DocumentStateError(f"cannot fail indexing from status {self.status.value!r}")
        self.status = IndexStatus.FAILED
        self.error = reason
        self.updated_at = now


@dataclass(frozen=True, slots=True)
class Chunk:
    """One indexed window of a ``Document`` (06 §7 E ``Chunk``); immutable
    once created. ``token_count``/``vector_ref`` are nullable to match the
    nullable DDL columns (01-data-model §2.7), though the 3.k4 indexing flow
    always sets both."""

    id: str
    document_id: str
    workspace_id: str
    seq: int
    text: str
    token_count: int | None
    vector_ref: VectorRef | None
