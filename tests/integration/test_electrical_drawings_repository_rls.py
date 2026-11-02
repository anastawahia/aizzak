"""Live-Postgres tests for the drawings SQL repositories and the ``electrical`` migration
(electrical-drawings INC-1, LLD 4.3 / 5.2, E12).

Runs against a real PostgreSQL 16 (see ``tests/integration/conftest.py``); auto-skips via
``live_db`` when unreachable. Every id is a real ``new_uuid7()``: the columns are typed ``uuid``.

Two halves of one proof, as for ``spaces``: the adapter has never touched a real table, and the
DDL has never been asked to refuse anything. What lives ONLY in the migration is asserted
directly (the unique indexes by name, the CHECKs, ``FORCE ROW LEVEL SECURITY``, ``trg_touch``);
the rest goes through the ports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.electrical.drawings.adapters.sql_repository import (
    SqlCharacterizationRepository,
    SqlDocumentRepository,
    SqlDrawingsSweep,
    SqlJobRepository,
    SqlPageRepository,
    SqlProfileRepository,
    SqlProjectRepository,
)
from app.modules.electrical.drawings.domain.characterization import Characterization
from app.modules.electrical.drawings.domain.document import (
    Document,
    DocumentStatus,
    ExclusionReason,
    ProfileState,
)
from app.modules.electrical.drawings.domain.job import IngestionJob, JobKind, JobStatus
from app.modules.electrical.drawings.domain.pages import (
    PageClassification,
    PageKind,
    PageLanguage,
    PageResult,
    StageReason,
    StageStatus,
)
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION, Stage
from app.modules.electrical.drawings.domain.profile import (
    DrawingProfile,
    ProfileSource,
    ProfileVersion,
    SymbolStandard,
)
from app.modules.electrical.drawings.domain.project import Project
from app.modules.electrical.shared.kinds import Convention, ConventionTally, PlcFamily
from tests.integration.conftest import LiveDbDsns
from tests.unit.support_electrical import make_signals

pytestmark = [pytest.mark.live_db]

_TABLES = (
    "drawings_projects",
    "drawings_documents",
    "drawings_jobs",
    "drawings_pages",
    "drawings_page_stages",
    "drawings_profile_versions",
    "drawings_characterizations",
)
_SHA_A = "a" * 64
_SHA_B = "b" * 64


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


class _Repos:
    def __init__(self, tenant_session: TenantSessionFactory) -> None:
        self.projects = SqlProjectRepository(tenant_session)
        self.profiles = SqlProfileRepository(tenant_session)
        self.documents = SqlDocumentRepository(tenant_session)
        self.jobs = SqlJobRepository(tenant_session)
        self.pages = SqlPageRepository(tenant_session)
        self.characterizations = SqlCharacterizationRepository(tenant_session)
        self.sweep = SqlDrawingsSweep(tenant_session)


@pytest.fixture
def repos(tenant_session: TenantSessionFactory) -> _Repos:
    return _Repos(tenant_session)


def _project(ws: str, *, space_id: str | None = None, name: str = "Plant 7") -> Project:
    now = utc_now()
    return Project(
        id=new_uuid7(),
        workspace_id=ws,
        space_id=space_id or new_uuid7(),
        name=name,
        description=None,
        created_by=new_uuid7(),
        created_at=now,
        updated_at=now,
        version=1,
    )


def _document(project: Project, **changes: object) -> Document:
    now = utc_now()
    fields: dict[str, object] = {
        "id": new_uuid7(),
        "workspace_id": project.workspace_id,
        "space_id": project.space_id,
        "project_id": project.id,
        "file_id": new_uuid7(),
        "status": DocumentStatus.ACTIVE,
        "exclusion_reason": None,
        "content_sha256": None,
        "page_count": None,
        "pdf_creator": None,
        "pdf_producer": None,
        "profile_state": ProfileState.NONE,
        "pipeline_version": None,
        "created_by": new_uuid7(),
        "created_at": now,
        "updated_at": now,
        "purging_at": None,
        "version": 1,
    }
    return Document(**{**fields, **changes})  # type: ignore[arg-type]


def _job(document: Document, **changes: object) -> IngestionJob:
    now = utc_now()
    fields: dict[str, object] = {
        "id": new_uuid7(),
        "workspace_id": document.workspace_id,
        "document_id": document.id,
        "kind": JobKind.INGEST,
        "pages": None,
        "status": JobStatus.QUEUED,
        "stage": None,
        "failure_code": None,
        "failure_detail": None,
        "attempt": 0,
        "lease_until": None,
        "seq": 0,
        "cancel_requested_at": None,
        "pipeline_version": PIPELINE_VERSION,
        "created_by": new_uuid7(),
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "version": 1,
    }
    return IngestionJob(**{**fields, **changes})  # type: ignore[arg-type]


def _result(
    page_no: int,
    kind: PageKind = PageKind.VECTOR_TEXT,
    *,
    status: StageStatus = StageStatus.SUCCEEDED,
    reason: StageReason | None = None,
    attempts: int = 1,
) -> PageResult:
    return PageResult(
        classification=PageClassification(
            page_no=page_no,
            kind=kind,
            language=PageLanguage.EN,
            needs_ocr=kind is PageKind.RASTER,
            status=status,
            reason=reason,
        ),
        signals=make_signals(page_no),
        attempts=attempts,
    )


def _profile_version(project: Project, document_id: str | None = None) -> ProfileVersion:
    profile = DrawingProfile.default()
    return ProfileVersion(
        id=new_uuid7(),
        workspace_id=project.workspace_id,
        project_id=project.id,
        document_id=document_id,
        version=0,  # assigned by `append`
        source=ProfileSource.DETECTED,
        profile=profile,
        fingerprint=profile.fingerprint(),
        confidence=0.9,
        mixed_standards=False,
        created_by=None,
        created_at=utc_now(),
    )


def _characterization(document: Document) -> Characterization:
    return Characterization(
        document_id=document.id,
        content_sha256=_SHA_A,
        producer_family="eplan",
        creator="EPLAN",
        producer="EPLAN PDF",
        page_count=3,
        text_kinds={PageKind.VECTOR_TEXT: 2, PageKind.RASTER: 1},
        page_languages={PageLanguage.EN: 3},
        document_language="en",
        conventions={Convention.IEC_81346: 5, Convention.NFPA_JIC: 1},
        symbol_standard_guess=SymbolStandard.IEC_60617,
        symbol_standard_basis=("iec-81346-majority",),
        plc_families={PlcFamily.S7_EN: 4},
        cross_reference_formats={"slash_page_dot_column": 2},
        ocr_needed_pages=1,
        cid_fonts_without_tounicode=0,
        pipeline_version=PIPELINE_VERSION,
        shared_version="1.2.0",
        rules_version="r1",
        mixed_standards=True,
    )


async def _seed(repos: _Repos, ws: str) -> tuple[ExecutionContext, Project, Document]:
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    document = _document(project)
    await repos.documents.add(ctx, document)
    return ctx, project, document


async def _owner_exec(owner_dsn: str, ws: str | None, sql: str, **params: object) -> list[tuple]:
    """Run raw SQL as the table owner (``FORCE`` makes even the owner policy-subject, so the
    GUC is set unless ``ws`` is ``None``, which is how the FORCE test asks its question)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            if ws is not None:
                await conn.execute(
                    text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws}
                )
            result = await conn.execute(text(sql), params)
            return [tuple(row) for row in result.all()] if result.returns_rows else []
    finally:
        await engine.dispose()


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


async def _raw_sqlstate(owner_dsn: str, ws: str, sql: str, **params: object) -> str | None:
    try:
        await _owner_exec(owner_dsn, ws, sql, **params)
    except DBAPIError as exc:
        return _sqlstate(exc)
    return None


def _ids(items: Sequence[object]) -> list[str]:
    return [item.id for item in items]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------------------
async def test_project_round_trips_and_one_project_per_space(repos: _Repos) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)

    assert await repos.projects.get(ctx, project.id) == project
    assert await repos.projects.get_by_space(ctx, project.space_id) == project

    with pytest.raises(ConflictError) as raised:
        await repos.projects.add(ctx, _project(ws, space_id=project.space_id))
    assert raised.value.code == "electrical.project_exists"
    # the same space in ANOTHER workspace is a different space: the index leads with the tenant
    other = _ctx(new_uuid7())
    await repos.projects.add(other, _project(other.workspace_id, space_id=project.space_id))


async def test_project_save_is_optimistic_and_returns_the_trigger_timestamp(
    repos: _Repos,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)

    stale = await repos.projects.get(ctx, project.id)
    assert stale is not None
    project.rename("Plant 8", utc_now())
    await repos.projects.save(ctx, project)
    assert project.version == 2
    assert project.updated_at > stale.updated_at  # trg_touch

    stale.rename("Plant 9", utc_now())
    with pytest.raises(ConflictError):
        await repos.projects.save(ctx, stale)
    reread = await repos.projects.get(ctx, project.id)
    assert reread is not None and reread.name == "Plant 8"


async def test_projects_list_newest_first_and_delete_in_space_counts(repos: _Repos) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = new_uuid7()
    only = _project(ws, space_id=space)
    await repos.projects.add(ctx, only)
    bystander = _project(ws)
    await repos.projects.add(ctx, bystander)

    page = await repos.projects.list_by_space(ctx, space, limit=10, cursor=None)
    assert _ids(page.data) == [only.id] and page.next_cursor is None

    assert await repos.projects.delete_in_space(ctx, space) == 1
    assert await repos.projects.delete_in_space(ctx, space) == 0
    assert await repos.projects.get(ctx, bystander.id) is not None
    assert await repos.projects.get(_ctx(new_uuid7()), bystander.id) is None


# --------------------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------------------
async def test_document_round_trips_through_every_column(repos: _Repos) -> None:
    ctx, project, document = await _seed(repos, new_uuid7())
    document.record_inspection(
        sha256=_SHA_A,
        page_count=12,
        creator="EPLAN",
        producer="Acrobat",
        pipeline_version=PIPELINE_VERSION,
        now=utc_now(),
    )
    await repos.documents.save(ctx, document)
    stored = await repos.documents.get(ctx, document.id)
    assert stored == document
    assert stored is not None and stored.version == 2 and stored.content_sha256 == _SHA_A
    assert await repos.documents.active_files(ctx, project.id) == {document.id: document.file_id}
    assert await repos.documents.active_with_content(ctx, project.id, _SHA_A) == document
    assert await repos.documents.active_with_content(ctx, project.id, _SHA_B) is None


async def test_two_concurrent_registrations_of_one_file_one_wins(repos: _Repos) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    file_id = new_uuid7()
    first = _document(project, file_id=file_id)
    second = _document(project, file_id=file_id)

    outcomes = await asyncio.gather(
        repos.documents.add(ctx, first),
        repos.documents.add(ctx, second),
        return_exceptions=True,
    )
    errors = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(errors) == 1
    assert isinstance(errors[0], ConflictError)
    assert errors[0].code == "electrical.document_exists"
    assert len(await repos.documents.not_purging_for_file(ctx, file_id)) == 1


async def test_a_purging_document_frees_its_file_for_a_new_registration(repos: _Repos) -> None:
    ctx, project, document = await _seed(repos, new_uuid7())
    document.mark_purging(utc_now())
    await repos.documents.save(ctx, document)
    assert await repos.documents.not_purging_for_file(ctx, document.file_id) == []
    await repos.documents.add(ctx, _document(project, file_id=document.file_id))


async def test_the_same_bytes_are_one_active_document_per_project(repos: _Repos) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    one = _document(project, content_sha256=_SHA_A)
    two = _document(project)
    await repos.documents.add(ctx, one)
    await repos.documents.add(ctx, two)

    two.content_sha256 = _SHA_A
    with pytest.raises(ConflictError) as raised:
        await repos.documents.save(ctx, two)
    assert raised.value.code == "electrical.duplicate_document"

    # EXCLUDED rows do not hold the hash: the index is partial on status = 'active'
    two.content_sha256 = _SHA_A
    two.exclude(ExclusionReason.DUPLICATE_CONTENT, utc_now())
    await repos.documents.save(ctx, two)
    stored = await repos.documents.get(ctx, two.id)
    assert stored is not None and stored.status is DocumentStatus.EXCLUDED
    assert stored.exclusion_reason is ExclusionReason.DUPLICATE_CONTENT

    # another project may hold the same bytes
    other_project = _project(ws)
    await repos.projects.add(ctx, other_project)
    await repos.documents.add(ctx, _document(other_project, content_sha256=_SHA_A))


async def test_an_excluded_document_can_still_be_purged(repos: _Repos) -> None:
    """The CHECK ties the reason to the EXCLUDED status; ``mark_purging`` clears the reason, so
    the PURGING row is written without it."""
    ctx, _, document = await _seed(repos, new_uuid7())
    document.exclude(ExclusionReason.FILE_UNREADABLE, utc_now())
    await repos.documents.save(ctx, document)
    document.mark_purging(utc_now())
    await repos.documents.save(ctx, document)
    stored = await repos.documents.get(ctx, document.id)
    assert stored is not None
    assert stored.status is DocumentStatus.PURGING and stored.exclusion_reason is None
    assert stored.purging_at is not None


async def test_lock_live_reads_active_only_and_reads_are_status_filtered(repos: _Repos) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    live = _document(project)
    excluded = _document(project)
    excluded.exclude(ExclusionReason.FILE_UNREADABLE, utc_now())
    await repos.documents.add(ctx, live)
    await repos.documents.add(ctx, excluded)

    assert (await repos.documents.lock_live(ctx, live.id)) == live
    assert await repos.documents.lock_live(ctx, excluded.id) is None
    assert set(await repos.documents.active_files(ctx, project.id)) == {live.id}
    # the other tenant cannot lock it
    assert await repos.documents.lock_live(_ctx(new_uuid7()), live.id) is None


async def test_documents_list_newest_first_with_status_filter_and_cursor(repos: _Repos) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    docs = [_document(project) for _ in range(3)]
    for doc in docs:
        await repos.documents.add(ctx, doc)
    docs[0].exclude(ExclusionReason.FILE_UNREADABLE, utc_now())
    await repos.documents.save(ctx, docs[0])

    first = await repos.documents.list_by_project(
        ctx, project.id, status=None, limit=2, cursor=None
    )
    assert _ids(first.data) == sorted((d.id for d in docs), reverse=True)[:2]
    assert first.next_cursor is not None
    second = await repos.documents.list_by_project(
        ctx, project.id, status=None, limit=2, cursor=first.next_cursor
    )
    assert len(second.data) == 1 and second.next_cursor is None
    excluded = await repos.documents.list_by_project(
        ctx, project.id, status=DocumentStatus.EXCLUDED, limit=10, cursor=None
    )
    assert _ids(excluded.data) == [docs[0].id]


# --------------------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------------------
async def test_one_open_job_per_document_and_closed_ones_do_not_count(repos: _Repos) -> None:
    ctx, _, document = await _seed(repos, new_uuid7())
    first = _job(document)
    await repos.jobs.add(ctx, first)

    with pytest.raises(ConflictError) as raised:
        await repos.jobs.add(ctx, _job(document))
    assert raised.value.code == "electrical.job_conflict"

    first.status = JobStatus.FAILED
    first.failure_code = "electrical.pdf_rejected"
    await repos.jobs.save(ctx, first)
    second = _job(document, created_at=first.created_at + timedelta(seconds=1))
    await repos.jobs.add(ctx, second)
    assert await repos.jobs.open_for_document(ctx, document.id) == second
    assert await repos.jobs.latest_for_document(ctx, document.id) == second

    # a requeue of the failed job would be a second open one: the index refuses it too
    first.requeue(utc_now())
    with pytest.raises(ConflictError) as again:
        await repos.jobs.save(ctx, first)
    assert again.value.code == "electrical.job_conflict"


async def test_job_round_trips_lifecycle_fields_and_the_cancel_code(repos: _Repos) -> None:
    ctx, _, document = await _seed(repos, new_uuid7())
    job = _job(document, kind=JobKind.REPROCESS, pages=(2, 3, 5), stage=Stage.CLASSIFY)
    await repos.jobs.add(ctx, job)
    assert await repos.jobs.get(ctx, job.id) == job

    now = utc_now()
    assert job.take_lease(now, 60)
    job.next_seq()
    await repos.jobs.save(ctx, job)
    running = await repos.jobs.get(ctx, job.id)
    assert running is not None and running.status is JobStatus.RUNNING
    assert (
        running.seq == 1 and running.lease_until == job.lease_until and running.pages == (2, 3, 5)
    )

    job.cancel(utc_now(), code="electrical.document_not_live")
    await repos.jobs.save(ctx, job)
    cancelled = await repos.jobs.get(ctx, job.id)
    assert cancelled is not None and cancelled.status is JobStatus.CANCELLED
    # the cancel code rides on `failure_code`: no extra column is needed
    assert cancelled.failure_code == "electrical.document_not_live"
    assert cancelled.finished_at is not None and cancelled.version == 3

    stale = await repos.jobs.get(ctx, job.id)
    assert stale is not None
    await repos.jobs.save(ctx, job)
    with pytest.raises(ConflictError):
        await repos.jobs.save(ctx, stale)


async def test_queue_position_open_count_and_recent_run_seconds(repos: _Repos) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    base = utc_now() - timedelta(minutes=10)
    jobs: list[IngestionJob] = []
    for i in range(3):
        doc = _document(project)
        await repos.documents.add(ctx, doc)
        job = _job(doc, created_at=base + timedelta(seconds=i))
        await repos.jobs.add(ctx, job)
        jobs.append(job)

    assert [await repos.jobs.queue_position(ctx, j) for j in jobs] == [1, 2, 3]
    assert await repos.jobs.open_count(ctx) == 3
    assert await repos.jobs.open_count(_ctx(new_uuid7())) == 0

    started = utc_now() - timedelta(seconds=90)
    jobs[0].take_lease(started, 60)
    jobs[0].succeed(started + timedelta(seconds=42))
    await repos.jobs.save(ctx, jobs[0])
    assert await repos.jobs.queue_position(ctx, jobs[2]) == 2
    assert await repos.jobs.open_count(ctx) == 2
    assert list(await repos.jobs.recent_run_seconds(ctx, limit=5)) == pytest.approx([42.0])


async def _classifications(repos: _Repos, ctx: ExecutionContext, document_id: str) -> list[Any]:
    """The stored classifications in page order (the page listing's rows that have one)."""
    listing = await repos.pages.list(ctx, document_id, limit=100, cursor=None)
    return [row.classification for row in listing.data if row.classification is not None]


# --------------------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------------------
async def test_a_document_of_the_page_limit_is_seeded_under_the_drivers_parameter_limit(
    repos: _Repos,
) -> None:
    """ELE_MAX_PAGES is 5,000: one multi-row INSERT of that many stage rows is 35,000 bound
    parameters and asyncpg refuses more than 32,767 (it failed from 4,682 pages)."""
    ctx, _, document = await _seed(repos, new_uuid7())
    pages = list(range(1, 5001))
    await repos.pages.seed(ctx, document.id, pages, Stage.CLASSIFY, PIPELINE_VERSION)
    await repos.pages.seed(ctx, document.id, pages, Stage.CLASSIFY, PIPELINE_VERSION)  # a resume
    assert await repos.pages.stage_counts(ctx, document.id, Stage.CLASSIFY) == {
        StageStatus.PENDING: 5000
    }
    assert await repos.pages.unfinished(ctx, document.id, Stage.CLASSIFY) == pages
    # the rows are the tenant's: another workspace sees none of them
    other = _ctx(new_uuid7())
    assert await repos.pages.stage_counts(other, document.id, Stage.CLASSIFY) == {}


async def test_pages_seed_record_resume_and_read(repos: _Repos) -> None:
    ctx, _, document = await _seed(repos, new_uuid7())
    await repos.pages.seed(ctx, document.id, [1, 2, 3, 4], Stage.CLASSIFY, PIPELINE_VERSION)
    await repos.pages.seed(ctx, document.id, [1, 2, 3, 4], Stage.CLASSIFY, PIPELINE_VERSION)
    assert await repos.pages.unfinished(ctx, document.id, Stage.CLASSIFY) == [1, 2, 3, 4]
    assert await repos.pages.stage_counts(ctx, document.id, Stage.CLASSIFY) == {
        StageStatus.PENDING: 4
    }
    assert await _classifications(repos, ctx, document.id) == []

    await repos.pages.record(
        ctx,
        document.id,
        [
            _result(1),
            _result(2, PageKind.RASTER),
            _result(
                3,
                PageKind.MIXED,
                status=StageStatus.NEEDS_REVIEW,
                reason=StageReason.AMBIGUOUS_CLASSIFICATION,
            ),
            _result(4, PageKind.EMPTY, status=StageStatus.FAILED, reason=StageReason.PARSE_ERROR),
        ],
    )
    assert await repos.pages.unfinished(ctx, document.id, Stage.CLASSIFY) == []
    assert await repos.pages.stage_counts(ctx, document.id, Stage.CLASSIFY) == {
        StageStatus.SUCCEEDED: 2,
        StageStatus.NEEDS_REVIEW: 1,
        StageStatus.FAILED: 1,
    }
    classified = await _classifications(repos, ctx, document.id)
    assert [c.page_no for c in classified] == [1, 2, 3, 4]
    assert classified[1].kind is PageKind.RASTER and classified[1].needs_ocr
    assert classified[2].reason is StageReason.AMBIGUOUS_CLASSIFICATION

    # a re-run of one page replaces its classification and stage in place
    await repos.pages.record(ctx, document.id, [_result(4, PageKind.RASTER, attempts=2)])
    again = {c.page_no: c for c in await _classifications(repos, ctx, document.id)}
    assert again[4].kind is PageKind.RASTER and again[4].status is StageStatus.SUCCEEDED

    first = await repos.pages.list(ctx, document.id, limit=3, cursor=None)
    assert [row.page_no for row in first.data] == [1, 2, 3] and first.next_cursor is not None
    assert first.data[0].stages == {Stage.CLASSIFY: StageStatus.SUCCEEDED}
    assert first.data[0].classification is not None and first.data[0].attempts == 1
    rest = await repos.pages.list(ctx, document.id, limit=3, cursor=first.next_cursor)
    assert [row.page_no for row in rest.data] == [4] and rest.next_cursor is None
    assert rest.data[0].attempts == 2
    assert await repos.pages.unfinished(_ctx(new_uuid7()), document.id, Stage.CLASSIFY) == []


async def test_evidence_reads_back_finished_pages_with_their_tally_and_reset_reopens_them(
    repos: _Repos,
) -> None:
    """S7: a resumed job folds in what an earlier run recorded (signals without the token
    sample, plus the tally), failed pages are not evidence, and ``reset`` returns stage rows to
    pending."""
    ctx, _, document = await _seed(repos, new_uuid7())
    await repos.pages.seed(ctx, document.id, [1, 2, 3], Stage.CLASSIFY, PIPELINE_VERSION)
    tally = ConventionTally(
        conventions={Convention.IEC_81346: 7, Convention.NFPA_JIC: 2},
        plc_families={PlcFamily.S7_EN: 3},
        cross_reference_formats={"slash_page_dot_column": 1},
        identifiers=12,
        tokens=40,
    )
    page_one = replace(_result(1), tally=tally)
    await repos.pages.record(
        ctx,
        document.id,
        [
            page_one,
            _result(2, PageKind.RASTER),
            _result(3, PageKind.EMPTY, status=StageStatus.FAILED, reason=StageReason.PARSE_ERROR),
        ],
    )
    evidence = await repos.pages.evidence(ctx, document.id)
    assert [e.classification.page_no for e in evidence] == [1, 2]  # page 3 failed
    assert evidence[0].tally == tally and evidence[1].tally is None
    assert evidence[0].signals.tokens == ()
    assert evidence[0].signals.char_count == make_signals(1).char_count
    assert evidence[1].classification.kind is PageKind.RASTER

    assert await repos.pages.reset(ctx, document.id, [2, 3], Stage.CLASSIFY) == 2
    assert await repos.pages.unfinished(ctx, document.id, Stage.CLASSIFY) == [2, 3]
    assert await repos.pages.reset(ctx, document.id, [2, 3], Stage.CLASSIFY) == 0  # nothing left
    assert [e.classification.page_no for e in await repos.pages.evidence(ctx, document.id)] == [1]
    assert await repos.pages.reset(ctx, document.id, None, Stage.CLASSIFY) == 1
    assert await repos.pages.unfinished(ctx, document.id, Stage.CLASSIFY) == [1, 2, 3]
    assert await repos.pages.reset(_ctx(new_uuid7()), document.id, None, Stage.CLASSIFY) == 0


async def test_delete_in_space_also_deletes_the_projects_own_profile_versions(
    repos: _Repos,
) -> None:
    ws = new_uuid7()
    ctx, project, document = await _seed(repos, ws)
    await repos.profiles.append(ctx, _profile_version(project))  # the project's series
    await repos.profiles.append(ctx, _profile_version(project, document.id))  # the document's
    assert await repos.projects.delete_in_space(ctx, project.space_id) == 1
    assert await repos.profiles.latest(ctx, project_id=project.id, document_id=None) is None
    # the document's own series goes with the document (sweep), not with the project
    assert await repos.profiles.latest(ctx, project_id=project.id, document_id=document.id)


async def test_a_pending_page_lists_without_a_classification(repos: _Repos) -> None:
    ctx, _, document = await _seed(repos, new_uuid7())
    await repos.pages.seed(ctx, document.id, [1], Stage.CLASSIFY, PIPELINE_VERSION)
    listed = await repos.pages.list(ctx, document.id, limit=5, cursor=None)
    assert listed.data[0].classification is None
    assert listed.data[0].stages == {Stage.CLASSIFY: StageStatus.PENDING}
    assert listed.data[0].attempts == 0


async def test_page_stage_checks_refuse_a_reasonless_failure_and_an_unknown_stage(
    repos: _Repos, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx, _, document = await _seed(repos, ws)
    await repos.pages.seed(ctx, document.id, [1], Stage.CLASSIFY, PIPELINE_VERSION)
    insert = (
        "INSERT INTO electrical.drawings_page_stages "
        "(workspace_id, document_id, page_no, stage, status, reason, pipeline_version) "
        "VALUES (:w, :doc, 1, :stage, :status, :reason, '1')"
    )
    # (status IN failed/needs_review) = (reason IS NOT NULL), both directions
    bad_status = await _raw_sqlstate(
        live_db.owner, ws, "UPDATE electrical.drawings_page_stages SET status='failed'"
    )
    assert bad_status == "23514"
    bad_stage = await _raw_sqlstate(
        live_db.owner,
        ws,
        insert,
        w=ws,
        doc=document.id,
        stage="ocr",
        status="pending",
        reason=None,
    )
    assert bad_stage == "23514"
    # a page_stages row needs its page row (composite FK)
    orphan = await _raw_sqlstate(
        live_db.owner,
        ws,
        insert.replace("1, :stage", "99, :stage"),
        w=ws,
        doc=document.id,
        stage="classify",
        status="pending",
        reason=None,
    )
    assert orphan == "23503"


# --------------------------------------------------------------------------------------
# profile versions
# --------------------------------------------------------------------------------------
async def test_profile_versions_number_per_owner_from_one_and_round_trip(repos: _Repos) -> None:
    ctx, project, document = await _seed(repos, new_uuid7())
    assert await repos.profiles.latest(ctx, project_id=project.id, document_id=None) is None

    one = await repos.profiles.append(ctx, _profile_version(project))
    two = await repos.profiles.append(ctx, _profile_version(project))
    doc_one = await repos.profiles.append(ctx, _profile_version(project, document.id))
    assert (one.version, two.version, doc_one.version) == (1, 2, 1)

    latest = await repos.profiles.latest(ctx, project_id=project.id, document_id=None)
    assert latest is not None and latest.version == 2 and latest.id == two.id
    assert latest.profile == DrawingProfile.default() and latest.fingerprint == two.fingerprint
    assert latest.confidence == 0.9 and latest.source is ProfileSource.DETECTED
    doc_latest = await repos.profiles.latest(ctx, project_id=project.id, document_id=document.id)
    assert doc_latest is not None and doc_latest.id == doc_one.id
    assert (
        await repos.profiles.latest(_ctx(new_uuid7()), project_id=project.id, document_id=None)
        is None
    )


async def test_a_lost_race_on_a_profile_version_is_a_conflict(
    repos: _Repos, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx, project, document = await _seed(repos, ws)
    await repos.profiles.append(ctx, _profile_version(project))
    await repos.profiles.append(ctx, _profile_version(project, document.id))
    body = DrawingProfile.default().fingerprint()
    insert = (
        "INSERT INTO electrical.drawings_profile_versions (id, workspace_id, project_id, "
        "document_id, version, source, body, fingerprint) VALUES "
        "(:id, :w, :project, :doc, 1, 'user', '{}'::jsonb, :fp)"
    )
    # the two partial unique indexes, one per owner kind
    for doc in (None, document.id):
        state = await _raw_sqlstate(
            live_db.owner, ws, insert, id=new_uuid7(), w=ws, project=project.id, doc=doc, fp=body
        )
        assert state == "23505", doc


async def test_detection_round_trips_and_its_size_check_rejects_an_oversized_value(
    repos: _Repos, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx, project, document = await _seed(repos, ws)
    detection = {
        "field_confidence": {"symbol_standard": 0.9, "languages": 0.75},
        "basis": {"symbol": ["tokens", "producer"]},
    }
    stored = await repos.profiles.append(
        ctx, replace(_profile_version(project, document.id), detection=detection)
    )
    latest = await repos.profiles.latest(ctx, project_id=project.id, document_id=document.id)
    assert latest == stored and latest.detection == detection
    # a version without a detection stores NULL and reads back None
    plain = await repos.profiles.append(ctx, _profile_version(project))
    assert plain.detection is None
    assert (await repos.profiles.latest(ctx, project_id=project.id, document_id=None)) == plain
    oversized = await _raw_sqlstate(
        live_db.owner,
        ws,
        "INSERT INTO electrical.drawings_profile_versions (id, workspace_id, project_id, "
        "document_id, version, source, body, fingerprint, detection) VALUES "
        "(:id, :w, :project, NULL, 9, 'detected', '{}'::jsonb, :fp, "
        "jsonb_build_object('x', repeat('a', 17000)))",
        id=new_uuid7(),
        w=ws,
        project=project.id,
        fp=DrawingProfile.default().fingerprint(),
    )
    assert oversized == "23514"


# --------------------------------------------------------------------------------------
# characterizations
# --------------------------------------------------------------------------------------
async def test_characterization_round_trips_and_for_project_reads_active_documents_only(
    repos: _Repos,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    active = _document(project)
    excluded = _document(project)
    await repos.documents.add(ctx, active)
    await repos.documents.add(ctx, excluded)
    for document in (active, excluded):
        await repos.characterizations.put(ctx, _characterization(document), project_id=project.id)
    excluded.exclude(ExclusionReason.DUPLICATE_CONTENT, utc_now())
    await repos.documents.save(ctx, excluded)

    stored = await repos.characterizations.get(ctx, active.id)
    assert stored == _characterization(active)
    assert stored is not None and stored.mixed_standards and stored.shared_version == "1.2.0"
    listed = await repos.characterizations.for_project(ctx, project.id)
    assert [item.document_id for item in listed] == [active.id]
    assert await repos.characterizations.get(_ctx(new_uuid7()), active.id) is None

    # a re-run replaces the record
    await repos.characterizations.put(
        ctx, replace(_characterization(active), page_count=9), project_id=project.id
    )
    replaced = await repos.characterizations.get(ctx, active.id)
    assert replaced is not None and replaced.page_count == 9


# --------------------------------------------------------------------------------------
# the purge sweep
# --------------------------------------------------------------------------------------
async def test_sweep_document_deletes_in_order_only_while_purging_and_only_its_own(
    repos: _Repos, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx, project, doomed = await _seed(repos, ws)
    keeper = _document(project)
    await repos.documents.add(ctx, keeper)
    for document in (doomed, keeper):
        await repos.jobs.add(ctx, _job(document))
        await repos.pages.seed(ctx, document.id, [1, 2], Stage.CLASSIFY, PIPELINE_VERSION)
        await repos.pages.record(ctx, document.id, [_result(1), _result(2)])
        await repos.profiles.append(ctx, _profile_version(project, document.id))
        await repos.characterizations.put(ctx, _characterization(document), project_id=project.id)

    # not PURGING: nothing is touched
    assert await repos.sweep.sweep_document(ctx, doomed.id) == 0
    assert await repos.documents.get(ctx, doomed.id) is not None

    doomed.mark_purging(utc_now())
    await repos.documents.save(ctx, doomed)
    # another tenant cannot sweep it
    assert await repos.sweep.sweep_document(_ctx(new_uuid7()), doomed.id) == 0
    # stage rows 2 + page rows 2 + characterization 1 + job 1 + profile 1 + document 1
    assert await repos.sweep.sweep_document(ctx, doomed.id) == 8
    assert await repos.sweep.sweep_document(ctx, doomed.id) == 0

    assert await repos.documents.get(ctx, doomed.id) is None
    for table in ("drawings_page_stages", "drawings_pages", "drawings_jobs",
                  "drawings_profile_versions", "drawings_characterizations"):  # fmt: skip
        left = await _owner_exec(
            live_db.owner, ws, f"SELECT count(*) FROM electrical.{table} WHERE document_id = :d",
            d=doomed.id,
        )  # fmt: skip
        assert left == [(0,)], table
    assert await repos.documents.get(ctx, keeper.id) is not None
    assert len(await _classifications(repos, ctx, keeper.id)) == 2
    assert await repos.characterizations.get(ctx, keeper.id) is not None
    # the project (and the profile of the project itself) are not the sweep's business
    assert await repos.projects.get(ctx, project.id) is not None


# --------------------------------------------------------------------------------------
# the migration itself: RLS on all seven tables, FORCE, trigger, CHECKs
# --------------------------------------------------------------------------------------
async def _populate_every_table(repos: _Repos, ws: str) -> tuple[ExecutionContext, Document]:
    ctx, project, document = await _seed(repos, ws)
    await repos.jobs.add(ctx, _job(document))
    await repos.pages.seed(ctx, document.id, [1], Stage.CLASSIFY, PIPELINE_VERSION)
    await repos.profiles.append(ctx, _profile_version(project))
    await repos.characterizations.put(ctx, _characterization(document), project_id=project.id)
    return ctx, document


@pytest.mark.parametrize("table", _TABLES)
async def test_another_tenant_sees_nothing_and_cannot_write_in_every_table(
    table: str, repos: _Repos, tenant_session: TenantSessionFactory
) -> None:
    ws = new_uuid7()
    await _populate_every_table(repos, ws)
    other = _ctx(new_uuid7())

    async with tenant_session(other) as session:
        seen = (
            await session.execute(text(f"SELECT count(*) FROM electrical.{table}"))
        ).scalar_one()
    assert seen == 0
    async with tenant_session(_ctx(ws)) as session:
        mine = (
            await session.execute(text(f"SELECT count(*) FROM electrical.{table}"))
        ).scalar_one()
    assert mine >= 1

    # a write naming the first tenant under the second tenant's GUC is refused by WITH CHECK;
    # an UPDATE/DELETE matches no row at all
    async with tenant_session(other) as session:
        updated = await session.execute(
            text(f"UPDATE electrical.{table} SET workspace_id = workspace_id RETURNING 1")
        )
        assert updated.all() == []
        deleted = await session.execute(text(f"DELETE FROM electrical.{table} RETURNING 1"))
        assert deleted.all() == []


async def test_a_forged_workspace_on_add_is_refused_by_the_with_check(repos: _Repos) -> None:
    ws = new_uuid7()
    project = _project(ws)
    with pytest.raises(AppError) as raised:
        await repos.projects.add(_ctx(new_uuid7()), project)
    assert raised.value.code == "common.internal"
    document = _document(project)
    with pytest.raises(AppError):
        await repos.documents.add(_ctx(new_uuid7()), document)
    with pytest.raises(AppError):
        await repos.jobs.add(_ctx(new_uuid7()), _job(document))


@pytest.mark.parametrize("table", _TABLES)
async def test_row_level_security_is_forced_even_for_the_table_owner(
    table: str, repos: _Repos, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    await _populate_every_table(repos, ws)
    # no GUC: the NULLIF policy matches nothing (and does not raise on the empty string)
    assert await _owner_exec(live_db.owner, None, f"SELECT count(*) FROM electrical.{table}") == [
        (0,)
    ]
    rows = await _owner_exec(live_db.owner, ws, f"SELECT count(*) FROM electrical.{table}")
    assert rows[0][0] >= 1


async def test_the_exclusion_check_binds_status_and_reason_together(
    repos: _Repos, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    _, _, document = await _seed(repos, ws)
    # excluded without a reason, and an active document with one: both refused
    assert (
        await _raw_sqlstate(
            live_db.owner, ws, "UPDATE electrical.drawings_documents SET status = 'excluded'"
        )
        == "23514"
    )
    assert (
        await _raw_sqlstate(
            live_db.owner,
            ws,
            "UPDATE electrical.drawings_documents SET exclusion_reason = 'file_unreadable'",
        )
        == "23514"
    )
    assert (
        await _raw_sqlstate(
            live_db.owner, ws, "UPDATE electrical.drawings_documents SET content_sha256 = 'XYZ'"
        )
        == "23514"
    )
    assert (
        await _raw_sqlstate(
            live_db.owner, ws, "UPDATE electrical.drawings_documents SET status = 'gone'"
        )
        == "23514"
    )
    assert document.status is DocumentStatus.ACTIVE


async def test_trg_touch_bumps_updated_at_on_the_five_tables_that_have_one(
    repos: _Repos, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    await _populate_every_table(repos, ws)
    await repos.pages.record(_ctx(ws), (await _any_document_id(live_db, ws)), [_result(1)])
    for table in ("drawings_projects", "drawings_documents", "drawings_jobs", "drawings_pages",
                  "drawings_page_stages"):  # fmt: skip
        before = await _owner_exec(
            live_db.owner, ws, f"SELECT updated_at FROM electrical.{table} LIMIT 1"
        )
        await asyncio.sleep(0.01)
        await _owner_exec(
            live_db.owner, ws, f"UPDATE electrical.{table} SET workspace_id = workspace_id"
        )
        after = await _owner_exec(
            live_db.owner, ws, f"SELECT updated_at FROM electrical.{table} LIMIT 1"
        )
        assert after[0][0] > before[0][0], table


async def _any_document_id(live_db: LiveDbDsns, ws: str) -> str:
    rows = await _owner_exec(live_db.owner, ws, "SELECT id FROM electrical.drawings_documents")
    return str(rows[0][0])
