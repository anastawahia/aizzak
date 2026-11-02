"""Ingestion jobs through ``JobService`` and ``DocumentService`` (BT20; INC1-AC-8).

The state machine itself is BT15's; here are the SERVICES around it: cancel (immediate for the
waiting, cooperative for the running, repeatable), resume, reprocess, the single open job of a
document, the derived counts, the queue position and the wait estimate.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.modules.electrical.drawings.domain.document import (
    DocumentStatus,
    ExclusionReason,
    ProfileState,
)
from app.modules.electrical.drawings.domain.job import JobKind, JobStatus
from app.modules.electrical.drawings.domain.pages import StageStatus
from app.modules.electrical.drawings.domain.pipeline import Stage
from tests.unit.support_electrical import (
    IEC_TOKENS,
    FakePdfInspection,
    Stack,
    build_stack,
    make_document,
    make_job,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)

LOW = {"schema": 1, "auto_accept_confidence": 0.5}
HIGH = {"schema": 1, "auto_accept_confidence": 1.0}


@pytest.fixture
def stack() -> Stack:
    return build_stack()


def _pdf(pages: int = 4) -> FakePdfInspection:
    return FakePdfInspection(
        make_summary(page_count=pages), [tokens_page(n, IEC_TOKENS) for n in range(1, pages + 1)]
    )


async def _registered(stack: Stack, file_id: str = "f1", profile: Any = LOW) -> tuple[str, str]:
    project = await stack.projects.get_by_space(stack.ctx, "22222222-2222-7222-8222-222222222222")
    if project is None:
        project = await seed_project(stack, **profile)
    await seed_file(stack, file_id, body=f"%PDF {file_id}".encode())
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id=file_id, checksum=None
    )
    return registered.document.id, registered.job.id


async def _state(stack: Stack, job_id: str) -> Any:
    job = await stack.jobs.get(stack.ctx, job_id)
    assert job is not None
    return job


# -------------------------------------------------------------------------------- cancelling
async def test_cancelling_a_queued_job_is_immediate_and_published(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.outbox.records.clear()

    view = await stack.job_service.cancel(stack.ctx, job_id)

    assert view.job.status is JobStatus.CANCELLED
    assert stack.outbox.data("electrical.job.completed.v1") == [
        {"job_id": job_id, "document_id": doc_id, "status": "cancelled"}
    ]
    assert (await stack.documents.get(stack.ctx, doc_id)) is not None  # the document stays


async def test_cancelling_a_running_job_only_records_the_request_and_repeating_writes_nothing(
    stack: Stack,
) -> None:
    _, job_id = await _registered(stack)
    job = stack.jobs.rows[job_id]
    job.take_lease(stack.clock(), 600)
    stack.outbox.records.clear()

    first = await stack.job_service.cancel(stack.ctx, job_id)
    assert first.job.status is JobStatus.RUNNING and first.job.cancel_requested_at is not None
    assert stack.outbox.records == []  # the worker publishes the end, at its batch boundary
    stack.uow.begun = 0
    stamp = first.job.cancel_requested_at

    again = await stack.job_service.cancel(stack.ctx, job_id)
    assert again.job.cancel_requested_at == stamp and stack.uow.begun == 0  # 200, no write


async def test_cancelling_a_job_that_is_already_cancelled_is_a_no_op(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    await stack.job_service.cancel(stack.ctx, job_id)
    stack.uow.begun = 0
    view = await stack.job_service.cancel(stack.ctx, job_id)
    assert view.job.status is JobStatus.CANCELLED and stack.uow.begun == 0


async def test_cancelling_a_finished_job_is_a_conflict(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    with pytest.raises(ConflictError) as raised:
        await stack.job_service.cancel(stack.ctx, job_id)
    assert raised.value.code == "electrical.job_conflict"


async def test_cancelling_a_job_awaiting_confirmation_is_immediate() -> None:
    stack = build_stack()
    _, job_id = await _registered(stack, profile=HIGH)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    assert (await _state(stack, job_id)).status is JobStatus.AWAITING_CONFIRMATION
    view = await stack.job_service.cancel(stack.ctx, job_id)
    assert view.job.status is JobStatus.CANCELLED


# --------------------------------------------------------------------------------- resuming
async def test_resuming_returns_the_same_job_to_the_queue_and_publishes_job_requested(
    stack: Stack,
) -> None:
    doc_id, job_id = await _registered(stack)
    await stack.job_service.cancel(stack.ctx, job_id)
    stack.outbox.records.clear()

    view = await stack.job_service.resume(stack.ctx, job_id)

    assert view.job.id == job_id and view.job.status is JobStatus.QUEUED
    assert view.job.attempt == 2 and view.job.cancel_requested_at is None
    assert stack.outbox.data("electrical.job.requested.v1") == [
        {"job_id": job_id, "document_id": doc_id, "reason": "resume"}
    ]


async def test_resume_is_allowed_for_an_expired_lease_and_refused_for_a_live_one(
    stack: Stack,
) -> None:
    _, job_id = await _registered(stack)
    stack.jobs.rows[job_id].take_lease(stack.clock(), 600)
    with pytest.raises(ConflictError) as raised:
        await stack.job_service.resume(stack.ctx, job_id)
    assert raised.value.code == "electrical.job_conflict"

    stack.clock.advance(601)
    view = await stack.job_service.resume(stack.ctx, job_id)
    assert view.job.status is JobStatus.QUEUED and view.job.attempt == 2


async def test_a_job_awaiting_confirmation_is_confirmed_not_resumed() -> None:
    stack = build_stack()
    _, job_id = await _registered(stack, profile=HIGH)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    with pytest.raises(ConflictError) as raised:
        await stack.job_service.resume(stack.ctx, job_id)
    assert raised.value.code == "electrical.profile_unconfirmed"


async def test_a_job_of_a_document_that_is_not_live_cannot_be_resumed(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    await stack.job_service.cancel(stack.ctx, job_id)
    stack.documents.rows[doc_id].status = DocumentStatus.EXCLUDED
    stack.documents.rows[doc_id].exclusion_reason = ExclusionReason.FILE_UNREADABLE
    with pytest.raises(ConflictError):
        await stack.job_service.resume(stack.ctx, job_id)


async def test_resume_processes_the_unfinished_pages_only(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    job = await _state(stack, job_id)
    assert job.status is JobStatus.SUCCEEDED
    # two pages lose their result (a crash after the job was marked, say)
    await stack.pages.reset(stack.ctx, doc_id, [2, 3], Stage.CLASSIFY)
    stack.jobs.rows[job_id].status = JobStatus.FAILED
    stack.jobs.rows[job_id].failure_code = "electrical.pdf_rejected"
    stack.jobs.rows[job_id].failure_detail = "x"

    await stack.job_service.resume(stack.ctx, job_id)
    fresh = _pdf()
    await stack.inspector(fresh).run(stack.ctx, job_id=job_id, event_id="e2")

    assert [c for c in fresh.calls if c[0] == "pages"] == [("pages", 2, 3)]
    assert await stack.pages.stage_counts(stack.ctx, doc_id, Stage.CLASSIFY) == {
        StageStatus.SUCCEEDED: 4
    }


# ------------------------------------------------------------------------------ reprocessing
async def test_reprocessing_creates_a_reprocess_job_and_returns_the_pages_to_pending(
    stack: Stack,
) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    stack.outbox.records.clear()

    job = await stack.document_service.reprocess(stack.ctx, doc_id, pages=[3, 2, 3])

    assert job.kind is JobKind.REPROCESS and job.pages == (2, 3) and job.id != job_id
    assert job.status is JobStatus.QUEUED
    assert await stack.pages.unfinished(stack.ctx, doc_id, Stage.CLASSIFY) == [2, 3]
    assert stack.outbox.data("electrical.job.requested.v1") == [
        {"job_id": job.id, "document_id": doc_id, "reason": "reprocess"}
    ]


async def test_reprocessing_the_whole_document_has_no_page_subset(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    job = await stack.document_service.reprocess(stack.ctx, doc_id, pages=None)
    assert job.pages is None
    assert await stack.pages.unfinished(stack.ctx, doc_id, Stage.CLASSIFY) == [1, 2, 3, 4]


async def test_a_document_has_one_open_job_at_a_time(stack: Stack) -> None:
    doc_id, _ = await _registered(stack)  # the ingestion job is still queued
    stack.documents.rows[doc_id].page_count = 4
    stack.outbox.records.clear()
    with pytest.raises(ConflictError) as raised:
        await stack.document_service.reprocess(stack.ctx, doc_id, pages=None)
    assert raised.value.code == "electrical.job_conflict"
    assert len(stack.jobs.rows) == 1 and stack.outbox.records == []


async def test_reprocessing_is_refused_while_the_proposal_awaits_confirmation() -> None:
    stack = build_stack()
    doc_id, job_id = await _registered(stack, profile=HIGH)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    assert stack.documents.rows[doc_id].profile_state is ProfileState.PROPOSED
    with pytest.raises(ConflictError) as raised:
        await stack.document_service.reprocess(stack.ctx, doc_id, pages=None)
    assert raised.value.code == "electrical.profile_unconfirmed"


async def test_reprocess_pages_must_exist(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    for bad in ([0], [5], []):
        with pytest.raises(ValidationError):
            await stack.document_service.reprocess(stack.ctx, doc_id, pages=bad)


async def test_a_document_never_inspected_cannot_reprocess_pages(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    await stack.job_service.cancel(stack.ctx, job_id)
    with pytest.raises(ConflictError):
        await stack.document_service.reprocess(stack.ctx, doc_id, pages=[1])


# ----------------------------------------------------------- the derived view of a job
async def test_the_counts_are_derived_from_the_stage_rows(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await stack.inspector().run(stack.ctx, job_id=job_id, event_id="e1")
    await stack.pages.reset(stack.ctx, doc_id, [4], Stage.CLASSIFY)

    view = await stack.job_service.get(stack.ctx, job_id)

    assert view.counts == {StageStatus.SUCCEEDED: 3, StageStatus.PENDING: 1}


async def test_queue_position_and_estimate_use_the_median_of_the_last_runs(stack: Stack) -> None:
    ids = []
    for index in range(3):
        ids.append((await _registered(stack, file_id=f"f{index}"))[1])
        stack.clock.advance(1)  # created_at orders the queue
    stack.jobs.run_seconds = [10.0, 30.0, 20.0]

    first = await stack.job_service.get(stack.ctx, ids[0])
    third = await stack.job_service.get(stack.ctx, ids[2])

    assert (first.queue_position, first.estimated_wait_s) == (1, 20.0)
    assert (third.queue_position, third.estimated_wait_s) == (3, 60.0)


async def test_without_history_there_is_no_estimate_and_only_queued_jobs_have_a_position(
    stack: Stack,
) -> None:
    _, job_id = await _registered(stack)
    view = await stack.job_service.get(stack.ctx, job_id)
    assert view.queue_position == 1 and view.estimated_wait_s is None  # null, not zero
    stack.jobs.rows[job_id].take_lease(stack.clock(), 600)
    running = await stack.job_service.get(stack.ctx, job_id)
    assert running.queue_position is None and running.estimated_wait_s is None and not running.stale
    stack.clock.advance(601)
    assert (await stack.job_service.get(stack.ctx, job_id)).stale is True


async def test_a_job_of_a_purging_document_or_another_workspace_is_a_404(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    with pytest.raises(NotFoundError):
        await stack.job_service.get(stack.ctx, "missing")
    stack.documents.rows[doc_id].status = DocumentStatus.PURGING
    with pytest.raises(NotFoundError):
        await stack.job_service.get(stack.ctx, job_id)
    with pytest.raises(NotFoundError):
        await stack.job_service.cancel(stack.ctx, job_id)


async def test_a_foreign_row_is_invisible_to_the_service(stack: Stack) -> None:
    stack.jobs.rows["j9"] = make_job(id="j9", workspace_id="other", document_id="d9")
    stack.documents.rows["d9"] = make_document(id="d9", workspace_id="other")
    with pytest.raises(NotFoundError):
        await stack.job_service.get(stack.ctx, "j9")
