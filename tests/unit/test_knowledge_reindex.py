"""Unit tests for the manual re-index (BE-RAG-007/008): the ``ReindexJob``
aggregate's derived progress, and the three use-cases over the shared
in-memory stack (``support_knowledge``).

What these pin, against 06 §7 INV-K3/K4/K5:

* re-indexing **supersedes**, never resets — a new ``pending`` document over
  the same ``file_id``, and the old one gone;
* the old document's vector points are deleted, and deleted BEFORE its rows;
* every target is validated before the first point is deleted, so a request
  naming one bad id destroys nothing;
* only terminal documents may be re-indexed;
* the job stores no progress — moving a document forward moves the job's
  numbers, with nothing written to the job;
* cancelling claims the ``pending`` documents and leaves the ``indexing``
  one alone, and it does that through the ordinary ``failed`` lifecycle the
  worker's redelivery guard already declines to run against.

The vector store is a recorder rather than a mock with expectations: what
matters is *what was deleted and in what order relative to the purge*, and an
assertion on a call list says that where a strict mock only says "something
was called".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.modules.knowledge.application.use_cases import CANCELLED_REASON
from app.modules.knowledge.domain.entities import ReindexItem, ReindexJob
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    ReindexJobStatus,
    VectorRef,
)
from tests.unit.support_knowledge import KnowledgeStack, build_knowledge, seed_document

_W1 = "ws1"
_W2 = "ws2"
_AT = datetime(2026, 8, 8, 9, 0, 0, tzinfo=UTC)


def _ctx(workspace_id: str = _W1) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


def _item(document_id: str, status: IndexStatus, file_id: str = "file-1") -> ReindexItem:
    return ReindexItem(
        document_id=document_id,
        file_id=file_id,
        source_document_id=f"old-{document_id}",
        status=status,
    )


def _job(*items: ReindexItem, cancelled_at: datetime | None = None) -> ReindexJob:
    return ReindexJob(
        id="job-1",
        workspace_id=_W1,
        items=tuple(items),
        cancelled_at=cancelled_at,
        created_at=_AT,
    )


def _seed(stack: KnowledgeStack, document_id: str, **kwargs: object) -> None:
    document = seed_document(document_id=document_id, workspace_id=_W1, **kwargs)  # type: ignore[arg-type]
    stack.repository.rows[document_id] = document


# --------------------------------------------------------------------------- #
# ReindexJob — the derived state (INV-K5)                                     #
# --------------------------------------------------------------------------- #
def test_a_job_whose_documents_are_all_pending_is_running_at_zero_percent() -> None:
    job = _job(_item("d1", IndexStatus.PENDING), _item("d2", IndexStatus.PENDING))

    assert job.status is ReindexJobStatus.RUNNING
    assert (job.finished, job.percent) == (0, 0)


def test_progress_counts_both_terminal_statuses() -> None:
    """A failed document is finished. Counting only the successes would leave
    a job that hit one parse error stuck at 66% forever, with nothing left to
    move it."""
    job = _job(
        _item("d1", IndexStatus.INDEXED),
        _item("d2", IndexStatus.FAILED),
        _item("d3", IndexStatus.INDEXING),
    )

    assert (job.finished, job.percent) == (2, 66)
    assert job.status is ReindexJobStatus.RUNNING


def test_a_job_is_completed_when_every_document_is_terminal() -> None:
    job = _job(_item("d1", IndexStatus.INDEXED), _item("d2", IndexStatus.FAILED))

    assert job.status is ReindexJobStatus.COMPLETED
    assert job.percent == 100


def test_cancelled_outranks_completed() -> None:
    """Cancelling is what drove these documents terminal; reporting the job
    as ``completed`` would describe the mechanism and hide the intent."""
    job = _job(_item("d1", IndexStatus.FAILED), cancelled_at=_AT)

    assert job.status is ReindexJobStatus.CANCELLED


def test_current_names_the_document_being_indexed_and_is_none_between_them() -> None:
    running = _job(
        _item("d1", IndexStatus.INDEXED, file_id="f1"),
        _item("d2", IndexStatus.INDEXING, file_id="f2"),
    )
    between = _job(_item("d1", IndexStatus.INDEXED), _item("d2", IndexStatus.PENDING))

    assert running.current is not None
    assert running.current.file_id == "f2"
    assert between.current is None


def test_cancel_claims_only_the_pending_documents() -> None:
    job = _job(
        _item("d1", IndexStatus.INDEXED),
        _item("d2", IndexStatus.INDEXING),
        _item("d3", IndexStatus.PENDING),
    )

    claimed = job.cancel(_AT)

    assert claimed == ("d3",)
    # The in-flight one keeps its status: its pipeline is running in another
    # process and will write its own outcome.
    assert [item.status for item in job.items] == [
        IndexStatus.INDEXED,
        IndexStatus.INDEXING,
        IndexStatus.FAILED,
    ]
    assert job.cancelled_at == _AT


def test_cancelling_twice_claims_nothing_and_keeps_the_original_instant() -> None:
    """The moment someone stopped a job is not re-datable."""
    job = _job(_item("d1", IndexStatus.PENDING))
    job.cancel(_AT)
    later = datetime(2026, 8, 8, 10, 0, 0, tzinfo=UTC)

    assert job.cancel(later) == ()
    assert job.cancelled_at == _AT


# --------------------------------------------------------------------------- #
# ReindexDocuments — supersede + destroy (INV-K3/K4)                          #
# --------------------------------------------------------------------------- #
async def test_reindexing_registers_a_new_pending_document_and_destroys_the_old() -> None:
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "doc-old", file_id="file-7", status=IndexStatus.INDEXED)

    job = await stack.knowledge.reindex.start(ctx, document_ids=["doc-old"])

    assert stack.repository.purged == ["doc-old"]
    assert "doc-old" not in stack.repository.rows
    (item,) = job.items
    assert item.source_document_id == "doc-old"
    assert item.document_id != "doc-old"
    fresh = stack.repository.rows[item.document_id]
    # Same file, new document, back at the start of the lifecycle (INV-K3).
    assert (fresh.file_id, fresh.status, fresh.chunk_count) == ("file-7", IndexStatus.PENDING, 0)


async def test_the_old_documents_points_are_deleted_before_its_rows() -> None:
    """The order is the contract's, not an implementation detail: if the
    vector delete fails, nothing has changed. The reverse would leave points
    behind for a document that no longer exists — and those points ARE
    reachable, because retrieval filters the payload and never joins
    Postgres."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "doc-old")
    stack.repository.refs["doc-old"] = [
        VectorRef("ws1-knowledge", "point-a"),
        VectorRef("ws1-knowledge", "point-b"),
    ]

    await stack.knowledge.reindex.start(ctx, document_ids=["doc-old"])

    assert stack.vectors.deleted == [("ws1-knowledge", ["point-a", "point-b"])]
    # `prepare` empties the vector store; `commit` deletes the rows. The
    # recorder proves the first happened, and the purge list that the second
    # did — the fake would look identical either way if the order were wrong,
    # which is why the live suite re-checks this against real Postgres.
    assert stack.repository.purged == ["doc-old"]


async def test_a_document_with_no_chunks_is_reindexed_without_touching_the_vector_store() -> None:
    """A ``failed`` document that never got past parsing has nothing indexed.
    Calling delete with an empty id list would be a round trip that buys
    nothing."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "doc-failed", status=IndexStatus.FAILED, chunk_count=0)

    await stack.knowledge.reindex.start(ctx, document_ids=["doc-failed"])

    assert stack.vectors.deleted == []


async def test_each_new_document_gets_a_registered_event() -> None:
    """The event is the whole mechanism: no worker is told about a document
    any other way, and a `pending` row without one would never be indexed."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1", file_id="f1")
    _seed(stack, "d2", file_id="f2")

    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1", "d2"])

    assert stack.outbox.event_types == [
        "knowledge.document.registered.v1",
        "knowledge.document.registered.v1",
    ]
    subjects = {record.aggregate_id for call in stack.outbox.calls for record in call}
    assert subjects == {item.document_id for item in job.items}


async def test_naming_a_document_twice_rebuilds_it_once() -> None:
    """A slip, not a request to rebuild the rebuild — and rebuilding twice
    would mean the second pass destroying what the first had just made."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "doc-old")

    job = await stack.knowledge.reindex.start(ctx, document_ids=["doc-old", "doc-old"])

    assert len(job.items) == 1
    assert stack.repository.purged == ["doc-old"]


async def test_an_unknown_id_destroys_nothing_at_all() -> None:
    """Validation of EVERY target precedes the first delete. A request that
    names one bad id must not half-rebuild the corpus and then report 404."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "doc-good")
    stack.repository.refs["doc-good"] = [VectorRef("c", "p1")]

    with pytest.raises(NotFoundError):
        await stack.knowledge.reindex.start(ctx, document_ids=["doc-good", "doc-missing"])

    assert stack.vectors.deleted == []
    assert stack.repository.purged == []
    assert "doc-good" in stack.repository.rows


async def test_another_tenants_document_is_not_found_rather_than_forbidden() -> None:
    stack, ctx = build_knowledge(), _ctx(_W2)
    _seed(stack, "doc-1")

    with pytest.raises(NotFoundError):
        await stack.knowledge.reindex.start(ctx, document_ids=["doc-1"])

    assert stack.repository.rows["doc-1"].status is IndexStatus.INDEXED


@pytest.mark.parametrize("status", [IndexStatus.PENDING, IndexStatus.INDEXING])
async def test_a_document_that_is_not_terminal_cannot_be_reindexed(status: IndexStatus) -> None:
    """409, and for two reasons at once: the rebuild being asked for is
    already under way, and destroying a row a worker is mid-pipeline on would
    break its chunk write against ``fk_chunk_doc``."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "doc-busy", status=status)

    with pytest.raises(ConflictError):
        await stack.knowledge.reindex.start(ctx, document_ids=["doc-busy"])

    assert stack.repository.purged == []


async def test_an_empty_request_is_rejected() -> None:
    stack, ctx = build_knowledge(), _ctx()

    with pytest.raises(ValidationError):
        await stack.knowledge.reindex.start(ctx, document_ids=[])


async def test_more_than_fifty_documents_is_rejected_before_anything_is_read() -> None:
    stack, ctx = build_knowledge(), _ctx()

    with pytest.raises(ValidationError):
        await stack.knowledge.reindex.start(ctx, document_ids=[f"d{n}" for n in range(51)])

    assert stack.repository.purged == []


# --------------------------------------------------------------------------- #
# GetReindexJob — progress that is read, not counted                          #
# --------------------------------------------------------------------------- #
async def test_progress_follows_the_documents_with_nothing_written_to_the_job() -> None:
    """The point of INV-K5: a worker moves a document, and the job's numbers
    move with it. Nothing wrote to the job row in between."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1")
    _seed(stack, "d2")
    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1", "d2"])
    stored = stack.jobs.rows[job.id]

    first, second = (item.document_id for item in job.items)
    stack.repository.rows[first] = seed_document(
        document_id=first, workspace_id=_W1, status=IndexStatus.INDEXED
    )
    stack.repository.rows[second] = seed_document(
        document_id=second, workspace_id=_W1, status=IndexStatus.INDEXING
    )

    live = await stack.knowledge.get_job.execute(ctx, job_id=job.id)

    assert (live.finished, live.percent) == (1, 50)
    assert live.status is ReindexJobStatus.RUNNING
    assert live.current is not None
    # And the stored aggregate never moved: the numbers came from the corpus.
    assert stack.jobs.rows[job.id] is stored
    assert stored.cancelled_at is None


async def test_an_unknown_job_is_not_found() -> None:
    stack, ctx = build_knowledge(), _ctx()

    with pytest.raises(NotFoundError):
        await stack.knowledge.get_job.execute(ctx, job_id="job-nope")


async def test_another_tenants_job_is_not_found() -> None:
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1")
    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1"])

    with pytest.raises(NotFoundError):
        await stack.knowledge.get_job.execute(_ctx(_W2), job_id=job.id)


# --------------------------------------------------------------------------- #
# CancelReindexJob                                                            #
# --------------------------------------------------------------------------- #
async def test_cancelling_drives_the_untouched_documents_terminal_with_a_reason() -> None:
    """The claimed documents land in exactly the state the worker's DD-09
    guard refuses to run against, so the already-published event arrives and
    does nothing — no worker change, and no need to un-publish a message."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1")
    _seed(stack, "d2")
    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1", "d2"])

    cancelled = await stack.knowledge.cancel_job.cancel(ctx, job_id=job.id)

    assert cancelled.status is ReindexJobStatus.CANCELLED
    for item in job.items:
        document = stack.repository.rows[item.document_id]
        assert document.status is IndexStatus.FAILED
        assert document.error == CANCELLED_REASON


async def test_cancelling_emits_a_failure_event_per_claimed_document() -> None:
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1")
    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1"])
    stack.outbox.calls.clear()

    await stack.knowledge.cancel_job.cancel(ctx, job_id=job.id)

    assert stack.outbox.event_types == ["knowledge.document.indexing_failed.v1"]
    (record,) = stack.outbox.calls[0]
    assert record.payload["data"] == {
        "document_id": job.items[0].document_id,
        "reason": CANCELLED_REASON,
    }


async def test_cancelling_leaves_an_in_flight_document_alone() -> None:
    """It cannot be stopped: the pipeline is running in another process. The
    response says so rather than claiming a stop that did not happen."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1")
    _seed(stack, "d2")
    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1", "d2"])
    running = job.items[0].document_id
    stack.repository.rows[running] = seed_document(
        document_id=running, workspace_id=_W1, status=IndexStatus.INDEXING
    )

    cancelled = await stack.knowledge.cancel_job.cancel(ctx, job_id=job.id)

    still_running = next(item for item in cancelled.items if item.document_id == running)
    assert still_running.status is IndexStatus.INDEXING
    assert stack.repository.rows[running].error is None


async def test_cancelling_twice_writes_nothing_and_returns_the_job() -> None:
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1")
    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1"])
    await stack.knowledge.cancel_job.cancel(ctx, job_id=job.id)
    first_cancel = stack.jobs.rows[job.id].cancelled_at
    stack.outbox.calls.clear()

    again = await stack.knowledge.cancel_job.cancel(ctx, job_id=job.id)

    assert again.status is ReindexJobStatus.CANCELLED
    assert stack.jobs.rows[job.id].cancelled_at == first_cancel
    # The service still opens its unit of work and appends an EMPTY batch —
    # the `SoftDeleteFileService` shape. What must not happen is a second
    # event, and a second `failed` document behind it.
    assert stack.outbox.event_types == []


async def test_cancelling_a_finished_job_is_a_conflict() -> None:
    """The work it asks to stop has already happened; answering 200 would
    suggest it was prevented."""
    stack, ctx = build_knowledge(), _ctx()
    _seed(stack, "d1")
    job = await stack.knowledge.reindex.start(ctx, document_ids=["d1"])
    done = job.items[0].document_id
    stack.repository.rows[done] = seed_document(
        document_id=done, workspace_id=_W1, status=IndexStatus.INDEXED
    )

    with pytest.raises(ConflictError):
        await stack.knowledge.cancel_job.cancel(ctx, job_id=job.id)

    assert stack.jobs.rows[job.id].cancelled_at is None


async def test_cancelling_an_unknown_job_is_not_found() -> None:
    stack, ctx = build_knowledge(), _ctx()

    with pytest.raises(NotFoundError):
        await stack.knowledge.cancel_job.cancel(ctx, job_id="job-nope")
