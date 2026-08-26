"""Unit tests for س-29 rule 1 — a duplicate NAME is a replacement
(``framework/di/file_replacement.py`` + ``FileRepository.live_namesakes``).

The owner's decision, 2026-08-25 (``docs/rag-fidelity-audit.md`` §4-هـ-2):
uploading a file under a name that already exists in its space replaces the
file that had it, index and all, and the new one is stored BEFORE the old one
is deleted — "رفعٌ أوّلًا ثمّ حذف" — because nothing existing may be destroyed
before its replacement is proven.

The defect it closes is measured, not hypothetical: the audit's log shows the
replacement already being performed BY HAND — ``criteria.pdf`` deleted at
22:10:45 and re-uploaded nine seconds later — and cause #7 ("the file is
indexed twice") is what happens on the attempts where the delete is forgotten.

Pure: every store is in-memory. What a live test proves is that the SQL and
the index agree on what "the same name" is (``test_file_namesakes_live.py``);
what THIS file proves is the RULE — which files are replaced, in which
direction, and that the sweep can never turn a completed upload into a
failure.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.file_replacement import ReplaceNamesakesService
from app.framework.errors import ConflictError, NotFoundError
from app.framework.identifiers import new_uuid7
from app.modules.files.application.use_cases import (
    CompleteUpload,
    CompleteUploadService,
    FindNamesakes,
    RenameFile,
)
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey
from tests.unit.support_files_media import (
    InMemoryFileRepository,
    NoopUnitOfWork,
    RecordingOutbox,
)

_SPACE = "sp-1"
_OTHER_SPACE = "sp-2"

# A name that genuinely differs between NFC and NFD — see the parametrised
# case below for why the choice of letter is load-bearing.
_ARABIC = "تأكيد الفصل.pdf"
_ARABIC_NFC = unicodedata.normalize("NFC", _ARABIC)
_ARABIC_NFD = unicodedata.normalize("NFD", _ARABIC)
assert _ARABIC_NFC != _ARABIC_NFD, "the fixture no longer exercises normalisation"


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


def _seed(
    files: InMemoryFileRepository,
    ctx: ExecutionContext,
    *,
    name: str,
    space_id: str | None = _SPACE,
    created_at: datetime | None = None,
    status: FileStatus = FileStatus.READY,
    deleted_at: datetime | None = None,
) -> File:
    now = created_at or utc_now()
    file_id = new_uuid7()
    file = File(
        id=file_id,
        workspace_id=ctx.workspace_id,
        space_id=space_id,
        name=FileName(name),
        content_type=ContentType("text/plain"),
        size_bytes=10,
        storage_key=StorageKey.for_file(ctx.workspace_id, file_id),
        checksum=None,
        status=status,
        uploaded_by=ctx.user_id,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
        version=1,
    )
    files.rows[file.id] = file
    return file


class _RecordingEraser:
    """A ``FileEraser`` that records what it was told to destroy AND actually
    marks the row, because the real one does.

    ``DeleteFileService`` soft-deletes before it purges, and a fake that only
    recorded would hide the half of the concurrency argument that matters:
    the loser of a race between two completions has to MEET a deleted row and
    be refused by it.
    """

    def __init__(self, files: InMemoryFileRepository, *, fails: set[str] | None = None) -> None:
        self._files = files
        self.deleted: list[str] = []
        self._fails = fails or set()

    async def delete(self, ctx: ExecutionContext, file_id: str) -> object:
        if file_id in self._fails:
            raise RuntimeError("qdrant is down")
        self._files.rows[file_id].soft_delete(utc_now())
        self.deleted.append(file_id)
        return None


def _build(
    files: InMemoryFileRepository, *, fails: set[str] | None = None
) -> tuple[ReplaceNamesakesService[File], _RecordingEraser]:
    erase = _RecordingEraser(files, fails=fails)
    uow = NoopUnitOfWork()
    return (
        ReplaceNamesakesService(
            CompleteUploadService(CompleteUpload(files), RecordingOutbox(), uow),
            RenameFile(files),
            namesakes=FindNamesakes(files),
            erase=erase,
        ),
        erase,
    )


# --------------------------------------------------------------------------- #
# The rule: which files a completion replaces                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_completing_an_upload_replaces_the_older_file_of_the_same_name() -> None:
    """س-29 rule 1, the whole of it: the older namesake is destroyed THROUGH
    the cascade, so its index goes with its row."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    older = _seed(files, ctx, name="criteria.pdf", created_at=utc_now() - timedelta(days=1))
    newer = _seed(files, ctx, name="criteria.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    completed = await service.complete(ctx, file_id=newer.id, checksum=None)

    assert completed.status is FileStatus.READY
    assert erase.deleted == [older.id]


@pytest.mark.asyncio
async def test_the_replacement_runs_after_the_upload_is_proven_and_not_before() -> None:
    """ "رفعٌ أوّلًا ثمّ حذف". Registering mints a row and presigns a PUT — a
    promise the bytes are coming — and a promise may not be traded for a file
    that exists. Until `complete` says they landed, nothing is destroyed."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    _seed(files, ctx, name="criteria.pdf", created_at=utc_now() - timedelta(days=1))
    _seed(files, ctx, name="criteria.pdf", status=FileStatus.UPLOADED)
    _, erase = _build(files)

    # No `complete` call at all — the state right after a registration.
    assert erase.deleted == []


@pytest.mark.asyncio
async def test_a_never_completed_upload_leaves_the_existing_file_alone() -> None:
    """The abandoned-upload case stated as its own guarantee: a client that
    registers `report.pdf` and then crashes must not have destroyed the
    `report.pdf` the space already held."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    older = _seed(files, ctx, name="report.pdf", created_at=utc_now() - timedelta(days=1))
    abandoned = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    # Some OTHER file completes; the abandoned registration never does.
    unrelated = _seed(files, ctx, name="notes.txt", status=FileStatus.UPLOADED)
    await service.complete(ctx, file_id=unrelated.id, checksum=None)

    assert erase.deleted == []
    assert files.rows[older.id].deleted_at is None
    assert files.rows[abandoned.id].status is FileStatus.UPLOADED


# --------------------------------------------------------------------------- #
# The direction: newer replaces older, never the reverse                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_older_file_never_replaces_a_newer_one() -> None:
    """The decision's reason is that the arriving file "قد يحتوي على بيانات
    محدثه" — it may hold UPDATED data. Read strictly, that makes the rule
    directional: fresh content supersedes stale, and stale never supersedes
    fresh. So renaming an OLD file onto a NEWER file's name destroys nothing.
    """
    ctx = _ctx()
    files = InMemoryFileRepository()
    old = _seed(files, ctx, name="draft.txt", created_at=utc_now() - timedelta(days=1))
    new = _seed(files, ctx, name="final.txt")
    service, erase = _build(files)

    await service.rename(ctx, old.id, name="final.txt")

    assert erase.deleted == []
    assert files.rows[new.id].deleted_at is None


@pytest.mark.asyncio
async def test_two_files_can_never_replace_each_other() -> None:
    """The concurrency argument, asserted rather than described. The
    completion path holds NO lock — the space row lock is taken on register
    and long since released — so two uploads of one name can complete at
    once. "Strictly older" is what makes that safe: it is a strict order, so
    of any two rows at most one is older, and mutual destruction is not a
    state the predicate can produce."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    first = _seed(
        files,
        ctx,
        name="ledger.xlsx",
        created_at=utc_now() - timedelta(seconds=1),
        status=FileStatus.UPLOADED,
    )
    second = _seed(files, ctx, name="ledger.xlsx", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    # Both complete, in the "wrong" order — the later arrival first.
    await service.complete(ctx, file_id=second.id, checksum=None)
    with pytest.raises(ConflictError):
        # `first` was marked deleted by `second`'s sweep, and `File.complete`
        # refuses a deleted row (`_guard_not_deleted`). The loser fails
        # honestly instead of resurrecting itself.
        await service.complete(ctx, file_id=first.id, checksum=None)

    assert erase.deleted == [first.id]
    assert files.rows[second.id].deleted_at is None


# --------------------------------------------------------------------------- #
# The scope: one space, and "the same name" up to case and normalisation       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_same_name_in_another_space_is_a_different_file() -> None:
    """The scope is the space, and it comes from the model rather than from a
    preference: spaces are isolated completely (س-32), so one space's
    `report.pdf` is not the other's and neither replaces it."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    elsewhere = _seed(
        files,
        ctx,
        name="report.pdf",
        space_id=_OTHER_SPACE,
        created_at=utc_now() - timedelta(days=1),
    )
    arriving = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    await service.complete(ctx, file_id=arriving.id, checksum=None)

    assert erase.deleted == []
    assert files.rows[elsewhere.id].deleted_at is None


@pytest.mark.asyncio
async def test_another_workspace_is_never_reached() -> None:
    """DD-04 restated where it would hurt most: a replacement that crossed
    tenants would delete another workspace's file and its corpus."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    foreign = _seed(files, _ctx("w2"), name="report.pdf", created_at=utc_now() - timedelta(days=1))
    arriving = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    await service.complete(ctx, file_id=arriving.id, checksum=None)

    assert erase.deleted == []
    assert files.rows[foreign.id].deleted_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "arriving"),
    [
        # `lower` — `ux_spaces_ws_name`'s rule: one name to the person who
        # uploaded them.
        ("Report.pdf", "report.pdf"),
        ("REPORT.PDF", "Report.pdf"),
        # `normalize(.., NFC)` — the half that makes the rule work for
        # ARABIC, which `lower` does nothing for. Same filename on screen,
        # different bytes: composed vs decomposed. The letter has to be one
        # that actually decomposes (U+0623 -> U+0627 + U+0654); plain Arabic
        # letters do not, and `تقرير.pdf` is byte-identical in both forms —
        # a case that asserts nothing. `_ARABIC_*` carries the assertion that
        # keeps that true.
        (_ARABIC_NFC, _ARABIC_NFD),
    ],
)
async def test_the_same_name_is_matched_up_to_case_and_unicode_normalisation(
    stored: str, arriving: str
) -> None:
    """Without either half, the two files are different files, both indexed,
    and cause #7 becomes reachable through a keyboard."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    older = _seed(files, ctx, name=stored, created_at=utc_now() - timedelta(days=1))
    newer = _seed(files, ctx, name=arriving, status=FileStatus.UPLOADED)
    service, erase = _build(files)

    await service.complete(ctx, file_id=newer.id, checksum=None)

    assert erase.deleted == [older.id]


@pytest.mark.asyncio
async def test_a_different_name_replaces_nothing() -> None:
    """The other side of the same rule — and the boundary between س-29's two
    halves. Content repeated under a DIFFERENT name is rule 2's problem, and
    rule 2 is a retrieval-side guard: nothing is deleted for it, ever."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    _seed(files, ctx, name="report.pdf", created_at=utc_now() - timedelta(days=1))
    arriving = _seed(files, ctx, name="report-final.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    await service.complete(ctx, file_id=arriving.id, checksum=None)

    assert erase.deleted == []


@pytest.mark.asyncio
async def test_an_already_deleted_namesake_is_not_deleted_again() -> None:
    """A soft-deleted file has already given up its name. Sweeping it again
    would make a second cascade's worth of Qdrant work out of nothing."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    _seed(
        files,
        ctx,
        name="report.pdf",
        created_at=utc_now() - timedelta(days=1),
        deleted_at=utc_now(),
    )
    arriving = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    await service.complete(ctx, file_id=arriving.id, checksum=None)

    assert erase.deleted == []


@pytest.mark.asyncio
async def test_an_older_namesake_still_uploading_is_replaced_too() -> None:
    """Status is deliberately NOT part of the predicate: an abandoned
    registration holds the name and the space's quota exactly as a ready file
    does, so leaving it would reserve a name forever."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    stalled = _seed(
        files,
        ctx,
        name="report.pdf",
        created_at=utc_now() - timedelta(days=1),
        status=FileStatus.UPLOADED,
    )
    arriving = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    await service.complete(ctx, file_id=arriving.id, checksum=None)

    assert erase.deleted == [stalled.id]


@pytest.mark.asyncio
async def test_every_older_namesake_goes_not_just_the_newest_of_them() -> None:
    """A space that already drifted into three copies of one name is cleaned
    up by the next upload, not merely trimmed to two."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    now = utc_now()
    first = _seed(files, ctx, name="report.pdf", created_at=now - timedelta(days=3))
    second = _seed(files, ctx, name="report.pdf", created_at=now - timedelta(days=2))
    arriving = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files)

    await service.complete(ctx, file_id=arriving.id, checksum=None)

    assert sorted(erase.deleted) == sorted([first.id, second.id])


# --------------------------------------------------------------------------- #
# The rename path — the audit named it, so it is owed the same rule            #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_renaming_onto_an_older_files_name_replaces_it() -> None:
    """`RenameFile` "can create the same collision after the fact", so the
    implementation owes BOTH paths — and they share one sweep so neither can
    implement a different rule."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    older = _seed(files, ctx, name="report.pdf", created_at=utc_now() - timedelta(days=1))
    newer = _seed(files, ctx, name="draft.pdf")
    service, erase = _build(files)

    renamed = await service.rename(ctx, newer.id, name="report.pdf")

    assert renamed.name.value == "report.pdf"
    assert erase.deleted == [older.id]


@pytest.mark.asyncio
async def test_a_no_op_rename_on_the_only_file_of_that_name_destroys_nothing() -> None:
    """Renaming a file to the name it already has is a successful no-op
    (`RenameFile`'s own rule), and with nothing to replace it stays one end to
    end — the sweep runs, finds no older namesake and deletes nothing."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    only = _seed(files, ctx, name="report.pdf")
    service, erase = _build(files)

    await service.rename(ctx, only.id, name="report.pdf")

    assert erase.deleted == []


@pytest.mark.asyncio
async def test_a_write_restores_the_rule_even_when_the_name_did_not_change() -> None:
    """The honest statement of what the sweep is: not "act on a change" but
    "restore the rule for this file". A space that already holds two
    `report.pdf` — uploaded before this feature existed, or left behind by a
    sweep that failed — is repaired by the next write on the newer one, and
    that includes a rename to the name it already has.

    Named rather than hidden because it IS surprising: a request that changed
    nothing deleted a file. The alternative is worse — a rule the product only
    applies to files that happened to arrive after it shipped."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    older = _seed(files, ctx, name="report.pdf", created_at=utc_now() - timedelta(days=1))
    newer = _seed(files, ctx, name="report.pdf")
    service, erase = _build(files)

    await service.rename(ctx, newer.id, name="report.pdf")

    assert erase.deleted == [older.id]


# --------------------------------------------------------------------------- #
# Failure: the sweep may not turn a successful write into a failed one         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_failed_purge_does_not_fail_the_completion() -> None:
    """The completion has already succeeded when the sweep starts — the row is
    `ready` and `FileUploaded` is in the outbox. Propagating a purge failure
    would hand the client a 5xx for an upload that worked, and their retry of
    `/complete` would then be refused with 409 (`ready` is not completable).
    They would be told twice that a successful upload failed."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    older = _seed(files, ctx, name="report.pdf", created_at=utc_now() - timedelta(days=1))
    newer = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, _ = _build(files, fails={older.id})

    completed = await service.complete(ctx, file_id=newer.id, checksum=None)

    assert completed.status is FileStatus.READY
    # The visible consequence is the mildest available: the older file is
    # still there, listed and deletable by hand — the state that existed
    # before this feature, with a log line naming it.
    assert files.rows[older.id].deleted_at is None


@pytest.mark.asyncio
async def test_one_unpurgeable_namesake_does_not_spare_the_others() -> None:
    """Per file, not per sweep."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    now = utc_now()
    doomed = _seed(files, ctx, name="report.pdf", created_at=now - timedelta(days=3))
    stubborn = _seed(files, ctx, name="report.pdf", created_at=now - timedelta(days=2))
    arriving = _seed(files, ctx, name="report.pdf", status=FileStatus.UPLOADED)
    service, erase = _build(files, fails={stubborn.id})

    await service.complete(ctx, file_id=arriving.id, checksum=None)

    assert erase.deleted == [doomed.id]


@pytest.mark.asyncio
async def test_a_failing_completion_sweeps_nothing() -> None:
    """The order is the guarantee. If the write that produces the new state
    raises, no sweep may have run — otherwise a rejected completion would have
    destroyed the file it was never allowed to replace."""
    ctx = _ctx()
    files = InMemoryFileRepository()
    older = _seed(files, ctx, name="report.pdf", created_at=utc_now() - timedelta(days=1))
    service, erase = _build(files)

    with pytest.raises(NotFoundError):
        await service.complete(ctx, file_id=new_uuid7(), checksum=None)

    assert erase.deleted == []
    assert files.rows[older.id].deleted_at is None
