"""STG-00 and the replacement preview (BT18; INC1-AC-6, INC1-AC-11).

``DocumentService.register`` runs the guard of HLD §8.11 in order and the first failure is the
answer, with nothing written; only a registration that passes opens ONE unit of work that
excludes the project's orphans, inserts the document and its job, and appends the event.
"""

from __future__ import annotations

import pytest

from app.framework.errors import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnsupportedTypeError,
    ValidationError,
)
from app.modules.electrical.drawings.domain.document import (
    DocumentStatus,
    ExclusionReason,
)
from app.modules.electrical.drawings.domain.job import JobKind, JobStatus
from tests.unit.support_electrical import (
    SPACE,
    FakeDrawingFile,
    Stack,
    build_stack,
    make_document,
    make_job,
    seed_file,
    seed_project,
)

SHA_A = "a" * 64


@pytest.fixture
def stack() -> Stack:
    return build_stack()


async def _register(stack: Stack, project_id: str, file_id: str, checksum: str | None = None):
    return await stack.document_service.register(
        stack.ctx, project_id, file_id=file_id, checksum=checksum
    )


def _nothing_written(stack: Stack) -> None:
    assert stack.uow.begun == 0
    assert stack.documents.rows == {} and stack.jobs.rows == {}
    assert stack.outbox.records == []


# ---------------------------------------------------------------------------- the happy path
async def test_a_registration_creates_the_document_the_job_and_the_event_in_one_unit(
    stack: Stack,
) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    stack.trace.entries.clear()

    result = await _register(stack, project.id, "f1")

    assert result.document.status is DocumentStatus.ACTIVE
    assert result.document.project_id == project.id and result.document.file_id == "f1"
    assert result.document.space_id == SPACE
    assert result.job.status is JobStatus.QUEUED and result.job.kind is JobKind.INGEST
    assert result.job.document_id == result.document.id and result.excluded == ()
    # ONE begin ... commit, with the outbox row between them
    assert stack.trace.entries == ["begin", "outbox:document.registered.v1", "commit"]
    (data,) = stack.outbox.data("electrical.document.registered.v1")
    assert data == {
        "document_id": result.document.id,
        "project_id": project.id,
        "file_id": "f1",
        "job_id": result.job.id,
    }
    assert await stack.documents.get(stack.ctx, result.document.id) is not None
    assert await stack.jobs.get(stack.ctx, result.job.id) is not None


async def test_the_client_checksum_is_accepted_in_either_case(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    result = await _register(stack, project.id, "f1", checksum=SHA_A.upper())
    assert result.document.status is DocumentStatus.ACTIVE


async def test_a_malformed_checksum_is_a_422_before_anything_is_read(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    with pytest.raises(ValidationError):
        await _register(stack, project.id, "f1", checksum="not-a-sha")
    _nothing_written(stack)


# --------------------------------------------------------------------------- the guard, in order
async def test_an_unknown_project_is_a_404(stack: Stack) -> None:
    await seed_file(stack, "f1")
    with pytest.raises(NotFoundError):
        await _register(stack, "nope", "f1")
    _nothing_written(stack)


async def test_a_project_of_another_workspace_is_a_404(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    other = build_stack(workspace_id="33333333-3333-7333-8333-333333333333")
    stack.projects.rows[project.id] = project
    other.projects.rows = dict(stack.projects.rows)  # the same row, another tenant's context
    await seed_file(other, "f1")
    with pytest.raises(NotFoundError):
        await _register(other, project.id, "f1")


async def test_an_unreadable_file_is_a_404_whatever_the_reason(stack: Stack) -> None:
    """Unknown, unfinished, foreign and deleted files are one answer: the fake only knows the
    files it was given, as ``FilesQuery.get_readable`` only returns ready ones."""
    project = await seed_project(stack)
    with pytest.raises(NotFoundError):
        await _register(stack, project.id, "never-uploaded")
    _nothing_written(stack)


async def test_only_a_pdf_can_be_registered(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1", content_type="image/png")
    with pytest.raises(UnsupportedTypeError) as raised:
        await _register(stack, project.id, "f1")
    assert raised.value.code == "electrical.not_a_pdf" and raised.value.status == 415
    _nothing_written(stack)


async def test_a_file_of_another_space_is_refused(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1", space_id="99999999-9999-7999-8999-999999999999")
    with pytest.raises(ValidationError) as raised:
        await _register(stack, project.id, "f1")
    assert raised.value.code == "electrical.file_not_in_project_space"
    _nothing_written(stack)


async def test_a_file_that_is_already_a_document_is_a_conflict(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    await _register(stack, project.id, "f1")
    before = stack.uow.begun
    with pytest.raises(ConflictError) as raised:
        await _register(stack, project.id, "f1")
    assert raised.value.code == "electrical.document_exists"
    assert stack.uow.begun == before  # the second call wrote nothing


async def test_an_excluded_document_still_owns_its_file(stack: Stack) -> None:
    """The unique index counts every non-purging row: an excluded document keeps the file."""
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    stack.documents.rows["d0"] = make_document(
        id="d0",
        project_id=project.id,
        file_id="f1",
        status=DocumentStatus.EXCLUDED,
        exclusion_reason=ExclusionReason.DUPLICATE_CONTENT,
    )
    with pytest.raises(ConflictError) as raised:
        await _register(stack, project.id, "f1")
    assert raised.value.code == "electrical.document_exists"


async def test_a_purging_document_does_not_block_a_new_registration(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    stack.documents.rows["d0"] = make_document(
        id="d0", project_id=project.id, file_id="f1", status=DocumentStatus.PURGING
    )
    result = await _register(stack, project.id, "f1")
    assert result.document.id != "d0"


async def test_a_namesake_that_was_not_replaced_names_the_older_files(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f2")
    stack.namesakes.set("f2", replaced=["older-1", "older-2"])
    with pytest.raises(ConflictError) as raised:
        await _register(stack, project.id, "f2")
    assert raised.value.code == "electrical.namesake_not_replaced"
    assert "older-1" in raised.value.detail and "older-2" in raised.value.detail
    _nothing_written(stack)


async def test_a_checksum_equal_to_an_active_documents_is_a_duplicate(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f2")
    stack.documents.rows["d0"] = make_document(
        id="d0", project_id=project.id, file_id="f1", content_sha256=SHA_A
    )
    with pytest.raises(ConflictError) as raised:
        await _register(stack, project.id, "f2", checksum=SHA_A)
    assert raised.value.code == "electrical.duplicate_document" and "d0" in raised.value.detail
    # without the checksum the guard cannot know, and the registration goes on
    assert (await _register(stack, project.id, "f2")).document.file_id == "f2"


async def test_the_queue_protection_limit_is_a_429_with_retry_after(stack: Stack) -> None:
    project = await seed_project(stack)
    for index in range(stack.settings.queue_protection_limit):
        stack.jobs.rows[f"j{index}"] = make_job(id=f"j{index}", document_id=f"d{index}")
    await seed_file(stack, "f1")
    with pytest.raises(RateLimitedError) as raised:
        await _register(stack, project.id, "f1")
    assert raised.value.code == "electrical.ingestion_queue_full"
    assert raised.value.status == 429 and raised.value.retry_after_s == 60
    assert stack.documents.rows == {} and stack.outbox.records == []


async def test_the_first_failing_check_is_the_answer(stack: Stack) -> None:
    """Not a PDF AND another space AND a namesake: the type check comes first."""
    project = await seed_project(stack)
    await seed_file(stack, "f1", content_type="text/plain", space_id="other")
    stack.namesakes.set("f1", replaced=["older"])
    with pytest.raises(UnsupportedTypeError):
        await _register(stack, project.id, "f1")
    await seed_file(stack, "f3", space_id="other")
    stack.namesakes.set("f3", replaced=["older"])
    with pytest.raises(ValidationError):  # the space check comes before the namesake check
        await _register(stack, project.id, "f3")


# ----------------------------------------------------------------------------- the unit of work
async def test_a_failing_outbox_leaves_no_document_and_no_job(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    stack.outbox.fail = True
    with pytest.raises(RuntimeError):
        await _register(stack, project.id, "f1")
    assert stack.documents.rows == {} and stack.jobs.rows == {}
    assert stack.trace.entries[-1] == "rollback"


async def test_two_registrations_of_one_file_are_decided_by_the_index(stack: Stack) -> None:
    """Both pass the reads (the second read ran before the first commit); the unique index
    refuses the second insert and nothing of it is kept."""
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    first = await _register(stack, project.id, "f1")

    async def blind(*_args: object, **_kwargs: object) -> list[object]:
        return []

    stack.documents.not_purging_for_file = blind  # type: ignore[method-assign]
    with pytest.raises(ConflictError) as raised:
        await _register(stack, project.id, "f1")
    assert raised.value.code == "electrical.document_exists"
    assert list(stack.documents.rows) == [first.document.id]
    assert len(stack.jobs.rows) == 1
    assert stack.outbox.types() == ["electrical.document.registered.v1"]


async def test_orphans_are_excluded_in_the_same_unit_and_reported(stack: Stack) -> None:
    project = await seed_project(stack)
    old = await _register(stack, project.id, (await seed_file(stack, "old")).file_id)
    await seed_file(stack, "kept")
    kept = await _register(stack, project.id, "kept")
    del stack.files._files[(stack.ctx.workspace_id, "old")]  # the file was deleted
    await seed_file(stack, "new")
    stack.trace.entries.clear()

    result = await _register(stack, project.id, "new")

    assert result.excluded == ((old.document.id, ExclusionReason.FILE_UNREADABLE),)
    orphan = await stack.documents.get(stack.ctx, old.document.id)
    assert orphan is not None and orphan.status is DocumentStatus.EXCLUDED
    assert orphan.exclusion_reason is ExclusionReason.FILE_UNREADABLE
    survivor = await stack.documents.get(stack.ctx, kept.document.id)
    assert survivor is not None and survivor.status is DocumentStatus.ACTIVE
    assert stack.trace.entries.count("begin") == 1  # exclusion and insertion: one unit


async def test_a_failed_registration_does_not_keep_the_exclusions(stack: Stack) -> None:
    project = await seed_project(stack)
    old = await _register(stack, project.id, (await seed_file(stack, "old")).file_id)
    del stack.files._files[(stack.ctx.workspace_id, "old")]
    await seed_file(stack, "new")
    stack.outbox.fail = True
    with pytest.raises(RuntimeError):
        await _register(stack, project.id, "new")
    still = await stack.documents.get(stack.ctx, old.document.id)
    assert still is not None and still.status is DocumentStatus.ACTIVE


# ------------------------------------------------------------------------ the replacement preview
async def test_the_preview_lists_replaced_files_their_documents_and_the_kept_namesakes(
    stack: Stack,
) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "older")
    await seed_file(stack, "target")
    stack.files.add(
        FakeDrawingFile(file_id="other", name="plant.pdf", tags={"system": "B"}),
        workspace_id=stack.ctx.workspace_id,
    )
    stack.documents.rows["d1"] = make_document(
        id="d1", project_id=project.id, file_id="older", content_sha256=SHA_A
    )
    stack.documents.rows["d2"] = make_document(
        id="d2", project_id=project.id, file_id="older", status=DocumentStatus.PURGING
    )
    stack.namesakes.set("target", replaced=["older"], kept=["other"])
    before = (stack.uow.begun, dict(stack.documents.rows))

    preview = await stack.services["replacement"].preview(stack.ctx, "target", checksum=SHA_A)

    assert preview.file_id == "target"
    (replaced,) = preview.replaced
    assert replaced.file_id == "older" and replaced.name == "older.pdf"
    (affected,) = replaced.documents  # the purging one is out of every read
    assert (affected.document_id, affected.project_id) == ("d1", project.id)
    assert affected.status is DocumentStatus.ACTIVE and affected.content_identical is True
    (kept,) = preview.kept
    assert kept.file_id == "other" and kept.name == "plant.pdf" and kept.tags == {"system": "B"}
    assert (stack.uow.begun, stack.documents.rows) == before  # a read: nothing written
    assert stack.outbox.records == []


async def test_content_identical_has_its_three_states(stack: Stack) -> None:
    project = await seed_project(stack)
    await seed_file(stack, "older")
    await seed_file(stack, "target")
    stack.documents.rows["d1"] = make_document(
        id="d1", project_id=project.id, file_id="older", content_sha256=SHA_A
    )
    stack.namesakes.set("target", replaced=["older"])
    preview = stack.services["replacement"].preview

    same = (await preview(stack.ctx, "target", checksum=SHA_A)).replaced[0].documents[0]
    other = (await preview(stack.ctx, "target", checksum="b" * 64)).replaced[0].documents[0]
    unknown = (await preview(stack.ctx, "target")).replaced[0].documents[0]
    assert (same.content_identical, other.content_identical, unknown.content_identical) == (
        True,
        False,
        None,
    )

    stack.documents.rows["d1"].content_sha256 = None  # the document was never inspected
    never = (await preview(stack.ctx, "target", checksum=SHA_A)).replaced[0].documents[0]
    assert never.content_identical is None


async def test_the_preview_of_an_unknown_file_is_a_404_and_a_bad_checksum_a_422(
    stack: Stack,
) -> None:
    with pytest.raises(NotFoundError):
        await stack.services["replacement"].preview(stack.ctx, "missing")
    with pytest.raises(ValidationError):
        await stack.services["replacement"].preview(stack.ctx, "target", checksum="zz")
