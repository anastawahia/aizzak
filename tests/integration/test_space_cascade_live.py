"""Live-Postgres proof of the space cascade (``docs/spaces-backend-plan.md``
§3.6, step 11; 09-testing-strategy §3).

The unit file next door proves the ORDER — mark, then knowledge, then files,
then conversations, each module's own store emptied through a fake. What it
cannot prove is that the three ``purge_space`` statements reach the right
tables in an order PostgreSQL will accept: ``fk_chunk_doc``,
``fk_summary_doc``, ``fk_reindex_item_doc`` and ``fk_msg_conv`` all reference
their parents with no ``ON DELETE``, so a cascade that deleted a parent one
statement too early raises ``23503`` against a database and nothing at all
against a dict.

So this file wires the real adapters exactly as the Composition Root does, and
seeds through the modules' own use-cases wherever one exists — the rows the
cascade destroys are the rows the platform would have written.

**Qdrant and MinIO are fakes here, deliberately.** Both are covered by their
own live suites (``test_qdrant_store.py`` / ``test_minio_storage.py``); what is
untested anywhere else, and untestable without a database, is the SQL. The
fakes still record, so "the points were asked for before the rows went" stays
assertable on the live path too.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.space_deletion import DeleteSpaceService
from app.framework.errors import NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.application.use_cases import (
    AppendMessage,
    PinConversationFile,
    PurgeSpaceConversations,
    SoftDeleteConversation,
    StartConversation,
)
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import (
    CompleteUpload,
    FilesQueryService,
    PurgeSpaceFiles,
    RegisterUpload,
    SoftDeleteFile,
)
from app.modules.knowledge.adapters.sql_repository import (
    SqlDocumentRepository,
    SqlReindexJobRepository,
    SqlSummaryJobRepository,
    SqlSummaryRepository,
)
from app.modules.knowledge.application.use_cases import PurgeSpaceKnowledge
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
from app.modules.spaces.application.use_cases import DeleteSpace, SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName

pytestmark = [pytest.mark.live_db]


class _RecordingVectors:
    """The one ``VectorStore`` method the cascade calls."""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, list[str]]] = []

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        self.deleted.append((collection, list(ids)))


class _RecordingStorage:
    """The one ``StorageProvider`` method the cascade calls."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


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


def _service(
    tenant_session: TenantSessionFactory,
    vectors: _RecordingVectors,
    storage: _RecordingStorage,
) -> DeleteSpaceService:
    """The production wiring of ``_build_space_services``, minus the two
    external adapters."""
    documents = SqlDocumentRepository(tenant_session)
    files = SqlFileRepository(tenant_session)
    return DeleteSpaceService(
        DeleteSpace(SqlSpaceRepository(tenant_session)),
        knowledge=PurgeSpaceKnowledge(documents, vectors),
        files=PurgeSpaceFiles(files, storage),
        conversations=PurgeSpaceConversations(SqlConversationRepository(tenant_session)),
    )


async def _ready_file(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str
) -> tuple[str, str]:
    """A ``ready`` file through the module's own path — its id and its key."""
    files = SqlFileRepository(tenant_session)
    register = RegisterUpload(
        files, Limits(), SpacesQueryService(SqlSpaceRepository(tenant_session))
    )
    file = await register.execute(
        ctx, space_id=space_id, name="paper.pdf", content_type="application/pdf", size_bytes=11
    )
    await CompleteUpload(files).execute(ctx, file_id=file.id, checksum=None)
    return file.id, file.storage_key.value


async def _thread_with_history(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str, file_id: str
) -> str:
    """A thread with a message and a pin — one row in each of the module's
    three tables, which is what makes the delete order testable."""
    repository = SqlConversationRepository(tenant_session)
    conversation, _events = await StartConversation(
        repository, SpacesQueryService(SqlSpaceRepository(tenant_session))
    ).execute(ctx, space_id=space_id, agent_key="rag-agent")
    await AppendMessage(repository).execute(ctx, conversation.id, role="user", text="hello")
    await PinConversationFile(
        repository, FilesQueryService(SqlFileRepository(tenant_session))
    ).execute(ctx, conversation.id, file_id)
    return conversation.id


async def _indexed_document(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str, file_id: str
) -> str:
    """A document with a chunk, a summary and a summary job — the three
    children ``purge_space`` must delete before the document itself."""
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
            updated_at=now,
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


async def test_deleting_a_space_empties_every_table_that_named_it(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The whole of §3.6 on the real path. The seeded corpus deliberately
    includes a ``reindex_job_items`` row: without the delete that precedes the
    document, this test fails with a foreign-key violation rather than a
    wrong count — which is exactly the failure a fake cannot produce."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    file_id, key = await _ready_file(tenant_session, ctx, space.id)
    conversation_id = await _thread_with_history(tenant_session, ctx, space.id, file_id)
    document_id = await _indexed_document(tenant_session, ctx, space.id, file_id)
    await _reindex_job(tenant_session, ctx, document_id)
    vectors, storage = _RecordingVectors(), _RecordingStorage()

    result = await _service(tenant_session, vectors, storage).delete(ctx, space.id)

    assert (result.documents, result.files, result.conversations) == (1, 1, 1)
    # The two external stores were asked for exactly what the rows named.
    assert vectors.deleted == [(knowledge_collection(ws), [chunk_point_id(document_id, 1)])]
    assert storage.deleted == [key]
    # Nothing survives in any of the eight tables the cascade touches.
    assert await _count(sessionmaker_app, ws, "files.files", "space_id", space.id) == 0
    assert (
        await _count(sessionmaker_app, ws, "conversations.conversations", "space_id", space.id) == 0
    )
    assert (
        await _count(
            sessionmaker_app, ws, "conversations.messages", "conversation_id", conversation_id
        )
        == 0
    )
    assert (
        await _count(
            sessionmaker_app,
            ws,
            "conversations.conversation_files",
            "conversation_id",
            conversation_id,
        )
        == 0
    )
    assert await _count(sessionmaker_app, ws, "knowledge.documents", "space_id", space.id) == 0
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


async def test_a_neighbouring_space_in_the_same_workspace_is_untouched(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The space is an axis INSIDE the tenant (§3.2): every statement carries
    both predicates, and dropping the space one would empty the workspace."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doomed, kept = _space(ws, "Research"), _space(ws, "Drafts")
    await repo_spaces.add(ctx, doomed)
    await repo_spaces.add(ctx, kept)
    await _ready_file(tenant_session, ctx, doomed.id)
    survivor_file, survivor_key = await _ready_file(tenant_session, ctx, kept.id)
    await _thread_with_history(tenant_session, ctx, kept.id, survivor_file)
    survivor_doc = await _indexed_document(tenant_session, ctx, kept.id, survivor_file)
    vectors, storage = _RecordingVectors(), _RecordingStorage()

    result = await _service(tenant_session, vectors, storage).delete(ctx, doomed.id)

    assert (result.documents, result.files, result.conversations) == (0, 1, 0)
    assert vectors.deleted == []
    assert survivor_key not in storage.deleted
    assert await _count(sessionmaker_app, ws, "files.files", "space_id", kept.id) == 1
    assert await _count(sessionmaker_app, ws, "knowledge.documents", "id", survivor_doc) == 1
    assert (
        await _count(sessionmaker_app, ws, "conversations.conversations", "space_id", kept.id) == 1
    )


async def test_a_reindex_job_spanning_two_spaces_survives_the_first_one(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """A job is a REQUEST, not content: deleting it with the first of the two
    spaces it names would erase the other space's progress view. Only a job
    with no items left goes."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    doomed, kept = _space(ws, "Research"), _space(ws, "Drafts")
    await repo_spaces.add(ctx, doomed)
    await repo_spaces.add(ctx, kept)
    doomed_file, _ = await _ready_file(tenant_session, ctx, doomed.id)
    kept_file, _ = await _ready_file(tenant_session, ctx, kept.id)
    doomed_doc = await _indexed_document(tenant_session, ctx, doomed.id, doomed_file)
    kept_doc = await _indexed_document(tenant_session, ctx, kept.id, kept_file)
    shared_job = await _reindex_job(tenant_session, ctx, doomed_doc, kept_doc)
    emptied_job = await _reindex_job(tenant_session, ctx, doomed_doc)

    await _service(tenant_session, _RecordingVectors(), _RecordingStorage()).delete(ctx, doomed.id)

    assert await _count(sessionmaker_app, ws, "knowledge.reindex_jobs", "id", shared_job) == 1
    assert await _count(sessionmaker_app, ws, "knowledge.reindex_jobs", "id", emptied_job) == 0
    assert (
        await _count(sessionmaker_app, ws, "knowledge.reindex_job_items", "job_id", shared_job) == 1
    )


async def test_a_soft_deleted_file_and_thread_go_with_their_space(
    tenant_session: TenantSessionFactory,
    sessionmaker_app: async_sessionmaker[AsyncSession],
    repo_spaces: SqlSpaceRepository,
) -> None:
    """``deleted_at`` is not a reason to skip a row here, and the file half is
    the one that matters: a soft-deleted file has already given its bytes back
    to the quota, but its OBJECT is still in MinIO. A ``deleted_at IS NULL``
    predicate on either statement — the one every OTHER read in these adapters
    carries — would leave storage behind with no row left naming it, and a
    thread tombstone under a space that no longer exists.
    """
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    files = SqlFileRepository(tenant_session)
    file_id, key = await _ready_file(tenant_session, ctx, space.id)
    conversation_id = await _thread_with_history(tenant_session, ctx, space.id, file_id)
    conversations = SqlConversationRepository(tenant_session)
    await SoftDeleteFile(files).execute(ctx, file_id)
    await SoftDeleteConversation(conversations).execute(ctx, conversation_id)
    storage = _RecordingStorage()

    result = await _service(tenant_session, _RecordingVectors(), storage).delete(ctx, space.id)

    assert (result.files, result.conversations) == (1, 1)
    assert storage.deleted == [key]
    assert await _count(sessionmaker_app, ws, "files.files", "id", file_id) == 0
    assert (
        await _count(sessionmaker_app, ws, "conversations.conversations", "id", conversation_id)
        == 0
    )


async def test_the_cascade_can_be_run_again_after_it_finished(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """Every step is idempotent (§3.6), which is what makes an interrupted
    cascade recoverable: the second run finds nothing and says so, instead of
    failing on the space it already marked."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    file_id, _ = await _ready_file(tenant_session, ctx, space.id)
    await _indexed_document(tenant_session, ctx, space.id, file_id)
    service = _service(tenant_session, _RecordingVectors(), _RecordingStorage())
    await service.delete(ctx, space.id)

    again = await service.delete(ctx, space.id)

    assert (again.documents, again.files, again.conversations) == (0, 0, 0)


async def test_a_space_that_does_not_exist_is_a_404_before_anything_is_touched(
    tenant_session: TenantSessionFactory,
) -> None:
    vectors, storage = _RecordingVectors(), _RecordingStorage()

    with pytest.raises(NotFoundError):
        await _service(tenant_session, vectors, storage).delete(_ctx(new_uuid7()), new_uuid7())

    assert vectors.deleted == []
    assert storage.deleted == []
