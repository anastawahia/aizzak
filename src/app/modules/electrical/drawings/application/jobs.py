"""Ingestion jobs as the API sees them (FR-ING-04, FR-ING-10; LLD 3.3.8 "المهام").

``JobView`` is a job with what is DERIVED at read time and never stored: the per-status page
counts (from ``page_stages``, HLD §7.3 item 4), the place in the workspace's queue, the wait
estimate (position x the median run of the last twenty succeeded jobs, ``None`` without
history, ``INC1-D-21``) and whether the lease has expired (``stale``).

``cancel`` on a ``queued`` or ``awaiting_confirmation`` job is immediate and publishes
``job.completed``; on a ``running`` one it records a request the worker honours at its next
batch boundary, and repeating it writes nothing. ``resume`` returns the SAME job to ``queued``
and publishes ``job.requested`` (``reason=resume``): only the unfinished pages are processed
again (FR-ING-04). A job that awaits confirmation is confirmed, not resumed.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.types import Uuid
from app.modules.electrical.drawings.application._common import Clock, emit, job_completed, retried
from app.modules.electrical.drawings.domain.document import Document, DocumentStatus
from app.modules.electrical.drawings.domain.errors import JobStateError
from app.modules.electrical.drawings.domain.events import (
    DrawingsEvent,
    JobRequested,
    JobRequestReason,
)
from app.modules.electrical.drawings.domain.job import IngestionJob, JobStatus
from app.modules.electrical.drawings.domain.pages import StageStatus
from app.modules.electrical.drawings.domain.pipeline import Stage
from app.modules.electrical.drawings.ports.repository import (
    DocumentRepository,
    JobRepository,
    PageRepository,
)

_RECENT_RUNS: Final = 20


@dataclass(frozen=True, slots=True)
class JobView:
    job: IngestionJob
    counts: Mapping[StageStatus, int]  # per status of the CLASSIFY stage; {} in a list
    queue_position: int | None  # only while queued
    estimated_wait_s: float | None
    stale: bool


class JobService:
    def __init__(
        self,
        jobs: JobRepository,
        documents: DocumentRepository,
        pages: PageRepository,
        outbox: EventOutbox,
        uow: UnitOfWork,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._jobs = jobs
        self._documents = documents
        self._pages = pages
        self._outbox = outbox
        self._uow = uow
        self._clock = clock

    async def view(
        self, ctx: ExecutionContext, job: IngestionJob, *, detailed: bool = True
    ) -> JobView:
        """``detailed=False`` skips the three reads a list does not need (counts, position,
        estimate)."""
        stale = job.is_stale(self._clock())
        if not detailed:
            return JobView(
                job=job, counts={}, queue_position=None, estimated_wait_s=None, stale=stale
            )
        counts = await self._pages.stage_counts(ctx, job.document_id, Stage.CLASSIFY)
        position: int | None = None
        estimate: float | None = None
        if job.status is JobStatus.QUEUED:
            position = await self._jobs.queue_position(ctx, job)
            runs = await self._jobs.recent_run_seconds(ctx, limit=_RECENT_RUNS)
            if runs:
                estimate = position * statistics.median(runs)
        return JobView(
            job=job, counts=counts, queue_position=position, estimated_wait_s=estimate, stale=stale
        )

    async def get(self, ctx: ExecutionContext, job_id: Uuid) -> JobView:
        job, _ = await self._require(ctx, job_id)
        return await self.view(ctx, job)

    async def cancel(self, ctx: ExecutionContext, job_id: Uuid) -> JobView:
        async def work() -> IngestionJob:
            job, _ = await self._require(ctx, job_id)
            if job.status is JobStatus.CANCELLED or (
                job.status is JobStatus.RUNNING and job.cancel_requested_at is not None
            ):
                return job  # repeated: 200, nothing written
            now = self._clock()
            try:
                if job.status is JobStatus.RUNNING:
                    job.request_cancel(now)
                    events: list[DrawingsEvent] = []
                else:
                    job.cancel(now)
                    events = [job_completed(job, now)]
            except JobStateError as exc:
                raise ConflictError(str(exc), code="electrical.job_conflict") from exc
            async with self._uow.begin(ctx):
                await self._jobs.save(ctx, job)
                await emit(ctx, self._outbox, events)
            return job

        return await self.view(ctx, await retried(work))

    async def resume(self, ctx: ExecutionContext, job_id: Uuid) -> JobView:
        async def work() -> IngestionJob:
            job, document = await self._require(ctx, job_id)
            if job.status is JobStatus.AWAITING_CONFIRMATION:
                raise ConflictError(
                    "the document profile awaits confirmation",
                    code="electrical.profile_unconfirmed",
                )
            if document.status is not DocumentStatus.ACTIVE:
                raise ConflictError(
                    "the document is no longer live", code="electrical.job_conflict"
                )
            now = self._clock()
            try:
                job.requeue(now)
            except JobStateError as exc:
                raise ConflictError(str(exc), code="electrical.job_conflict") from exc
            event = JobRequested(
                job_id=job.id,
                workspace_id=job.workspace_id,
                document_id=job.document_id,
                reason=JobRequestReason.RESUME,
                occurred_at=now,
            )
            async with self._uow.begin(ctx):
                await self._jobs.save(ctx, job)  # job_conflict from the open-job index
                await emit(ctx, self._outbox, [event])
            return job

        return await self.view(ctx, await retried(work))

    async def _require(self, ctx: ExecutionContext, job_id: Uuid) -> tuple[IngestionJob, Document]:
        """The job and its document; a job whose document is being purged is gone."""
        job = await self._jobs.get(ctx, job_id)
        document = None if job is None else await self._documents.get(ctx, job.document_id)
        if job is None or document is None or document.status is DocumentStatus.PURGING:
            raise NotFoundError("job not found")
        return job, document
