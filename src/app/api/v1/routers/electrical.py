"""The WireTwin router -- ``/api/v1/electrical`` (LLD 3.3.14, 03-api-spec section 1).

Twenty operations, each a thin delegate over the ``DrawingsUseCases`` bundle:

* projects (one per space, SRS D-29): ``POST/GET /projects``, ``GET/PATCH /projects/{id}``,
  ``GET/PUT /projects/{id}/profile``, ``GET /projects/{id}/characterization``;
* documents: ``POST/GET /projects/{id}/documents`` (registration is STG-00 and answers 202),
  ``GET /documents/{id}``, ``GET/PUT /documents/{id}/profile``,
  ``POST /documents/{id}/profile-confirmation`` and ``POST /documents/{id}/reprocessing`` (202),
  ``GET /documents/{id}/pages``, ``GET /documents/{id}/characterization``;
* jobs: ``GET /ingestion-jobs/{id}``, ``POST .../cancel`` (200), ``POST .../resume`` (202);
* ``GET /files/{id}/replacement-preview`` (PLT-24): what registering or re-tagging a file would
  replace. A read.

**Two gates before any handler, in this order** (the router's own ``dependencies``):
``current_principal`` (401 for an anonymous caller) and ``electrical_enabled``
(``503 electrical.disabled`` while ``ELECTRICAL_ENABLED=false``, INC1-D-12). The permission guard
of each route comes AFTER both: asking whether a caller may use a feature that is not there makes
no sense, so the 503 wins over a 403.

**The four routes that answer 202** (register, confirm, reprocess, resume) are the queue's
entrances: each carries ``Depends(heavy_job)`` after its permission guard and runs inside
``idempotent(...)`` (``Idempotency-Key``), exactly ``routers/knowledge.py``'s shape.
``test_heavy_job_limit.py`` pins the guarded set to the 202 set in both directions. The fingerprint
of a keyed request includes the id in the path, so one key cannot replay one document's answer for
another's.

An unwired bundle (``services.electrical is None``) is ``common.internal``, loud and not an empty
answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel

from app.api.middleware.heavy_jobs import heavy_job
from app.api.middleware.rbac import require
from app.api.v1.dependencies import (
    Context,
    Services,
    current_principal,
    electrical_enabled,
)
from app.api.v1.dto.electrical import (
    AffectedDocumentOut,
    CharacterizationOut,
    CharacterizationReportOut,
    DocumentPageOut,
    DocumentRegisteredOut,
    DocumentRegisterIn,
    DrawingProfileIn,
    DrawingProfileOut,
    EffectiveProfileOut,
    ElectricalDocumentOut,
    ElectricalProjectIn,
    ElectricalProjectOut,
    ElectricalProjectPatchIn,
    IngestionJobOut,
    KeptNamesakeOut,
    PageStageOut,
    ProfileConfirmIn,
    ProfileProposalOut,
    ReplacedFileOut,
    ReplacementPreviewOut,
    ReprocessIn,
)
from app.api.v1.dto.files import FileTagsOut
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.api.v1.idempotency import IdempotencyKey, idempotent
from app.framework.errors import AppError
from app.framework.observability.metrics import ele_documents_excluded_total
from app.framework.observability.untrusted import escape_untrusted_text
from app.modules.access.domain.value_objects import Permission
from app.modules.electrical.drawings.application.documents import (
    DocumentView,
    EffectiveProfile,
)
from app.modules.electrical.drawings.application.jobs import JobView
from app.modules.electrical.drawings.application.projects import ProjectDetail
from app.modules.electrical.drawings.application.replacement import ReplacementPreview
from app.modules.electrical.drawings.application.use_cases import DrawingsUseCases
from app.modules.electrical.drawings.domain.characterization import (
    Characterization,
    CharacterizationReport,
)
from app.modules.electrical.drawings.domain.document import DocumentStatus
from app.modules.electrical.drawings.domain.pages import PageRow
from app.modules.electrical.drawings.domain.pipeline import Stage
from app.modules.electrical.drawings.domain.profile import ProfileVersion
from app.modules.electrical.drawings.domain.project import Project

_UNWIRED = "WireTwin is not configured on this deployment"

router = APIRouter(
    prefix="/electrical",
    tags=["electrical"],
    dependencies=[Depends(current_principal), Depends(electrical_enabled)],
)

_READ = Depends(require(Permission.ELECTRICAL_READ))
_MANAGE = Depends(require(Permission.ELECTRICAL_MANAGE))


def _bundle(services: Services) -> DrawingsUseCases:
    bundle = services.electrical
    if bundle is None:
        raise AppError(_UNWIRED, code="common.internal")
    return bundle


class _Scope(BaseModel):
    """What an ``Idempotency-Key`` is fingerprinted over: the id in the path AND the request body.
    Without the id, one key could replay a first answer into a different document's request."""

    subject: str
    payload: dict[str, Any] | None = None


# --------------------------------------------------------------------------- mapping
def _project_out(project: Project) -> ElectricalProjectOut:
    return ElectricalProjectOut(
        id=project.id,
        space_id=project.space_id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _profile_out(version: ProfileVersion) -> DrawingProfileOut:
    return DrawingProfileOut(
        version=version.version,
        source=version.source.value,
        fingerprint=version.fingerprint,
        body=version.profile.to_body(),
        created_at=version.created_at,
    )


def _plain_confidences(raw: object) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): float(value)
        for key, value in raw.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }


def _plain_basis(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): [str(item) for item in values]
        for key, values in raw.items()
        if isinstance(values, list | tuple)
    }


def _proposal_out(version: ProfileVersion) -> ProfileProposalOut:
    """``field_confidence`` and ``basis`` come from the version's ``detection`` (LLD 4.3); a
    version that stored none answers empty maps."""
    detection = version.detection
    return ProfileProposalOut(
        confidence=0.0 if version.confidence is None else version.confidence,
        field_confidence=_plain_confidences(
            None if detection is None else detection.get("field_confidence")
        ),
        mixed_standards=version.mixed_standards,
        basis=_plain_basis(None if detection is None else detection.get("basis")),
        body=version.profile.to_body(),
    )


def _effective_out(effective: EffectiveProfile) -> EffectiveProfileOut:
    return EffectiveProfileOut(
        state=effective.state.value,
        effective=_profile_out(effective.effective),
        project_version=effective.project_version,
        proposal=None if effective.proposal is None else _proposal_out(effective.proposal),
    )


def _job_out(view: JobView) -> IngestionJobOut:
    job = view.job
    return IngestionJobOut(
        id=job.id,
        document_id=job.document_id,
        kind=job.kind.value,
        status=job.status.value,
        stage=None if job.stage is None else job.stage.value,
        pages=None if job.pages is None else list(job.pages),
        counts={status.value: count for status, count in view.counts.items()},
        seq=job.seq,
        attempt=job.attempt,
        failure_code=job.failure_code,
        failure_detail=job.failure_detail,
        queue_position=view.queue_position,
        estimated_wait_s=view.estimated_wait_s,
        stale=view.stale,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _tags_out(tags: Mapping[str, str]) -> FileTagsOut:
    return FileTagsOut(
        system=tags.get("system"),
        equipment=tags.get("equipment"),
        department=tags.get("department"),
    )


def _document_out(view: DocumentView) -> ElectricalDocumentOut:
    document = view.document
    return ElectricalDocumentOut(
        id=document.id,
        project_id=document.project_id,
        file_id=document.file_id,
        file_name=view.file_name,
        tags=None if view.tags is None else _tags_out(view.tags),
        status=document.status.value,
        exclusion_reason=(
            None if document.exclusion_reason is None else document.exclusion_reason.value
        ),
        page_count=document.page_count,
        content_sha256=document.content_sha256,
        profile_state=document.profile_state.value,
        latest_job=None if view.latest_job is None else _job_out(view.latest_job),
        created_at=document.created_at,
    )


def _page_out(row: PageRow) -> DocumentPageOut:
    classification = row.classification
    return DocumentPageOut(
        page_no=row.page_no,
        kind=None if classification is None else classification.kind.value,
        language=None if classification is None else classification.language.value,
        needs_ocr=None if classification is None else classification.needs_ocr,
        stages=[
            PageStageOut(
                stage=stage.value,
                status=status.value,
                reason=(
                    classification.reason.value
                    if stage is Stage.CLASSIFY
                    and classification is not None
                    and classification.reason is not None
                    else None
                ),
                attempts=row.attempts,
            )
            for stage, status in row.stages.items()
        ],
    )


def _characterization_out(item: Characterization) -> CharacterizationOut:
    return CharacterizationOut(
        document_id=item.document_id,
        content_sha256=item.content_sha256,
        producer_family=item.producer_family,
        # untrusted PDF metadata: escaped on the way out (R1-4)
        creator=None if item.creator is None else escape_untrusted_text(item.creator),
        producer=None if item.producer is None else escape_untrusted_text(item.producer),
        page_count=item.page_count,
        text_kinds={kind.value: n for kind, n in item.text_kinds.items()},
        page_languages={lang.value: n for lang, n in item.page_languages.items()},
        document_language=item.document_language,
        conventions={c.value: n for c, n in item.conventions.items()},
        symbol_standard_guess=(
            None if item.symbol_standard_guess is None else item.symbol_standard_guess.value
        ),
        symbol_standard_basis=list(item.symbol_standard_basis),
        plc_families={f.value: n for f, n in item.plc_families.items()},
        cross_reference_formats=dict(item.cross_reference_formats),
        ocr_needed_pages=item.ocr_needed_pages,
        cid_fonts_without_tounicode=item.cid_fonts_without_tounicode,
        pipeline_version=item.pipeline_version,
        shared_version=item.shared_version,
        rules_version=item.rules_version,
        mixed_standards=item.mixed_standards,
        failed_pages=item.failed_pages,
    )


def _report_out(report: CharacterizationReport) -> CharacterizationReportOut:
    return CharacterizationReportOut(
        documents=report.documents,
        pages=report.pages,
        failed_pages=report.failed_pages,
        producers=dict(report.producers),
        text_kinds={kind.value: n for kind, n in report.text_kinds.items()},
        document_languages=dict(report.document_languages),
        conventions={c.value: n for c, n in report.conventions.items()},
        symbol_standards=dict(report.symbol_standards),
        plc_families={f.value: n for f, n in report.plc_families.items()},
        ocr_needed_ratio=report.ocr_needed_ratio,
        mixed_standard_documents=report.mixed_standard_documents,
        versions={name: list(found) for name, found in report.versions.items()},
    )


def _preview_out(preview: ReplacementPreview) -> ReplacementPreviewOut:
    return ReplacementPreviewOut(
        file_id=preview.file_id,
        replaced=[
            ReplacedFileOut(
                file_id=replaced.file_id,
                name=replaced.name,
                documents=[
                    AffectedDocumentOut(
                        document_id=d.document_id,
                        project_id=d.project_id,
                        status=d.status.value,
                        content_identical=d.content_identical,
                    )
                    for d in replaced.documents
                ],
            )
            for replaced in preview.replaced
        ],
        kept=[
            KeptNamesakeOut(file_id=k.file_id, name=k.name, tags=_tags_out(k.tags))
            for k in preview.kept
        ],
    )


def _project_detail_out(detail: ProjectDetail) -> ElectricalProjectOut:
    return _project_out(detail.project)


# --------------------------------------------------------------------------- projects
@router.post("/projects", status_code=201, dependencies=[_MANAGE])
async def create_electrical_project(
    body: ElectricalProjectIn, services: Services, ctx: Context
) -> ElectricalProjectOut:
    """Create the space's WireTwin project (201) with its first profile version: ``default``,
    or the given body as ``user``. One project per space: a second is 409
    ``electrical.project_exists``; an unknown or foreign space is 404."""
    detail = await _bundle(services).projects.create(
        ctx,
        space_id=body.space_id,
        name=body.name,
        description=body.description,
        profile=body.profile,
    )
    return _project_detail_out(detail)


@router.get("/projects", dependencies=[_READ])
async def list_electrical_projects(
    services: Services,
    ctx: Context,
    space_id: str,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> Page[ElectricalProjectOut]:
    """The space's project: a page of zero or one (``space_id`` is required, the ``listFiles``
    rule -- an unknown space answers an empty page, a listing is not an existence oracle)."""
    page = await _bundle(services).projects.list(ctx, space_id=space_id, limit=limit, cursor=cursor)
    return Page(
        data=[_project_out(p) for p in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.get("/projects/{project_id}", dependencies=[_READ])
async def get_electrical_project(
    project_id: str, services: Services, ctx: Context
) -> ElectricalProjectOut:
    return _project_detail_out(await _bundle(services).projects.get(ctx, project_id))


@router.patch("/projects/{project_id}", dependencies=[_MANAGE])
async def update_electrical_project(
    project_id: str, body: ElectricalProjectPatchIn, services: Services, ctx: Context
) -> ElectricalProjectOut:
    """Rename or re-describe the project; an absent field is left alone and a blank
    description clears it. Changing nothing writes nothing."""
    detail = await _bundle(services).projects.update(
        ctx, project_id, name=body.name, description=body.description
    )
    return _project_detail_out(detail)


@router.get("/projects/{project_id}/profile", dependencies=[_READ])
async def get_project_profile(
    project_id: str, services: Services, ctx: Context
) -> DrawingProfileOut:
    return _profile_out(await _bundle(services).projects.get_profile(ctx, project_id))


@router.put("/projects/{project_id}/profile", dependencies=[_MANAGE])
async def replace_project_profile(
    project_id: str, body: DrawingProfileIn, services: Services, ctx: Context
) -> DrawingProfileOut:
    """Append a ``user`` version of the project's profile. Documents already processed are not
    reprocessed: that is an explicit call (``reprocessDocument``). An invalid body is 422
    ``electrical.invalid_profile`` naming the field, never echoing what was sent."""
    version = await _bundle(services).projects.replace_profile(ctx, project_id, body.body)
    return _profile_out(version)


@router.get("/projects/{project_id}/characterization", dependencies=[_READ])
async def get_project_characterization(
    project_id: str, services: Services, ctx: Context
) -> CharacterizationReportOut:
    """The project's characterization, derived from its ACTIVE documents' records on every read."""
    return _report_out(await _bundle(services).characterization.for_project(ctx, project_id))


# --------------------------------------------------------------------------- documents
@router.post(
    "/projects/{project_id}/documents",
    status_code=202,
    dependencies=[_MANAGE, Depends(heavy_job)],
)
async def register_electrical_document(
    project_id: str,
    body: DocumentRegisterIn,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> DocumentRegisteredOut:
    """STG-00: register a ``ready`` PDF of the project's space as a drawing document (202).

    The document, its ``queued`` job and ``electrical.document.registered.v1`` are written in one
    unit of work; the worker takes it from there. The guard answers in order, and the first
    refusal is the answer: 404 (project, or a file that is unknown, unfinished, foreign or
    deleted), 415 ``electrical.not_a_pdf``, 422 ``electrical.file_not_in_project_space``, 409
    ``electrical.document_exists`` (the file already has a document, an excluded one included),
    409 ``electrical.namesake_not_replaced``, 409 ``electrical.duplicate_document`` (only when
    ``checksum`` is sent), 429 ``electrical.ingestion_queue_full`` (with ``Retry-After``). A
    ``checksum`` that is not a SHA-256 in hex is 422."""
    bundle = _bundle(services)

    async def _register() -> DocumentRegisteredOut:
        registered = await bundle.documents.register(
            ctx, project_id, file_id=body.file_id, checksum=body.checksum
        )
        for _document_id, reason in registered.excluded:
            ele_documents_excluded_total.labels(reason=reason.value).inc()
        view = await bundle.documents.get(ctx, registered.document.id)
        job = await bundle.jobs.view(ctx, registered.job)
        return DocumentRegisteredOut(document=_document_out(view), job=_job_out(job))

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /electrical/projects/{id}/documents",
        key=idempotency_key,
        body=_Scope(subject=project_id, payload=body.model_dump()),
        model=DocumentRegisteredOut,
        run=_register,
    )


@router.get("/projects/{project_id}/documents", dependencies=[_READ])
async def list_electrical_documents(
    project_id: str,
    services: Services,
    ctx: Context,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    status: Annotated[Literal["active", "excluded"] | None, Query()] = None,
) -> Page[ElectricalDocumentOut]:
    """The project's documents, newest first, each with its file's current name. A document that
    is being purged is out of every read."""
    page = await _bundle(services).documents.list(
        ctx,
        project_id,
        status=None if status is None else DocumentStatus(status),
        limit=limit,
        cursor=cursor,
    )
    return Page(
        data=[_document_out(view) for view in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.get("/documents/{document_id}", dependencies=[_READ])
async def get_electrical_document(
    document_id: str, services: Services, ctx: Context
) -> ElectricalDocumentOut:
    """One document with its file's name and tags and its latest job."""
    return _document_out(await _bundle(services).documents.get(ctx, document_id))


@router.get("/documents/{document_id}/profile", dependencies=[_READ])
async def get_document_profile(
    document_id: str, services: Services, ctx: Context
) -> EffectiveProfileOut:
    """The profile the document is processed under, and the latest proposal while one awaits."""
    return _effective_out(await _bundle(services).documents.get_profile(ctx, document_id))


@router.put("/documents/{document_id}/profile", dependencies=[_MANAGE])
async def replace_document_profile(
    document_id: str, body: DrawingProfileIn, services: Services, ctx: Context
) -> EffectiveProfileOut:
    """A person's override of this document's profile (a ``user`` version; the document becomes
    ``confirmed`` under it). 409 ``electrical.job_conflict`` while a job awaits confirmation:
    confirm it instead."""
    bundle = _bundle(services)
    await bundle.documents.replace_profile(ctx, document_id, body.body)
    return _effective_out(await bundle.documents.get_profile(ctx, document_id))


@router.post(
    "/documents/{document_id}/profile-confirmation",
    status_code=202,
    dependencies=[_MANAGE, Depends(heavy_job)],
)
async def confirm_document_profile(
    document_id: str,
    services: Services,
    ctx: Context,
    body: Annotated[ProfileConfirmIn | None, Body()] = None,
    idempotency_key: IdempotencyKey = None,
) -> IngestionJobOut:
    """Confirm the proposal as it is (no body, or ``body`` absent) or as edited, and requeue the
    job that awaited it (202). 409 ``electrical.job_conflict`` when no job awaits confirmation."""
    bundle = _bundle(services)
    edited = None if body is None else body.body

    async def _confirm() -> IngestionJobOut:
        job = await bundle.documents.confirm_profile(ctx, document_id, body=edited)
        return _job_out(await bundle.jobs.view(ctx, job))

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /electrical/documents/{id}/profile-confirmation",
        key=idempotency_key,
        body=_Scope(subject=document_id, payload=None if body is None else body.model_dump()),
        model=IngestionJobOut,
        run=_confirm,
    )


@router.post(
    "/documents/{document_id}/reprocessing",
    status_code=202,
    dependencies=[_MANAGE, Depends(heavy_job)],
)
async def reprocess_document(
    document_id: str,
    services: Services,
    ctx: Context,
    body: Annotated[ReprocessIn | None, Body()] = None,
    idempotency_key: IdempotencyKey = None,
) -> IngestionJobOut:
    """A new ``reprocess`` job for the whole document or the listed pages (202). 409
    ``electrical.job_conflict`` while another job is open, 409 ``electrical.profile_unconfirmed``
    while the proposal awaits a person, 422 for a page outside ``1..page_count``."""
    bundle = _bundle(services)
    pages = None if body is None else body.pages

    async def _reprocess() -> IngestionJobOut:
        job = await bundle.documents.reprocess(ctx, document_id, pages=pages)
        return _job_out(await bundle.jobs.view(ctx, job))

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /electrical/documents/{id}/reprocessing",
        key=idempotency_key,
        body=_Scope(subject=document_id, payload=None if body is None else body.model_dump()),
        model=IngestionJobOut,
        run=_reprocess,
    )


@router.get("/documents/{document_id}/pages", dependencies=[_READ])
async def list_document_pages(
    document_id: str,
    services: Services,
    ctx: Context,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> Page[DocumentPageOut]:
    """The document's pages by page number, each with the state of its stages. One page failing
    does not fail the job: its state is here, not in an HTTP status."""
    page = await _bundle(services).documents.list_pages(
        ctx, document_id, limit=limit, cursor=cursor
    )
    return Page(
        data=[_page_out(row) for row in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.get("/documents/{document_id}/characterization", dependencies=[_READ])
async def get_document_characterization(
    document_id: str, services: Services, ctx: Context
) -> CharacterizationOut:
    """404 until STG-01 has characterized the document."""
    return _characterization_out(
        await _bundle(services).characterization.for_document(ctx, document_id)
    )


# --------------------------------------------------------------------------- jobs
@router.get("/ingestion-jobs/{job_id}", dependencies=[_READ])
async def get_ingestion_job(job_id: str, services: Services, ctx: Context) -> IngestionJobOut:
    """The job with what is derived at read time: the per-status page counts, the queue position
    and wait estimate while queued, and ``stale`` once its lease has expired."""
    return _job_out(await _bundle(services).jobs.get(ctx, job_id))


@router.post("/ingestion-jobs/{job_id}/cancel", dependencies=[_MANAGE])
async def cancel_ingestion_job(job_id: str, services: Services, ctx: Context) -> IngestionJobOut:
    """Cancel (200): at once for a waiting job, at the next batch boundary for a running one;
    repeating it writes nothing. 409 ``electrical.job_conflict`` for a finished job. Cancelling
    removes work from the queue, so it is not charged to the heavy-job budget."""
    return _job_out(await _bundle(services).jobs.cancel(ctx, job_id))


@router.post(
    "/ingestion-jobs/{job_id}/resume",
    status_code=202,
    dependencies=[_MANAGE, Depends(heavy_job)],
)
async def resume_ingestion_job(
    job_id: str, services: Services, ctx: Context, idempotency_key: IdempotencyKey = None
) -> IngestionJobOut:
    """Return the SAME job to ``queued`` (202): only its unfinished pages are processed again.
    Allowed from ``failed``, ``cancelled`` and a ``running`` job whose lease has expired. 409
    ``electrical.job_conflict`` otherwise, 409 ``electrical.profile_unconfirmed`` while it awaits
    confirmation."""
    bundle = _bundle(services)

    async def _resume() -> IngestionJobOut:
        return _job_out(await bundle.jobs.resume(ctx, job_id))

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /electrical/ingestion-jobs/{id}/resume",
        key=idempotency_key,
        body=_Scope(subject=job_id),
        model=IngestionJobOut,
        run=_resume,
    )


# --------------------------------------------------------------------------- replacement
@router.get("/files/{file_id}/replacement-preview", dependencies=[_READ])
async def preview_file_replacement(
    file_id: str,
    services: Services,
    ctx: Context,
    checksum: Annotated[str | None, Query(max_length=128)] = None,
) -> ReplacementPreviewOut:
    """What registering or re-tagging this file would replace: the older namesakes it would
    delete, their documents and projects, and the same-name files kept for their different tags.
    With ``checksum`` (a SHA-256 in hex, either case; otherwise 422) each affected document says
    whether its content is identical. Writes nothing; an unknown or foreign file is 404."""
    preview = await _bundle(services).replacement.preview(ctx, file_id, checksum=checksum)
    return _preview_out(preview)
