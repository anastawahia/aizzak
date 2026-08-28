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

The ``Summary*`` trio (BE-RAG-009/011) repeats that shape one aggregate over:
``SummaryRequested`` is the internal one a worker consumes, ``SummaryBuilt``
is global, ``SummaryBuildFailed`` mirrors it — and carries a CANCELLED job's
reason rather than earning a fourth type nobody would handle differently.
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


@dataclass(frozen=True, slots=True)
class SummaryRequested:
    """A ``SummaryJob`` was queued and is waiting for a worker (internal —
    wire ``knowledge.summary.requested.v1``) — BE-RAG-009.

    Carries the whole build key (``document_id``/``kind``/``lang``) and not
    only ``job_id``. The worker could load all of it from the job row, but the
    row is not where the message's meaning should live: a consumer reading the
    stream has to be able to tell WHAT was asked for without a database it may
    not have, which is the same rule ``DocumentRegistered`` follows by
    carrying ``file_id`` next to ``document_id``.

    ``conversation_id`` is the thread the build was asked for INSIDE, or
    ``None`` when it was asked for outside one (`F-7`). It rides the MESSAGE
    rather than a column on the job row, for the reason the build key rides
    it: the worker that finishes the build has to know where the answer is
    owed, and a column no query would ever filter on is a schema change
    bought to move a value the message already carries.

    ``None`` is a real value here, not a forgotten one. ``POST
    /documents/{id}/summary`` has no thread and never will — a build asked
    for there owes its answer to the route that reads it back, which is
    exactly what ``AgentRequest.space_id`` means by defaulting.
    """

    job_id: str
    workspace_id: str
    document_id: str
    kind: str
    lang: str
    conversation_id: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SummaryBuilt:
    """A ``Summary`` was written (global -> WebSocket notification; wire
    ``knowledge.summary.built.v1``) — BE-RAG-009.

    Global for the reason ``DocumentIndexed`` is: a full summary takes long
    enough that the tab which asked for it is often not the tab still open,
    and the notification is how the other one learns to stop polling.

    ``conversation_id`` is carried through from ``SummaryRequested`` (`F-7`),
    not re-derived: the worker reads it off the message it is handling and
    stamps it here, so the subscriber that appends the finished text to a
    thread learns which thread from the event rather than from a row nobody
    wrote it to. A build that had no thread publishes ``None`` and that
    subscriber has nothing to do.
    """

    job_id: str
    workspace_id: str
    document_id: str
    kind: str
    lang: str
    conversation_id: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SummaryBuildFailed:
    """A ``SummaryJob`` ended without a summary (wire
    ``knowledge.summary.build_failed.v1``) — BE-RAG-009/011.

    Emitted for a CANCELLED job too, carrying the cancellation as its reason —
    the ``CANCELLED_REASON`` precedent from re-indexing. A separate
    "cancelled" event type would be a third schema with the same consumer and
    the same handling, which is the ``FileRenamed`` mistake in another
    costume: a promise no one is waiting for.
    """

    job_id: str
    workspace_id: str
    document_id: str
    reason: str
    occurred_at: datetime


KnowledgeEvent = (
    DocumentRegistered
    | DocumentIndexed
    | DocumentIndexingFailed
    | SummaryRequested
    | SummaryBuilt
    | SummaryBuildFailed
)
