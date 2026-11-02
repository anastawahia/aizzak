"""The purge of a deleted file's or space's drawing documents (PLT-04, HLD §8.8, INC1-AC-10).

TWO PHASES. Phase 1 (``PurgeFileDocuments``, ``PurgeSpaceDrawings``) runs inside the delete
request, in ONE unit of work: every document of the file (or space) that is not yet ``purging``
becomes ``purging`` (out of every read from that instant), its open job is cancelled with
``electrical.document_not_live``, and ``electrical.document.purged.v1`` (plus ``job.completed``)
joins the outbox. One row and one event per document keeps the request small however big the
drawing (RSK-17). It returns how many documents it marked, so a repeat returns 0: a re-run of
``DELETE`` finishes an interrupted cascade.

Phase 2 (``SweepPurgedDocument``) is the worker's consumer of the purged event: a unit of work that
starts with the idempotency claim and then deletes the document's rows in foreign-key order
(``DrawingsSweep``). There are no derived objects in MinIO in INC-1, so nothing else to delete yet.

No route purges: only a file or space deletion reaches the first two, through
``framework/di/electrical_purge.py``.
"""

from __future__ import annotations

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.types import Uuid
from app.modules.electrical.drawings.application._common import (
    DOCUMENT_NOT_LIVE,
    Clock,
    emit,
    job_completed,
)
from app.modules.electrical.drawings.domain.document import Document, DocumentStatus
from app.modules.electrical.drawings.domain.events import DocumentPurged, DrawingsEvent
from app.modules.electrical.drawings.ports.ledger import ProcessedEvents
from app.modules.electrical.drawings.ports.repository import (
    DocumentRepository,
    DrawingsSweep,
    JobRepository,
    ProjectRepository,
)

_PAGE = 200


class _Marker:
    """Phase 1 for a set of documents, inside the caller's unit of work."""

    def __init__(
        self,
        documents: DocumentRepository,
        jobs: JobRepository,
        outbox: EventOutbox,
        clock: Clock,
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._outbox = outbox
        self._clock = clock

    async def mark(self, ctx: ExecutionContext, documents: list[Document]) -> int:
        now = self._clock()
        marked = 0
        events: list[DrawingsEvent] = []
        for document in documents:
            if not document.mark_purging(now):
                continue
            await self._documents.save(ctx, document)
            marked += 1
            job = await self._jobs.open_for_document(ctx, document.id)
            if job is not None:
                job.cancel(now, code=DOCUMENT_NOT_LIVE)
                await self._jobs.save(ctx, job)
                events.append(job_completed(job, now))
            events.append(
                DocumentPurged(
                    document_id=document.id,
                    workspace_id=document.workspace_id,
                    project_id=document.project_id,
                    file_id=document.file_id,
                    occurred_at=now,
                )
            )
        await emit(ctx, self._outbox, events)
        return marked


class PurgeFileDocuments:
    """``DrawingsFilePurge``: phase 1 for one file."""

    def __init__(
        self,
        documents: DocumentRepository,
        jobs: JobRepository,
        outbox: EventOutbox,
        uow: UnitOfWork,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._documents = documents
        self._uow = uow
        self._marker = _Marker(documents, jobs, outbox, clock)

    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> int:
        async with self._uow.begin(ctx):
            documents = list(await self._documents.not_purging_for_file(ctx, file_id))
            return await self._marker.mark(ctx, documents)


class PurgeSpaceDrawings:
    """``DrawingsSpacePurge``: phase 1 for a space, then the project and its profile versions
    go in the same unit of work (INC1-D-16)."""

    def __init__(
        self,
        projects: ProjectRepository,
        documents: DocumentRepository,
        jobs: JobRepository,
        outbox: EventOutbox,
        uow: UnitOfWork,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._projects = projects
        self._documents = documents
        self._uow = uow
        self._marker = _Marker(documents, jobs, outbox, clock)

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        async with self._uow.begin(ctx):
            project = await self._projects.get_by_space(ctx, space_id)
            if project is None:
                return 0
            marked = 0
            cursor: str | None = None
            while True:
                page = await self._documents.list_by_project(
                    ctx, project.id, status=None, limit=_PAGE, cursor=cursor
                )
                live = [d for d in page.data if d.status is not DocumentStatus.PURGING]
                marked += await self._marker.mark(ctx, live)
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            await self._projects.delete_in_space(ctx, space_id)
            return marked


class SweepPurgedDocument:
    """Phase 2, in the worker."""

    def __init__(self, sweep: DrawingsSweep, ledger: ProcessedEvents, uow: UnitOfWork) -> None:
        self._sweep = sweep
        self._ledger = ledger
        self._uow = uow

    async def execute(self, ctx: ExecutionContext, document_id: Uuid, *, event_id: str) -> int:
        """Rows deleted; 0 for a duplicate delivery or a document already gone."""
        async with self._uow.begin(ctx):
            if not await self._ledger.claim(ctx, event_id=event_id):
                return 0
            return await self._sweep.sweep_document(ctx, document_id)
