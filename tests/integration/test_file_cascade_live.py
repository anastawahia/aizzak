"""Live-Postgres proof of the file cascade (``framework/di/file_deletion.py``;
09-testing-strategy §3).

The unit file next door proves the ORDER — mark, then knowledge, each store
emptied through a fake. What it cannot prove is that ``purge_file``'s six
statements reach the right tables in an order PostgreSQL will accept:
``fk_chunk_doc``, ``fk_summary_doc`` and ``fk_reindex_item_doc`` all reference
``knowledge.documents(id)`` with no ``ON DELETE``, so a cascade that deleted a
parent one statement too early raises ``23503`` against a database and nothing
at all against a dict.

That last constraint is why this file exists at all rather than leaning on the
space suite: a re-indexed file is the ordinary way a ``reindex_job_items`` row
comes to name a document, and re-indexing then deleting is an ordinary thing
for a user to do. Reaching a file's corpus through ``purge`` (one document) —
the smaller change — would have made that sequence a 500.

So this file wires the real adapters exactly as the Composition Root does, and
seeds through the modules' own use-cases wherever one exists — the rows the
cascade destroys are the rows the platform would have written.

**Qdrant is a fake here, deliberately** (``test_space_cascade_live.py``'s
reason): it has its own live suite, and what is untestable without a database
is the SQL. The fake still records, so "the points were asked for before the
rows went" stays assertable on the live path too.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.file_deletion import DeleteFileService
from app.framework.errors import NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import (
    CompleteUpload,
    RegisterUpload,
    SoftDeleteFile,
    SoftDeleteFileService,
)
from app.modules.knowledge.adapters.sql_repository import (
    SqlDocumentRepository,
    SqlReindexJobRepository,
    SqlSummaryJobRepository,
    SqlSummaryRepository,
)
from app.modules.knowledge.application.use_cases import PurgeFileKnowledge
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.entities import (
    Chunk,
    Document,
    ReindexItem,
    ReindexJob,
    Summary,
    SummaryJob,
)
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
    VectorRef,
)
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName

pytestmark = [pytest.mark.live_db]


class _RecordingVectors:
    """The one ``VectorStore`` method the cascade calls."""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, list[str]]] = []

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        self.deleted.append((collection, list(ids)))


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _space(workspace_id: str, name: str) -> Space:
    at = utc_now()
    return Space(
        id=new_uuid7(),
        workspace_id=workspace_id,
        name=SpaceName(name),
        created_by=new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=None,
        version=1,
    )


class _NoopUnitOfWork:
    """The atomic service's unit of work. The live outbox/transaction pairing
    is `test_producer_atomicity.py`'s subject; what this file owes is the
    cascade's SQL."""

    def begin(self, ctx: ExecutionContext) -> _NoopUnitOfWork:
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _NoopOutbox:
    """``FileDeleted`` is internal-only (04 §5), so the delete path's append is
    provably empty — see ``SoftDeleteFileService``."""

    async def append(self, ctx: ExecutionContext, records: Sequence[object]) -> None:
        assert list(records) == []


def _service(tenant_session: TenantSessionFactory, vectors: _RecordingVectors) -> DeleteFileService:
    """The production wiring of the Composition Root, minus Qdrant."""
    return DeleteFileService(
        SoftDeleteFileService(
            SoftDeleteFile(SqlFileRepository(tenant_session)),
            _NoopOutbox(),  # type: ignore[arg-type]
            _NoopUnitOfWork(),  # type: ignore[arg-type]
        ),
        knowledge=PurgeFileKnowledge(SqlDocumentRepository(tenant_session), vectors),
    )


async def _ready_file(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str
) -> str:
    """A ``ready`` file through the module's own path."""
    files = SqlFileRepository(tenant_session)
    register = RegisterUpload(
        files, Limits(), SpacesQueryService(SqlSpaceRepository(tenant_session))
    )
    file = await register.execute(
        ctx, space_id=space_id, name="paper.pdf", content_type="application/pdf", size_bytes=11
    )
    await CompleteUpload(files).execute(ctx, file_id=file.id, checksum=None)
    return file.id


async def _indexed_document(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str, file_id: str
) -> str:
    """A document with a chunk, a summary and a summary job — the three
    children ``purge_file`` must delete before the document itself."""
    documents = SqlDocumentRepository(tenant_session)
    now = utc_now()
    document = Document(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        space_id=space_id,
        file_id=file_id,
        status=IndexStatus.INDEXED,
        chunk_count=1,
        error=None,
        created_at=now,
        updated_at=now,
        version=1,
    )
    await documents.add(ctx, document)
    await documents.add_chunks(
        ctx,
        [
            Chunk(
                id=new_uuid7(),
                document_id=document.id,
                workspace_id=ctx.workspace_id,
                seq=1,
                text="hello",
                token_count=1,
                vector_ref=VectorRef(
                    collection=knowledge_collection(ctx.workspace_id),
                    point_id=chunk_point_id(document.id, 1),
                ),
            )
        ],
    )
    await SqlSummaryRepository(tenant_session).upsert(
        ctx,
        Summary(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            document_id=document.id,
            kind=SummaryKind.OVERVIEW,
            lang=SummaryLanguage.EN,
            text="a summary",
            model="stub",
            source_chunks=1,
            truncated=False,
            built_at=now,
        ),
    )
    await SqlSummaryJobRepository(tenant_session).add(
        ctx,
        SummaryJob(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            document_id=document.id,
            kind=SummaryKind.FULL,
            lang=SummaryLanguage.EN,
            status=SummaryJobStatus.QUEUED,
            total_chunks=1,
            done_chunks=0,
            error=None,
            cancelled_at=None,
            finished_at=None,
            created_at=now,
        ),
    )
    return document.id


async def _reindex_job(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, *document_ids: str
) -> str:
    job = ReindexJob(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        items=tuple(
            ReindexItem(
                document_id=document_id,
                file_id=new_uuid7(),
                source_document_id=new_uuid7(),
                status=IndexStatus.PENDING,
            )
            for document_id in document_ids
        ),
        cancelled_at=None,
        created_at=utc_now(),
    )
    await SqlReindexJobRepository(tenant_session).add(ctx, job)
    return job.id


async def _count(
    sessionmaker_app: async_sessionmaker[AsyncSession], ws: str, table: str, column: str, value: str
) -> int:
    """One table's surviving rows, read outside every repository — the only
    way to tell "the adapter deleted the rows" from "the adapter stopped
    returning them"."""
    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws})
        result = await session.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :value"), {"value": value}
        )
        return int(result.scalar_one())


async def test_deleting_a_file_empties_every_knowledge_table_that_named_it(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The defect, on the real path: before the cascade the document below
    survived its file as ``indexed``, with its chunk row joinable and its point
    still answering searches.

    The seeded corpus deliberately includes a ``reindex_job_items`` row —
    without the delete that precedes the document this test fails with a
    foreign-key violation rather than a wrong count, which is exactly the
    failure a fake cannot produce."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    file_id = await _ready_file(tenant_session, ctx, space.id)
    document_id = await _indexed_document(tenant_session, ctx, space.id, file_id)
    await _reindex_job(tenant_session, ctx, document_id)
    vectors = _RecordingVectors()

    result = await _service(tenant_session, vectors).delete(ctx, file_id)

    assert result.documents == 1
    # Qdrant was asked for exactly what the rows named, and asked BEFORE they
    # went — the refs are unreadable afterwards.
    assert vectors.deleted == [(knowledge_collection(ws), [chunk_point_id(document_id, 1)])]
    assert await _count(sessionmaker_app, ws, "knowledge.documents", "file_id", file_id) == 0
    assert await _count(sessionmaker_app, ws, "knowledge.chunks", "document_id", document_id) == 0
    assert (
        await _count(sessionmaker_app, ws, "knowledge.summaries", "document_id", document_id) == 0
    )
    assert (
        await _count(sessionmaker_app, ws, "knowledge.summary_jobs", "document_id", document_id)
        == 0
    )
    assert (
        await _count(
            sessionmaker_app, ws, "knowledge.reindex_job_items", "document_id", document_id
        )
        == 0
    )


async def test_the_file_row_survives_the_purge_as_a_soft_delete(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The asymmetry the cascade's docstring argues: the INDEX goes hard
    because a Qdrant point has no ``deleted_at`` to respect, while the file
    stays a soft delete — its row and its MinIO object are still there, which
    is what keeps the operation recoverable at the storage layer."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    file_id = await _ready_file(tenant_session, ctx, space.id)

    await _service(tenant_session, _RecordingVectors()).delete(ctx, file_id)

    assert await _count(sessionmaker_app, ws, "files.files", "id", file_id) == 1
    row = await SqlFileRepository(tenant_session).get(ctx, file_id)
    assert row is not None and row.deleted_at is not None


async def test_every_document_of_the_file_goes_including_a_reindex_replacement(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The "file indexed twice" shape on the real path. A re-index leaves the
    replacement under the SAME ``file_id``; a delete that reached one document
    would leave the other's points answering searches under a file the user
    just removed."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    file_id = await _ready_file(tenant_session, ctx, space.id)
    superseded = await _indexed_document(tenant_session, ctx, space.id, file_id)
    replacement = await _indexed_document(tenant_session, ctx, space.id, file_id)
    # The row that makes this the FK case: the replacement is what a re-index
    # job's item names.
    await _reindex_job(tenant_session, ctx, replacement)
    vectors = _RecordingVectors()

    result = await _service(tenant_session, vectors).delete(ctx, file_id)

    assert result.documents == 2
    collection = knowledge_collection(ws)
    assert sorted(vectors.deleted[0][1]) == sorted(
        [chunk_point_id(superseded, 1), chunk_point_id(replacement, 1)]
    )
    assert vectors.deleted[0][0] == collection
    assert await _count(sessionmaker_app, ws, "knowledge.documents", "file_id", file_id) == 0


async def test_a_neighbouring_file_in_the_same_space_is_untouched(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The predicate is ``file_id``, not ``space_id``: deleting one file must
    not empty the space it lived in."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    doomed = await _ready_file(tenant_session, ctx, space.id)
    kept = await _ready_file(tenant_session, ctx, space.id)
    await _indexed_document(tenant_session, ctx, space.id, doomed)
    survivor = await _indexed_document(tenant_session, ctx, space.id, kept)

    await _service(tenant_session, _RecordingVectors()).delete(ctx, doomed)

    assert await _count(sessionmaker_app, ws, "knowledge.documents", "file_id", kept) == 1
    assert await _count(sessionmaker_app, ws, "knowledge.chunks", "document_id", survivor) == 1


async def test_a_reindex_job_spanning_two_files_survives_the_first_one(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """A job is a request, not content, and one request may name documents from
    two files. Deleting the job with the first file would erase the other
    file's progress view — so only the ITEM goes, and the job stays while it
    still has one."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    doomed = await _ready_file(tenant_session, ctx, space.id)
    kept = await _ready_file(tenant_session, ctx, space.id)
    first = await _indexed_document(tenant_session, ctx, space.id, doomed)
    second = await _indexed_document(tenant_session, ctx, space.id, kept)
    job_id = await _reindex_job(tenant_session, ctx, first, second)

    await _service(tenant_session, _RecordingVectors()).delete(ctx, doomed)

    assert await _count(sessionmaker_app, ws, "knowledge.reindex_jobs", "id", job_id) == 1
    assert await _count(sessionmaker_app, ws, "knowledge.reindex_job_items", "job_id", job_id) == 1
    assert (
        await _count(sessionmaker_app, ws, "knowledge.reindex_job_items", "document_id", second)
        == 1
    )


async def test_the_cascade_can_be_run_again_after_it_finished(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The resume path, which is also what a client's retry of a lost 204 does:
    the mark is idempotent and the purge deletes nothing the second time."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    file_id = await _ready_file(tenant_session, ctx, space.id)
    await _indexed_document(tenant_session, ctx, space.id, file_id)
    service = _service(tenant_session, _RecordingVectors())
    await service.delete(ctx, file_id)

    again = await service.delete(ctx, file_id)

    assert again.documents == 0
    assert await _count(sessionmaker_app, ws, "knowledge.documents", "file_id", file_id) == 0


async def test_a_file_that_does_not_exist_is_a_404_before_anything_is_touched(
    tenant_session: TenantSessionFactory,
) -> None:
    """The mark is the cascade's only existence check: the purge cannot tell a
    file with no documents from a file that never existed, and would report a
    successful deletion of nothing."""
    vectors = _RecordingVectors()

    with pytest.raises(NotFoundError):
        await _service(tenant_session, vectors).delete(_ctx(new_uuid7()), new_uuid7())

    assert vectors.deleted == []
