"""Drawing documents: registration (STG-00), the profile flow, reprocessing (LLD 3.3.8).

``register`` is the guard of HLD §8.11. The checks run in order and the first to fail is the
answer; none writes. Only when all pass does ONE unit of work (a) excludes the project's
orphans (active documents whose file is no longer readable, ``ExclusionReason.FILE_UNREADABLE``),
(b) inserts the document ``active`` and its job ``queued``, and (c) appends
``electrical.document.registered.v1``. The unique indexes decide a race: of two concurrent
registrations of one file the second is ``409 electrical.document_exists``.

The profile of a document is a series of its own (``ProfileRepository`` with ``document_id``).
EFFECTIVE profile: the document's latest version when its state is ``accepted`` or
``confirmed``, otherwise the project's latest. ``confirm_profile`` appends a ``confirmed``
version (the proposal as it is, or edited), requeues the job and publishes ``job.requested``
(``reason=confirmation``), all in one unit of work. ``replace_profile`` appends a ``user`` version
and is refused while a job awaits confirmation, because that job's way out is the confirmation.
Changing a profile never reprocesses anything: ``reprocess`` is an explicit call (HLD §7.5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnsupportedTypeError,
    ValidationError,
)
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.types import Uuid
from app.modules.electrical.drawings.application._common import (
    Clock,
    emit,
    new_job,
    new_profile_version,
    normalize_checksum,
    retried,
)
from app.modules.electrical.drawings.application.jobs import JobService, JobView
from app.modules.electrical.drawings.application.projects import parse_profile
from app.modules.electrical.drawings.domain.document import (
    Document,
    DocumentStatus,
    ExclusionReason,
    ProfileState,
)
from app.modules.electrical.drawings.domain.events import (
    DocumentRegistered,
    JobRequested,
    JobRequestReason,
)
from app.modules.electrical.drawings.domain.job import IngestionJob, JobKind, JobStatus
from app.modules.electrical.drawings.domain.pages import PageRow
from app.modules.electrical.drawings.domain.pipeline import Stage
from app.modules.electrical.drawings.domain.profile import (
    ProfileSource,
    ProfileVersion,
)
from app.modules.electrical.drawings.ports.files import DrawingFiles
from app.modules.electrical.drawings.ports.namesakes import OlderNamesakes
from app.modules.electrical.drawings.ports.repository import (
    DocumentRepository,
    JobRepository,
    PageRepository,
    ProfileRepository,
    ProjectRepository,
)
from app.modules.electrical.drawings.ports.rules import RuleSource

PDF_CONTENT_TYPE = "application/pdf"
QUEUE_RETRY_AFTER_S = 60


@dataclass(frozen=True, slots=True)
class RegisteredDocument:
    document: Document
    job: IngestionJob
    excluded: tuple[tuple[str, ExclusionReason], ...]  # orphans this call excluded


@dataclass(frozen=True, slots=True)
class DocumentView:
    """A document with what ``files`` and ``jobs`` add. ``file_name`` is ``None`` once the file
    is unreadable; ``tags`` is read on a single ``get`` only (``INC1-D-26``)."""

    document: Document
    file_name: str | None
    tags: Mapping[str, str] | None
    latest_job: JobView | None


@dataclass(frozen=True, slots=True)
class EffectiveProfile:
    """What a document is processed under: ``state``, the version in force, and (while it
    matters) the latest detected proposal. ``project_version`` is the project's own latest."""

    state: ProfileState
    effective: ProfileVersion
    project_version: int
    proposal: ProfileVersion | None


class DocumentService:
    def __init__(
        self,
        projects: ProjectRepository,
        documents: DocumentRepository,
        jobs: JobRepository,
        pages: PageRepository,
        profiles: ProfileRepository,
        files: DrawingFiles,
        namesakes: OlderNamesakes,
        rules: RuleSource,
        job_service: JobService,
        outbox: EventOutbox,
        uow: UnitOfWork,
        *,
        queue_protection_limit: int,
        clock: Clock = utc_now,
    ) -> None:
        self._projects = projects
        self._documents = documents
        self._jobs = jobs
        self._pages = pages
        self._profiles = profiles
        self._files = files
        self._namesakes = namesakes
        self._rules = rules
        self._job_service = job_service
        self._outbox = outbox
        self._uow = uow
        self._queue_limit = queue_protection_limit
        self._clock = clock

    # ------------------------------------------------------------------ STG-00
    async def register(
        self, ctx: ExecutionContext, project_id: Uuid, *, file_id: Uuid, checksum: str | None
    ) -> RegisteredDocument:
        sha256 = normalize_checksum(checksum)
        project = await self._projects.get(ctx, project_id)
        if project is None:
            raise NotFoundError("project not found")  # 1
        file = await self._files.get_readable(ctx, file_id)
        if file is None:  # 2: unknown, unfinished, foreign and deleted look alike
            raise NotFoundError("file not found")
        if file.content_type != PDF_CONTENT_TYPE:  # 3
            raise UnsupportedTypeError(
                "only PDF files can be registered as drawings", code="electrical.not_a_pdf"
            )
        if file.space_id != project.space_id:  # 4
            raise ValidationError(
                "the file belongs to another space than the project's",
                code="electrical.file_not_in_project_space",
            )
        if await self._documents.not_purging_for_file(ctx, file_id):  # 5
            raise ConflictError(
                "the file is already a drawing document", code="electrical.document_exists"
            )
        older = await self._namesakes.execute(ctx, file_id)
        if older:  # 6: a sweep that failed; the cure is deleting the older file
            raise ConflictError(
                "an older file with this name and tags is still live: "
                + ", ".join(sorted(older))
                + "; delete it and register again",
                code="electrical.namesake_not_replaced",
            )
        if sha256 is not None:  # 7
            twin = await self._documents.active_with_content(ctx, project_id, sha256)
            if twin is not None:
                raise ConflictError(
                    f"the same content is already document {twin.id} of this project",
                    code="electrical.duplicate_document",
                )
        if await self._jobs.open_count(ctx) >= self._queue_limit:  # 8
            raise RateLimitedError(
                "the ingestion queue protection limit is reached; retry later",
                code="electrical.ingestion_queue_full",
                retry_after_s=QUEUE_RETRY_AFTER_S,
            )
        return await self._register(ctx, project.id, project.space_id, file_id)  # 9

    async def _register(
        self, ctx: ExecutionContext, project_id: Uuid, space_id: Uuid, file_id: Uuid
    ) -> RegisteredDocument:
        now = self._clock()
        document = Document(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            space_id=space_id,
            project_id=project_id,
            file_id=file_id,
            status=DocumentStatus.ACTIVE,
            exclusion_reason=None,
            content_sha256=None,
            page_count=None,
            pdf_creator=None,
            pdf_producer=None,
            profile_state=ProfileState.NONE,
            pipeline_version=None,
            created_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            purging_at=None,
            version=1,
        )
        job = new_job(ctx, document_id=document.id, kind=JobKind.INGEST, pages=None, now=now)
        event = DocumentRegistered(
            document_id=document.id,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            file_id=file_id,
            job_id=job.id,
            occurred_at=now,
        )
        excluded: list[tuple[str, ExclusionReason]] = []
        async with self._uow.begin(ctx):
            active = await self._documents.active_files(ctx, project_id)
            readable = await self._files.names_for_files(ctx, list(active.values()))
            for orphan_id, orphan_file in sorted(active.items()):
                if orphan_file in readable:
                    continue
                orphan = await self._documents.get(ctx, orphan_id)
                if orphan is None or not orphan.is_live:
                    continue
                orphan.exclude(ExclusionReason.FILE_UNREADABLE, now)
                await self._documents.save(ctx, orphan)
                excluded.append((orphan_id, ExclusionReason.FILE_UNREADABLE))
            await self._documents.add(ctx, document)  # document_exists
            await self._jobs.add(ctx, job)
            await emit(ctx, self._outbox, [event])
        return RegisteredDocument(document=document, job=job, excluded=tuple(excluded))

    # ------------------------------------------------------------------ reads
    async def get(self, ctx: ExecutionContext, document_id: Uuid) -> DocumentView:
        document = await self._require(ctx, document_id)
        file = await self._files.get_readable(ctx, document.file_id)
        latest = await self._jobs.latest_for_document(ctx, document.id)
        return DocumentView(
            document=document,
            file_name=None if file is None else file.name,
            tags=None if file is None else dict(file.tags),
            latest_job=None if latest is None else await self._job_service.view(ctx, latest),
        )

    async def list(
        self,
        ctx: ExecutionContext,
        project_id: Uuid,
        *,
        status: DocumentStatus | None,
        limit: int,
        cursor: str | None,
    ) -> Page[DocumentView]:
        """``purging`` documents are out of every read, whatever the filter says."""
        if status is DocumentStatus.PURGING:
            return Page(data=[], next_cursor=None, limit=limit)
        if await self._projects.get(ctx, project_id) is None:
            raise NotFoundError("project not found")
        page = await self._documents.list_by_project(
            ctx, project_id, status=status, limit=limit, cursor=cursor
        )
        shown = [d for d in page.data if d.status is not DocumentStatus.PURGING]
        names = await self._files.names_for_files(ctx, [d.file_id for d in shown])
        views: list[DocumentView] = []
        for document in shown:
            latest = await self._jobs.latest_for_document(ctx, document.id)
            views.append(
                DocumentView(
                    document=document,
                    file_name=names.get(document.file_id),
                    tags=None,
                    latest_job=None
                    if latest is None
                    else await self._job_service.view(ctx, latest, detailed=False),
                )
            )
        return Page(data=views, next_cursor=page.next_cursor, limit=page.limit)

    async def list_pages(
        self, ctx: ExecutionContext, document_id: Uuid, *, limit: int, cursor: str | None
    ) -> Page[PageRow]:
        document = await self._require(ctx, document_id)
        return await self._pages.list(ctx, document.id, limit=limit, cursor=cursor)

    # ------------------------------------------------------------------ profile
    async def get_profile(self, ctx: ExecutionContext, document_id: Uuid) -> EffectiveProfile:
        document = await self._require(ctx, document_id)
        project_latest = await self._profiles.latest(
            ctx, project_id=document.project_id, document_id=None
        )
        if project_latest is None:
            raise NotFoundError("project profile not found")
        own = await self._profiles.latest(
            ctx, project_id=document.project_id, document_id=document.id
        )
        in_force = own is not None and document.profile_state in (
            ProfileState.ACCEPTED,
            ProfileState.CONFIRMED,
        )
        proposal = (
            own
            if own is not None
            and own.source is ProfileSource.DETECTED
            and document.profile_state is ProfileState.PROPOSED
            else None
        )
        return EffectiveProfile(
            state=document.profile_state,
            effective=own if in_force and own is not None else project_latest,
            project_version=project_latest.version,
            proposal=proposal,
        )

    async def replace_profile(
        self, ctx: ExecutionContext, document_id: Uuid, body: Mapping[str, object]
    ) -> ProfileVersion:
        """A person's override of this document: a ``user`` version, and the document is
        ``confirmed`` under it. ``409 electrical.job_conflict`` while a job awaits
        confirmation (confirm it instead)."""
        parsed = parse_profile(self._rules, body)

        async def work() -> ProfileVersion:
            document = await self._require_live(ctx, document_id)
            open_job = await self._jobs.open_for_document(ctx, document.id)
            if open_job is not None and open_job.status is JobStatus.AWAITING_CONFIRMATION:
                raise ConflictError(
                    "the document profile awaits confirmation: confirm it instead",
                    code="electrical.job_conflict",
                )
            now = self._clock()
            version = new_profile_version(
                ctx,
                project_id=document.project_id,
                document_id=document.id,
                source=ProfileSource.USER,
                profile=parsed,
                now=now,
            )
            document.set_profile_state(ProfileState.CONFIRMED, now)
            async with self._uow.begin(ctx):
                stored = await self._profiles.append(ctx, version)
                await self._documents.save(ctx, document)
            return stored

        return await retried(work)

    async def confirm_profile(
        self, ctx: ExecutionContext, document_id: Uuid, *, body: Mapping[str, object] | None
    ) -> IngestionJob:
        """Confirm the proposal as it is (``body`` is ``None``) or as edited. The job must be
        awaiting confirmation (``409 electrical.job_conflict`` otherwise)."""
        edited = None if body is None else parse_profile(self._rules, body)

        async def work() -> IngestionJob:
            document = await self._require_live(ctx, document_id)
            job = await self._jobs.open_for_document(ctx, document.id)
            if job is None or job.status is not JobStatus.AWAITING_CONFIRMATION:
                raise ConflictError(
                    "no job of this document awaits confirmation", code="electrical.job_conflict"
                )
            profile = edited
            if profile is None:
                proposal = await self._profiles.latest(
                    ctx, project_id=document.project_id, document_id=document.id
                )
                if proposal is None or proposal.source is not ProfileSource.DETECTED:
                    raise ConflictError(
                        "the document has no proposal to confirm", code="electrical.job_conflict"
                    )
                profile = proposal.profile
            now = self._clock()
            version = new_profile_version(
                ctx,
                project_id=document.project_id,
                document_id=document.id,
                source=ProfileSource.CONFIRMED,
                profile=profile,
                now=now,
            )
            document.set_profile_state(ProfileState.CONFIRMED, now)
            job.requeue(now)
            event = JobRequested(
                job_id=job.id,
                workspace_id=ctx.workspace_id,
                document_id=document.id,
                reason=JobRequestReason.CONFIRMATION,
                occurred_at=now,
            )
            async with self._uow.begin(ctx):
                await self._profiles.append(ctx, version)
                await self._documents.save(ctx, document)
                await self._jobs.save(ctx, job)
                await emit(ctx, self._outbox, [event])
            return job

        return await retried(work)

    # --------------------------------------------------------------- reprocess
    async def reprocess(
        self, ctx: ExecutionContext, document_id: Uuid, *, pages: Sequence[int] | None
    ) -> IngestionJob:
        """A new ``reprocess`` job for the whole document or the given pages (their stage
        returns to ``pending`` in the same unit of work). ``409 electrical.profile_unconfirmed``
        while the proposal awaits a person, ``409 electrical.job_conflict`` while another job is
        open."""
        document = await self._require_live(ctx, document_id)
        subset = self._pages_in_range(document, pages)
        if document.profile_state is ProfileState.PROPOSED:
            raise ConflictError(
                "the document profile awaits confirmation", code="electrical.profile_unconfirmed"
            )
        now = self._clock()
        job = new_job(ctx, document_id=document.id, kind=JobKind.REPROCESS, pages=subset, now=now)
        event = JobRequested(
            job_id=job.id,
            workspace_id=ctx.workspace_id,
            document_id=document.id,
            reason=JobRequestReason.REPROCESS,
            occurred_at=now,
        )
        async with self._uow.begin(ctx):
            locked = await self._documents.lock_live(ctx, document.id)
            if locked is None:
                raise NotFoundError("document not found")
            await self._jobs.add(ctx, job)  # job_conflict when one is open
            await self._pages.reset(ctx, document.id, subset, Stage.CLASSIFY)
            await emit(ctx, self._outbox, [event])
        return job

    @staticmethod
    def _pages_in_range(document: Document, pages: Sequence[int] | None) -> tuple[int, ...] | None:
        if pages is None:
            return None
        wanted = tuple(sorted(set(pages)))
        if not wanted:
            raise ValidationError("pages cannot be empty; omit it to reprocess every page")
        last = document.page_count
        if last is None:
            raise ConflictError(
                "the document has not been inspected yet", code="electrical.job_conflict"
            )
        if wanted[0] < 1 or wanted[-1] > last:
            raise ValidationError(f"pages must be within 1..{last}")
        return wanted

    # ----------------------------------------------------------------- helpers
    async def _require(self, ctx: ExecutionContext, document_id: Uuid) -> Document:
        document = await self._documents.get(ctx, document_id)
        if document is None or document.status is DocumentStatus.PURGING:
            raise NotFoundError("document not found")
        return document

    async def _require_live(self, ctx: ExecutionContext, document_id: Uuid) -> Document:
        document = await self._require(ctx, document_id)
        if not document.is_live:
            raise ConflictError("the document is no longer live", code="electrical.job_conflict")
        return document
