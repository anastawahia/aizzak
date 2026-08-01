"""Live-Postgres tests for ``SqlDocumentRepository`` + RLS
(09-testing-strategy §3).

Runs against a real, local PostgreSQL 16 (no Docker/testcontainers -- see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. The two binding behaviours under test beyond the standard
round-trip/RLS pattern: ``add_chunks``'s idempotent ``ON CONFLICT
(document_id, seq) DO NOTHING`` (INV-K1/DD-09 -- an at-least-once worker
redelivery neither duplicates nor overwrites first-written rows) and
``set_status('indexed')``'s same-statement refresh of the denormalized
``documents.chunk_count`` from the chunks actually persisted. ``Chunk.id``
(application-minted UUIDv7) and ``point_id`` (deterministic uuid5) are
asserted to persist as SEPARATE columns (the 3.k3→3.k4 handoff contract).
"""

from __future__ import annotations

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
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.entities import Chunk, Document
from app.modules.knowledge.domain.value_objects import IndexStatus, VectorRef
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
    doc_id: str | None = None,
    status: IndexStatus = IndexStatus.PENDING,
    chunk_count: int = 0,
    error: str | None = None,
) -> Document:
    now = utc_now()
    return Document(
        id=doc_id or new_uuid7(),
        workspace_id=workspace_id,
        file_id=new_uuid7(),
        status=status,
        chunk_count=chunk_count,
        error=error,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _chunk(*, document_id: str, workspace_id: str, seq: int, chunk_text: str) -> Chunk:
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
    )


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
                    "SELECT id, document_id, seq, text, token_count, collection, point_id"
                    " FROM knowledge.chunks WHERE document_id = :doc ORDER BY seq"
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

    page1 = await repo_knowledge.list(ctx, limit=2, cursor=None)
    assert [doc.id for doc in page1.data] == expected[:2]
    assert page1.next_cursor is not None

    page2 = await repo_knowledge.list(ctx, limit=2, cursor=page1.next_cursor)
    assert [doc.id for doc in page2.data] == expected[2:4]

    page3 = await repo_knowledge.list(ctx, limit=2, cursor=page2.next_cursor)
    assert [doc.id for doc in page3.data] == expected[4:]
    assert page3.next_cursor is None  # last page, and the other tenant never appears


async def test_list_rejects_a_cursor_that_is_not_a_keyset_id(
    repo_knowledge: SqlDocumentRepository,
) -> None:
    """Well-formed base64 carrying non-UUID text is refused BEFORE it reaches
    the ``uuid`` column — where it used to surface as a 500 (6.3-أ)."""
    with pytest.raises(AppError) as excinfo:
        await repo_knowledge.list(_ctx(new_uuid7()), limit=10, cursor="aGVsbG8")
    assert excinfo.value.code == "common.invalid_cursor"
