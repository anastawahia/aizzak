"""Helpers the drawings services share. Private to the application layer."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import EventOutbox
from app.framework.types import Uuid
from app.modules.electrical.drawings.application.event_mapping import to_outbox_record
from app.modules.electrical.drawings.domain.errors import InvalidDrawingInput, InvalidProfile
from app.modules.electrical.drawings.domain.events import DrawingsEvent, JobCompleted
from app.modules.electrical.drawings.domain.job import IngestionJob, JobKind, JobStatus
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION
from app.modules.electrical.drawings.domain.profile import (
    DrawingProfile,
    ProfileSource,
    ProfileVersion,
)

Clock = Callable[[], datetime]
_SHA256: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)


def invalid_profile(exc: InvalidProfile) -> ValidationError:
    """``electrical.invalid_profile`` (422), naming the field (NFR-CFG-02)."""
    return ValidationError(f"{exc.path}: {exc.message}", code="electrical.invalid_profile")


def invalid_input(exc: InvalidDrawingInput) -> ValidationError:
    return ValidationError(str(exc))


def normalize_checksum(checksum: str | None) -> str | None:
    """A client-sent SHA-256 as 64 lower-case hex characters, or a 422."""
    if checksum is None:
        return None
    text = checksum.strip().lower()
    if _SHA256.fullmatch(text) is None:
        raise ValidationError("checksum must be a SHA-256 as 64 hexadecimal characters")
    return text


def job_completed(job: IngestionJob, now: datetime) -> JobCompleted:
    """``job.completed`` for a job that has just reached a terminal status."""
    return JobCompleted(
        job_id=job.id,
        workspace_id=job.workspace_id,
        document_id=job.document_id,
        status=job.status,
        failure_code=job.failure_code,
        occurred_at=now,
    )


async def emit(ctx: ExecutionContext, outbox: EventOutbox, events: Sequence[DrawingsEvent]) -> None:
    """Append the events to the outbox inside the caller's unit of work."""
    if events:
        await outbox.append(ctx, [to_outbox_record(ctx, event) for event in events])


def new_job(
    ctx: ExecutionContext,
    *,
    document_id: Uuid,
    kind: JobKind,
    pages: tuple[int, ...] | None,
    now: datetime,
) -> IngestionJob:
    return IngestionJob(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        document_id=document_id,
        kind=kind,
        pages=pages,
        status=JobStatus.QUEUED,
        stage=None,
        failure_code=None,
        failure_detail=None,
        attempt=1,
        lease_until=None,
        seq=0,
        cancel_requested_at=None,
        pipeline_version=PIPELINE_VERSION,
        created_by=ctx.user_id,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        version=1,
    )


def new_profile_version(
    ctx: ExecutionContext,
    *,
    project_id: Uuid,
    document_id: Uuid | None,
    source: ProfileSource,
    profile: DrawingProfile,
    now: datetime,
    confidence: float | None = None,
    mixed_standards: bool = False,
    created_by: str | None = None,
    detection: Mapping[str, Any] | None = None,
) -> ProfileVersion:
    """A version to append; the repository assigns the real ``version`` number."""
    return ProfileVersion(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        project_id=project_id,
        document_id=document_id,
        version=1,
        source=source,
        profile=profile,
        fingerprint=profile.fingerprint(),
        confidence=confidence,
        mixed_standards=mixed_standards,
        created_by=created_by if created_by is not None else ctx.user_id,
        created_at=now,
        detection=detection,
    )


async def retried[T](work: Callable[[], Awaitable[T]]) -> T:
    """Run a whole unit of work once more when it loses a version race (``common.conflict``
    only: a coded conflict such as ``electrical.job_conflict`` is an answer, not a race)."""
    try:
        return await work()
    except ConflictError as exc:
        if exc.code != "common.conflict":
            raise
        return await work()
