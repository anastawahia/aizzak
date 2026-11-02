"""QA of PR-C against a real Postgres: what the in-memory fakes can only pretend (INC1-AC-8,
INC1-AC-10).

Two questions:

* the DELETE cascade through the real repositories and the real outbox, for the shapes that hide
  bugs: an EXCLUDED document (its reason must go with the ``purging`` mark, or the CHECK refuses
  the write), a document with a RUNNING job, one AWAITING a person, a second delivery of the same
  purge, and the sweep that follows;
* the cancel/lease race: a person cancels while a worker claims the same job. The job's
  optimistic version must make one of them lose and retry, never lose the cancellation.

Runs only against a throwaway database (see ``tests/integration/conftest.py``); the helpers are
the drawings repository suite's own.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.framework.clock import utc_now
from app.framework.errors import ConflictError
from app.framework.identifiers import new_uuid7
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.electrical.drawings.application._common import DOCUMENT_NOT_LIVE
from app.modules.electrical.drawings.application.jobs import JobService
from app.modules.electrical.drawings.application.purge import (
    PurgeFileDocuments,
    PurgeSpaceDrawings,
    SweepPurgedDocument,
)
from app.modules.electrical.drawings.domain.document import (
    DocumentStatus,
    ExclusionReason,
)
from app.modules.electrical.drawings.domain.job import JobStatus
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION, Stage
from tests.integration.conftest import LiveDbDsns
from tests.integration.test_electrical_drawings_repository_rls import (
    _characterization,
    _ctx,
    _document,
    _job,
    _owner_exec,
    _profile_version,
    _project,
    _Repos,
)

pytestmark = [pytest.mark.live_db]


async def _events(live_db: LiveDbDsns, ws: str) -> list[str]:
    rows = await _owner_exec(
        live_db.owner,
        ws,
        "SELECT event_type FROM platform.outbox WHERE event_type LIKE 'electrical.%' "
        "ORDER BY created_at, id",
    )
    return [row[0] for row in rows]


class _Ledger:
    """A processed-events ledger that always accepts (the worker's own claim is not on trial)."""

    async def claim(self, ctx: object, *, event_id: str) -> bool:
        return True


async def test_a_file_purge_over_an_excluded_a_running_and_an_awaiting_document(
    tenant_session: TenantSessionFactory, live_db: LiveDbDsns
) -> None:
    repos = _Repos(tenant_session)
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    await repos.projects.add(ctx, project)
    excluded = _document(project)
    excluded.exclude(ExclusionReason.FILE_UNREADABLE, utc_now())
    running = _document(project)
    awaiting = _document(project)
    for document in (excluded, running, awaiting):
        await repos.documents.add(ctx, document)
    now = utc_now()
    run_job = _job(running, status=JobStatus.RUNNING, lease_until=now + timedelta(seconds=600))
    wait_job = _job(awaiting, status=JobStatus.AWAITING_CONFIRMATION)
    await repos.jobs.add(ctx, run_job)
    await repos.jobs.add(ctx, wait_job)
    for document in (excluded, running, awaiting):
        await repos.pages.seed(ctx, document.id, [1], Stage.CLASSIFY, PIPELINE_VERSION)
    await repos.profiles.append(ctx, _profile_version(project, awaiting.id))
    await repos.characterizations.put(ctx, _characterization(awaiting), project_id=project.id)
    purge = PurgeFileDocuments(
        repos.documents, repos.jobs, SqlEventOutbox(tenant_session), tenant_session
    )

    # an excluded document: the mark must clear its reason in the same write (the CHECK)
    assert await purge.execute(ctx, excluded.file_id) == 1
    stored = await repos.documents.get(ctx, excluded.id)
    assert stored is not None
    assert stored.status is DocumentStatus.PURGING and stored.exclusion_reason is None
    assert await _events(live_db, ws) == ["electrical.document.purged.v1"]

    # a running job is cancelled with the platform's reason, in the same unit as the mark
    assert await purge.execute(ctx, running.file_id) == 1
    assert await purge.execute(ctx, awaiting.file_id) == 1
    for job in (run_job, wait_job):
        fresh = await repos.jobs.get(ctx, job.id)
        assert fresh is not None
        assert fresh.status is JobStatus.CANCELLED and fresh.failure_code == DOCUMENT_NOT_LIVE
        assert fresh.finished_at is not None and fresh.lease_until is None
    kinds = await _events(live_db, ws)
    assert kinds.count("electrical.document.purged.v1") == 3
    assert kinds.count("electrical.job.completed.v1") == 2

    # the second delivery of the same cascade is a no-op and publishes nothing
    for document in (excluded, running, awaiting):
        assert await purge.execute(ctx, document.file_id) == 0
    assert await _events(live_db, ws) == kinds

    # nothing reads a purging document, even by id
    assert await repos.documents.lock_live(ctx, running.id) is None
    assert await repos.jobs.open_for_document(ctx, running.id) is None

    # phase 2: the worker's sweep takes every row of every document, once
    sweep = SweepPurgedDocument(repos.sweep, _Ledger(), tenant_session)  # type: ignore[arg-type]
    for document in (excluded, running, awaiting):
        assert await sweep.execute(ctx, document.id, event_id=new_uuid7()) > 0
        assert await sweep.execute(ctx, document.id, event_id=new_uuid7()) == 0
    left = await _owner_exec(
        live_db.owner,
        ws,
        "SELECT (SELECT count(*) FROM electrical.drawings_documents),"
        " (SELECT count(*) FROM electrical.drawings_jobs),"
        " (SELECT count(*) FROM electrical.drawings_pages),"
        " (SELECT count(*) FROM electrical.drawings_profile_versions),"
        " (SELECT count(*) FROM electrical.drawings_characterizations)",
    )
    assert left == [(0, 0, 0, 0, 0)]
    assert await repos.projects.get(ctx, project.id) is not None  # a file purge keeps the project


async def test_a_space_purge_takes_every_status_and_the_project_in_one_unit(
    tenant_session: TenantSessionFactory, live_db: LiveDbDsns
) -> None:
    repos = _Repos(tenant_session)
    ws = new_uuid7()
    ctx = _ctx(ws)
    project = _project(ws)
    other = _project(ws)  # another space of the same workspace: untouched
    await repos.projects.add(ctx, project)
    await repos.projects.add(ctx, other)
    active = _document(project)
    excluded = _document(project)
    excluded.exclude(ExclusionReason.DUPLICATE_CONTENT, utc_now())
    stranger = _document(other)
    for document in (active, excluded, stranger):
        await repos.documents.add(ctx, document)
    await repos.jobs.add(ctx, _job(active))
    await repos.profiles.append(ctx, _profile_version(project))
    purge = PurgeSpaceDrawings(
        repos.projects,
        repos.documents,
        repos.jobs,
        SqlEventOutbox(tenant_session),
        tenant_session,
    )

    assert await purge.execute(ctx, project.space_id) == 2
    assert await repos.projects.get_by_space(ctx, project.space_id) is None
    assert await repos.projects.get_by_space(ctx, other.space_id) is not None
    kept = await repos.documents.get(ctx, stranger.id)
    assert kept is not None and kept.status is DocumentStatus.ACTIVE
    # a second delivery: no project, nothing marked, nothing published
    before = await _events(live_db, ws)
    assert await purge.execute(ctx, project.space_id) == 0
    assert await _events(live_db, ws) == before
    # the project's own profile versions went with it
    versions = await _owner_exec(
        live_db.owner,
        ws,
        "SELECT count(*) FROM electrical.drawings_profile_versions WHERE project_id = :p",
        p=project.id,
    )
    assert versions == [(0,)]


async def test_a_cancel_and_a_lease_claim_racing_never_lose_the_cancellation(
    tenant_session: TenantSessionFactory,
) -> None:
    """Twenty rounds: the person's cancel and the worker's claim of one queued job run at the
    same time. Afterwards the job is either ``cancelled`` (the cancel won), or ``running`` WITH a
    recorded cancel request (the claim won first, the cancel became a request). A ``running`` job
    with no request means the cancel was overwritten by the claim: a lost update.

    Measured: the two tasks mostly run back to back and the claim wins; the assertion guards
    the interleavings that do occur.
    """
    repos = _Repos(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    service = JobService(repos.jobs, repos.documents, repos.pages, outbox, tenant_session)
    outcomes: set[str] = set()
    for _ in range(20):
        ws = new_uuid7()
        ctx = _ctx(ws)
        project = _project(ws)
        await repos.projects.add(ctx, project)
        document = _document(project)
        await repos.documents.add(ctx, document)
        job = _job(document)
        await repos.jobs.add(ctx, job)

        async def claim(job_id: str = job.id, ctx: object = ctx) -> None:
            async with tenant_session.begin(ctx):  # type: ignore[arg-type]
                fresh = await repos.jobs.get(ctx, job_id)  # type: ignore[arg-type]
                assert fresh is not None
                if fresh.take_lease(utc_now(), 600):
                    await repos.jobs.save(ctx, fresh)  # type: ignore[arg-type]

        async def cancel(job_id: str = job.id, ctx: object = ctx) -> None:
            await service.cancel(ctx, job_id)  # type: ignore[arg-type]

        results = await asyncio.gather(claim(), cancel(), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                # the loser of the optimistic race may be told so; it must be a conflict
                assert isinstance(result, ConflictError), repr(result)
        final = await repos.jobs.get(ctx, job.id)
        assert final is not None
        if final.status is JobStatus.CANCELLED:
            outcomes.add("cancelled")
        else:
            assert final.status is JobStatus.RUNNING, final.status
            assert final.cancel_requested_at is not None, "the claim overwrote the cancel"
            outcomes.add("requested")
    assert outcomes  # at least one shape was seen; both are correct


async def test_an_id_that_is_not_a_uuid_is_simply_not_found(
    tenant_session: TenantSessionFactory,
) -> None:
    repos = _Repos(tenant_session)
    ctx = _ctx(new_uuid7())
    assert await repos.documents.get(ctx, "not-a-uuid") is None
    assert await repos.jobs.get(ctx, "not-a-uuid") is None
    assert await repos.projects.get(ctx, "not-a-uuid") is None
