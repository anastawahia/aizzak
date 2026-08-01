"""SQL adapter for ``DocumentRepository`` (02-port-contracts §2; 01-data-model
§2.7; ``migrations/versions/knowledge/0001_knowledge.py``).

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
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

from sqlalchemy import (
    Column,
    ColumnElement,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
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
from app.modules.knowledge.domain.entities import Chunk, Document
from app.modules.knowledge.domain.value_objects import IndexStatus

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
    schema="knowledge",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


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
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None
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

    async def add(self, ctx: ExecutionContext, doc: Document) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched doc.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's
        # tenant (media precedent).
        stmt = insert(documents).values(
            id=doc.id,
            workspace_id=doc.workspace_id,
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
    }


def _hydrate_document(row: RowMapping) -> Document:
    return Document(
        id=row["id"],
        workspace_id=row["workspace_id"],
        file_id=row["file_id"],
        status=IndexStatus(row["status"]),
        chunk_count=row["chunk_count"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
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
    if sqlstate == "42501":
        return AppError("knowledge write rejected by row-level security", code="common.internal")
    return AppError(
        "unexpected database error while persisting knowledge data", code="common.internal"
    )
