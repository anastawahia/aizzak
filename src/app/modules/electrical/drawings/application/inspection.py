"""STG-01: inspect one document (FR-ING-02/04/05/06/08/10/12; LLD 3.3.8, INC1-AC-7/8).

``InspectDocument.run`` is the worker's whole job for one ``registered`` / ``job.requested``
delivery. Every network call (object storage, ele-parse) runs OUTSIDE any transaction (R2) and
every transaction is short; a document's progress is therefore a sequence of small commits and a
crash resumes from the last one.

1. LEASE. A short transaction claims the job: ``queued -> running`` with ``lease_until`` (or a
   takeover of a ``running`` job whose lease expired). A live lease held by another run is not
   ours: return without claiming, the other run completes. A document no longer ``active`` cancels
   the job (``electrical.document_not_live``).
2. NOTHING LEFT TO DO? When no page is unfinished and the document is already characterized
   (the follow-on of a confirmation) jump to 6 without downloading.
3. DOWNLOAD. Stream the object to the stage volume, hashing as it is written.
4. FINGERPRINT. The SHA-256 is recorded; the same bytes already an ACTIVE document of the
   project exclude this one and fail the job (``electrical.duplicate_document``).
5. PAGES. ``summary`` (``PdfRejected`` fails the job; more than ``max_pages`` fails it), then the
   unfinished pages in batches: each batch checks for a cancellation or a purge, asks ele-parse
   (a failed range is retried page by page and only the failing page is recorded ``failed``:
   NFR-REL-01), classifies and tallies each page, and commits the pages, the lease extension and
   (rate-limited) ``job.progressed``. A fault (``InspectionUnavailable``, a database error)
   RELEASES the run's own lease and propagates, so the engine's redelivery of the same message
   takes the job over at once (and, past its limit, dead-letters it).
6. PROPOSAL AND CHARACTERIZATION. From the stored rows: ``detect_profile`` and ``characterize``;
   the last transaction STARTS WITH the idempotency claim, then writes the characterization, the
   proposed profile version, the document state and the job's end.
7. The staged copy is discarded in a ``finally``.

**The tally uses the FULL packaged rules**, not the project's selection: ``rule_selection()``
narrows ``conventions`` to the profile's own (IEC 81346 by default), so tallying with it would see
no other convention and never propose one. Narrowing is for parsing a document under a CHOSEN
profile (later increments), not for proposing one.

**Resuming reads stored evidence**, never the PDF's text again: each page's tally is stored with
its signals (``PageRepository.evidence``), so a resumed or confirmed job folds in the pages an
earlier run finished and a fresh run takes the same path; the outputs are a function of the
bytes, the profile and the versions only (FR-ING-05, NFR-REP-02).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from enum import Enum
from typing import Final, NoReturn

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.observability import get_logger
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.settings.settings import ElectricalSettings
from app.framework.types import Uuid
from app.modules.electrical.drawings.application._common import (
    DOCUMENT_NOT_LIVE,
    Clock,
    emit,
    job_completed,
    new_profile_version,
)
from app.modules.electrical.drawings.domain.characterization import Characterization, characterize
from app.modules.electrical.drawings.domain.classification import classify_page
from app.modules.electrical.drawings.domain.document import (
    Document,
    ExclusionReason,
    ProfileState,
)
from app.modules.electrical.drawings.domain.errors import (
    PageRangeFailed,
    PdfRejected,
)
from app.modules.electrical.drawings.domain.events import (
    DocumentProfileProposed,
    DrawingsEvent,
    JobProgressed,
)
from app.modules.electrical.drawings.domain.job import IngestionJob, JobKind, JobStatus
from app.modules.electrical.drawings.domain.pages import (
    ClassificationThresholds,
    PageClassification,
    PageKind,
    PageLanguage,
    PageResult,
    PageSignals,
    PdfSummary,
    StageReason,
    StageStatus,
)
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION, Stage
from app.modules.electrical.drawings.domain.profile import (
    DrawingProfile,
    ProfileSource,
)
from app.modules.electrical.drawings.domain.profile_detection import ProfileProposal, detect_profile
from app.modules.electrical.drawings.ports.files import DrawingFiles
from app.modules.electrical.drawings.ports.ledger import ProcessedEvents
from app.modules.electrical.drawings.ports.liveness import Liveness
from app.modules.electrical.drawings.ports.pdf import PdfInspection
from app.modules.electrical.drawings.ports.repository import (
    CharacterizationRepository,
    DocumentRepository,
    JobRepository,
    PageRepository,
    ProfileRepository,
)
from app.modules.electrical.drawings.ports.rules import RuleSource
from app.modules.electrical.drawings.ports.source import SourceStager, StagedSource
from app.modules.electrical.shared.kinds import Convention, ConventionTally, PlcFamily
from app.modules.electrical.shared.parser import IdentifierParser

_logger = get_logger(__name__)

PDF_REJECTED: Final = "electrical.pdf_rejected"
TOO_MANY_PAGES: Final = "electrical.too_many_pages"
SOURCE_UNREADABLE: Final = "electrical.source_unreadable"
DUPLICATE_DOCUMENT: Final = "electrical.duplicate_document"
_MAX_CREATOR_CHARS: Final = 512  # ck_drawings_documents_pdf_*
_RELEASE_TRIES: Final = 3  # optimistic-lock retries when a run gives its lease back
# ``failure_detail`` for a PDF whose summary could not be read: fixed texts, one per reason
_SUMMARY_FAILED: Final = {
    StageReason.PARSE_TIMEOUT: "the PDF could not be opened in time (parse_timeout)",
    StageReason.MEMORY_LIMIT: "the PDF needs more memory than the limit to open (memory_limit)",
    StageReason.PARSE_ERROR: "the PDF could not be parsed (parse_error)",
}


class _Fingerprint(Enum):
    RECORDED = "recorded"
    GONE = "gone"
    CHANGED = "changed"
    DUPLICATE = "duplicate"


class _Stop(Exception):
    """The run ends here without an error: the job finished, was cancelled, or the document is
    gone. Never leaves ``run``."""


def combine_tallies(tallies: Iterable[ConventionTally | None]) -> ConventionTally:
    """The sum of per-page tallies, in a stable order (enum order, then name)."""
    conventions: Counter[Convention] = Counter()
    families: Counter[PlcFamily] = Counter()
    formats: Counter[str] = Counter()
    identifiers = tokens = 0
    for tally in tallies:
        if tally is None:
            continue
        conventions.update(tally.conventions)
        families.update(tally.plc_families)
        formats.update(tally.cross_reference_formats)
        identifiers += tally.identifiers
        tokens += tally.tokens
    return ConventionTally(
        conventions={c: conventions[c] for c in Convention if conventions[c] > 0},
        plc_families={f: families[f] for f in PlcFamily if families[f] > 0},
        cross_reference_formats=dict(sorted((k, v) for k, v in formats.items() if v > 0)),
        identifiers=identifiers,
        tokens=tokens,
    )


def _runs(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive page numbers, as (first, last)."""
    runs: list[tuple[int, int]] = []
    for page in pages:
        if runs and runs[-1][1] + 1 == page:
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))
    return runs


def _blank_signals(page_no: int) -> PageSignals:
    return PageSignals(
        page_no=page_no,
        width_pt=0.0,
        height_pt=0.0,
        rotation=0,
        char_count=0,
        unmapped_char_count=0,
        pua_char_count=0,
        cjk_char_count=0,
        latin_char_count=0,
        word_count=0,
        vector_path_count=0,
        short_curve_path_count=0,
        image_count=0,
        image_area_ratio=0.0,
        link_count=0,
        fonts=(),
        tokens=(),
    )


def failed_page(page_no: int, reason: StageReason, attempts: int) -> PageResult:
    """A page that could not be read: recorded ``failed`` with its reason and no content
    (kind ``empty``, the neutral value the table allows). ``evidence`` leaves it out."""
    return PageResult(
        classification=PageClassification(
            page_no=page_no,
            kind=PageKind.EMPTY,
            language=PageLanguage.NONE,
            needs_ocr=False,
            status=StageStatus.FAILED,
            reason=reason,
        ),
        signals=_blank_signals(page_no),
        attempts=attempts,
        tally=None,
    )


class InspectDocument:
    def __init__(
        self,
        documents: DocumentRepository,
        jobs: JobRepository,
        pages: PageRepository,
        profiles: ProfileRepository,
        characterizations: CharacterizationRepository,
        files: DrawingFiles,
        stager: SourceStager,
        pdf: PdfInspection,
        rules: RuleSource,
        ledger: ProcessedEvents,
        outbox: EventOutbox,
        uow: UnitOfWork,
        liveness: Liveness,
        settings: ElectricalSettings,
        *,
        clock: Clock = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._pages = pages
        self._profiles = profiles
        self._characterizations = characterizations
        self._files = files
        self._stager = stager
        self._pdf = pdf
        self._rules = rules
        self._ledger = ledger
        self._outbox = outbox
        self._uow = uow
        self._liveness = liveness
        self._settings = settings
        self._clock = clock
        self._monotonic = monotonic
        # The FULL packaged rules: see the module docstring.
        self._parser = IdentifierParser(rules.identifier_rules())

    # ------------------------------------------------------------------ run
    async def run(self, ctx: ExecutionContext, *, job_id: Uuid, event_id: str) -> None:
        try:
            document, job = await self._take(ctx, job_id=job_id, event_id=event_id)
        except _Stop:
            return
        attempt = job.attempt  # this run's token: only it may write the job while it is running
        _logger.info(
            "electrical.inspect.started",
            extra={"job_id": job.id, "document_id": document.id, "attempt": attempt},
        )
        try:
            base = await self._effective_profile(ctx, document)
            if await self._has_remaining_work(ctx, document):
                await self._inspect(ctx, document, job, event_id, base)
            await self._conclude(ctx, document.id, job.id, event_id, base, attempt)
        except _Stop:
            return
        except Exception:
            # a fault: the lease this run holds is of no use to anyone now, and left alive it
            # would turn the engine's redelivery of this very message away (a job `running`
            # with no worker, never retried, never dead-lettered)
            await self._release_lease(ctx, job.id, attempt)
            raise

    async def _release_lease(self, ctx: ExecutionContext, job_id: Uuid, attempt: int) -> None:
        """Give this run's lease back in a short transaction, guarded by the run's ``attempt``
        (a later takeover or a resume has another one and keeps what it holds). Never raises:
        the fault being handled is the one to report."""
        for _ in range(_RELEASE_TRIES):
            try:
                async with self._uow.begin(ctx):
                    job = await self._jobs.get(ctx, job_id)
                    if job is None or not self._owned(job, attempt):
                        return
                    job.release_lease(self._clock())
                    await self._jobs.save(ctx, job)
                return
            except ConflictError:
                continue  # the job moved under us (a cancel request): read it again
            except Exception as exc:
                _logger.warning(
                    "electrical.inspect.release_failed",
                    extra={"job_id": job_id, "error": type(exc).__name__},
                )
                return
        # every try met a conflict: the lease stays, and the redelivery will be turned away
        _logger.warning(
            "electrical.inspect.release_failed",
            extra={"job_id": job_id, "error": "ConflictError"},
        )

    @staticmethod
    def _owned(job: IngestionJob, attempt: int) -> bool:
        """The run that took the job at ``attempt`` still owns it."""
        return job.status is JobStatus.RUNNING and job.attempt == attempt

    # ------------------------------------------------------------- 1. lease
    async def _take(
        self, ctx: ExecutionContext, *, job_id: Uuid, event_id: str
    ) -> tuple[Document, IngestionJob]:
        """Step 1. The run leaves the unit of work BEFORE it stops: an exception inside would
        roll back the claim and the cancellation it just wrote."""
        stop = False
        document: Document | None = None
        async with self._uow.begin(ctx):
            job = await self._jobs.get(ctx, job_id)
            if job is None or job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                await self._ledger.claim(ctx, event_id=event_id)  # nothing to do, ever
                stop = True
            else:
                document = await self._documents.lock_live(ctx, job.document_id)
                now = self._clock()
                if document is None:
                    job.cancel(now, code=DOCUMENT_NOT_LIVE)
                    await self._jobs.save(ctx, job)
                    await emit(ctx, self._outbox, [job_completed(job, now)])
                    await self._ledger.claim(ctx, event_id=event_id)
                    stop = True
                elif not job.take_lease(now, self._settings.job_lease_s):
                    stop = True  # a live lease elsewhere: that run completes (INC1-D-9)
                else:
                    job.stage = Stage.CLASSIFY
                    await self._jobs.save(ctx, job)
        if stop or job is None or document is None:
            _logger.info("electrical.inspect.not_taken", extra={"job_id": job_id})
            raise _Stop
        return document, job

    async def _effective_profile(self, ctx: ExecutionContext, document: Document) -> DrawingProfile:
        """The document's own latest version when accepted or confirmed, else the project's."""
        if document.profile_state in (ProfileState.ACCEPTED, ProfileState.CONFIRMED):
            own = await self._profiles.latest(
                ctx, project_id=document.project_id, document_id=document.id
            )
            if own is not None:
                return own.profile
        project = await self._profiles.latest(ctx, project_id=document.project_id, document_id=None)
        return DrawingProfile.default() if project is None else project.profile

    async def _has_remaining_work(self, ctx: ExecutionContext, document: Document) -> bool:
        unfinished = await self._pages.unfinished(ctx, document.id, Stage.CLASSIFY)
        if unfinished:
            return True
        return await self._characterizations.get(ctx, document.id) is None

    # ----------------------------------------------------- 3..5. the bytes
    async def _inspect(
        self,
        ctx: ExecutionContext,
        document: Document,
        job: IngestionJob,
        event_id: str,
        base: DrawingProfile,
    ) -> None:
        log = {"job_id": job.id, "document_id": document.id}
        file = await self._files.get_readable(ctx, document.file_id)
        if file is None:
            await self._fail(
                ctx,
                job.id,
                job.attempt,
                event_id,
                SOURCE_UNREADABLE,
                "the file is no longer readable",
            )
        try:
            source = await self._stager.stage(
                ctx,
                document_id=document.id,
                storage_key=file.storage_key,
                expected_size=file.size_bytes,
            )
        except (NotFoundError, ValidationError) as exc:
            if isinstance(exc, ValidationError) and exc.code != SOURCE_UNREADABLE:
                raise
            await self._fail(ctx, job.id, job.attempt, event_id, SOURCE_UNREADABLE, str(exc))
        try:
            await self._record_fingerprint(ctx, document.id, job.id, job.attempt, source, event_id)
            try:
                await self._pages_stage(ctx, document, job, source, base, event_id)
            except PdfRejected as exc:
                _logger.info("electrical.inspect.pdf_rejected", extra={**log, "code": exc.code})
                await self._fail(ctx, job.id, job.attempt, event_id, PDF_REJECTED, exc.detail)
        finally:
            await self._stager.discard(source)

    async def _record_fingerprint(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        job_id: Uuid,
        attempt: int,
        source: StagedSource,
        event_id: str,
    ) -> None:
        """Step 4. The bytes already an ACTIVE document of the project: exclude, fail. A
        reprocessing must see the bytes the document was first read from."""
        outcome = await self._try_record(ctx, document_id, source)
        if outcome is _Fingerprint.GONE:
            await self._stop_not_live(ctx, job_id, attempt, event_id)
        if outcome is _Fingerprint.CHANGED:
            await self._fail(
                ctx,
                job_id,
                attempt,
                event_id,
                SOURCE_UNREADABLE,
                "the file's content changed since it was first read",
            )
        if outcome is _Fingerprint.DUPLICATE:
            await self._exclude_duplicate(ctx, document_id, job_id, attempt, source, event_id)

    async def _try_record(
        self, ctx: ExecutionContext, document_id: Uuid, source: StagedSource
    ) -> _Fingerprint:
        try:
            async with self._uow.begin(ctx):
                document = await self._documents.lock_live(ctx, document_id)
                if document is None:
                    return _Fingerprint.GONE
                if document.content_sha256 not in (None, source.sha256):
                    return _Fingerprint.CHANGED
                twin = await self._documents.active_with_content(
                    ctx, document.project_id, source.sha256
                )
                if twin is not None and twin.id != document.id:
                    return _Fingerprint.DUPLICATE
                document.record_inspection(
                    sha256=source.sha256,
                    page_count=document.page_count or 0,
                    creator=document.pdf_creator,
                    producer=document.pdf_producer,
                    pipeline_version=PIPELINE_VERSION,
                    now=self._clock(),
                )
                await self._documents.save(ctx, document)
                return _Fingerprint.RECORDED
        except ConflictError as exc:
            if exc.code != DUPLICATE_DOCUMENT:
                raise
            return _Fingerprint.DUPLICATE  # lost a race with a concurrent identical document

    async def _exclude_duplicate(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        job_id: Uuid,
        attempt: int,
        source: StagedSource,
        event_id: str,
    ) -> NoReturn:
        async with self._uow.begin(ctx):
            document = await self._documents.lock_live(ctx, document_id)
            job = await self._jobs.get(ctx, job_id)
            if document is None or job is None or not self._owned(job, attempt):
                raise _Stop  # taken over: write nothing
            now = self._clock()
            twin = await self._documents.active_with_content(
                ctx, document.project_id, source.sha256
            )
            document.record_inspection(
                sha256=source.sha256,
                page_count=document.page_count or 0,
                creator=document.pdf_creator,
                producer=document.pdf_producer,
                pipeline_version=PIPELINE_VERSION,
                now=now,
            )
            document.exclude(ExclusionReason.DUPLICATE_CONTENT, now)
            await self._documents.save(ctx, document)
            other = "another document" if twin is None else f"document {twin.id}"
            job.fail(
                DUPLICATE_DOCUMENT, f"the same content is already {other} of this project", now
            )
            await self._jobs.save(ctx, job)
            await emit(ctx, self._outbox, [job_completed(job, now)])
            await self._ledger.claim(ctx, event_id=event_id)
        _logger.info(
            "electrical.inspect.duplicate", extra={"job_id": job_id, "document_id": document_id}
        )
        raise _Stop

    async def _pages_stage(
        self,
        ctx: ExecutionContext,
        document: Document,
        job: IngestionJob,
        source: StagedSource,
        base: DrawingProfile,
        event_id: str,
    ) -> None:
        try:
            summary = await self._pdf.summary(source)
        except PageRangeFailed as exc:
            # the file cannot even be opened within the limits: not a page's fault, the file's
            _logger.info(
                "electrical.inspect.pdf_rejected",
                extra={"job_id": job.id, "document_id": document.id, "code": exc.reason.value},
            )
            await self._fail(
                ctx, job.id, job.attempt, event_id, PDF_REJECTED, _SUMMARY_FAILED[exc.reason]
            )
        if summary.page_count > self._settings.max_pages:
            await self._fail(
                ctx,
                job.id,
                job.attempt,
                event_id,
                TOO_MANY_PAGES,
                f"the PDF has {summary.page_count} pages; the limit is {self._settings.max_pages}",
            )
        wanted = (
            tuple(p for p in job.pages if p <= summary.page_count)
            if job.pages is not None and job.kind is JobKind.REPROCESS
            else tuple(range(1, summary.page_count + 1))
        )
        async with self._uow.begin(ctx):
            locked = await self._documents.lock_live(ctx, document.id)
            if locked is None:
                await self._stop_not_live(ctx, job.id, job.attempt, event_id)
            locked.record_inspection(
                sha256=source.sha256,
                page_count=summary.page_count,
                creator=clip_metadata(summary.creator),
                producer=clip_metadata(summary.producer),
                pipeline_version=PIPELINE_VERSION,
                now=self._clock(),
            )
            await self._documents.save(ctx, locked)
            await self._pages.seed(ctx, document.id, wanted, Stage.CLASSIFY, PIPELINE_VERSION)
        todo = list(await self._pages.unfinished(ctx, document.id, Stage.CLASSIFY))
        batch_size = self._settings.inspect_batch_pages
        last_emit: float | None = None
        for start in range(0, len(todo), batch_size):
            batch = todo[start : start + batch_size]
            await self._honour_cancel_and_purge(ctx, document.id, job.id, event_id, job.attempt)
            results = await self._classify(
                ctx,
                source,
                batch,
                base.classification,
                job,
            )
            is_last = start + batch_size >= len(todo)
            last_emit = await self._commit_batch(
                ctx, document.id, job.id, job.attempt, results, is_last, last_emit
            )
            self._liveness.beat()

    async def _classify(
        self,
        ctx: ExecutionContext,
        source: StagedSource,
        batch: Sequence[int],
        thresholds: ClassificationThresholds,
        job: IngestionJob,
    ) -> list[PageResult]:
        log = {"job_id": job.id, "document_id": job.document_id}
        results: list[PageResult] = []
        for first, last in _runs(batch):
            try:
                signals = await self._pdf.pages(source, first, last)
                results.extend(self._result(s, thresholds, 1) for s in signals)
                got = {s.page_no for s in signals}
                results.extend(
                    self._failed(n, StageReason.PARSE_ERROR, 1, log)
                    for n in range(first, last + 1)
                    if n not in got
                )
            except PageRangeFailed:
                # a range that failed is asked again page by page: only the culprit is lost. The
                # page-by-page pass can outlast the lease (each page has its own time limit), so
                # the lease is extended after every page, which also proves we still own the job.
                for number in range(first, last + 1):
                    results.append(await self._one_page(source, number, thresholds, log))
                    await self._keep_lease(ctx, job.id, job.attempt)
        return sorted(results, key=lambda r: r.classification.page_no)

    async def _keep_lease(self, ctx: ExecutionContext, job_id: Uuid, attempt: int) -> None:
        async with self._uow.begin(ctx):
            job = await self._jobs.get(ctx, job_id)
            if job is None or not self._owned(job, attempt):
                raise _Stop  # taken over, resumed, cancelled or purged: write nothing more
            job.extend_lease(self._clock(), self._settings.job_lease_s)
            await self._jobs.save(ctx, job)

    async def _one_page(
        self,
        source: StagedSource,
        number: int,
        thresholds: ClassificationThresholds,
        log: dict[str, str],
    ) -> PageResult:
        try:
            found = await self._pdf.pages(source, number, number)
        except PageRangeFailed as exc:
            return self._failed(number, exc.reason, 2, log)
        for signals in found:
            if signals.page_no == number:
                return self._result(signals, thresholds, 2)
        return self._failed(number, StageReason.PARSE_ERROR, 2, log)

    def _result(
        self, signals: PageSignals, thresholds: ClassificationThresholds, attempts: int
    ) -> PageResult:
        return PageResult(
            classification=classify_page(signals, thresholds),
            signals=signals,
            attempts=attempts,
            tally=self._parser.tally(signals.tokens),
        )

    @staticmethod
    def _failed(number: int, reason: StageReason, attempts: int, log: dict[str, str]) -> PageResult:
        _logger.warning(
            "electrical.inspect.page_failed",
            extra={**log, "page": number, "reason": reason.value},
        )
        return failed_page(number, reason, attempts)

    async def _honour_cancel_and_purge(
        self, ctx: ExecutionContext, document_id: Uuid, job_id: Uuid, event_id: str, attempt: int
    ) -> None:
        document = await self._documents.get(ctx, document_id)
        job = await self._jobs.get(ctx, job_id)
        if job is None or not self._owned(job, attempt):
            raise _Stop  # cancelled by a purge, or taken over: nothing more to say
        if document is None or not document.is_live:
            await self._stop_not_live(ctx, job_id, attempt, event_id)
        if job.cancel_requested_at is not None:
            async with self._uow.begin(ctx):
                fresh = await self._jobs.get(ctx, job_id)
                if fresh is None or not self._owned(fresh, attempt):
                    raise _Stop
                now = self._clock()
                fresh.cancel(now)
                await self._jobs.save(ctx, fresh)
                await emit(ctx, self._outbox, [job_completed(fresh, now)])
                await self._ledger.claim(ctx, event_id=event_id)
            _logger.info(
                "electrical.inspect.cancelled", extra={"job_id": job_id, "document_id": document_id}
            )
            raise _Stop

    async def _commit_batch(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        job_id: Uuid,
        attempt: int,
        results: Sequence[PageResult],
        is_last: bool,
        last_emit: float | None,
    ) -> float | None:
        events: list[DrawingsEvent] = []
        async with self._uow.begin(ctx):
            document = await self._documents.lock_live(ctx, document_id)
            job = await self._jobs.get(ctx, job_id)
            if document is None or job is None or not self._owned(job, attempt):
                raise _Stop  # purged or taken over under us: write nothing
            now = self._clock()
            await self._pages.record(ctx, document_id, results)
            job.extend_lease(now, self._settings.job_lease_s)
            moment = self._monotonic()
            due = (
                last_emit is None
                or moment - last_emit >= self._settings.progress_min_interval_s
                or is_last
            )
            if due:
                counts = await self._pages.stage_counts(ctx, document_id, Stage.CLASSIFY)
                total = sum(counts.values())
                pending = counts.get(StageStatus.PENDING, 0) + counts.get(StageStatus.RUNNING, 0)
                events.append(
                    JobProgressed(
                        job_id=job.id,
                        workspace_id=job.workspace_id,
                        document_id=job.document_id,
                        seq=job.next_seq(),
                        stage=Stage.CLASSIFY,
                        done=total - pending,
                        total=total,
                        failed=counts.get(StageStatus.FAILED, 0),
                        needs_review=counts.get(StageStatus.NEEDS_REVIEW, 0),
                        occurred_at=now,
                    )
                )
                last_emit = moment
            await self._jobs.save(ctx, job)
            await emit(ctx, self._outbox, events)
        return last_emit

    # ------------------------------------------------- 6. proposal, the end
    async def _conclude(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        job_id: Uuid,
        event_id: str,
        base: DrawingProfile,
        attempt: int,
    ) -> None:
        document = await self._documents.get(ctx, document_id)
        if document is None:
            raise _Stop
        evidence = await self._pages.evidence(ctx, document_id)
        summary = PdfSummary(
            page_count=document.page_count or 0,
            pdf_version="",
            encrypted=False,
            creator=document.pdf_creator,
            producer=document.pdf_producer,
            outline_count=0,
        )
        classifications = [e.classification for e in evidence]
        tally = combine_tallies(e.tally for e in evidence)
        producers = self._rules.producer_signatures()
        proposal = detect_profile(
            base=base, summary=summary, pages=classifications, tally=tally, producers=producers
        )
        record = characterize(
            document.id,
            document.content_sha256 or "",
            summary,
            classifications,
            [e.signals for e in evidence],
            tally,
            proposal,
            producers,
            rules_version=self._rules.identifier_rules().version,
        )
        async with self._uow.begin(ctx):
            if not await self._ledger.claim(ctx, event_id=event_id):
                raise _Stop  # a duplicate delivery: already done
            locked = await self._documents.lock_live(ctx, document_id)
            job = await self._jobs.get(ctx, job_id)
            if locked is None or job is None or not self._owned(job, attempt):
                raise _Stop
            now = self._clock()
            if job.cancel_requested_at is not None:
                # asked for while the last batch was running (or after it): honoured here, in
                # the transaction that would have ended the job, so the request is never lost
                job.cancel(now)
                await self._jobs.save(ctx, job)
                await emit(ctx, self._outbox, [job_completed(job, now)])
                cancelled = True
            else:
                cancelled = False
                succeeded = await self._settle(ctx, locked, job, record, proposal, base, now)
        if cancelled:
            _logger.info(
                "electrical.inspect.cancelled", extra={"job_id": job_id, "document_id": document_id}
            )
            return
        _logger.info(
            "electrical.inspect.finished",
            extra={
                "job_id": job_id,
                "document_id": document_id,
                "needs_confirmation": not succeeded,
            },
        )

    async def _settle(
        self,
        ctx: ExecutionContext,
        locked: Document,
        job: IngestionJob,
        record: Characterization,
        proposal: ProfileProposal,
        base: DrawingProfile,
        now: datetime,
    ) -> bool:
        """The ordinary end, inside ``_conclude``'s transaction: the characterization, the
        proposed profile version, the document's state and the job's end. ``True`` when the job
        ``succeeded`` (it did not wait for a person)."""
        await self._characterizations.put(ctx, record, project_id=locked.project_id)
        events: list[DrawingsEvent] = []
        succeeded = True
        if locked.profile_state is ProfileState.CONFIRMED:
            job.succeed(now)  # a person already decided; the detection changes nothing
        else:
            await self._profiles.append(
                ctx,
                new_profile_version(
                    ctx,
                    project_id=locked.project_id,
                    document_id=locked.id,
                    source=ProfileSource.DETECTED,
                    profile=proposal.profile,
                    now=now,
                    confidence=proposal.confidence,
                    mixed_standards=proposal.mixed_standards,
                    detection={
                        "field_confidence": dict(proposal.field_confidence),
                        "basis": {k: list(v) for k, v in proposal.basis.items()},
                    },
                    created_by=job.created_by,
                ),
            )
            accepted = proposal.confidence >= base.auto_accept_confidence
            locked.set_profile_state(
                ProfileState.ACCEPTED if accepted else ProfileState.PROPOSED, now
            )
            succeeded = accepted
            if accepted:
                job.succeed(now)
            else:
                job.await_confirmation(now)
            events.append(
                DocumentProfileProposed(
                    document_id=locked.id,
                    workspace_id=locked.workspace_id,
                    job_id=job.id,
                    confidence=proposal.confidence,
                    needs_confirmation=not accepted,
                    mixed_standards=proposal.mixed_standards,
                    occurred_at=now,
                )
            )
        if succeeded:
            events.append(job_completed(job, now))
        await self._documents.save(ctx, locked)
        await self._jobs.save(ctx, job)
        await emit(ctx, self._outbox, events)
        return succeeded

    # ------------------------------------------------------------ endings
    async def _fail(
        self,
        ctx: ExecutionContext,
        job_id: Uuid,
        attempt: int,
        event_id: str,
        code: str,
        detail: str,
    ) -> NoReturn:
        """End the job ``failed`` with a catalogue code, then leave ``run``."""
        async with self._uow.begin(ctx):
            job = await self._jobs.get(ctx, job_id)
            if job is None or not self._owned(job, attempt):
                raise _Stop  # taken over: write nothing
            now = self._clock()
            job.fail(code, detail, now)
            await self._jobs.save(ctx, job)
            await emit(ctx, self._outbox, [job_completed(job, now)])
            await self._ledger.claim(ctx, event_id=event_id)
            document_id = job.document_id
        _logger.info(
            "electrical.inspect.failed",
            extra={"job_id": job_id, "document_id": document_id, "code": code},
        )
        raise _Stop

    async def _stop_not_live(
        self, ctx: ExecutionContext, job_id: Uuid, attempt: int, event_id: str
    ) -> NoReturn:
        """The document stopped being active under a running job: cancel it and leave."""
        async with self._uow.begin(ctx):
            job = await self._jobs.get(ctx, job_id)
            if job is None or not self._owned(job, attempt):
                raise _Stop  # taken over: write nothing
            now = self._clock()
            job.cancel(now, code=DOCUMENT_NOT_LIVE)
            await self._jobs.save(ctx, job)
            await emit(ctx, self._outbox, [job_completed(job, now)])
            await self._ledger.claim(ctx, event_id=event_id)
        raise _Stop


def clip_metadata(text: str | None) -> str | None:
    """A PDF's Creator / Producer as the document row stores them (the column's bound); the
    offline characterization tool clips the same way so both build the same record."""
    return None if text is None else text[:_MAX_CREATOR_CHARS]


__all__ = ["InspectDocument", "clip_metadata", "combine_tallies", "failed_page"]
