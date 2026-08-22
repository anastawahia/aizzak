"""Live-Postgres tests for ``SqlDocumentRepository`` + RLS
(09-testing-strategy §3).

Runs against the real local Compose PostgreSQL 16 (see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. The two binding behaviours under test beyond the standard
round-trip/RLS pattern: ``add_chunks``'s idempotent ``ON CONFLICT
(document_id, seq) DO NOTHING`` (INV-K1/DD-09 -- an at-least-once worker
redelivery neither duplicates nor overwrites first-written rows) and
``set_status('indexed')``'s same-statement refresh of the denormalized
``documents.chunk_count`` from the chunks actually persisted. ``Chunk.id``
(application-minted UUIDv7) and ``point_id`` (deterministic uuid5) are
asserted to persist as SEPARATE columns (the 3.k3→3.k4 handoff contract).

The final sections cover ``add_parent_chunks``/``parent_chunk_texts`` and
``Chunk.parent_id`` (P-14, rag-indexing-plan.md §3.2, step 6,
``migrations/versions/knowledge/0005_parent_chunks.py``): the same
idempotent-upsert/RLS/forged-write behaviours proven above for ``chunks``,
mirrored onto ``knowledge.parent_chunks``, plus the one thing only a live
database can show -- that ``purge`` needs no new statement because
``fk_parent_chunk_doc ... ON DELETE CASCADE`` removes a document's parent
rows for free once its ``chunks`` rows (which reference them) are already
gone; then ``chunk_texts``'s P-42 parent substitution (plan §4 step 18,
§3.10), the LEFT JOIN + consecutive-collapse only a real query can prove.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.modules.knowledge.adapters.sql_repository import (
    SqlDocumentRepository,
    SqlReindexJobRepository,
    SqlSummaryRepository,
)
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.entities import (
    Chunk,
    Document,
    ParentChunk,
    ReindexItem,
    ReindexJob,
    Summary,
)
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    ReindexJobStatus,
    SummaryKind,
    SummaryLanguage,
    VectorRef,
)
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )


def _document(
    *,
    workspace_id: str,
    space_id: str | None = None,
    doc_id: str | None = None,
    status: IndexStatus = IndexStatus.PENDING,
    chunk_count: int = 0,
    error: str | None = None,
) -> Document:
    now = utc_now()
    return Document(
        id=doc_id or new_uuid7(),
        workspace_id=workspace_id,
        space_id=space_id,
        file_id=new_uuid7(),
        status=status,
        chunk_count=chunk_count,
        error=error,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _chunk(
    *,
    document_id: str,
    workspace_id: str,
    seq: int,
    chunk_text: str,
    parent_id: str | None = None,
) -> Chunk:
    """Mirrors the application layer's minting contract: ``id`` is a fresh
    UUIDv7 minted per chunk, ``point_id`` the deterministic
    ``uuid5(document_id, seq)`` -- two DIFFERENT identifiers by design."""
    return Chunk(
        id=new_uuid7(),
        document_id=document_id,
        workspace_id=workspace_id,
        seq=seq,
        text=chunk_text,
        token_count=len(chunk_text.split()),
        vector_ref=VectorRef(
            collection=knowledge_collection(workspace_id),
            point_id=chunk_point_id(document_id, seq),
        ),
        parent_id=parent_id,
    )


def _parent_chunk(
    *,
    document_id: str,
    workspace_id: str,
    seq: int,
    parent_text: str,
    is_complete: bool = True,
) -> ParentChunk:
    """Mirrors the same application-minting contract as ``_chunk``: ``id``
    is a fresh UUIDv7, minted before any ``Chunk`` referencing it exists.

    ``is_complete`` defaults to the whole-segment parent plan §3.2
    describes; pass ``False`` for P-13's header-only parent (a table past
    ``TABLE_PARENT_MAX_ROWS``)."""
    return ParentChunk(
        id=new_uuid7(),
        document_id=document_id,
        workspace_id=workspace_id,
        seq=seq,
        text=parent_text,
        created_at=utc_now(),
        is_complete=is_complete,
    )


async def _document_row_as_owner(owner_dsn: str, workspace_id: str, doc_id: str) -> RowMapping:
    """The raw ``documents`` row, read outside the repository — the only way
    to tell "the adapter wrote the column" from "the adapter remembered what
    I handed it"."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT id, space_id, status FROM knowledge.documents WHERE id = :doc"),
                {"doc": doc_id},
            )
            return result.mappings().one()
    finally:
        await engine.dispose()


async def _chunk_rows_as_owner(
    owner_dsn: str, workspace_id: str, document_id: str
) -> list[RowMapping]:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text(
                    "SELECT id, document_id, seq, text, token_count, collection, point_id,"
                    " parent_id"
                    " FROM knowledge.chunks WHERE document_id = :doc ORDER BY seq"
                ),
                {"doc": document_id},
            )
            return list(result.mappings().all())
    finally:
        await engine.dispose()


async def _parent_chunk_rows_as_owner(
    owner_dsn: str, workspace_id: str, document_id: str
) -> list[RowMapping]:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text(
                    "SELECT id, document_id, seq, text, is_complete"
                    " FROM knowledge.parent_chunks WHERE document_id = :doc ORDER BY seq"
                ),
                {"doc": document_id},
            )
            return list(result.mappings().all())
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1)-(2) document round-trip                                                 #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips_the_document(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)

    await repo_knowledge.add(ctx, doc)
    loaded = await repo_knowledge.get(ctx, doc.id)

    assert loaded is not None
    assert loaded.id == doc.id
    assert loaded.workspace_id == ws
    assert loaded.file_id == doc.file_id
    assert loaded.status is IndexStatus.PENDING
    assert loaded.chunk_count == 0
    assert loaded.error is None
    assert loaded.version == 1


async def test_get_missing_document_returns_none(repo_knowledge: SqlDocumentRepository) -> None:
    assert await repo_knowledge.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (3) set_status transitions + failure error                                  #
# --------------------------------------------------------------------------- #
async def test_set_status_persists_transition_and_failure_error(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)

    await repo_knowledge.set_status(ctx, doc.id, IndexStatus.INDEXING.value)
    mid = await repo_knowledge.get(ctx, doc.id)
    assert mid is not None and mid.status is IndexStatus.INDEXING and mid.error is None

    await repo_knowledge.set_status(ctx, doc.id, IndexStatus.FAILED.value, error="parser blew up")
    failed = await repo_knowledge.get(ctx, doc.id)
    assert failed is not None
    assert failed.status is IndexStatus.FAILED
    assert failed.error == "parser blew up"


# --------------------------------------------------------------------------- #
# (4)-(6) add_chunks: round-trip, idempotency, chunk_count refresh            #
# --------------------------------------------------------------------------- #
async def test_add_chunks_persists_id_and_point_id_as_separate_columns(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    chunks = [
        _chunk(document_id=doc.id, workspace_id=ws, seq=i, chunk_text=f"chunk number {i}")
        for i in range(1, 4)
    ]

    await repo_knowledge.add_chunks(ctx, chunks)

    rows = await _chunk_rows_as_owner(live_db.owner, ws, doc.id)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert [r["text"] for r in rows] == ["chunk number 1", "chunk number 2", "chunk number 3"]
    for row, chunk in zip(rows, chunks, strict=True):
        assert str(row["id"]) == chunk.id
        assert chunk.vector_ref is not None
        assert str(row["point_id"]) == chunk.vector_ref.point_id
        assert str(row["id"]) != str(row["point_id"])  # the 3.k4 handoff contract
        assert row["collection"] == knowledge_collection(ws)


async def test_add_chunks_redelivery_is_idempotent_and_preserves_first_write(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    """INV-K1/DD-09: the redelivered batch (same document_id+seq, fresh ids,
    different text) inserts nothing, raises nothing, overwrites nothing."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_knowledge.add_chunks(
        ctx, [_chunk(document_id=doc.id, workspace_id=ws, seq=1, chunk_text="original text")]
    )

    await repo_knowledge.add_chunks(
        ctx, [_chunk(document_id=doc.id, workspace_id=ws, seq=1, chunk_text="redelivered text")]
    )

    rows = await _chunk_rows_as_owner(live_db.owner, ws, doc.id)
    assert len(rows) == 1
    assert rows[0]["text"] == "original text"


async def test_set_status_indexed_refreshes_chunk_count_from_persisted_rows(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(document_id=doc.id, workspace_id=ws, seq=i, chunk_text=f"chunk {i}")
            for i in range(1, 6)
        ],
    )

    await repo_knowledge.set_status(ctx, doc.id, IndexStatus.INDEXED.value)

    indexed = await repo_knowledge.get(ctx, doc.id)
    assert indexed is not None
    assert indexed.status is IndexStatus.INDEXED
    assert indexed.chunk_count == 5  # refreshed from real rows, not trusted input


# --------------------------------------------------------------------------- #
# (7)-(9) RLS: no context / empty-string GUC / tenant isolation               #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_sees_zero_rows(
    repo_knowledge: SqlDocumentRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_knowledge.add_chunks(
        ctx, [_chunk(document_id=doc.id, workspace_id=ws, seq=1, chunk_text="secret")]
    )

    async with sessionmaker_app() as session:  # no GUC ever set
        for table in ("knowledge.documents", "knowledge.chunks"):
            result = await session.execute(text(f"SELECT count(*) AS n FROM {table}"))
            assert result.scalar_one() == 0


async def test_empty_string_guc_sees_zero_rows_without_error(
    repo_knowledge: SqlDocumentRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ctx = _ctx(new_uuid7())
    await repo_knowledge.add(ctx, _document(workspace_id=ctx.workspace_id))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        result = await session.execute(text("SELECT count(*) AS n FROM knowledge.documents"))
        assert result.scalar_one() == 0


async def test_two_tenant_isolation_on_documents_and_chunks(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a, ctx_b = _ctx(ws_a), _ctx(ws_b)
    doc_a = _document(workspace_id=ws_a)
    doc_b = _document(workspace_id=ws_b)
    await repo_knowledge.add(ctx_a, doc_a)
    await repo_knowledge.add(ctx_b, doc_b)
    await repo_knowledge.add_chunks(
        ctx_a, [_chunk(document_id=doc_a.id, workspace_id=ws_a, seq=1, chunk_text="a-only")]
    )

    assert await repo_knowledge.get(ctx_b, doc_a.id) is None
    assert await repo_knowledge.get(ctx_a, doc_b.id) is None
    assert await _chunk_rows_as_owner(live_db.owner, ws_b, doc_a.id) == []


# --------------------------------------------------------------------------- #
# (10)-(11) forged cross-tenant writes -> RLS WITH CHECK rejects              #
# --------------------------------------------------------------------------- #
async def test_forged_cross_tenant_document_add_is_rejected(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    ws_victim, ws_attacker = new_uuid7(), new_uuid7()
    forged = _document(workspace_id=ws_victim)

    with pytest.raises(AppError) as excinfo:
        await repo_knowledge.add(_ctx(ws_attacker), forged)

    assert not isinstance(excinfo.value, ConflictError)
    assert excinfo.value.code == "common.internal"
    assert await repo_knowledge.get(_ctx(ws_victim), forged.id) is None


async def test_forged_cross_tenant_chunk_add_is_rejected(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws_victim, ws_attacker = new_uuid7(), new_uuid7()
    ctx_victim = _ctx(ws_victim)
    doc = _document(workspace_id=ws_victim)
    await repo_knowledge.add(ctx_victim, doc)
    forged_chunk = _chunk(
        document_id=doc.id, workspace_id=ws_victim, seq=1, chunk_text="forged payload"
    )

    with pytest.raises(AppError) as excinfo:
        await repo_knowledge.add_chunks(_ctx(ws_attacker), [forged_chunk])

    assert excinfo.value.code == "common.internal"
    assert await _chunk_rows_as_owner(live_db.owner, ws_victim, doc.id) == []


# --------------------------------------------------------------------------- #
# (7) cursor pagination: newest first, tenant-scoped, every status included  #
# --------------------------------------------------------------------------- #
async def test_list_pages_newest_first_and_stays_tenant_scoped(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """The corpus listing became paginated in 6.3-ب — the one collection with
    no structural ceiling on its size.

    Proves the two halves agree against real SQL: ``ORDER BY id DESC`` and the
    ``id < cursor`` predicate. Pointing them apart yields a page that is
    silently empty or never ends, and only a live query can show that.
    """
    ws, other_ws = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    created = []
    for status in (
        IndexStatus.PENDING,
        IndexStatus.INDEXING,
        IndexStatus.INDEXED,
        IndexStatus.FAILED,
        IndexStatus.PENDING,
    ):
        doc = _document(workspace_id=ws, status=status)
        await repo_knowledge.add(ctx, doc)
        created.append(doc)
    await repo_knowledge.add(_ctx(other_ws), _document(workspace_id=other_ws))

    # UUIDv7 ids are monotonic, so insertion order IS id order — reversed.
    expected = [doc.id for doc in reversed(created)]

    page1 = await repo_knowledge.list(ctx, space_id=None, limit=2, cursor=None)
    assert [doc.id for doc in page1.data] == expected[:2]
    assert page1.next_cursor is not None

    page2 = await repo_knowledge.list(ctx, space_id=None, limit=2, cursor=page1.next_cursor)
    assert [doc.id for doc in page2.data] == expected[2:4]

    page3 = await repo_knowledge.list(ctx, space_id=None, limit=2, cursor=page2.next_cursor)
    assert [doc.id for doc in page3.data] == expected[4:]
    assert page3.next_cursor is None  # last page, and the other tenant never appears


async def test_list_rejects_a_cursor_that_is_not_a_keyset_id(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """Well-formed base64 carrying non-UUID text is refused BEFORE it reaches
    the ``uuid`` column — where it used to surface as a 500 (6.3-أ)."""
    with pytest.raises(AppError) as excinfo:
        await repo_knowledge.list(_ctx(new_uuid7()), space_id=None, limit=10, cursor="aGVsbG8")
    assert excinfo.value.code == "common.invalid_cursor"


# --------------------------------------------------------------------------- #
# (6-b) the space column: written, read back, filtered on, and never updated  #
# --------------------------------------------------------------------------- #
async def test_the_space_a_document_was_added_under_round_trips(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    """``add`` names ``space_id`` in its INSERT and ``_hydrate_document``
    reads it back (spaces plan step 8). Asserted against the RAW row too:
    "the repository gives me back what I handed it" is also exactly what an
    adapter that never wrote the column would look like from the aggregate
    side."""
    ws, space = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws, space_id=space)

    await repo_knowledge.add(ctx, doc)

    loaded = await repo_knowledge.get(ctx, doc.id)
    assert loaded is not None
    assert loaded.space_id == space
    row = await _document_row_as_owner(live_db.owner, ws, doc.id)
    assert str(row["space_id"]) == space


async def test_a_document_registered_without_a_space_stores_and_reads_back_null(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """The transitional state the column is NULLable for (plan row 8-b): a
    file with no space produces a document with none, and that has to be
    storable rather than an INSERT that fails inside a worker."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws, space_id=None)

    await repo_knowledge.add(ctx, doc)

    loaded = await repo_knowledge.get(ctx, doc.id)
    assert loaded is not None
    assert loaded.space_id is None


async def test_listing_by_space_returns_that_spaces_documents_and_nothing_else(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """The narrowing is an ADDED condition, never a replacement: the
    workspace filter and the space filter both have to survive, and each row
    below fails the page for a different one of them."""
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    mine, sibling = new_uuid7(), new_uuid7()

    keep = _document(workspace_id=ws_a, space_id=mine)
    await repo_knowledge.add(ctx_a, keep)
    await repo_knowledge.add(ctx_a, _document(workspace_id=ws_a, space_id=sibling))
    await repo_knowledge.add(ctx_a, _document(workspace_id=ws_a, space_id=None))
    # The SAME space id under another tenant: a space id is not a secret, and
    # RLS plus the WHERE both have to hold for this row to stay invisible.
    await repo_knowledge.add(_ctx(ws_b), _document(workspace_id=ws_b, space_id=mine))

    page = await repo_knowledge.list(ctx_a, space_id=mine, limit=10, cursor=None)

    assert [doc.id for doc in page.data] == [keep.id]


async def test_listing_without_a_space_still_spans_every_space(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """``None`` means EVERY space, never "the documents that have no space".
    Reading it as ``space_id IS NULL`` would turn ``GET /documents`` into a
    listing of orphans, and no test whose rows are all of one kind would
    notice — hence a mixed corpus here."""
    ws, space = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    spaced = _document(workspace_id=ws, space_id=space)
    await repo_knowledge.add(ctx, spaced)
    spaceless = _document(workspace_id=ws, space_id=None)
    await repo_knowledge.add(ctx, spaceless)

    page = await repo_knowledge.list(ctx, space_id=None, limit=10, cursor=None)

    assert {doc.id for doc in page.data} == {spaced.id, spaceless.id}


async def test_a_status_transition_never_moves_a_document_between_spaces(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    """Decision 3, applied to the derived row: ``set_status`` writes status,
    error and chunk_count and NOTHING else. The whole indexing lifecycle runs
    through it, so a column it touched would drift on every worker pass."""
    ws, space = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws, space_id=space)
    await repo_knowledge.add(ctx, doc)

    await repo_knowledge.set_status(ctx, doc.id, IndexStatus.INDEXING.value)
    await repo_knowledge.set_status(ctx, doc.id, IndexStatus.INDEXED.value)

    row = await _document_row_as_owner(live_db.owner, ws, doc.id)
    assert row["status"] == IndexStatus.INDEXED.value
    assert str(row["space_id"]) == space


# --------------------------------------------------------------------------- #
# (7) purge + re-index jobs (BE-RAG-007/008)                                  #
# --------------------------------------------------------------------------- #
async def test_vector_refs_returns_what_was_actually_written(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """The points a purge must delete are read from the chunk rows, never
    recomputed: the ids are deterministic today, but what has to go is what
    was WRITTEN, and only the rows know that."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws, status=IndexStatus.INDEXING)
    await repo_knowledge.add(ctx, doc)
    chunks = [
        _chunk(document_id=doc.id, workspace_id=ws, seq=seq, chunk_text=f"body {seq}")
        for seq in range(3)
    ]
    await repo_knowledge.add_chunks(ctx, chunks)

    refs = await repo_knowledge.vector_refs(ctx, doc.id)

    assert {ref.point_id for ref in refs} == {chunk_point_id(doc.id, seq) for seq in range(3)}
    assert {ref.collection for ref in refs} == {knowledge_collection(ws)}


async def test_purge_destroys_the_document_and_its_chunks(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    """``fk_chunk_doc`` makes the order mandatory rather than stylistic, and
    only a live database can prove the statement pair honours it — an
    in-memory fake deletes a dict entry either way."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws, status=IndexStatus.INDEXING)
    await repo_knowledge.add(ctx, doc)
    await repo_knowledge.add_chunks(
        ctx, [_chunk(document_id=doc.id, workspace_id=ws, seq=0, chunk_text="body")]
    )

    await repo_knowledge.purge(ctx, doc.id)

    assert await repo_knowledge.get(ctx, doc.id) is None
    assert await _chunk_rows_as_owner(live_db.owner, ws, doc.id) == []


async def test_purging_an_absent_document_is_a_no_op(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """The caller has already read it, and losing a race to another purge is
    the outcome it wanted anyway."""
    await repo_knowledge.purge(_ctx(new_uuid7()), new_uuid7())


async def test_purge_cannot_reach_another_tenants_document(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """The one hard delete in this module, so the tenant filter matters more
    here than anywhere: a leak would not be a wrong read, it would be data
    gone."""
    owner_ws, intruder_ws = new_uuid7(), new_uuid7()
    owner_ctx = _ctx(owner_ws)
    doc = _document(workspace_id=owner_ws)
    await repo_knowledge.add(owner_ctx, doc)

    await repo_knowledge.purge(_ctx(intruder_ws), doc.id)

    assert await repo_knowledge.get(owner_ctx, doc.id) is not None


async def test_a_job_round_trips_with_its_items_status_joined_from_the_documents(
    repo_knowledge: SqlDocumentRepository, repo_reindex_jobs: SqlReindexJobRepository
) -> None:
    """INV-K5 end to end: nothing about progress is stored, so moving the
    DOCUMENT is what moves the job. This is the assertion no unit test can
    make — an in-memory fake could report progress from anywhere."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    replacement = _document(workspace_id=ws, status=IndexStatus.PENDING)
    await repo_knowledge.add(ctx, replacement)
    job = ReindexJob(
        id=new_uuid7(),
        workspace_id=ws,
        items=(
            ReindexItem(
                document_id=replacement.id,
                file_id=replacement.file_id,
                source_document_id=new_uuid7(),
                status=IndexStatus.PENDING,
            ),
        ),
        cancelled_at=None,
        created_at=utc_now(),
    )

    await repo_reindex_jobs.add(ctx, job)
    loaded = await repo_reindex_jobs.get(ctx, job.id)
    assert loaded is not None
    assert loaded.status is ReindexJobStatus.RUNNING
    assert loaded.percent == 0

    await repo_knowledge.set_status(ctx, replacement.id, IndexStatus.INDEXED.value)
    finished = await repo_reindex_jobs.get(ctx, job.id)

    assert finished is not None
    assert finished.status is ReindexJobStatus.COMPLETED
    assert finished.percent == 100
    assert finished.items[0].source_document_id == job.items[0].source_document_id


async def test_marking_a_job_cancelled_persists_and_outranks_completion(
    repo_knowledge: SqlDocumentRepository, repo_reindex_jobs: SqlReindexJobRepository
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    document = _document(workspace_id=ws, status=IndexStatus.FAILED)
    await repo_knowledge.add(ctx, document)
    job = ReindexJob(
        id=new_uuid7(),
        workspace_id=ws,
        items=(
            ReindexItem(
                document_id=document.id,
                file_id=document.file_id,
                source_document_id=new_uuid7(),
                status=IndexStatus.FAILED,
            ),
        ),
        cancelled_at=None,
        created_at=utc_now(),
    )
    await repo_reindex_jobs.add(ctx, job)
    at = utc_now()

    await repo_reindex_jobs.mark_cancelled(ctx, job.id, at)
    loaded = await repo_reindex_jobs.get(ctx, job.id)

    assert loaded is not None
    assert loaded.cancelled_at == at
    # Every item is terminal, yet the job reads `cancelled`: what happened to
    # it outranks the mechanism that finished it.
    assert loaded.status is ReindexJobStatus.CANCELLED


async def test_another_tenant_cannot_read_a_job(
    repo_knowledge: SqlDocumentRepository, repo_reindex_jobs: SqlReindexJobRepository
) -> None:
    ws, intruder_ws = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    document = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, document)
    job = ReindexJob(
        id=new_uuid7(),
        workspace_id=ws,
        items=(
            ReindexItem(
                document_id=document.id,
                file_id=document.file_id,
                source_document_id=new_uuid7(),
                status=IndexStatus.PENDING,
            ),
        ),
        cancelled_at=None,
        created_at=utc_now(),
    )
    await repo_reindex_jobs.add(ctx, job)

    assert await repo_reindex_jobs.get(_ctx(intruder_ws), job.id) is None


# --------------------------------------------------------------------------- #
# (12) parent chunks (P-14, plan §3.2, step 6)                                #
# --------------------------------------------------------------------------- #
async def test_add_parent_chunks_round_trips_text_in_seq_order(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    parents = [
        _parent_chunk(document_id=doc.id, workspace_id=ws, seq=i, parent_text=f"segment {i}")
        for i in range(2)
    ]

    await repo_knowledge.add_parent_chunks(ctx, parents)

    texts = await repo_knowledge.parent_chunk_texts(ctx, doc.id)
    assert list(texts) == ["segment 0", "segment 1"]
    rows = await _parent_chunk_rows_as_owner(live_db.owner, ws, doc.id)
    assert [str(row["id"]) for row in rows] == [p.id for p in parents]


async def test_add_parent_chunks_redelivery_is_idempotent_and_preserves_first_write(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    """INV-K1/DD-09, mirrored onto ``parent_chunks``: a redelivered batch
    (same document_id+seq, fresh id, different text) inserts nothing, raises
    nothing, overwrites nothing."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_knowledge.add_parent_chunks(
        ctx, [_parent_chunk(document_id=doc.id, workspace_id=ws, seq=0, parent_text="original")]
    )

    await repo_knowledge.add_parent_chunks(
        ctx, [_parent_chunk(document_id=doc.id, workspace_id=ws, seq=0, parent_text="redelivered")]
    )

    rows = await _parent_chunk_rows_as_owner(live_db.owner, ws, doc.id)
    assert len(rows) == 1
    assert rows[0]["text"] == "original"


async def test_a_document_with_no_parent_chunks_yields_an_empty_sequence(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)

    assert await repo_knowledge.parent_chunk_texts(ctx, doc.id) == []


async def test_a_chunks_parent_id_round_trips_as_a_separate_column(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    parent = _parent_chunk(document_id=doc.id, workspace_id=ws, seq=0, parent_text="whole segment")
    await repo_knowledge.add_parent_chunks(ctx, [parent])

    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=0,
                chunk_text="a window",
                parent_id=parent.id,
            )
        ],
    )

    rows = await _chunk_rows_as_owner(live_db.owner, ws, doc.id)
    assert str(rows[0]["parent_id"]) == parent.id


async def test_a_chunk_with_no_parent_stores_and_reads_back_null(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)

    await repo_knowledge.add_chunks(
        ctx, [_chunk(document_id=doc.id, workspace_id=ws, seq=0, chunk_text="no parent yet")]
    )

    rows = await _chunk_rows_as_owner(live_db.owner, ws, doc.id)
    assert rows[0]["parent_id"] is None


async def test_two_tenant_isolation_on_parent_chunks(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a, ctx_b = _ctx(ws_a), _ctx(ws_b)
    doc_a = _document(workspace_id=ws_a)
    await repo_knowledge.add(ctx_a, doc_a)
    await repo_knowledge.add_parent_chunks(
        ctx_a,
        [_parent_chunk(document_id=doc_a.id, workspace_id=ws_a, seq=0, parent_text="a-only")],
    )

    assert await repo_knowledge.parent_chunk_texts(ctx_b, doc_a.id) == []
    assert await _parent_chunk_rows_as_owner(live_db.owner, ws_b, doc_a.id) == []


async def test_forged_cross_tenant_parent_chunk_add_is_rejected(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    ws_victim, ws_attacker = new_uuid7(), new_uuid7()
    ctx_victim = _ctx(ws_victim)
    doc = _document(workspace_id=ws_victim)
    await repo_knowledge.add(ctx_victim, doc)
    forged = _parent_chunk(
        document_id=doc.id, workspace_id=ws_victim, seq=0, parent_text="forged payload"
    )

    with pytest.raises(AppError) as excinfo:
        await repo_knowledge.add_parent_chunks(_ctx(ws_attacker), [forged])

    assert excinfo.value.code == "common.internal"
    assert await _parent_chunk_rows_as_owner(live_db.owner, ws_victim, doc.id) == []


async def test_purging_a_document_cascades_its_parent_chunks_away(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    """The constraint the migration's docstring is about: ``purge`` deletes
    ``chunks`` explicitly and never touches ``parent_chunks`` at all -- the
    final ``DELETE`` of the document row cascades (``fk_parent_chunk_doc ...
    ON DELETE CASCADE``) and removes the now-unreferenced parent rows for
    free. Only a live database can show the cascade actually fires."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws, status=IndexStatus.INDEXING)
    await repo_knowledge.add(ctx, doc)
    parent = _parent_chunk(document_id=doc.id, workspace_id=ws, seq=0, parent_text="segment")
    await repo_knowledge.add_parent_chunks(ctx, [parent])
    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(
                document_id=doc.id, workspace_id=ws, seq=0, chunk_text="window", parent_id=parent.id
            )
        ],
    )

    await repo_knowledge.purge(ctx, doc.id)

    assert await repo_knowledge.get(ctx, doc.id) is None
    assert await _parent_chunk_rows_as_owner(live_db.owner, ws, doc.id) == []


# --------------------------------------------------------------------------- #
# chunk_texts's P-42 parent substitution (plan §4 step 18, §3.10)             #
# --------------------------------------------------------------------------- #
async def test_chunk_texts_with_no_parent_chunks_is_unchanged_from_before_step_18(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """The common case today (no table in the document, P-13 is the only
    writer of ``parent_chunks``): every chunk's ``parent_id`` is ``NULL``, so
    the LEFT JOIN matches nothing and ``chunk_texts`` reads back exactly the
    leaf text that was written -- byte for byte, in ``seq`` order, same as
    before this step."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(document_id=doc.id, workspace_id=ws, seq=i, chunk_text=f"leaf {i}")
            for i in range(3)
        ],
    )

    assert await repo_knowledge.chunk_texts(ctx, doc.id) == ["leaf 0", "leaf 1", "leaf 2"]


async def test_chunk_texts_collapses_a_tables_rows_into_one_appearance_of_its_parent(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """A table's exploded rows (P-13) are written contiguously and share ONE
    ``parent_id`` -- ``chunk_texts`` must read that parent's text back only
    ONCE, not once per row, which is the whole "~40 sections instead of
    ~240 fragments" win (plan §3.10)."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    parent = _parent_chunk(
        document_id=doc.id, workspace_id=ws, seq=0, parent_text="Name: Ahmad; Salary: 5000"
    )
    await repo_knowledge.add_parent_chunks(ctx, [parent])
    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=i,
                chunk_text=f"row {i}",
                parent_id=parent.id,
            )
            for i in range(3)
        ],
    )

    texts = await repo_knowledge.chunk_texts(ctx, doc.id)

    assert texts == ["Name: Ahmad; Salary: 5000"]
    assert "row 0" not in texts and "row 1" not in texts and "row 2" not in texts


async def test_chunk_texts_falls_back_to_leaf_text_for_chunks_with_no_parent_and_never_dedups_them(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """P-42's fallback half: a document with a table AND ordinary prose
    around it must keep every prose chunk -- individually, with no dedup
    applied between them even when two happen to hold identical text --
    while the table's rows still collapse to their one parent. Losing the
    prose here would be exactly the content-loss step 18 must not cause."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    parent = _parent_chunk(document_id=doc.id, workspace_id=ws, seq=0, parent_text="table body")
    await repo_knowledge.add_parent_chunks(ctx, [parent])
    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(document_id=doc.id, workspace_id=ws, seq=0, chunk_text="intro"),
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=1,
                chunk_text="row a",
                parent_id=parent.id,
            ),
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=2,
                chunk_text="row b",
                parent_id=parent.id,
            ),
            # Deliberately identical text to another orphan leaf -- "no
            # dedup" for the fallback path means BOTH survive.
            _chunk(document_id=doc.id, workspace_id=ws, seq=3, chunk_text="intro"),
        ],
    )

    texts = await repo_knowledge.chunk_texts(ctx, doc.id)

    assert texts == ["intro", "table body", "intro"]


async def test_chunk_texts_keeps_every_row_when_the_parent_holds_only_the_header(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """P-13's OTHER parent shape: a table past ``TABLE_PARENT_MAX_ROWS`` gets
    a parent that is the header line alone. Collapsing that run -- or even
    substituting its text for a single row -- would hand the summariser
    ``"Name; Salary"`` and lose every value in the table, which is exactly
    the content loss P-42 forbids ("falls back to the leaf text with no
    dedup"). The rows must come back whole, in ``seq`` order, and the header
    must not appear at all: it is context to widen a RETRIEVED chunk with,
    never a substitute for the chunk."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    parent = _parent_chunk(
        document_id=doc.id,
        workspace_id=ws,
        seq=0,
        parent_text="Name; Salary",
        is_complete=False,
    )
    await repo_knowledge.add_parent_chunks(ctx, [parent])
    rows = ["Name: Ahmad; Salary: 5000", "Name: Sara; Salary: 6000", "Name: Omar; Salary: 7000"]
    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=i,
                chunk_text=row_text,
                parent_id=parent.id,
            )
            for i, row_text in enumerate(rows)
        ],
    )

    texts = await repo_knowledge.chunk_texts(ctx, doc.id)

    assert texts == rows
    assert "Name; Salary" not in texts


async def test_chunk_texts_mixes_a_header_parent_and_a_complete_parent_in_one_document(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """One document, both rungs of the §3.3 ladder plus prose: the small
    table collapses to its complete parent, the large one keeps every row,
    and the prose between them survives untouched."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    header_parent = _parent_chunk(
        document_id=doc.id, workspace_id=ws, seq=0, parent_text="Name; Dept", is_complete=False
    )
    full_parent = _parent_chunk(
        document_id=doc.id, workspace_id=ws, seq=1, parent_text="whole small table"
    )
    await repo_knowledge.add_parent_chunks(ctx, [header_parent, full_parent])
    await repo_knowledge.add_chunks(
        ctx,
        [
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=0,
                chunk_text="Name: Ahmad; Dept: Ops",
                parent_id=header_parent.id,
            ),
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=1,
                chunk_text="Name: Sara; Dept: Eng",
                parent_id=header_parent.id,
            ),
            _chunk(document_id=doc.id, workspace_id=ws, seq=2, chunk_text="prose between"),
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=3,
                chunk_text="small row a",
                parent_id=full_parent.id,
            ),
            _chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=4,
                chunk_text="small row b",
                parent_id=full_parent.id,
            ),
        ],
    )

    texts = await repo_knowledge.chunk_texts(ctx, doc.id)

    assert texts == [
        "Name: Ahmad; Dept: Ops",
        "Name: Sara; Dept: Eng",
        "prose between",
        "whole small table",
    ]


async def test_add_parent_chunks_writes_is_complete_as_given(
    repo_knowledge: SqlDocumentRepository, live_db: LiveDbDsns
) -> None:
    """The bit the summariser depends on is a COLUMN, read back outside the
    repository -- "the adapter wrote it" told apart from "the adapter
    remembered what I handed it" (``_document_row_as_owner``'s reason)."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_knowledge.add_parent_chunks(
        ctx,
        [
            _parent_chunk(document_id=doc.id, workspace_id=ws, seq=0, parent_text="whole table"),
            _parent_chunk(
                document_id=doc.id,
                workspace_id=ws,
                seq=1,
                parent_text="Name; Salary",
                is_complete=False,
            ),
        ],
    )

    rows = await _parent_chunk_rows_as_owner(live_db.owner, ws, doc.id)

    assert [row["is_complete"] for row in rows] == [True, False]


# --------------------------------------------------------------------------- #
# (10) newest_in_other_language -- P-44's translation source (step 20)        #
# --------------------------------------------------------------------------- #
def _summary(
    *,
    document_id: str,
    workspace_id: str,
    lang: SummaryLanguage,
    summary_text: str,
    built_at: datetime,
    kind: SummaryKind = SummaryKind.FULL,
) -> Summary:
    return Summary(
        id=new_uuid7(),
        workspace_id=workspace_id,
        document_id=document_id,
        kind=kind,
        lang=lang,
        text=summary_text,
        model="stub",
        source_chunks=3,
        truncated=False,
        built_at=built_at,
    )


async def test_newest_in_other_language_returns_the_latest_built_of_the_others(
    repo_knowledge: SqlDocumentRepository, repo_summaries: SqlSummaryRepository
) -> None:
    """The ORDER BY is the port's contract, and only a real query proves it:
    an in-memory fake can hand back rows in insertion order and look right.

    The OLDER row is inserted FIRST on purpose. A query that dropped its
    ``ORDER BY`` would return whatever the scan reached first — the older
    one — so this fails rather than passing by accident."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    base = utc_now()
    await repo_summaries.upsert(
        ctx,
        _summary(
            document_id=doc.id,
            workspace_id=ws,
            lang=SummaryLanguage.AUTO,
            summary_text="older auto",
            built_at=base - timedelta(days=3),
        ),
    )
    await repo_summaries.upsert(
        ctx,
        _summary(
            document_id=doc.id,
            workspace_id=ws,
            lang=SummaryLanguage.EN,
            summary_text="newer english",
            built_at=base,
        ),
    )

    found = await repo_summaries.newest_in_other_language(
        ctx, doc.id, SummaryKind.FULL, SummaryLanguage.AR
    )

    assert found is not None
    assert (found.lang, found.text) == (SummaryLanguage.EN, "newer english")


async def test_newest_in_other_language_never_returns_the_requested_language(
    repo_knowledge: SqlDocumentRepository, repo_summaries: SqlSummaryRepository
) -> None:
    """Handing back the row under the requested key would turn every rebuild
    into a translation of itself — a POST that can never replace what it was
    asked to replace."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_summaries.upsert(
        ctx,
        _summary(
            document_id=doc.id,
            workspace_id=ws,
            lang=SummaryLanguage.AR,
            summary_text="arabic",
            built_at=utc_now(),
        ),
    )

    assert (
        await repo_summaries.newest_in_other_language(
            ctx, doc.id, SummaryKind.FULL, SummaryLanguage.AR
        )
        is None
    )


async def test_newest_in_other_language_does_not_cross_kinds_or_tenants(
    repo_knowledge: SqlDocumentRepository, repo_summaries: SqlSummaryRepository
) -> None:
    """``kind`` is part of the key for the reason ``lang`` is: translating an
    ``overview`` into a ``full`` would answer a question nobody asked. And
    another tenant's summary is not a source — RLS, proven rather than
    assumed."""
    ws = new_uuid7()
    intruder = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    await repo_summaries.upsert(
        ctx,
        _summary(
            document_id=doc.id,
            workspace_id=ws,
            lang=SummaryLanguage.EN,
            summary_text="english overview",
            built_at=utc_now(),
            kind=SummaryKind.OVERVIEW,
        ),
    )

    assert (
        await repo_summaries.newest_in_other_language(
            ctx, doc.id, SummaryKind.FULL, SummaryLanguage.AR
        )
        is None
    )
    assert (
        await repo_summaries.newest_in_other_language(
            _ctx(intruder), doc.id, SummaryKind.OVERVIEW, SummaryLanguage.AR
        )
        is None
    )


async def test_parent_texts_for_chunk_ids_reports_is_complete_for_both_rungs(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """⚠️ Regression, found live: the parent-widening lookup must carry
    ``is_complete``, and carry it CORRECTLY per row.

    ``_widen_to_parents`` (retrieval plan §3.7, ``P-34``) substitutes a
    parent's text for the leaf that was retrieved. Doing that to an
    INCOMPLETE parent — §3.3's header-only row for a table past
    ``TABLE_PARENT_MAX_ROWS`` — replaces the matched passage with a string
    that does not contain it, the same content loss ``chunk_texts`` already
    refuses for ``P-42`` above. The caller can only refuse it if this query
    reports the bit, which it did not until this test existed.

    Both rungs of the §3.3 ladder are seeded in ONE document so the mapping
    is proven to report them per-row rather than one blanket value — a query
    that hard-coded either constant would pass a single-rung test."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doc = _document(workspace_id=ws)
    await repo_knowledge.add(ctx, doc)
    header_parent = _parent_chunk(
        document_id=doc.id, workspace_id=ws, seq=0, parent_text="Name; Dept", is_complete=False
    )
    full_parent = _parent_chunk(
        document_id=doc.id, workspace_id=ws, seq=1, parent_text="the whole small table"
    )
    await repo_knowledge.add_parent_chunks(ctx, [header_parent, full_parent])
    under_header = _chunk(
        document_id=doc.id,
        workspace_id=ws,
        seq=0,
        chunk_text="Name: Ahmad; Dept: Electrical",
        parent_id=header_parent.id,
    )
    under_full = _chunk(
        document_id=doc.id,
        workspace_id=ws,
        seq=1,
        chunk_text="Name: Sara; Dept: Civil",
        parent_id=full_parent.id,
    )
    orphan = _chunk(document_id=doc.id, workspace_id=ws, seq=2, chunk_text="ordinary prose")
    await repo_knowledge.add_chunks(ctx, [under_header, under_full, orphan])

    resolved = await repo_knowledge.parent_texts_for_chunk_ids(
        ctx,
        [
            under_header.vector_ref.point_id,
            under_full.vector_ref.point_id,
            orphan.vector_ref.point_id,
        ],
    )

    header_hit = resolved[under_header.vector_ref.point_id]
    assert header_hit.is_complete is False
    assert header_hit.text == "Name; Dept"
    full_hit = resolved[under_full.vector_ref.point_id]
    assert full_hit.is_complete is True
    assert full_hit.text == "the whole small table"
    # A parent-less chunk stays ABSENT — never a placeholder entry.
    assert orphan.vector_ref.point_id not in resolved
