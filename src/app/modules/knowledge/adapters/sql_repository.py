"""SQL adapters for ``DocumentRepository`` + ``ReindexJobRepository``
(02-port-contracts §2; 01-data-model §2.7; ``migrations/versions/knowledge/
0001_knowledge.py`` + ``0002_reindex_jobs.py``).

Declares its own local Core ``Table``\\ s against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly in every method below, on both ``documents`` and ``chunks``.

``set_status`` writes ``status``/``error`` unconditionally; when ``status ==
'indexed'`` it ALSO refreshes the denormalized ``documents.chunk_count``
column from a correlated ``COUNT(*)`` over that document's own
``knowledge.chunks`` rows, in the SAME ``UPDATE`` statement (port docstring,
binding) -- the row store's count can never drift from what ``add_chunks``
actually persisted.

``add_chunks`` is an idempotent bulk insert (INV-K1, DD-09): ``ON CONFLICT
(document_id, seq) DO NOTHING`` so redelivering the same batch after a
worker crash leaves the first-written rows untouched rather than duplicating
or overwriting them. The DB table name is ``chunks``, but the module-local
Core ``Table`` is named ``knowledge_chunks`` here purely to avoid shadowing
the port's own ``add_chunks(self, ctx, chunks: Sequence[Chunk])`` parameter
name (binding signature, kept verbatim).

``purge`` (BE-RAG-007) is this module's ONLY hard delete, and it is one
statement pair in one transaction: chunks then the document, an order
``fk_chunk_doc`` enforces. ``SqlReindexJobRepository.get`` stores no progress
and joins each item onto ``documents`` for its live status (INV-K5) — see its
own docstring for why that join is INNER.

``add_parent_chunks``/``parent_chunk_texts`` (P-14, rag-indexing-plan.md
§3.2, ``migrations/versions/knowledge/0005_parent_chunks.py``) are
``add_chunks``/``chunk_texts`` mirrored onto ``knowledge.parent_chunks``: the
same idempotent ``ON CONFLICT (document_id, seq) DO NOTHING`` upsert and the
same ``seq``-ordered read, over the coarser-grained table a matched
``Chunk`` widens to via ``Chunk.parent_id``. ``purge``/``purge_space`` need
no new statement for it: ``fk_parent_chunk_doc`` carries ``ON DELETE
CASCADE`` on ``document_id``, so the existing final ``DELETE`` of the
document row removes its now-unreferenced parent rows for free (chunks are
always deleted first, so nothing still points at them by then).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    ColumnElement,
    DateTime,
    Integer,
    MetaData,
    Select,
    Table,
    Text,
    Uuid,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.types import Uuid as UuidStr
from app.modules.knowledge.domain.entities import (
    Chunk,
    Document,
    ParentChunk,
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

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

documents = Table(
    "documents",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    # `migrations/versions/knowledge/0004_document_space.py` (§3.142), written
    # by `add` and filtered on by `list` since plan step 8. Still NULLable
    # until plan row 8-b, because a document built from a file that has no
    # space yet must be able to say so rather than invent one.
    Column("space_id", _uuid_col, nullable=True),
    Column("file_id", _uuid_col, nullable=False),
    Column("status", Text, nullable=False),
    Column("chunk_count", Integer, nullable=False),
    Column("error", Text, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="knowledge",
)

# SQL table name is `knowledge.chunks` -- see module docstring for why the
# Python identifier differs.
knowledge_chunks = Table(
    "chunks",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("document_id", _uuid_col, nullable=False),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("token_count", Integer, nullable=True),
    Column("collection", Text, nullable=True),
    Column("point_id", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    # P-14 (rag-indexing-plan.md §3.2, `0005_parent_chunks.py`) -- points at
    # the `parent_chunks` row this window was cut from. NULLable: a chunk
    # written before that pipeline stage runs has none to carry.
    Column("parent_id", _uuid_col, nullable=True),
    schema="knowledge",
)

# `knowledge.parent_chunks` (P-14, `0005_parent_chunks.py`) -- one row per
# parsed segment (`SourceSegment`), coarser-grained than `chunks`. Named
# `parent_chunks` here too: unlike `chunks`/`knowledge_chunks`, no Python
# identifier collision to dodge (the port method is `add_parent_chunks`, not
# `add_parents`).
parent_chunks = Table(
    "parent_chunks",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("document_id", _uuid_col, nullable=False),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema="knowledge",
)

reindex_jobs = Table(
    "reindex_jobs",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("cancelled_at", _timestamptz, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="knowledge",
)

reindex_job_items = Table(
    "reindex_job_items",
    _metadata,
    Column("job_id", _uuid_col, primary_key=True),
    Column("document_id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("file_id", _uuid_col, nullable=False),
    Column("source_document_id", _uuid_col, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema="knowledge",
)

summaries = Table(
    "summaries",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("document_id", _uuid_col, nullable=False),
    Column("kind", Text, nullable=False),
    Column("lang", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("source_chunks", Integer, nullable=False),
    Column("truncated", Boolean, nullable=False),
    Column("built_at", _timestamptz, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="knowledge",
)

summary_jobs = Table(
    "summary_jobs",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("document_id", _uuid_col, nullable=False),
    Column("kind", Text, nullable=False),
    Column("lang", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("total_chunks", Integer, nullable=False),
    Column("done_chunks", Integer, nullable=False),
    Column("error", Text, nullable=True),
    Column("cancelled_at", _timestamptz, nullable=True),
    Column("finished_at", _timestamptz, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="knowledge",
)

# The statuses a `summary_jobs` row is in while it still has work left --
# `uq_summary_job_active`'s own predicate, and the guard `record_progress`
# writes under. Kept as one tuple so the index and the query can never drift.
_ACTIVE_JOB_STATUSES = (SummaryJobStatus.QUEUED.value, SummaryJobStatus.RUNNING.value)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


def _documents_in_space(ctx: ExecutionContext, space_id: UuidStr) -> Select[tuple[str]]:
    """The ids of one space's documents, as a SUBQUERY (spaces plan step 11).

    Written once and reused by both space-wide methods so the set they read
    and the set they delete are defined by the same two predicates. Returned
    unexecuted on purpose: the ids stay inside the database, where no page
    size can make the corpus a `vector_refs_in_space` collected differ from
    the corpus `purge_space` destroys a moment later.
    """
    return select(documents.c.id).where(
        documents.c.workspace_id == ctx.workspace_id,
        documents.c.space_id == space_id,
    )


class SqlDocumentRepository:
    """SQL ``DocumentRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — ``set_status``'s ``chunk_count`` refresh is a
    single ``UPDATE`` statement (a correlated subquery), so it needs no
    second round trip to stay within "the same transaction" (port docstring).
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(self, ctx: ExecutionContext, doc_id: UuidStr) -> Document | None:
        stmt = select(documents).where(
            documents.c.id == doc_id, documents.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_document(row)

    async def list(
        self, ctx: ExecutionContext, *, space_id: UuidStr | None, limit: int, cursor: str | None
    ) -> Page[Document]:
        # Layer 2 on its own reads every status (the port's reasoning): a
        # `pending` or `failed` document is exactly what a client needs to
        # see. Ordered by `id` rather than `created_at` because UUIDv7 is
        # time-ordered — the same sort, but total, so two documents
        # registered in the same millisecond still have a stable order.
        #
        # NEWEST FIRST (`framework/pagination`): `id DESC` paired with a
        # `id < cursor` predicate. The two must point the same way — pointing
        # them apart gives a page that is silently empty or never ends.
        conditions = [documents.c.workspace_id == ctx.workspace_id]
        if space_id is not None:
            # The space narrowing (step 8) -- backed by `ix_kndoc_space`
            # (`knowledge/0004_document_space.py`), a FULL index because this
            # table has no `deleted_at` to make it partial on (§4's 📌).
            # An EQUALITY on an opaque id: this adapter never joins to
            # `spaces.spaces`, which it cannot see, and the workspace
            # condition above stays in place -- a space is an axis inside the
            # tenant, never a replacement for it.
            conditions.append(documents.c.space_id == space_id)
        if cursor is not None:
            conditions.append(documents.c.id < decode_id_cursor(cursor))
        stmt = select(documents).where(*conditions).order_by(documents.c.id.desc()).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = encode_id_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
        return Page(
            data=[_hydrate_document(row) for row in page_rows],
            next_cursor=next_cursor,
            limit=limit,
        )

    async def ids_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[UuidStr]
    ) -> Sequence[UuidStr]:
        # Short-circuit BEFORE the query: `IN ()` is a syntax error in
        # Postgres, and SQLAlchemy's empty-`in_` renders a guaranteed-false
        # predicate whose round trip buys nothing.
        if not file_ids:
            return []
        stmt = select(documents.c.id).where(
            documents.c.workspace_id == ctx.workspace_id,
            documents.c.file_id.in_(list(file_ids)),
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).scalars().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return list(rows)

    async def add(self, ctx: ExecutionContext, doc: Document) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched doc.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's
        # tenant (media precedent).
        stmt = insert(documents).values(
            id=doc.id,
            workspace_id=doc.workspace_id,
            space_id=doc.space_id,
            file_id=doc.file_id,
            status=doc.status.value,
            chunk_count=doc.chunk_count,
            error=doc.error,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            version=doc.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def set_status(
        self, ctx: ExecutionContext, doc_id: UuidStr, status: str, error: str | None = None
    ) -> None:
        values: dict[str, object] = {"status": status, "error": error}
        if status == IndexStatus.INDEXED.value:
            # Refresh the denormalized chunk_count from what add_chunks
            # actually persisted (port docstring, binding) -- a correlated
            # scalar subquery in the SAME UPDATE, so it can never drift from
            # (or race against) a separate read-then-write round trip.
            values["chunk_count"] = _chunk_count_subquery()
        stmt = (
            update(documents)
            .where(documents.c.id == doc_id, documents.c.workspace_id == ctx.workspace_id)
            .values(**values)
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def vector_refs(self, ctx: ExecutionContext, doc_id: UuidStr) -> Sequence[VectorRef]:
        # Rows with a NULL collection/point_id are skipped rather than
        # hydrated into a half-built VectorRef: the value object refuses
        # empties, and a chunk that never reached the vector store has
        # nothing to delete there.
        stmt = select(knowledge_chunks.c.collection, knowledge_chunks.c.point_id).where(
            knowledge_chunks.c.document_id == doc_id,
            knowledge_chunks.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [
            VectorRef(row["collection"], row["point_id"])
            for row in rows
            if row["collection"] and row["point_id"]
        ]

    async def chunk_texts(self, ctx: ExecutionContext, doc_id: UuidStr) -> Sequence[str]:
        # `seq` ASC is the document's reading order, and the summariser
        # depends on it -- see the port. Only the text column is selected:
        # this is the one query in the module that can return an entire
        # document's body, and dragging the vector refs along with it would
        # double the row width for a caller that never looks at them.
        stmt = (
            select(knowledge_chunks.c.text)
            .where(
                knowledge_chunks.c.document_id == doc_id,
                knowledge_chunks.c.workspace_id == ctx.workspace_id,
            )
            .order_by(knowledge_chunks.c.seq)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).scalars().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return list(rows)

    async def purge(self, ctx: ExecutionContext, doc_id: UuidStr) -> None:
        # Summaries, then chunks, then the document -- the missing `ON DELETE`
        # on `fk_summary_doc`/`fk_chunk_doc` makes the order mandatory. All
        # three statements run in ONE session/transaction, so a failure
        # between them cannot leave an orphaned child row behind.
        drop_summaries = delete(summaries).where(
            summaries.c.document_id == doc_id,
            summaries.c.workspace_id == ctx.workspace_id,
        )
        drop_chunks = delete(knowledge_chunks).where(
            knowledge_chunks.c.document_id == doc_id,
            knowledge_chunks.c.workspace_id == ctx.workspace_id,
        )
        drop_document = delete(documents).where(
            documents.c.id == doc_id, documents.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(drop_summaries)
                await session.execute(drop_chunks)
                await session.execute(drop_document)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def vector_refs_in_space(
        self, ctx: ExecutionContext, space_id: UuidStr
    ) -> Sequence[VectorRef]:
        # `vector_refs` widened to a space through a subquery on `documents`:
        # `chunks` carries no `space_id` of its own (only `documents` grew
        # one, §3.142), and adding one would be a second place for a
        # document's space to be recorded and to drift.
        stmt = select(knowledge_chunks.c.collection, knowledge_chunks.c.point_id).where(
            knowledge_chunks.c.workspace_id == ctx.workspace_id,
            knowledge_chunks.c.document_id.in_(_documents_in_space(ctx, space_id)),
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        # Half-written chunks are skipped for `vector_refs`'s reason: there is
        # nothing in the vector store to delete for a chunk that never
        # reached it, and `VectorRef` refuses to be built empty.
        return [
            VectorRef(row["collection"], row["point_id"])
            for row in rows
            if row["collection"] and row["point_id"]
        ]

    async def purge_space(self, ctx: ExecutionContext, space_id: UuidStr) -> int:
        docs = _documents_in_space(ctx, space_id)
        # Children before parents, and the order is the FK's, not a
        # preference: `fk_summary_doc`, `fk_chunk_doc` and
        # `fk_reindex_item_doc` all reference `knowledge.documents(id)` with
        # no `ON DELETE`, so a document deleted first is a `23503`.
        #
        # `summary_jobs` has no FK at all and is deleted anyway: nothing would
        # break if it stayed, which is exactly why leaving it out would be a
        # leak nobody notices -- a queued build for a document that no longer
        # exists, in a space that no longer exists.
        statements = [
            delete(summaries).where(
                summaries.c.workspace_id == ctx.workspace_id,
                summaries.c.document_id.in_(docs),
            ),
            delete(summary_jobs).where(
                summary_jobs.c.workspace_id == ctx.workspace_id,
                summary_jobs.c.document_id.in_(docs),
            ),
            delete(reindex_job_items).where(
                reindex_job_items.c.workspace_id == ctx.workspace_id,
                reindex_job_items.c.document_id.in_(docs),
            ),
            delete(knowledge_chunks).where(
                knowledge_chunks.c.workspace_id == ctx.workspace_id,
                knowledge_chunks.c.document_id.in_(docs),
            ),
        ]
        # `RETURNING id` rather than a driver `rowcount` -- the count is part
        # of the port's contract, and `rowcount` is typed `Any`.
        drop_documents = (
            delete(documents)
            .where(
                documents.c.workspace_id == ctx.workspace_id,
                documents.c.space_id == space_id,
            )
            .returning(documents.c.id)
        )
        # LAST, and only the childless ones (port docstring): a job whose
        # items were all this space's now reports on nothing, but a job that
        # still has items reports on ANOTHER space's rebuild and must survive.
        # `NOT EXISTS` asks that question of the rows as they are AFTER the
        # deletes above, inside the same transaction.
        drop_empty_jobs = delete(reindex_jobs).where(
            reindex_jobs.c.workspace_id == ctx.workspace_id,
            ~select(reindex_job_items.c.job_id)
            .where(reindex_job_items.c.job_id == reindex_jobs.c.id)
            .exists(),
        )
        try:
            # ONE transaction for all six: a corpus half-deleted across a
            # failure is a document whose chunks are gone but whose row still
            # says `indexed`, which no re-run of the cascade would repair.
            async with self._tenant_session(ctx) as session:
                for statement in statements:
                    await session.execute(statement)
                deleted = (await session.execute(drop_documents)).scalars().all()
                await session.execute(drop_empty_jobs)
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return len(deleted)

    async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
        if not chunks:
            return
        # Every chunk's OWN workspace_id is written (not ctx.workspace_id) --
        # the same forged-write guard as `add` (media precedent).
        stmt = (
            pg_insert(knowledge_chunks)
            .values([_chunk_row(chunk) for chunk in chunks])
            .on_conflict_do_nothing(
                index_elements=[knowledge_chunks.c.document_id, knowledge_chunks.c.seq]
            )
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def add_parent_chunks(
        self, ctx: ExecutionContext, parents: Sequence[ParentChunk]
    ) -> None:
        if not parents:
            return
        # Every parent's OWN workspace_id is written (not ctx.workspace_id)
        # -- the same forged-write guard `add`/`add_chunks` use.
        stmt = (
            pg_insert(parent_chunks)
            .values([_parent_chunk_row(parent) for parent in parents])
            .on_conflict_do_nothing(
                index_elements=[parent_chunks.c.document_id, parent_chunks.c.seq]
            )
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def parent_chunk_texts(self, ctx: ExecutionContext, doc_id: UuidStr) -> Sequence[str]:
        # `seq` ASC mirrors `chunk_texts`'s own reading-order contract.
        stmt = (
            select(parent_chunks.c.text)
            .where(
                parent_chunks.c.document_id == doc_id,
                parent_chunks.c.workspace_id == ctx.workspace_id,
            )
            .order_by(parent_chunks.c.seq)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).scalars().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return list(rows)


class SqlReindexJobRepository:
    """SQL ``ReindexJobRepository`` adapter (BE-RAG-007/008).

    ``get`` is the interesting one: it JOINS the item rows onto
    ``knowledge.documents`` and reads each item's status from there, because
    no status is stored on the item (INV-K5). An INNER join is correct rather
    than merely convenient — ``fk_reindex_item_doc`` guarantees the document
    row exists, so a missing one is a corrupted database, not a job with a
    hole in it, and hiding it behind a LEFT join and a default status would
    report progress about a document nobody can find.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def add(self, ctx: ExecutionContext, job: ReindexJob) -> None:
        # The aggregate's OWN workspace_id on every row (not ctx's) -- the
        # same forged-write guard the document writes use: a mismatch is
        # rejected by the RLS WITH CHECK rather than silently persisted.
        job_stmt = insert(reindex_jobs).values(
            id=job.id,
            workspace_id=job.workspace_id,
            cancelled_at=job.cancelled_at,
            created_at=job.created_at,
            updated_at=job.created_at,
            version=1,
        )
        item_rows = [
            {
                "job_id": job.id,
                "document_id": item.document_id,
                "workspace_id": job.workspace_id,
                "file_id": item.file_id,
                "source_document_id": item.source_document_id,
                "created_at": job.created_at,
            }
            for item in job.items
        ]
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(job_stmt)
                if item_rows:
                    await session.execute(insert(reindex_job_items).values(item_rows))
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def get(self, ctx: ExecutionContext, job_id: UuidStr) -> ReindexJob | None:
        job_stmt = select(reindex_jobs).where(
            reindex_jobs.c.id == job_id, reindex_jobs.c.workspace_id == ctx.workspace_id
        )
        # Ordered by the item's own document id so a job's file list is
        # stable across polls -- a progress panel that reshuffles its rows
        # every two seconds is unreadable even when every number is right.
        items_stmt = (
            select(
                reindex_job_items.c.document_id,
                reindex_job_items.c.file_id,
                reindex_job_items.c.source_document_id,
                documents.c.status,
            )
            .join_from(
                reindex_job_items,
                documents,
                reindex_job_items.c.document_id == documents.c.id,
            )
            .where(
                reindex_job_items.c.job_id == job_id,
                reindex_job_items.c.workspace_id == ctx.workspace_id,
                documents.c.workspace_id == ctx.workspace_id,
            )
            .order_by(reindex_job_items.c.document_id)
        )
        try:
            async with self._tenant_session(ctx) as session:
                job_row = (await session.execute(job_stmt)).mappings().first()
                if job_row is None:
                    return None
                item_rows = (await session.execute(items_stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return ReindexJob(
            id=job_row["id"],
            workspace_id=job_row["workspace_id"],
            items=tuple(
                ReindexItem(
                    document_id=row["document_id"],
                    file_id=row["file_id"],
                    source_document_id=row["source_document_id"],
                    status=IndexStatus(row["status"]),
                )
                for row in item_rows
            ),
            cancelled_at=job_row["cancelled_at"],
            created_at=job_row["created_at"],
        )

    async def mark_cancelled(self, ctx: ExecutionContext, job_id: UuidStr, at: datetime) -> None:
        stmt = (
            update(reindex_jobs)
            .where(reindex_jobs.c.id == job_id, reindex_jobs.c.workspace_id == ctx.workspace_id)
            .values(cancelled_at=at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc


class SqlSummaryRepository:
    """SQL ``SummaryRepository`` adapter (BE-RAG-009/010/011).

    ``upsert`` targets ``uq_summary_key`` with ``ON CONFLICT ... DO UPDATE``:
    a rebuild is the reason this is ever called twice, and the newer text is
    meant to win. The row's ``id`` is deliberately NOT among the updated
    columns — the summary of this document in this kind and this language is
    one thing whose body changed, not a new thing, and re-minting its
    identifier on every rebuild would break any reference taken to it.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(
        self,
        ctx: ExecutionContext,
        document_id: UuidStr,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> Summary | None:
        stmt = select(summaries).where(
            summaries.c.document_id == document_id,
            summaries.c.kind == kind.value,
            summaries.c.lang == lang.value,
            summaries.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_summary(row)

    async def upsert(self, ctx: ExecutionContext, summary: Summary) -> None:
        # The aggregate's OWN workspace_id (not ctx's) -- the forged-write
        # guard every write in this adapter uses: a mismatch is rejected by
        # the RLS WITH CHECK rather than silently persisted.
        stmt = (
            pg_insert(summaries)
            .values(
                id=summary.id,
                workspace_id=summary.workspace_id,
                document_id=summary.document_id,
                kind=summary.kind.value,
                lang=summary.lang.value,
                text=summary.text,
                model=summary.model,
                source_chunks=summary.source_chunks,
                truncated=summary.truncated,
                built_at=summary.built_at,
                created_at=summary.built_at,
                updated_at=summary.built_at,
                version=1,
            )
            .on_conflict_do_update(
                index_elements=[summaries.c.document_id, summaries.c.kind, summaries.c.lang],
                set_={
                    "text": summary.text,
                    "model": summary.model,
                    "source_chunks": summary.source_chunks,
                    "truncated": summary.truncated,
                    "built_at": summary.built_at,
                    "version": summaries.c.version + 1,
                },
            )
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def delete(
        self,
        ctx: ExecutionContext,
        document_id: UuidStr,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> bool:
        # `RETURNING` rather than a rowcount (the module-wide idiom): the
        # boolean is part of the contract, and a row that came back is
        # evidence rather than a driver statistic.
        stmt = (
            delete(summaries)
            .where(
                summaries.c.document_id == document_id,
                summaries.c.kind == kind.value,
                summaries.c.lang == lang.value,
                summaries.c.workspace_id == ctx.workspace_id,
            )
            .returning(summaries.c.id)
        )
        try:
            async with self._tenant_session(ctx) as session:
                deleted = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return deleted is not None

    async def delete_for_document(self, ctx: ExecutionContext, document_id: UuidStr) -> None:
        stmt = delete(summaries).where(
            summaries.c.document_id == document_id,
            summaries.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc


class SqlSummaryJobRepository:
    """SQL ``SummaryJobRepository`` adapter (BE-RAG-009/011).

    Nothing here joins anything: unlike ``SqlReindexJobRepository.get``, whose
    every number comes from a join onto ``documents`` (INV-K5), this
    aggregate's progress is on its own row because there is no second witness
    to read it from — see ``SummaryJobStatus``.

    ``record_progress`` is the one method that is deliberately NOT a
    whole-row write. Its ``WHERE status = 'running'`` guard is what stops a
    worker holding a stale in-memory job from writing ``running`` back over a
    cancellation that landed while it was mid-build; see the port.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def add(self, ctx: ExecutionContext, job: SummaryJob) -> None:
        stmt = insert(summary_jobs).values(
            id=job.id,
            workspace_id=job.workspace_id,
            document_id=job.document_id,
            kind=job.kind.value,
            lang=job.lang.value,
            status=job.status.value,
            total_chunks=job.total_chunks,
            done_chunks=job.done_chunks,
            error=job.error,
            cancelled_at=job.cancelled_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
            updated_at=job.created_at,
            version=1,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def get(self, ctx: ExecutionContext, job_id: UuidStr) -> SummaryJob | None:
        stmt = select(summary_jobs).where(
            summary_jobs.c.id == job_id, summary_jobs.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_summary_job(row)

    async def active_for(
        self,
        ctx: ExecutionContext,
        document_id: UuidStr,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> SummaryJob | None:
        # `uq_summary_job_active` guarantees at most one row matches, so
        # there is nothing to order by and no ambiguity to resolve.
        stmt = select(summary_jobs).where(
            summary_jobs.c.document_id == document_id,
            summary_jobs.c.kind == kind.value,
            summary_jobs.c.lang == lang.value,
            summary_jobs.c.status.in_(_ACTIVE_JOB_STATUSES),
            summary_jobs.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_summary_job(row)

    async def save(self, ctx: ExecutionContext, job: SummaryJob) -> None:
        stmt = (
            update(summary_jobs)
            .where(summary_jobs.c.id == job.id, summary_jobs.c.workspace_id == ctx.workspace_id)
            .values(
                status=job.status.value,
                total_chunks=job.total_chunks,
                done_chunks=job.done_chunks,
                error=job.error,
                cancelled_at=job.cancelled_at,
                finished_at=job.finished_at,
                version=summary_jobs.c.version + 1,
            )
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def record_progress(
        self, ctx: ExecutionContext, job_id: UuidStr, done_chunks: int
    ) -> None:
        stmt = (
            update(summary_jobs)
            .where(
                summary_jobs.c.id == job_id,
                summary_jobs.c.workspace_id == ctx.workspace_id,
                # THE guard (port docstring): after a cancellation this
                # matches no row, so the write silently does nothing instead
                # of resurrecting a job the user stopped.
                summary_jobs.c.status == SummaryJobStatus.RUNNING.value,
            )
            .values(done_chunks=done_chunks)
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc


def _chunk_count_subquery() -> ColumnElement[int]:
    """A correlated scalar subquery: ``COUNT(*)`` over a document's own
    ``knowledge.chunks`` rows -- labeled implicitly via ``.values(chunk_count=...)``
    (mirrors ``conversations``'s ``_message_count_column`` correlation
    pattern)."""
    return (
        select(func.count())
        .select_from(knowledge_chunks)
        .where(
            knowledge_chunks.c.document_id == documents.c.id,
            knowledge_chunks.c.workspace_id == documents.c.workspace_id,
        )
        .scalar_subquery()
    )


def _chunk_row(chunk: Chunk) -> dict[str, object]:
    # `created_at` is intentionally omitted -- `Chunk` carries no such field
    # (immutable value shape, domain/entities.py), so the column's own
    # `DEFAULT now()` (01 §2.7) is what populates it.
    return {
        "id": chunk.id,
        "document_id": chunk.document_id,
        "workspace_id": chunk.workspace_id,
        "seq": chunk.seq,
        "text": chunk.text,
        "token_count": chunk.token_count,
        "collection": chunk.vector_ref.collection if chunk.vector_ref else None,
        "point_id": chunk.vector_ref.point_id if chunk.vector_ref else None,
        "parent_id": chunk.parent_id,
    }


def _parent_chunk_row(parent: ParentChunk) -> dict[str, object]:
    return {
        "id": parent.id,
        "document_id": parent.document_id,
        "workspace_id": parent.workspace_id,
        "seq": parent.seq,
        "text": parent.text,
        "created_at": parent.created_at,
    }


def _hydrate_document(row: RowMapping) -> Document:
    return Document(
        id=row["id"],
        workspace_id=row["workspace_id"],
        space_id=row["space_id"],
        file_id=row["file_id"],
        status=IndexStatus(row["status"]),
        chunk_count=row["chunk_count"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _hydrate_summary(row: RowMapping) -> Summary:
    return Summary(
        id=row["id"],
        workspace_id=row["workspace_id"],
        document_id=row["document_id"],
        kind=SummaryKind(row["kind"]),
        lang=SummaryLanguage(row["lang"]),
        text=row["text"],
        model=row["model"],
        source_chunks=row["source_chunks"],
        truncated=row["truncated"],
        built_at=row["built_at"],
    )


def _hydrate_summary_job(row: RowMapping) -> SummaryJob:
    return SummaryJob(
        id=row["id"],
        workspace_id=row["workspace_id"],
        document_id=row["document_id"],
        kind=SummaryKind(row["kind"]),
        lang=SummaryLanguage(row["lang"]),
        status=SummaryJobStatus(row["status"]),
        total_chunks=row["total_chunks"],
        done_chunks=row["done_chunks"],
        error=row["error"],
        cancelled_at=row["cancelled_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race: a duplicate
    ``id`` on ``add``/``add_chunks`` -- ``ConflictError`` (409,
    ``common.conflict``). ``uq_chunk_seq`` (``document_id, seq``) itself
    never raises here: ``add_chunks`` targets it with ``ON CONFLICT ... DO
    NOTHING`` (INV-K1/DD-09), so a redelivered batch is absorbed silently,
    not surfaced as a conflict.
    ``23503`` (``foreign_key_violation``) -- ``ConflictError`` too, and it has
    exactly one site: ``SqlSummaryRepository.upsert`` storing a summary whose
    document was destroyed by a re-index while the build was running
    (``fk_summary_doc``). A conflict is the honest code — the caller's request
    was valid when it was made and lost to another operation — and the summary
    worker relies on getting it back rather than a 500, because that is what
    lets it end the job with a reason instead of redelivering forever.

    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``workspace_id`` on
    ``add``/``add_chunks``) -- an internal/500-class error
    (``common.internal``): a well-behaved caller can never trigger this, so
    it is not a normal 4xx. Anything else is an unexpected database failure,
    folded into the same 500-class error rather than leaking the driver
    exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("knowledge write lost a uniqueness race")
    if sqlstate == "23503":
        return ConflictError("knowledge write referenced a row that no longer exists")
    if sqlstate == "42501":
        return AppError("knowledge write rejected by row-level security", code="common.internal")
    return AppError(
        "unexpected database error while persisting knowledge data", code="common.internal"
    )
