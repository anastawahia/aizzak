"""The Media router — ``/api/v1/media`` (03-api-spec §1 · FR-100) — Phase
6.1-هـ-3.

Two routes over the ``MediaUseCases`` bundle (§3.60):

* ``POST /media/jobs`` — enqueue a heavy generation job (**202** Accepted);
* ``GET /media/jobs/{id}`` — read one job back.

**202, deliberately the odd one out** (openapi.yaml's own status): the POST
does not create a finished resource — it queues work the Phase-5
``media_worker`` performs later — so 201 would promise a result that does not
exist yet. The body is the job's full face with ``result_file_id``/``error``
still ``null``; the client polls the GET (or receives the 5.3-د notification)
until the worker fills them in. That asymmetry is exactly why ``submit``
returns the whole aggregate (§3.60) — this router just renders it.

The interesting failure faces are the use-case's own: an out-of-bounds
dimension/duration is ``media.invalid_params``/422 (INV-MJ3), a blank prompt
422, an unknown job ``common.not_found``/404 — through the app-level RFC 9457
handlers. A ``kind`` outside the contract's ``Literal`` never reaches the
use-case at all (DTO validation, ``common.validation_error``/422).

**``Idempotency-Key`` (3.79)** — the files router's note, and this is the route
that needed it most: a duplicate ``POST /media/jobs`` enqueues a SECOND
generation job, which the media worker then actually runs and the workspace
actually pays for. Backed by the Postgres ledger (``api/v1/idempotency.py``):
same key + same body replays the first ``MediaJobOut`` (same job id, so the
client polls the job that exists rather than one it accidentally created);
same key + a different body is a 409. Absent header ⇒ the previous behaviour
exactly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.media import MediaJobCreateIn, MediaJobOut
from app.api.v1.idempotency import IdempotencyKey, idempotent
from app.modules.access.domain.value_objects import Permission
from app.modules.media.domain.entities import MediaJob

# Belt and braces, knowingly (the §3.56 precedent): both routes build `ctx`.
router = APIRouter(prefix="/media", tags=["media"], dependencies=[Depends(current_principal)])


def _to_job_out(job: MediaJob) -> MediaJobOut:
    return MediaJobOut(
        id=job.id,
        kind=job.kind.value,
        status=job.status.value,
        result_file_id=job.result_file_id,
        error=job.error,
        created_at=job.created_at,
    )


@router.post("/jobs", status_code=202, dependencies=[Depends(require(Permission.MEDIA_CREATE))])
async def create_media_job(
    body: MediaJobCreateIn,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> MediaJobOut:
    """Queue a generation job (D-04: the answer is a job to await, never an
    asset). ``submit`` is the same atomic core the agents use — one
    transaction for the row and its ``MediaRequested`` event.

    A replay returns the job as it was AT SUBMISSION (``status: "queued"``),
    not as it is now — this route's contract is "here is the job you queued",
    and the current state is what ``GET /media/jobs/{id}`` is for.
    """

    async def _submit() -> MediaJobOut:
        job = await services.media.requests.submit(
            ctx, agent_key=body.agent_key, kind=body.kind, prompt=body.prompt, params=body.params
        )
        return _to_job_out(job)

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /media/jobs",
        key=idempotency_key,
        body=body,
        model=MediaJobOut,
        run=_submit,
    )


@router.get("/jobs/{job_id}", dependencies=[Depends(require(Permission.MEDIA_READ))])
async def get_media_job(job_id: str, services: Services, ctx: Context) -> MediaJobOut:
    """One job, bare — terminal or not; the worker's outcome shows up in
    ``result_file_id``/``error`` when it lands. Unknown or another tenant's ⇒
    404."""
    job = await services.media.get_status.execute(ctx, job_id)
    return _to_job_out(job)
