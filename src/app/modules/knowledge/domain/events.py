"""Knowledge domain events (pure — 06-domain-models §7, docs/design/events).

In-memory domain events: plain frozen records with no shared base class (the
domain imports no framework code) — the ``files`` event-module precedent.
Fields mirror the published wire schemas (``docs/design/events/schemas/
knowledge.document.*.v1.json``) plus the envelope fields ``workspace_id``/
``occurred_at`` carried on the domain event itself, exactly like
``files.FileUploaded``. Dispatch to the event bus / outbox happens in the
application and infrastructure layers, not here.

``DocumentRegistered`` is internal-only (06 §7 marks it "داخلي" — internal);
``DocumentIndexed`` is the **global** event (-> WebSocket notification, 06
§11 cross-module table) and ``DocumentIndexingFailed`` mirrors it for the
failure path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DocumentRegistered:
    """A ``Document`` was registered, usually from a ``files.FileUploaded``
    event (internal — wire ``knowledge.document.registered.v1``)."""

    document_id: str
    workspace_id: str
    file_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentIndexed:
    """A ``Document`` finished indexing successfully (global -> WebSocket
    notification; wire ``knowledge.document.indexed.v1``)."""

    document_id: str
    workspace_id: str
    file_id: str
    chunk_count: int
    collection: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentIndexingFailed:
    """A ``Document``'s indexing pipeline failed (wire
    ``knowledge.document.indexing_failed.v1``). INV-K3: recovering means
    registering a NEW document, never resubmitting this one."""

    document_id: str
    workspace_id: str
    reason: str
    occurred_at: datetime


KnowledgeEvent = DocumentRegistered | DocumentIndexed | DocumentIndexingFailed
