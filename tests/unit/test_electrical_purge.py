"""The purge of drawing documents (BT21; INC1-AC-10, PLT-04, HLD §8.8).

Phase 1 (``PurgeFileDocuments`` / ``PurgeSpaceDrawings``) is ONE unit of work inside the delete
request; phase 2 (``SweepPurgedDocument``) is the worker's. Both are idempotent. The framework
participants (``framework/di/electrical_purge.py``) are tested here too: they only fan out to
drawings in a fixed order and return its count.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.framework.di.electrical_purge import ElectricalFilePurge, ElectricalSpacePurge
from app.framework.errors import NotFoundError
from app.modules.electrical.drawings.domain.document import DocumentStatus, ExclusionReason
from app.modules.electrical.drawings.domain.job import JobStatus
from tests.unit.support_electrical import (
    IEC_TOKENS,
    FakePdfInspection,
    Stack,
    build_stack,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)

LOW = {"schema": 1, "auto_accept_confidence": 0.5}


@pytest.fixture
def stack() -> Stack:
    return build_stack()


async def _document(stack: Stack, file_id: str, *, inspect: bool = False) -> tuple[str, str]:
    project = await stack.projects.get_by_space(stack.ctx, "22222222-2222-7222-8222-222222222222")
    if project is None:
        project = await seed_project(stack, **LOW)
    await seed_file(stack, file_id, body=f"%PDF {file_id}".encode())
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id=file_id, checksum=None
    )
    if inspect:
        stack.pdf = FakePdfInspection(
            make_summary(page_count=2), [tokens_page(n, IEC_TOKENS) for n in (1, 2)]
        )
        await stack.inspector().run(stack.ctx, job_id=registered.job.id, event_id=f"e-{file_id}")
    stack.outbox.records.clear()
    stack.trace.entries.clear()
    stack.uow.begun = 0
    return registered.document.id, registered.job.id


# ------------------------------------------------------------------------------------ phase 1
async def test_a_file_purge_marks_its_documents_cancels_their_jobs_and_publishes_in_one_unit(
    stack: Stack,
) -> None:
    doc_id, job_id = await _document(stack, "f1")

    marked = await stack.services["purge_file"].execute(stack.ctx, "f1")

    assert marked == 1
    document = await stack.documents.get(stack.ctx, doc_id)
    assert document is not None and document.status is DocumentStatus.PURGING
    assert document.purging_at is not None and document.exclusion_reason is None
    job = await stack.jobs.get(stack.ctx, job_id)
    assert job is not None and job.status is JobStatus.CANCELLED
    assert job.failure_code == "electrical.document_not_live"
    assert stack.outbox.types() == ["electrical.job.completed.v1", "electrical.document.purged.v1"]
    assert stack.outbox.data("electrical.document.purged.v1") == [
        {"document_id": doc_id, "project_id": document.project_id, "file_id": "f1"}
    ]
    assert stack.outbox.data("electrical.job.completed.v1")[0]["failure_code"] == (
        "electrical.document_not_live"
    )
    # ONE unit of work: the marks, the cancellation and both events
    assert stack.uow.begun == 1 and stack.trace.entries[0] == "begin"
    assert stack.trace.entries[-1] == "commit"


async def test_the_reads_stop_seeing_a_purging_document_at_once(stack: Stack) -> None:
    doc_id, _ = await _document(stack, "f1", inspect=True)
    project_id = stack.documents.rows[doc_id].project_id
    assert await stack.characterizations.for_project(stack.ctx, project_id)

    await stack.services["purge_file"].execute(stack.ctx, "f1")

    with pytest.raises(NotFoundError):
        await stack.document_service.get(stack.ctx, doc_id)
    with pytest.raises(NotFoundError):
        await stack.services["characterization"].for_document(stack.ctx, doc_id)
    assert await stack.characterizations.for_project(stack.ctx, project_id) == []
    assert await stack.documents.active_files(stack.ctx, project_id) == {}
    assert (
        await stack.documents.live_in_space(stack.ctx, stack.documents.rows[doc_id].space_id) == []
    )
    listed = await stack.document_service.list(
        stack.ctx, project_id, status=None, limit=10, cursor=None
    )
    assert listed.data == []
    assert (
        await stack.services["characterization"].for_project(stack.ctx, project_id)
    ).documents == 0


async def test_a_second_purge_returns_zero_and_writes_no_event(stack: Stack) -> None:
    await _document(stack, "f1")
    assert await stack.services["purge_file"].execute(stack.ctx, "f1") == 1
    stack.outbox.records.clear()

    assert await stack.services["purge_file"].execute(stack.ctx, "f1") == 0
    assert stack.outbox.records == []


async def test_a_file_without_documents_purges_to_zero(stack: Stack) -> None:
    assert await stack.services["purge_file"].execute(stack.ctx, "never-registered") == 0
    assert stack.outbox.records == []


async def test_an_excluded_document_is_purged_too_and_loses_its_reason(stack: Stack) -> None:
    doc_id, _ = await _document(stack, "f1")
    stack.documents.rows[doc_id].exclude(ExclusionReason.FILE_UNREADABLE, stack.clock())
    assert await stack.services["purge_file"].execute(stack.ctx, "f1") == 1
    document = await stack.documents.get(stack.ctx, doc_id)
    assert document is not None and document.status is DocumentStatus.PURGING
    assert document.exclusion_reason is None  # the fake enforces ck_drawings_documents_exclusion


async def test_a_failing_outbox_rolls_the_whole_phase_back(stack: Stack) -> None:
    doc_id, job_id = await _document(stack, "f1")
    stack.outbox.fail = True
    with pytest.raises(RuntimeError):
        await stack.services["purge_file"].execute(stack.ctx, "f1")
    document = await stack.documents.get(stack.ctx, doc_id)
    job = await stack.jobs.get(stack.ctx, job_id)
    assert document is not None and document.is_live
    assert job is not None and job.status is JobStatus.QUEUED


async def test_a_file_replaced_by_rename_or_retag_is_purged_the_same_way(stack: Stack) -> None:
    """The four paths (delete, complete, rename, retag) all end in the same call: the
    cascade's ``electrical`` participant, which is ``PurgeFileDocuments.execute``."""
    doc_id, _ = await _document(stack, "old")
    participant = ElectricalFilePurge(drawings=stack.services["purge_file"])
    assert await participant.execute(stack.ctx, "old") == 1
    assert stack.documents.rows[doc_id].status is DocumentStatus.PURGING


# -------------------------------------------------------------------------- a whole space
async def test_a_space_purge_takes_every_document_and_the_project(stack: Stack) -> None:
    first, _ = await _document(stack, "f1")
    second, _ = await _document(stack, "f2")
    stack.documents.rows[second].exclude(ExclusionReason.FILE_UNREADABLE, stack.clock())
    space_id = stack.documents.rows[first].space_id
    project_id = stack.documents.rows[first].project_id

    marked = await stack.services["purge_space"].execute(stack.ctx, space_id)

    assert marked == 2
    assert {stack.documents.rows[d].status for d in (first, second)} == {DocumentStatus.PURGING}
    assert await stack.projects.get(stack.ctx, project_id) is None
    assert sorted(
        stack.outbox.data("electrical.document.purged.v1"), key=lambda d: d["file_id"]
    ) == [
        {"document_id": first, "project_id": project_id, "file_id": "f1"},
        {"document_id": second, "project_id": project_id, "file_id": "f2"},
    ]
    assert stack.uow.begun == 1  # one unit for the lot
    assert await stack.services["purge_space"].execute(stack.ctx, space_id) == 0  # idempotent


async def test_a_space_without_a_project_purges_to_zero(stack: Stack) -> None:
    assert await stack.services["purge_space"].execute(stack.ctx, "empty-space") == 0
    assert stack.uow.begun == 1 and stack.outbox.records == []


async def test_another_spaces_documents_are_left_alone(stack: Stack) -> None:
    doc_id, _ = await _document(stack, "f1")
    assert await stack.services["purge_space"].execute(stack.ctx, "another-space") == 0
    assert stack.documents.rows[doc_id].status is DocumentStatus.ACTIVE


# ------------------------------------------------------------------------------------ phase 2
async def test_the_sweep_claims_then_deletes_everything_under_the_document(stack: Stack) -> None:
    doc_id, job_id = await _document(stack, "f1", inspect=True)
    await stack.services["purge_file"].execute(stack.ctx, "f1")
    stack.trace.entries.clear()

    deleted = await stack.services["sweep"].execute(stack.ctx, doc_id, event_id="evt-purge")

    assert deleted > 1
    assert stack.trace.entries[:2] == ["begin", "claim:evt-purge"]  # the claim opens the unit
    assert doc_id not in stack.documents.rows and job_id not in stack.jobs.rows
    assert [k for k in stack.pages.stages if k[1] == doc_id] == []
    assert [k for k in stack.characterizations.rows if k[1] == doc_id] == []
    assert [v for v in stack.profiles.rows if v.document_id == doc_id] == []


async def test_a_duplicate_sweep_delivery_and_a_missing_document_delete_nothing(
    stack: Stack,
) -> None:
    doc_id, _ = await _document(stack, "f1", inspect=True)
    await stack.services["purge_file"].execute(stack.ctx, "f1")
    assert await stack.services["sweep"].execute(stack.ctx, doc_id, event_id="evt-1") > 0

    assert await stack.services["sweep"].execute(stack.ctx, doc_id, event_id="evt-1") == 0
    assert await stack.services["sweep"].execute(stack.ctx, doc_id, event_id="evt-2") == 0
    assert await stack.services["sweep"].execute(stack.ctx, "gone", event_id="evt-3") == 0


async def test_the_sweep_refuses_a_document_that_is_not_purging(stack: Stack) -> None:
    doc_id, _ = await _document(stack, "f1")
    assert await stack.services["sweep"].execute(stack.ctx, doc_id, event_id="evt-1") == 0
    assert stack.documents.rows[doc_id].status is DocumentStatus.ACTIVE


# --------------------------------------------------------------------- the framework side
class _Counting:
    def __init__(self, trace: list[str], name: str, count: int) -> None:
        self.trace, self.name, self.count = trace, name, count
        self.calls: list[Any] = []

    async def execute(self, ctx: Any, subject: str) -> int:
        self.trace.append(self.name)
        self.calls.append(subject)
        return self.count


async def test_the_framework_participants_fan_out_to_drawings_and_return_its_count() -> None:
    trace: list[str] = []
    file_side = _Counting(trace, "drawings", 3)
    space_side = _Counting(trace, "drawings", 5)
    ctx = build_stack().ctx

    assert await ElectricalFilePurge(drawings=file_side).execute(ctx, "file-1") == 3
    assert await ElectricalSpacePurge(drawings=space_side).execute(ctx, "space-1") == 5
    assert file_side.calls == ["file-1"] and space_side.calls == ["space-1"]


async def test_the_participants_return_what_the_real_use_cases_mark(stack: Stack) -> None:
    await _document(stack, "f1")
    await _document(stack, "f2")
    file_participant = ElectricalFilePurge(drawings=stack.services["purge_file"])
    space_participant = ElectricalSpacePurge(drawings=stack.services["purge_space"])
    space_id = next(iter(stack.documents.rows.values())).space_id

    assert await file_participant.execute(stack.ctx, "f1") == 1
    assert await file_participant.execute(stack.ctx, "f1") == 0
    assert await space_participant.execute(stack.ctx, space_id) == 1  # f2 only: f1 was done
