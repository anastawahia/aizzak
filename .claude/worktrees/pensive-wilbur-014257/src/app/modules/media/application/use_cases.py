"""Media use-cases (06-domain-models §8).

Thin application services that coordinate the pure domain over injected
ports. They own identity/time (framework ``new_uuid7`` / ``utc_now``) and
translate domain-rule violations into the shared framework error hierarchy at
this boundary — the domain itself stays framework-free (files/knowledge
precedent).

``RequestMedia`` is the only use-case that enforces the *configurable*
numeric ceilings from 07-nfr-slo §4 (``max_image_dim``, ``max_video_seconds``)
against an injected ``Limits`` (INV-MJ3, second half — ``GenParams`` itself
only validates shape, the ``files.RegisterUpload`` precedent); a violation
raises ``ValidationError`` with the stable ``media.invalid_params`` code
(03-api-spec §4 catalog).

``RunMediaJob`` is the worker-facing lifecycle wrapper (06 §8 "RunMediaJob
(worker)"): the future Phase-5 ``media_worker`` becomes a thin adapter over
THIS use-case, not over a provider call directly, so status transitions,
event emission, and idempotent-redelivery guards (DD-09) all live in exactly
one place — the ``knowledge.IndexRegisteredDocument`` precedent, including its
broad ``except Exception`` (retry/DLQ mechanics are a Phase-5 worker concern,
not this use-case's).

``MediaRequestService`` (4.7-d-2) implements the ``MediaRequests`` inbound port
over ``RequestMedia`` + the ``EventOutbox``: the agent-facing entry point, and
the first place in the platform where a use-case's returned events are actually
published rather than discarded.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.settings.settings import Limits
from app.framework.types import Json, Uuid
from app.modules.media.application.event_mapping import to_outbox_record
from app.modules.media.domain.entities import MediaJob
from app.modules.media.domain.errors import MediaError, MediaJobStateError
from app.modules.media.domain.events import (
    MediaEvent,
    MediaFailed,
    MediaGenerated,
    MediaRequested,
)
from app.modules.media.domain.value_objects import AgentKey, GenParams, JobStatus, MediaKind
from app.modules.media.ports.generation import MediaGenerator
from app.modules.media.ports.inbound import RequestedMedia
from app.modules.media.ports.repository import MediaJobRepository

# Idempotent-redelivery guard (DD-09): a terminal job is a silent no-op for
# RunMediaJob -- the generation call never re-runs against it. `succeeded`
# already finished; `failed` is terminal too (INV-MJ2: recovering means
# submitting a brand-new job via RequestMedia, never resurrecting this one).
_TERMINAL = (JobStatus.SUCCEEDED, JobStatus.FAILED)


class RequestMedia:
    """Validate + queue a new generation job (06 §8 ``RequestMedia``).

    Shape validation (``GenParams.from_dict``) and the configured numeric
    ceilings (INV-MJ3) both run *before* the job is queued, per 06 §8's
    invariant text ("قبل الوضع في المجرى") — a job is never persisted, let
    alone published as ``MediaRequested``, with out-of-bounds params.
    """

    def __init__(self, jobs: MediaJobRepository, limits: Limits) -> None:
        self._jobs = jobs
        self._limits = limits

    async def execute(
        self, ctx: ExecutionContext, *, agent_key: str, kind: str, prompt: str, params: Json
    ) -> tuple[MediaJob, tuple[MediaEvent, ...]]:
        try:
            key = AgentKey(agent_key)
        except MediaError as exc:
            raise ValidationError(str(exc)) from exc
        try:
            media_kind = MediaKind(kind)
        except ValueError as exc:
            raise ValidationError(f"invalid media kind: {kind!r}") from exc
        if not prompt.strip():
            raise ValidationError("prompt must not be empty")
        try:
            gen_params = GenParams.from_dict(media_kind, params)
        except MediaError as exc:
            raise ValidationError(str(exc)) from exc

        self._enforce_limits(media_kind, gen_params)

        now = utc_now()
        job = MediaJob(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            agent_key=key,
            kind=media_kind,
            prompt=prompt,
            params=gen_params,
            status=JobStatus.QUEUED,
            result_file_id=None,
            error=None,
            created_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        await self._jobs.add(ctx, job)
        event = MediaRequested(
            job.id, ctx.workspace_id, media_kind.value, prompt, key.value, gen_params.as_dict(), now
        )
        return job, (event,)

    def _enforce_limits(self, kind: MediaKind, params: GenParams) -> None:
        """INV-MJ3 (ceiling half): the configurable 07-nfr-slo §4 maxima,
        checked against the injected ``Limits`` — never hard-coded (the
        ``files.RegisterUpload`` precedent). Raises with the stable
        ``media.invalid_params`` code (03-api-spec §4)."""
        if kind is MediaKind.IMAGE:
            width, height = params.width, params.height
            if width is not None and width > self._limits.max_image_dim:
                raise ValidationError(
                    f"image width {width} exceeds the {self._limits.max_image_dim}px limit",
                    code="media.invalid_params",
                )
            if height is not None and height > self._limits.max_image_dim:
                raise ValidationError(
                    f"image height {height} exceeds the {self._limits.max_image_dim}px limit",
                    code="media.invalid_params",
                )
        else:
            duration = params.duration_seconds
            if duration is not None and duration > self._limits.max_video_seconds:
                raise ValidationError(
                    f"video duration {duration}s exceeds the "
                    f"{self._limits.max_video_seconds}s limit",
                    code="media.invalid_params",
                )


@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    """The I/O phase's outcome (``RunMediaJob.run``), awaiting its terminal
    transaction (``finalize``) — the ``knowledge.IndexAttempt`` precedent.

    Exactly one of ``result_file_id``/``error`` is set when generation
    actually ran (``MediaGenerator.generate`` returns a file id, never
    ``None``); both ``None`` means the job was already terminal — the DD-09
    idempotent-redelivery no-op, for which ``finalize`` is a pure
    pass-through.
    """

    job: MediaJob
    result_file_id: Uuid | None
    error: str | None

    @property
    def is_redelivery_noop(self) -> bool:
        return self.result_file_id is None and self.error is None


class RunMediaJob:
    """Run generation for a queued/in-flight job and persist the outcome (06
    §8 "RunMediaJob (worker)").

    The Phase-5 ``media_worker`` is a thin adapter over this use-case rather
    than over the provider call directly, so all lifecycle bookkeeping lives
    here, not in the worker.

    **Split in 5.2-أ into ``run`` (I/O phase) + ``finalize`` (terminal
    phase), closing 5.1's documented D5 terminal window — the
    ``knowledge.IndexRegisteredDocument`` precedent.** ``run`` claims the job
    (``queued → running``, its own short transaction) and calls the generator
    — external I/O that must never run under an open DB transaction (R2).
    ``finalize`` persists the terminal outcome: status + the follow-on event.
    The worker handler wraps ``finalize`` (plus the outbox append and the
    DD-09 ``processed_events`` claim) in ONE ``uow.begin`` block. ``execute``
    composes the two halves with per-call transactions — byte-for-byte the
    pre-split behaviour.
    """

    def __init__(self, jobs: MediaJobRepository, generator: MediaGenerator) -> None:
        self._jobs = jobs
        self._generator = generator

    async def execute(
        self, ctx: ExecutionContext, *, job_id: Uuid
    ) -> tuple[MediaJob, tuple[MediaEvent, ...]]:
        attempt = await self.run(ctx, job_id=job_id)
        return await self.finalize(ctx, attempt)

    async def run(self, ctx: ExecutionContext, *, job_id: Uuid) -> GenerationAttempt:
        job = await self._jobs.get(ctx, job_id)
        if job is None:
            raise NotFoundError("media job not found")

        if job.status in _TERMINAL:
            return GenerationAttempt(job=job, result_file_id=None, error=None)

        now = utc_now()
        try:
            job.start_running(now)
        except MediaJobStateError as exc:
            raise ConflictError(str(exc)) from exc
        await self._jobs.save(ctx, job)

        try:
            result_file_id = await self._generator.generate(
                ctx, kind=job.kind, prompt=job.prompt, params=job.params
            )
        except Exception as exc:
            # Broad catch is deliberate (knowledge.IndexRegisteredDocument
            # precedent): ANY generation failure (provider outage, a
            # rejected prompt, a storage error persisting the result through
            # `files`) must land the job in `failed` with its reason
            # recorded, not crash the caller. Retry/DLQ mechanics belong to
            # the worker's redelivery handling; the failure is carried as
            # data to `finalize` and never re-raised.
            return GenerationAttempt(job=job, result_file_id=None, error=str(exc))

        return GenerationAttempt(job=job, result_file_id=result_file_id, error=None)

    async def finalize(
        self, ctx: ExecutionContext, attempt: GenerationAttempt
    ) -> tuple[MediaJob, tuple[MediaEvent, ...]]:
        job = attempt.job

        if attempt.result_file_id is not None:
            now = utc_now()
            job.complete(attempt.result_file_id, now)
            await self._jobs.save(ctx, job)
            generated_event = MediaGenerated(job.id, ctx.workspace_id, attempt.result_file_id, now)
            return job, (generated_event,)

        if attempt.error is not None:
            now = utc_now()
            job.fail(attempt.error, now)
            await self._jobs.save(ctx, job)
            failed_event = MediaFailed(job.id, ctx.workspace_id, attempt.error, now)
            return job, (failed_event,)

        # Redelivery no-op (both fields None): nothing ran, nothing to persist.
        return job, ()


class GetJobStatus:
    """Fetch a single ``MediaJob`` by id (06 §8 ``GetJobStatus``)."""

    def __init__(self, jobs: MediaJobRepository) -> None:
        self._jobs = jobs

    async def execute(self, ctx: ExecutionContext, job_id: Uuid) -> MediaJob:
        job = await self._jobs.get(ctx, job_id)
        if job is None:
            raise NotFoundError("media job not found")
        return job


class MediaRequestService:
    """Implements the ``MediaRequests`` inbound port (02 §2) over the
    ``RequestMedia`` use-case plus the Outbox (the ``FilesQueryService`` /
    ``KnowledgeRetrievalService`` precedent for an inbound port composed over an
    existing use-case).

    This is the first caller in the platform that does not drop a use-case's
    returned events on the floor. Every Phase-3 use-case returns
    ``tuple[Aggregate, tuple[Event, ...]]``; until now every caller ignored the
    second element, because no outbox writer existed (4.7-d-1's discovery).

    **An outbox failure is deliberately NOT swallowed.** If the append raises,
    the caller gets the error and the agent surfaces a failure. Catching it
    would hand the user a job id for a job that nothing will ever pick up —
    a queued row with no event is invisible to the Phase-5 ``media_worker``.
    Failing loudly is the honest outcome; the persisted row is then an orphan a
    reconciler can find, not a promise silently broken.

    **Atomic since 5.1-أ.** ``uow.begin(ctx)`` opens ONE transaction for the
    whole call: the aggregate write (inside ``RequestMedia``, through the
    injected ``MediaJobRepository``) and this append both resolve their
    session through the SAME ``TenantSessionFactory`` — its ``__call__``
    detects the active unit of work and joins it rather than opening a
    second transaction — so 04 §3.1's "لا فقد" guarantee ("الأثر النطاقي +
    صف Outbox في نفس المعاملة") now holds for real. Before 5.1-أ this was two
    transactions (``infrastructure/persistence/outbox.py`` records that
    history honestly): a crash between them could leave a job with no event.
    That gap is now closed.
    """

    def __init__(self, request_media: RequestMedia, outbox: EventOutbox, uow: UnitOfWork) -> None:
        self._request_media = request_media
        self._outbox = outbox
        self._uow = uow

    async def submit(
        self, ctx: ExecutionContext, *, agent_key: str, kind: str, prompt: str, params: Json
    ) -> MediaJob:
        """The atomic core, returning the FULL aggregate (6.1-هـ-2).

        Split out of ``request`` for the API layer: 03 §2's ``MediaJobOut``
        needs ``result_file_id``/``error``/``created_at``, which the
        agent-facing three-field ``RequestedMedia`` deliberately omits.
        Re-reading the job after ``request`` would be a second transaction to
        recover data this method already held — so the transaction/outbox
        logic lives here once, and ``request`` is a thin projection over it
        (zero wire change for the agents' inbound port).
        """
        async with self._uow.begin(ctx):
            job, events = await self._request_media.execute(
                ctx, agent_key=agent_key, kind=kind, prompt=prompt, params=params
            )
            await self._outbox.append(ctx, [to_outbox_record(ctx, event) for event in events])
            return job

    async def request(
        self, ctx: ExecutionContext, *, agent_key: str, kind: str, prompt: str, params: Json
    ) -> RequestedMedia:
        job = await self.submit(ctx, agent_key=agent_key, kind=kind, prompt=prompt, params=params)
        return RequestedMedia(job_id=job.id, status=job.status.value, kind=job.kind.value)


@dataclass(frozen=True, slots=True)
class MediaUseCases:
    """The bundle the API layer consumes (6.1-هـ-2, the ``ConversationUseCases``
    precedent): ``requests.submit`` for ``POST /media/jobs`` and ``get_status``
    for ``GET /media/jobs/{id}`` — the first caller ``GetJobStatus`` has ever
    had, which is why the Composition Root only now constructs it."""

    requests: MediaRequestService
    get_status: GetJobStatus
