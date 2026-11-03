"""Map drawings domain events onto Outbox records (D-18, LLD 3.3.11).

``files/application/event_mapping.py``'s pattern: an exhaustive ``match`` with ``assert_never``,
one event id minted per record, ``correlationid`` from the context. Every payload carries
references and numbers, never drawing data (HLD §7.8), and an optional field that is absent is
OMITTED, never sent as ``null`` (``failure_code`` on ``job.completed``).

All six types travel on ``stream.electrical``. The first, second and sixth have a consumer group
(``cg.electrical.document``); the others are read only by the per-process notification family.
"""

from __future__ import annotations

from typing import assert_never

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.types import Json
from app.modules.electrical.drawings.domain.events import (
    DocumentProfileProposed,
    DocumentPurged,
    DocumentRegistered,
    DrawingsEvent,
    JobCompleted,
    JobProgressed,
    JobRequested,
)

SOURCE = "electrical"
STREAM = "stream.electrical"
_DOCUMENT = "drawings_document"
_JOB = "drawings_job"


def to_outbox_record(ctx: ExecutionContext, event: DrawingsEvent) -> OutboxRecord:
    match event:
        case DocumentRegistered():
            event_type = "electrical.document.registered.v1"
            aggregate_type, subject = _DOCUMENT, event.document_id
            data: Json = {
                "document_id": event.document_id,
                "project_id": event.project_id,
                "file_id": event.file_id,
                "job_id": event.job_id,
            }
        case JobRequested():
            event_type = "electrical.job.requested.v1"
            aggregate_type, subject = _JOB, event.job_id
            data = {
                "job_id": event.job_id,
                "document_id": event.document_id,
                "reason": event.reason.value,
            }
        case DocumentProfileProposed():
            event_type = "electrical.document.profile_proposed.v1"
            aggregate_type, subject = _DOCUMENT, event.document_id
            data = {
                "document_id": event.document_id,
                "job_id": event.job_id,
                "confidence": event.confidence,
                "needs_confirmation": event.needs_confirmation,
                "mixed_standards": event.mixed_standards,
            }
        case JobProgressed():
            event_type = "electrical.job.progressed.v1"
            aggregate_type, subject = _JOB, event.job_id
            data = {
                "job_id": event.job_id,
                "document_id": event.document_id,
                "seq": event.seq,
                "stage": event.stage.value,
                "done": event.done,
                "total": event.total,
                "failed": event.failed,
                "needs_review": event.needs_review,
            }
        case JobCompleted():
            event_type = "electrical.job.completed.v1"
            aggregate_type, subject = _JOB, event.job_id
            data = {
                "job_id": event.job_id,
                "document_id": event.document_id,
                "status": event.status.value,
            }
            if event.failure_code is not None:
                data["failure_code"] = event.failure_code
        case DocumentPurged():
            event_type = "electrical.document.purged.v1"
            aggregate_type, subject = _DOCUMENT, event.document_id
            data = {
                "document_id": event.document_id,
                "project_id": event.project_id,
                "file_id": event.file_id,
            }
        case _:  # pragma: no cover - mypy proves this is unreachable
            assert_never(event)

    event_id = new_uuid7()
    return OutboxRecord(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=subject,
        event_type=event_type,
        stream=STREAM,
        payload=build_envelope(
            event_id=event_id,
            source=SOURCE,
            event_type=event_type,
            subject=subject,
            occurred_at=event.occurred_at,
            workspace_id=event.workspace_id,
            data=data,
            correlation_id=ctx.correlation_id,
        ),
    )
