"""Map knowledge domain events onto Outbox records (D-18 · 04-event-catalog §1/§2).

The seam between a PURE domain event (``domain/events.py``: no framework
imports, no wire concerns) and the row the ``outbox_relay`` will publish. It
belongs to the application layer because it mints identity (``new_uuid7``)
and reads the request's ``ExecutionContext`` — neither of which the domain
may touch. The ``media``/``files`` precedent (``modules/media/application/
event_mapping.py``) is followed faithfully: exhaustive ``match`` +
``assert_never``, one event id minted per record, ``correlationid`` set from
the context here rather than left to the adapter.

**Every ``KnowledgeEvent`` variant becomes a wire event.** 04 §5 marks
``DocumentRegistered``, ``DocumentIndexed`` AND ``DocumentIndexingFailed``
all with the promotion asterisk (*) — unlike ``files``/``memory``, knowledge
has no internal-only domain event, so ``to_outbox_record`` never returns
``None`` here. ``DocumentRegistered`` reads "internal" in its OWN
``domain/events.py`` docstring only in the sense that it is not itself
consumed by an external caller of THIS module — 04 §4's catalog table still
routes it to ``knowledge``'s own worker over ``stream.knowledge``, so it is
promoted exactly like the other two. The ``Summary*`` trio (BE-RAG-009/011)
is published on the same terms, ``SummaryRequested`` included: it is how the
worker is told there is a summary to build at all.

No producer SERVICE is composed over this mapping in 5.1-أ: knowledge's
Outbox writes are worker-produced (``IndexRegisteredDocument`` and friends,
5.1-ج), not request-scoped, so there is nothing here analogous to
``MediaRequestService``/``CompleteUploadService`` yet.

**``correlationid`` is set from the context here, not left to the adapter.**
``SqlEventOutbox`` writes a ``correlation_id`` COLUMN, but that column is the
relay's own bookkeeping: the relay republishes ``payload`` *verbatim*, so a
correlation id that lives only in the column never reaches a consumer.
"""

from __future__ import annotations

from typing import assert_never

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.types import Json
from app.modules.knowledge.domain.events import (
    DocumentIndexed,
    DocumentIndexingFailed,
    DocumentRegistered,
    KnowledgeEvent,
    SummaryBuildFailed,
    SummaryBuilt,
    SummaryRequested,
)

# The producing module's name (04 §1: `"source": "knowledge"`) and its stream
# (04 §2: `stream.knowledge`, read by the per-process `cg.notify.<host>.<pid>`
# group family, not one shared group -- docs/log/3.81.md).
SOURCE = "knowledge"
STREAM = "stream.knowledge"

# The aggregate each family of events is about. `AGGREGATE_TYPE` was a single
# module constant while every event here concerned a document; BE-RAG-009 gave
# the module a second aggregate root whose events are NOT about one, and
# filing a summary job's message under `document` would have made the outbox's
# own `aggregate_id` point at a row that is not the subject.
AGGREGATE_DOCUMENT = "document"
AGGREGATE_SUMMARY_JOB = "summary_job"


def to_outbox_record(ctx: ExecutionContext, event: KnowledgeEvent) -> OutboxRecord:
    """Build the complete, publishable record for one knowledge domain event.

    The ``match`` is EXHAUSTIVE over ``KnowledgeEvent`` (``assert_never`` on
    the fallthrough): adding another knowledge event turns this function red
    under mypy instead of silently dropping it. Every ``data`` dict mirrors
    its published schema (``docs/design/events/schemas/
    knowledge.*.v1.json``) AND 04 §4's own catalog-table listing for
    that type field for field — deliberately not every field the domain
    event itself carries (``DocumentIndexed`` also has ``file_id``/
    ``collection``, neither of which 04 §4 lists for the wire event).
    """
    match event:
        case DocumentRegistered():
            event_type = "knowledge.document.registered.v1"
            data: Json = {"document_id": event.document_id, "file_id": event.file_id}
            aggregate_type, aggregate_id = AGGREGATE_DOCUMENT, event.document_id
        case DocumentIndexed():
            event_type = "knowledge.document.indexed.v1"
            data = {"document_id": event.document_id, "chunk_count": event.chunk_count}
            aggregate_type, aggregate_id = AGGREGATE_DOCUMENT, event.document_id
        case DocumentIndexingFailed():
            event_type = "knowledge.document.indexing_failed.v1"
            data = {"document_id": event.document_id, "reason": event.reason}
            aggregate_type, aggregate_id = AGGREGATE_DOCUMENT, event.document_id
        case SummaryRequested():
            event_type = "knowledge.summary.requested.v1"
            data = {
                "job_id": event.job_id,
                "document_id": event.document_id,
                "kind": event.kind,
                "lang": event.lang,
            }
            aggregate_type, aggregate_id = AGGREGATE_SUMMARY_JOB, event.job_id
        case SummaryBuilt():
            event_type = "knowledge.summary.built.v1"
            data = {
                "job_id": event.job_id,
                "document_id": event.document_id,
                "kind": event.kind,
                "lang": event.lang,
            }
            aggregate_type, aggregate_id = AGGREGATE_SUMMARY_JOB, event.job_id
        case SummaryBuildFailed():
            event_type = "knowledge.summary.build_failed.v1"
            data = {
                "job_id": event.job_id,
                "document_id": event.document_id,
                "reason": event.reason,
            }
            aggregate_type, aggregate_id = AGGREGATE_SUMMARY_JOB, event.job_id
        case _:  # pragma: no cover - mypy proves this is unreachable
            assert_never(event)

    event_id = new_uuid7()
    return OutboxRecord(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        stream=STREAM,
        payload=build_envelope(
            event_id=event_id,
            source=SOURCE,
            event_type=event_type,
            # The same id the outbox files the record under, not `document_id`
            # unconditionally: a summary event's subject is its JOB, and every
            # summary event on one document would otherwise share a subject
            # while describing different builds of it.
            subject=aggregate_id,
            # The DOMAIN's instant, not `utc_now()` here: `occurred_at` is
            # when the thing happened, and re-reading the clock at mapping
            # time would publish a timestamp that drifts from the
            # aggregate's own row.
            occurred_at=event.occurred_at,
            # The event's own workspace, which the use-case took from this
            # same context -- an event never crosses tenants.
            workspace_id=event.workspace_id,
            data=data,
            correlation_id=ctx.correlation_id,
        ),
    )
