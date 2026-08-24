"""Unit tests for the file cascade (``framework/di/file_deletion.py``) — the
coordination service and the knowledge purge it drives.

The defect these tests pin down: ``files.FileDeleted`` reached no receiver in
``knowledge``. A user deleted a file, the row got its ``deleted_at`` and
vanished from every listing, and the ``Document`` stayed ``indexed`` with its
``chunks`` rows joinable and its Qdrant points searchable — so retrieval went on
answering out of a file that was gone, and delete-then-re-upload left two
corpora over one file's content.

Pure: every store is in-memory, so nothing here touches Postgres or Qdrant. What
a live file would prove is that the SQL reaches the right tables in an order the
foreign keys accept; what THIS file proves is that the cascade asks for both
steps in the order the module docstring fixes, and that the purge empties what
the file owned and nothing else.

Ordering carries most of the correctness, so the assertions are traces rather
than end-states: a cascade that deleted the chunk ROWS before the POINTS leaves
exactly the same empty corpus behind, and leaves the points answering searches
forever.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.file_deletion import DeleteFileService, FileDeletion
from app.framework.errors import NotFoundError
from app.framework.identifiers import new_uuid7
from app.modules.files.application.use_cases import SoftDeleteFile, SoftDeleteFileService
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey
from app.modules.knowledge.application.use_cases import PurgeFileKnowledge
from app.modules.knowledge.domain.value_objects import VectorRef
from tests.unit.support_files_media import (
    InMemoryFileRepository,
    NoopUnitOfWork,
    RecordingOutbox,
)
from tests.unit.support_knowledge import build_knowledge, seed_document


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


def _seed_file(
    files: InMemoryFileRepository,
    ctx: ExecutionContext,
    *,
    deleted_at: datetime | None = None,
) -> File:
    now = utc_now()
    file_id = new_uuid7()
    file = File(
        id=file_id,
        workspace_id=ctx.workspace_id,
        space_id="sp-1",
        name=FileName("seed.txt"),
        content_type=ContentType("text/plain"),
        size_bytes=10,
        storage_key=StorageKey.for_file(ctx.workspace_id, file_id),
        checksum=None,
        status=FileStatus.READY,
        uploaded_by=ctx.user_id,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
        version=1,
    )
    files.rows[file.id] = file
    return file


class _CountingPurge:
    """A ``FileContentPurge`` that says WHEN it ran and with which id."""

    def __init__(self, trace: list[str], count: int) -> None:
        self._trace = trace
        self._count = count
        self.calls: list[str] = []

    async def execute(self, ctx: ExecutionContext, file_id: str) -> int:
        self._trace.append("knowledge")
        self.calls.append(file_id)
        return self._count


def _build_service(
    *, documents: int = 2
) -> tuple[DeleteFileService, InMemoryFileRepository, _CountingPurge, list[str]]:
    trace: list[str] = []
    files = InMemoryFileRepository()

    class _TracingMarker:
        def __init__(self, inner: SoftDeleteFileService) -> None:
            self._inner = inner

        async def delete(self, ctx: ExecutionContext, file_id: str) -> object:
            trace.append("mark")
            return await self._inner.delete(ctx, file_id)

    purge = _CountingPurge(trace, documents)
    # The REAL atomic service, not the bare use-case: `FileMarker` binds to the
    # face that pairs the soft delete with its outbox append, and a cascade
    # wired to the use-case underneath would drop the events on the floor the
    # day 04 §5 promotes `FileDeleted` to the wire.
    service = DeleteFileService(
        _TracingMarker(
            SoftDeleteFileService(SoftDeleteFile(files), RecordingOutbox(), NoopUnitOfWork())
        ),
        knowledge=purge,
    )
    return service, files, purge, trace


# --------------------------------------------------------------------------- #
# (1) the two steps, in the module docstring's order                          #
# --------------------------------------------------------------------------- #
async def test_the_cascade_marks_the_file_first_then_empties_its_corpus() -> None:
    """The order is the design. Purging first would risk the failure that
    cannot be undone — a live, still-listed file whose index was destroyed
    under it — while marking first risks only the state being repaired here,
    transiently, with the retry closing it."""
    service, files, purge, trace = _build_service()
    ctx = _ctx()
    file = _seed_file(files, ctx)

    result = await service.delete(ctx, file.id)

    assert trace == ["mark", "knowledge"]
    assert files.rows[file.id].deleted_at is not None
    assert purge.calls == [file.id]
    assert result == FileDeletion(file_id=file.id, documents=2)


async def test_a_file_this_workspace_does_not_own_is_a_404_and_nothing_is_purged() -> None:
    """The marking step is the cascade's only existence check: the purge cannot
    tell a file with no documents from a file that never existed, and would
    report a successful deletion of nothing."""
    service, _files, purge, trace = _build_service()

    with pytest.raises(NotFoundError):
        await service.delete(_ctx(), new_uuid7())

    assert trace == ["mark"]
    assert purge.calls == []


async def test_deleting_an_already_deleted_file_runs_the_whole_cascade_again() -> None:
    """The resume path: an interrupted cascade leaves a deleted file with its
    corpus still standing — which is the exact defect — and the only way back
    is to run it again. A service that short-circuited on ``deleted_at`` would
    strand precisely the points the first run failed to reach."""
    service, files, purge, trace = _build_service()
    ctx = _ctx()
    file = _seed_file(files, ctx)
    await service.delete(ctx, file.id)
    trace.clear()

    await service.delete(ctx, file.id)

    assert trace == ["mark", "knowledge"]
    assert purge.calls == [file.id, file.id]


async def test_a_never_indexed_file_still_deletes_cleanly() -> None:
    """The common case on this path: most files are never indexed at all, and
    a purge that found nothing is a 204 rather than a 404."""
    service, files, _purge, _trace = _build_service(documents=0)
    ctx = _ctx()
    file = _seed_file(files, ctx)

    result = await service.delete(ctx, file.id)

    assert result == FileDeletion(file_id=file.id, documents=0)
    assert files.rows[file.id].deleted_at is not None


# --------------------------------------------------------------------------- #
# (2) knowledge — the purge itself                                            #
# --------------------------------------------------------------------------- #
async def test_the_files_points_are_deleted_before_its_rows() -> None:
    """A point outlives its row invisibly: retrieval filters on the payload and
    never joins Postgres, so a chunk row deleted first leaves content answering
    searches with nothing left to identify it. Deleting the points first makes
    the failure recoverable instead."""
    stack = build_knowledge()
    ctx = _ctx()
    file_id = new_uuid7()
    document = seed_document(
        document_id=new_uuid7(), workspace_id=ctx.workspace_id, file_id=file_id
    )
    stack.repository.rows[document.id] = document
    stack.repository.refs[document.id] = [VectorRef("kn-w1", "point-1"), VectorRef("kn-w1", "p2")]

    purged = await PurgeFileKnowledge(stack.repository, stack.vectors).execute(ctx, file_id)

    assert purged == 1
    assert stack.vectors.deleted == [("kn-w1", ["point-1", "p2"])]
    # The rows went too — and they went AFTER, which is why the refs above
    # were still readable when the vector call was made.
    assert stack.repository.rows == {}


async def test_every_document_of_the_file_goes_not_only_the_newest() -> None:
    """The "file indexed twice" shape, and the reason the purge is keyed by
    FILE rather than by document: a re-index leaves the replacement under the
    same ``file_id``, and a delete that reached one of them would leave the
    other answering searches under a file the user just removed."""
    stack = build_knowledge()
    ctx = _ctx()
    file_id = new_uuid7()
    superseded = seed_document(
        document_id=new_uuid7(), workspace_id=ctx.workspace_id, file_id=file_id
    )
    replacement = seed_document(
        document_id=new_uuid7(), workspace_id=ctx.workspace_id, file_id=file_id
    )
    stack.repository.rows = {superseded.id: superseded, replacement.id: replacement}
    stack.repository.refs = {
        superseded.id: [VectorRef("kn-w1", "old")],
        replacement.id: [VectorRef("kn-w1", "new")],
    }

    purged = await PurgeFileKnowledge(stack.repository, stack.vectors).execute(ctx, file_id)

    assert purged == 2
    assert stack.vectors.deleted == [("kn-w1", ["old", "new"])]
    assert stack.repository.rows == {}


async def test_only_this_files_corpus_is_destroyed() -> None:
    stack = build_knowledge()
    ctx = _ctx()
    doomed, kept = new_uuid7(), new_uuid7()
    mine = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, file_id=doomed)
    theirs = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, file_id=kept)
    stack.repository.rows = {mine.id: mine, theirs.id: theirs}
    stack.repository.refs = {
        mine.id: [VectorRef("kn-w1", "p1")],
        theirs.id: [VectorRef("kn-w1", "p2")],
    }

    purged = await PurgeFileKnowledge(stack.repository, stack.vectors).execute(ctx, doomed)

    assert purged == 1
    assert stack.vectors.deleted == [("kn-w1", ["p1"])]
    assert list(stack.repository.rows) == [theirs.id]


async def test_another_tenants_file_id_destroys_nothing() -> None:
    """The purge is tenant-scoped through the repository's own predicate, not
    through the cascade: a workspace that guessed another's file id would
    otherwise empty a corpus it cannot even read."""
    stack = build_knowledge()
    file_id = new_uuid7()
    theirs = seed_document(document_id=new_uuid7(), workspace_id="w2", file_id=file_id)
    stack.repository.rows = {theirs.id: theirs}
    stack.repository.refs = {theirs.id: [VectorRef("kn-w2", "p1")]}

    purged = await PurgeFileKnowledge(stack.repository, stack.vectors).execute(_ctx("w1"), file_id)

    assert purged == 0
    assert stack.vectors.deleted == []
    assert list(stack.repository.rows) == [theirs.id]


async def test_points_are_grouped_by_their_own_collection() -> None:
    """Every chunk of a workspace lives in one collection today, but each
    ``VectorRef`` names its own — and a purge that assumed otherwise would
    delete one collection's ids from another's, which Qdrant accepts
    silently."""
    stack = build_knowledge()
    ctx = _ctx()
    file_id = new_uuid7()
    first = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, file_id=file_id)
    second = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, file_id=file_id)
    stack.repository.rows = {first.id: first, second.id: second}
    stack.repository.refs = {
        first.id: [VectorRef("kn-old", "p1")],
        second.id: [VectorRef("kn-new", "p2")],
    }

    await PurgeFileKnowledge(stack.repository, stack.vectors).execute(ctx, file_id)

    assert sorted(stack.vectors.deleted) == [("kn-new", ["p2"]), ("kn-old", ["p1"])]


async def test_a_file_with_nothing_indexed_asks_the_vector_store_for_nothing() -> None:
    """A ``delete`` with an empty id list is a round trip that can fail, on a
    path whose common case is a file that was never indexed at all."""
    stack = build_knowledge()

    purged = await PurgeFileKnowledge(stack.repository, stack.vectors).execute(_ctx(), new_uuid7())

    assert purged == 0
    assert stack.vectors.deleted == []
