"""SQL adapter for ``FileRepository`` (02-port-contracts §2; 01-data-model
§2.6; ``migrations/versions/files/0001_files.py``).

Declares its own local Core ``Table`` against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly in every method below. ``list``/``count`` additionally filter
``deleted_at IS NULL`` (only active files, backed by the partial index
``ix_files_ws``, 01 §2.6) — ``get`` does not, so a caller that already holds
a ``File`` id can still re-``get`` it after a soft-delete (idempotent
``SoftDeleteFile``, media/workspace precedent).

The inbound port ``FilesQuery.get_readable`` (``ports/inbound.py``) is
already implemented in the application layer by ``FilesQueryService`` (over
this SAME ``FileRepository`` — see
``modules/files/application/use_cases.py``); there is nothing further to
wire here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    cast,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.types import Uuid as UuidStr
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import (
    ContentType,
    FileName,
    FileStatus,
    Sha256,
    StorageKey,
)

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

files = Table(
    "files",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("name", Text, nullable=False),
    Column("content_type", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("storage_key", Text, nullable=False),
    Column("checksum", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("uploaded_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("deleted_at", _timestamptz, nullable=True),
    Column("version", Integer, nullable=False),
    # `migrations/versions/files/0002_file_space.py` (§3.142), written by
    # `add` and filtered on by `list`/`bytes_in_space` since plan step 6.
    # Still NULLable until plan row 8-b, because a writer that has no space to
    # name yet (the media worker) must be able to say so rather than invent
    # one; 8-b's `SET NOT NULL` is what finds any writer step 6-8 forgot.
    Column("space_id", _uuid_col, nullable=True),
    schema="files",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlFileRepository:
    """SQL ``FileRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — there is no cross-call unit of work yet.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(self, ctx: ExecutionContext, file_id: UuidStr) -> File | None:
        stmt = select(files).where(files.c.id == file_id, files.c.workspace_id == ctx.workspace_id)
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate(row)

    async def add(self, ctx: ExecutionContext, file: File) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched file.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's
        # tenant (media precedent).
        stmt = insert(files).values(
            id=file.id,
            workspace_id=file.workspace_id,
            space_id=file.space_id,
            name=file.name.value,
            content_type=file.content_type.value,
            size_bytes=file.size_bytes,
            storage_key=file.storage_key.value,
            checksum=file.checksum.value if file.checksum else None,
            status=file.status.value,
            uploaded_by=file.uploaded_by,
            created_at=file.created_at,
            updated_at=file.updated_at,
            deleted_at=file.deleted_at,
            version=file.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def save(self, ctx: ExecutionContext, file: File) -> None:
        # Optimistic lock: only a row still at `file.version` is updated.
        # `content_type`/`size_bytes`/`storage_key`/`uploaded_by`/`space_id`
        # never change after creation (no domain mutator touches them), and
        # leaving them out of the UPDATE is what enforces that at the adapter
        # -- for `space_id` that is decision 3, "no moving a file between
        # spaces", made unwritable rather than merely undocumented. Only the
        # fields `mark_scanning`/`complete`/`quarantine`/`soft_delete`/
        # `rename` can change are written. `name` joined them in BE-RAG-006:
        # for every OTHER caller it re-writes the value just hydrated, a
        # provable no-op, which is why one `save` still serves them all.
        stmt = (
            update(files)
            .where(
                files.c.id == file.id,
                files.c.workspace_id == ctx.workspace_id,
                files.c.version == file.version,
            )
            .values(
                name=file.name.value,
                status=file.status.value,
                checksum=file.checksum.value if file.checksum else None,
                deleted_at=file.deleted_at,
                version=files.c.version + 1,
            )
            .returning(files.c.version, files.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(f"file {file.id} was modified concurrently (stale version)")
        # `version`/`updated_at` are repository-owned (02-port-contracts §2)
        # -- write the fresh values back onto the aggregate, media precedent.
        file.version, file.updated_at = row

    async def list(
        self, ctx: ExecutionContext, *, space_id: UuidStr | None, limit: int, cursor: str | None
    ) -> Page[File]:
        # Active files only (`ListFiles`'s own docstring) -- backed by the
        # partial index `ix_files_ws` (01 §2.6).
        # NEWEST FIRST (`framework/pagination`, 6.3-ب): `id DESC` with an
        # `id <` predicate. UUIDv7 is time-ordered, so this is "most recently
        # registered first" — what a client opening a file list is asking
        # for, and the direction every other collection already documented.
        conditions = [files.c.workspace_id == ctx.workspace_id, files.c.deleted_at.is_(None)]
        if space_id is not None:
            # The space narrowing (step 6) -- backed by `ix_files_space`
            # (`files/0002_file_space.py`), partial on `deleted_at IS NULL`
            # exactly like this predicate. Appended as an EQUALITY on an
            # opaque id: this adapter never joins to `spaces.spaces`, which it
            # cannot see, and the workspace condition above stays in place --
            # a space is an axis inside the tenant, never a replacement for it.
            conditions.append(files.c.space_id == space_id)
        if cursor is not None:
            conditions.append(files.c.id < decode_id_cursor(cursor))
        stmt = select(files).where(*conditions).order_by(files.c.id.desc()).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate(rows, limit, _hydrate)

    async def count(self, ctx: ExecutionContext) -> int:
        # Active files only (port docstring: "counts only active
        # (non-deleted) files") -- backs the per-workspace file cap.
        stmt = (
            select(func.count())
            .select_from(files)
            .where(files.c.workspace_id == ctx.workspace_id, files.c.deleted_at.is_(None))
        )
        try:
            async with self._tenant_session(ctx) as session:
                count = (await session.execute(stmt)).scalar_one()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return count

    async def bytes_in_space(self, ctx: ExecutionContext, space_id: UuidStr) -> int:
        # Active files only -- the port's docstring says why (a deleted file
        # must give its bytes back before the purge sweep runs).
        #
        # The CAST is not decoration. `size_bytes` is `BIGINT`, and PostgreSQL
        # types `sum(bigint)` as `numeric`, so asyncpg hands back a
        # `decimal.Decimal` -- measured on the live database (§3.143), not
        # inferred. A port that promises `int` and returns `Decimal` type-checks
        # green and then leaks a `Decimal` into every arithmetic and JSON path
        # downstream, so the conversion happens HERE, in SQL, where the column
        # it can never overflow is stated. `count(*)` needs none of this:
        # `count` is already `bigint`.
        stmt = select(cast(func.coalesce(func.sum(files.c.size_bytes), 0), BigInteger)).where(
            files.c.workspace_id == ctx.workspace_id,
            files.c.space_id == space_id,
            files.c.deleted_at.is_(None),
        )
        try:
            async with self._tenant_session(ctx) as session:
                total = (await session.execute(stmt)).scalar_one()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return total

    async def storage_keys_in_space(
        self, ctx: ExecutionContext, space_id: UuidStr
    ) -> Sequence[str]:
        # NO `deleted_at` predicate, and the port says why: a soft-deleted
        # file's object is still in MinIO. This is the one read in this
        # adapter that deliberately looks past the tombstone.
        stmt = select(files.c.storage_key).where(
            files.c.workspace_id == ctx.workspace_id,
            files.c.space_id == space_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).scalars().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        # A registered-but-never-completed file still has a key (it is minted
        # with the row, `RegisterUpload`), so the guard is defensive rather
        # than expected -- and an empty string handed to `storage.delete`
        # would be a request to delete the bucket root.
        return [key for key in rows if key]

    async def purge_space(self, ctx: ExecutionContext, space_id: UuidStr) -> int:
        # The module's only hard delete (port docstring). Two-layer isolation
        # as everywhere else: the RLS GUC is Layer 1, the explicit
        # `workspace_id` predicate here is Layer 2 -- and on a DELETE this
        # wide, Layer 2 is what stands between one space and a whole tenant.
        #
        # `RETURNING id` rather than a driver `rowcount`: the count is part of
        # this port's contract, and `rowcount` is a DBAPI attribute typed
        # `Any` -- a number the type checker cannot vouch for is exactly the
        # kind this platform refuses to let into a signature (`bytes_in_space`
        # made the same call about `sum()`).
        stmt = (
            delete(files)
            .where(
                files.c.workspace_id == ctx.workspace_id,
                files.c.space_id == space_id,
            )
            .returning(files.c.id)
        )
        try:
            async with self._tenant_session(ctx) as session:
                deleted = (await session.execute(stmt)).scalars().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return len(deleted)


def _paginate[T](
    rows: Sequence[RowMapping], limit: int, hydrate: Callable[[RowMapping], T]
) -> Page[T]:
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = encode_id_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
    return Page(data=[hydrate(row) for row in page_rows], next_cursor=next_cursor, limit=limit)


def _hydrate(row: RowMapping) -> File:
    return File(
        id=row["id"],
        workspace_id=row["workspace_id"],
        space_id=row["space_id"],
        name=FileName(row["name"]),
        content_type=ContentType(row["content_type"]),
        size_bytes=row["size_bytes"],
        storage_key=StorageKey(row["storage_key"]),
        checksum=Sha256(row["checksum"]) if row["checksum"] else None,
        status=FileStatus(row["status"]),
        uploaded_by=row["uploaded_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
        version=row["version"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race: a duplicate
    ``id`` or ``storage_key`` on ``add`` -- ``ConflictError`` (409,
    ``common.conflict``).
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``file.workspace_id`` on
    ``add``) -- an internal/500-class error (``common.internal``): a
    well-behaved caller can never trigger this, so it is not a normal 4xx.
    Anything else is an unexpected database failure, folded into the same
    500-class error rather than leaking the driver exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("file write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError("file write rejected by row-level security", code="common.internal")
    return AppError("unexpected database error while persisting a file", code="common.internal")
