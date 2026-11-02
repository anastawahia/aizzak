"""SQL adapters for the drawings repositories (LLD 3.3.7, 4.3; E11).

Implements, over the seven ``electrical.drawings_*`` tables of
``migrations/versions/electrical/0001_electrical_drawings.py``, the six repository ports of
``drawings/ports/repository.py`` -- projects, profile versions, documents, jobs, pages and
characterizations -- plus ``DrawingsSweep``. Each class takes the injected tenant-session
provider; the composition root builds it (this module never imports ``app.infrastructure``).

Tables are declared on a module-local ``MetaData`` (R9). Two-layer isolation (DD-04): Layer 1 is
the RLS GUC the provider sets as the first statement of every transaction; Layer 2 is the
explicit ``workspace_id = ctx.workspace_id`` predicate in every statement below. The space and the
project are filters in the ``WHERE``, never part of RLS (LLD 4.5).

**Unique violations are translated by the name of the index that fired** (LLD 3.3.7):
``ux_drawings_projects_space`` -> ``electrical.project_exists``, ``ux_drawings_documents_file``
-> ``electrical.document_exists``, ``ux_drawings_documents_content`` ->
``electrical.duplicate_document``, ``ux_drawings_jobs_open`` -> ``electrical.job_conflict``;
anything else is ``common.conflict``. No read-then-insert: the indexes are the whole defence, so
two racing requests are decided by the database.

Optimistic ``save`` (project, document, job): the ``UPDATE`` matches ``version`` and bumps it;
``updated_at`` comes back from ``trg_touch``. Fields that never change after ``add`` (ids,
``workspace_id``, ``space_id``, ``project_id``, ``file_id``, ``kind``, ``pages``,
``created_*``) are left out of the ``UPDATE``, which is what enforces it at the adapter.

**The exclusion reason follows the status.** ``ck_drawings_documents_exclusion`` says
``(status = 'excluded') = (exclusion_reason IS NOT NULL)``; the domain keeps the two together
(``Document.exclude`` sets both, ``mark_purging`` clears the reason), so ``save`` writes the
aggregate as it is and only asserts the pairing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    and_,
    delete,
    extract,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.pagination import (
    Page,
    decode_id_cursor,
    decode_seq_cursor,
    encode_id_cursor,
    encode_seq_cursor,
    is_uuid_text,
)
from app.framework.types import Uuid as UuidStr
from app.modules.electrical.drawings.domain.characterization import Characterization
from app.modules.electrical.drawings.domain.document import (
    Document,
    DocumentStatus,
    ExclusionReason,
    ProfileState,
)
from app.modules.electrical.drawings.domain.errors import DOCUMENT_EXISTS_DETAIL
from app.modules.electrical.drawings.domain.job import IngestionJob, JobKind, JobStatus
from app.modules.electrical.drawings.domain.pages import (
    FontSignal,
    PageClassification,
    PageEvidence,
    PageKind,
    PageLanguage,
    PageResult,
    PageRow,
    PageSignals,
    StageReason,
    StageStatus,
)
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION, Stage
from app.modules.electrical.drawings.domain.profile import (
    DrawingProfile,
    ProfileSource,
    ProfileVersion,
    SymbolStandard,
)
from app.modules.electrical.drawings.domain.project import Project
from app.modules.electrical.shared.kinds import Convention, ConventionTally, PlcFamily

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)
_SCHEMA: Final = "electrical"

drawings_projects = Table(
    "drawings_projects",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("space_id", _uuid_col, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=True),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema=_SCHEMA,
)

drawings_documents = Table(
    "drawings_documents",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("space_id", _uuid_col, nullable=False),
    Column("project_id", _uuid_col, nullable=False),
    Column("file_id", _uuid_col, nullable=False),
    Column("status", Text, nullable=False),
    Column("exclusion_reason", Text, nullable=True),
    Column("content_sha256", Text, nullable=True),
    Column("page_count", Integer, nullable=True),
    Column("pdf_creator", Text, nullable=True),
    Column("pdf_producer", Text, nullable=True),
    Column("profile_state", Text, nullable=False),
    Column("pipeline_version", Text, nullable=True),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("purging_at", _timestamptz, nullable=True),
    Column("version", Integer, nullable=False),
    schema=_SCHEMA,
)

drawings_jobs = Table(
    "drawings_jobs",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column(
        "document_id", _uuid_col, ForeignKey("electrical.drawings_documents.id"), nullable=False
    ),
    Column("kind", Text, nullable=False),
    Column("pages", ARRAY(Integer), nullable=True),
    Column("status", Text, nullable=False),
    Column("stage", Text, nullable=True),
    Column("failure_code", Text, nullable=True),
    Column("failure_detail", Text, nullable=True),
    Column("attempt", Integer, nullable=False),
    Column("lease_until", _timestamptz, nullable=True),
    Column("seq", BigInteger, nullable=False),
    Column("cancel_requested_at", _timestamptz, nullable=True),
    Column("pipeline_version", Text, nullable=False),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("started_at", _timestamptz, nullable=True),
    Column("finished_at", _timestamptz, nullable=True),
    Column("version", Integer, nullable=False),
    schema=_SCHEMA,
)

drawings_pages = Table(
    "drawings_pages",
    _metadata,
    Column("workspace_id", _uuid_col, primary_key=True),
    Column("document_id", _uuid_col, primary_key=True),
    Column("page_no", Integer, primary_key=True),
    Column("kind", Text, nullable=True),
    Column("language", Text, nullable=True),
    Column("needs_ocr", Boolean, nullable=True),
    Column("signals", JSONB, nullable=True),
    Column("updated_at", _timestamptz, nullable=False),
    schema=_SCHEMA,
)

drawings_page_stages = Table(
    "drawings_page_stages",
    _metadata,
    Column("workspace_id", _uuid_col, primary_key=True),
    Column("document_id", _uuid_col, primary_key=True),
    Column("page_no", Integer, primary_key=True),
    Column("stage", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("reason", Text, nullable=True),
    Column("attempts", Integer, nullable=False),
    Column("pipeline_version", Text, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    schema=_SCHEMA,
)

drawings_profile_versions = Table(
    "drawings_profile_versions",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("project_id", _uuid_col, nullable=False),
    Column("document_id", _uuid_col, nullable=True),
    Column("version", Integer, nullable=False),
    Column("source", Text, nullable=False),
    Column("body", JSONB, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("confidence", Float(24), nullable=True),
    Column("mixed_standards", Boolean, nullable=False),
    Column("detection", JSONB, nullable=True),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    schema=_SCHEMA,
)

drawings_characterizations = Table(
    "drawings_characterizations",
    _metadata,
    Column("workspace_id", _uuid_col, primary_key=True),
    Column("document_id", _uuid_col, primary_key=True),
    Column("project_id", _uuid_col, nullable=False),
    Column("report", JSONB, nullable=False),
    Column("pipeline_version", Text, nullable=False),
    Column("shared_version", Text, nullable=False),
    Column("rules_version", Text, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema=_SCHEMA,
)

# A request-scoped session-provider seam (structurally satisfies ``TenantSessionFactory``).
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]

_OPEN_JOB: Final = (
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.AWAITING_CONFIRMATION.value,
)
_UNFINISHED_STAGE: Final = (StageStatus.PENDING.value, StageStatus.RUNNING.value)


# --------------------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------------------
class _Base:
    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session


def _paginate_id[T](
    rows: Sequence[RowMapping], limit: int, hydrate: Callable[[RowMapping], T]
) -> Page[T]:
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = encode_id_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
    return Page(data=[hydrate(row) for row in page_rows], next_cursor=next_cursor, limit=limit)


_SIGNALS_BUDGET_BYTES: Final = 15_000  # under the CHECK's 16,384, jsonb text adds a little
_FONT_TYPE_CHARS: Final = 32
_INT4_MAX: Final = 2**31 - 1  # page_no is an int4
SEED_CHUNK_PAGES: Final = 1_000  # 7 parameters a row: far under the driver's 32,767


def _is_uuid(value: str) -> bool:
    """Whether ``value`` can be compared with a ``uuid`` column. A client may send anything as an
    id; one that is not a UUID names nothing, so the lookup answers "not found" (as for an
    unknown id) instead of letting the driver refuse the parameter (a 500). The check is the
    driver's own strictness (canonical ASCII hex), not ``uuid.UUID()``'s."""
    return is_uuid_text(value)


def _unique_violation(text: str) -> ConflictError:
    """A ``23505`` named by the index that fired. Literal codes, one branch per index:
    ``test_error_catalog`` finds the raise sites by source."""
    if "ux_drawings_projects_space" in text:
        return ConflictError(
            "The space already has a WireTwin project", code="electrical.project_exists"
        )
    if "ux_drawings_documents_file" in text:
        return ConflictError(DOCUMENT_EXISTS_DETAIL, code="electrical.document_exists")
    if "ux_drawings_documents_content" in text:
        return ConflictError(
            "The same content is already a document of this project",
            code="electrical.duplicate_document",
        )
    if "ux_drawings_jobs_open" in text:
        return ConflictError(
            "Ingestion job is not in a state that allows this", code="electrical.job_conflict"
        )
    return ConflictError("a drawings write lost a uniqueness race")


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver failure onto the framework hierarchy; no driver type escapes.

    ``23505`` is named by its index (``_unique_violation``). ``42501`` is the RLS ``WITH CHECK``
    refusing a forged tenant -- a 500-class error, a well-behaved caller cannot trigger it.
    Anything else is an unexpected database failure.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return _unique_violation(str(exc.orig))
    if sqlstate == "42501":
        return AppError("drawings write rejected by row-level security", code="common.internal")
    return AppError("unexpected database error while persisting drawings", code="common.internal")


def _stale(what: str, ident: str) -> ConflictError:
    return ConflictError(f"{what} {ident} was modified concurrently (stale version)")


# --------------------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------------------
def _project(row: RowMapping) -> Project:
    return Project(
        id=row["id"],
        workspace_id=row["workspace_id"],
        space_id=row["space_id"],
        name=row["name"],
        description=row["description"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


class SqlProjectRepository(_Base):
    async def get(self, ctx: ExecutionContext, project_id: UuidStr) -> Project | None:
        if not _is_uuid(project_id):
            return None
        t = drawings_projects
        stmt = select(t).where(t.c.id == project_id, t.c.workspace_id == ctx.workspace_id)
        return await self._one(ctx, stmt)

    async def get_by_space(self, ctx: ExecutionContext, space_id: UuidStr) -> Project | None:
        if not _is_uuid(space_id):
            return None
        t = drawings_projects
        stmt = select(t).where(t.c.workspace_id == ctx.workspace_id, t.c.space_id == space_id)
        return await self._one(ctx, stmt)

    async def add(self, ctx: ExecutionContext, project: Project) -> None:
        # The aggregate's OWN workspace_id is written, so a forged one meets the RLS
        # WITH CHECK instead of being persisted under ctx's tenant.
        stmt = insert(drawings_projects).values(
            id=project.id,
            workspace_id=project.workspace_id,
            space_id=project.space_id,
            name=project.name,
            description=project.description,
            created_by=project.created_by,
            created_at=project.created_at,
            updated_at=project.updated_at,
            version=project.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def save(self, ctx: ExecutionContext, project: Project) -> None:
        t = drawings_projects
        stmt = (
            update(t)
            .where(
                t.c.id == project.id,
                t.c.workspace_id == ctx.workspace_id,
                t.c.version == project.version,
            )
            .values(name=project.name, description=project.description, version=t.c.version + 1)
            .returning(t.c.version, t.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise _stale("project", project.id)
        project.version, project.updated_at = row

    async def list_by_space(
        self, ctx: ExecutionContext, space_id: UuidStr, *, limit: int, cursor: str | None
    ) -> Page[Project]:
        if not _is_uuid(space_id):
            return Page(data=[], next_cursor=None, limit=limit)
        t = drawings_projects
        conditions = [t.c.workspace_id == ctx.workspace_id, t.c.space_id == space_id]
        if cursor is not None:
            conditions.append(t.c.id < decode_id_cursor(cursor))
        stmt = select(t).where(*conditions).order_by(t.c.id.desc()).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate_id(rows, limit, _project)

    async def delete_in_space(self, ctx: ExecutionContext, space_id: UuidStr) -> int:
        """The space's project and the PROJECT's profile versions (``document_id IS NULL``), in
        one transaction. A document's own versions go with the document (``DrawingsSweep``)."""
        t, v = drawings_projects, drawings_profile_versions
        ws = ctx.workspace_id
        project_ids = select(t.c.id).where(t.c.workspace_id == ws, t.c.space_id == space_id)
        versions = delete(v).where(
            v.c.workspace_id == ws, v.c.document_id.is_(None), v.c.project_id.in_(project_ids)
        )
        stmt = delete(t).where(t.c.workspace_id == ws, t.c.space_id == space_id).returning(t.c.id)
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(versions)
                return len((await session.execute(stmt)).all())
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def _one(self, ctx: ExecutionContext, stmt: Any) -> Project | None:
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _project(row)


# --------------------------------------------------------------------------------------
# profile versions
# --------------------------------------------------------------------------------------
def _profile_version(row: RowMapping) -> ProfileVersion:
    body: Mapping[str, Any] = row["body"]
    # The body was validated against the live rule names when it was written; reading it back
    # trusts its own pattern names (the adapter has no rule source, and must not need one).
    known = frozenset(str(name) for name in body.get("wire_number_patterns", ()))
    confidence = row["confidence"]
    return ProfileVersion(
        id=row["id"],
        workspace_id=row["workspace_id"],
        project_id=row["project_id"],
        document_id=row["document_id"],
        version=row["version"],
        source=ProfileSource(row["source"]),
        profile=DrawingProfile.from_body(body, known_patterns=known),
        fingerprint=row["fingerprint"],
        # `real` is float4: 0.9 comes back as 0.8999999761...; four decimals is the contract.
        confidence=None if confidence is None else round(float(confidence), 4),
        mixed_standards=row["mixed_standards"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        detection=row["detection"],
    )


class SqlProfileRepository(_Base):
    async def latest(
        self, ctx: ExecutionContext, *, project_id: UuidStr, document_id: UuidStr | None
    ) -> ProfileVersion | None:
        t = drawings_profile_versions
        stmt = (
            select(t)
            .where(
                t.c.workspace_id == ctx.workspace_id,
                t.c.project_id == project_id,
                _owner(document_id),
            )
            .order_by(t.c.version.desc())
            .limit(1)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _profile_version(row)

    async def append(self, ctx: ExecutionContext, version: ProfileVersion) -> ProfileVersion:
        """``version = max + 1`` computed inside the INSERT; two racers compute the same number
        and the owner's partial unique index refuses the second (a ConflictError: the caller
        retries once)."""
        t = drawings_profile_versions
        next_version = (
            select(func.coalesce(func.max(t.c.version), 0) + 1)
            .where(
                t.c.workspace_id == version.workspace_id,
                t.c.project_id == version.project_id,
                _owner(version.document_id),
            )
            .scalar_subquery()
        )
        stmt = (
            insert(t)
            .values(
                id=version.id,
                workspace_id=version.workspace_id,
                project_id=version.project_id,
                document_id=version.document_id,
                version=next_version,
                source=version.source.value,
                body=version.profile.to_body(),
                fingerprint=version.fingerprint,
                confidence=version.confidence,
                mixed_standards=version.mixed_standards,
                detection=version.detection,
                created_by=version.created_by,
                created_at=version.created_at,
            )
            .returning(t.c.version)
        )
        try:
            async with self._tenant_session(ctx) as session:
                stored = (await session.execute(stmt)).scalar_one()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return replace(version, version=int(stored))


def _owner(document_id: UuidStr | None) -> ColumnElement[bool]:
    """The version series' owner: a document, or (``None``) the project itself."""
    column = drawings_profile_versions.c.document_id
    return column.is_(None) if document_id is None else column == document_id


# --------------------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------------------
def _document(row: RowMapping) -> Document:
    reason = row["exclusion_reason"]
    return Document(
        id=row["id"],
        workspace_id=row["workspace_id"],
        space_id=row["space_id"],
        project_id=row["project_id"],
        file_id=row["file_id"],
        status=DocumentStatus(row["status"]),
        exclusion_reason=None if reason is None else ExclusionReason(reason),
        content_sha256=row["content_sha256"],
        page_count=row["page_count"],
        pdf_creator=row["pdf_creator"],
        pdf_producer=row["pdf_producer"],
        profile_state=ProfileState(row["profile_state"]),
        pipeline_version=row["pipeline_version"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        purging_at=row["purging_at"],
        version=row["version"],
    )


def _stored_reason(document: Document) -> str | None:
    """The reason as stored. The domain keeps it paired with EXCLUDED (see the module
    docstring); the assertion names a violation before the CHECK does."""
    assert (document.status is DocumentStatus.EXCLUDED) == (document.exclusion_reason is not None)
    return None if document.exclusion_reason is None else document.exclusion_reason.value


class SqlDocumentRepository(_Base):
    async def get(self, ctx: ExecutionContext, document_id: UuidStr) -> Document | None:
        if not _is_uuid(document_id):
            return None
        t = drawings_documents
        stmt = select(t).where(t.c.id == document_id, t.c.workspace_id == ctx.workspace_id)
        rows = await self._all(ctx, stmt)
        return rows[0] if rows else None

    async def lock_live(self, ctx: ExecutionContext, document_id: UuidStr) -> Document | None:
        """``SELECT ... FOR SHARE`` of an ACTIVE document, inside the caller's unit of work: a
        purge's ``UPDATE ... status = 'purging'`` then waits for the writer to commit."""
        if not _is_uuid(document_id):
            return None
        t = drawings_documents
        stmt = (
            select(t)
            .where(
                t.c.id == document_id,
                t.c.workspace_id == ctx.workspace_id,
                t.c.status == DocumentStatus.ACTIVE.value,
            )
            .with_for_update(read=True)
        )
        rows = await self._all(ctx, stmt)
        return rows[0] if rows else None

    async def not_purging_for_file(
        self, ctx: ExecutionContext, file_id: UuidStr
    ) -> Sequence[Document]:
        t = drawings_documents
        stmt = (
            select(t)
            .where(
                t.c.workspace_id == ctx.workspace_id,
                t.c.file_id == file_id,
                t.c.status != DocumentStatus.PURGING.value,
            )
            .order_by(t.c.id)
        )
        return await self._all(ctx, stmt)

    async def active_files(
        self, ctx: ExecutionContext, project_id: UuidStr
    ) -> Mapping[UuidStr, UuidStr]:
        t = drawings_documents
        stmt = select(t.c.id, t.c.file_id).where(
            t.c.workspace_id == ctx.workspace_id,
            t.c.project_id == project_id,
            t.c.status == DocumentStatus.ACTIVE.value,
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return {row[0]: row[1] for row in rows}

    async def active_with_content(
        self, ctx: ExecutionContext, project_id: UuidStr, sha256: str
    ) -> Document | None:
        t = drawings_documents
        stmt = select(t).where(
            t.c.workspace_id == ctx.workspace_id,
            t.c.project_id == project_id,
            t.c.status == DocumentStatus.ACTIVE.value,
            t.c.content_sha256 == sha256,
        )
        rows = await self._all(ctx, stmt)
        return rows[0] if rows else None

    async def add(self, ctx: ExecutionContext, document: Document) -> None:
        stmt = insert(drawings_documents).values(
            id=document.id,
            workspace_id=document.workspace_id,
            space_id=document.space_id,
            project_id=document.project_id,
            file_id=document.file_id,
            status=document.status.value,
            exclusion_reason=_stored_reason(document),
            content_sha256=document.content_sha256,
            page_count=document.page_count,
            pdf_creator=document.pdf_creator,
            pdf_producer=document.pdf_producer,
            profile_state=document.profile_state.value,
            pipeline_version=document.pipeline_version,
            created_by=document.created_by,
            created_at=document.created_at,
            updated_at=document.updated_at,
            purging_at=document.purging_at,
            version=document.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def save(self, ctx: ExecutionContext, document: Document) -> None:
        t = drawings_documents
        stmt = (
            update(t)
            .where(
                t.c.id == document.id,
                t.c.workspace_id == ctx.workspace_id,
                t.c.version == document.version,
            )
            .values(
                status=document.status.value,
                exclusion_reason=_stored_reason(document),
                content_sha256=document.content_sha256,
                page_count=document.page_count,
                pdf_creator=document.pdf_creator,
                pdf_producer=document.pdf_producer,
                profile_state=document.profile_state.value,
                pipeline_version=document.pipeline_version,
                purging_at=document.purging_at,
                version=t.c.version + 1,
            )
            .returning(t.c.version, t.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise _stale("document", document.id)
        document.version, document.updated_at = row

    async def list_by_project(
        self,
        ctx: ExecutionContext,
        project_id: UuidStr,
        *,
        status: DocumentStatus | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Document]:
        t = drawings_documents
        conditions = [t.c.workspace_id == ctx.workspace_id, t.c.project_id == project_id]
        if status is not None:
            conditions.append(t.c.status == status.value)
        if cursor is not None:
            conditions.append(t.c.id < decode_id_cursor(cursor))
        stmt = select(t).where(*conditions).order_by(t.c.id.desc()).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate_id(rows, limit, _document)

    async def _all(self, ctx: ExecutionContext, stmt: Any) -> list[Document]:
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_document(row) for row in rows]


# --------------------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------------------
def _job(row: RowMapping) -> IngestionJob:
    pages = row["pages"]
    stage = row["stage"]
    return IngestionJob(
        id=row["id"],
        workspace_id=row["workspace_id"],
        document_id=row["document_id"],
        kind=JobKind(row["kind"]),
        pages=None if pages is None else tuple(pages),
        status=JobStatus(row["status"]),
        stage=None if stage is None else Stage(stage),
        failure_code=row["failure_code"],
        failure_detail=row["failure_detail"],
        attempt=row["attempt"],
        lease_until=row["lease_until"],
        seq=row["seq"],
        cancel_requested_at=row["cancel_requested_at"],
        pipeline_version=row["pipeline_version"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        version=row["version"],
    )


class SqlJobRepository(_Base):
    async def get(self, ctx: ExecutionContext, job_id: UuidStr) -> IngestionJob | None:
        if not _is_uuid(job_id):
            return None
        t = drawings_jobs
        stmt = select(t).where(t.c.id == job_id, t.c.workspace_id == ctx.workspace_id)
        return await self._one(ctx, stmt)

    async def add(self, ctx: ExecutionContext, job: IngestionJob) -> None:
        stmt = insert(drawings_jobs).values(
            id=job.id,
            workspace_id=job.workspace_id,
            document_id=job.document_id,
            kind=job.kind.value,
            pages=None if job.pages is None else list(job.pages),
            status=job.status.value,
            stage=None if job.stage is None else job.stage.value,
            failure_code=job.failure_code,
            failure_detail=job.failure_detail,
            attempt=job.attempt,
            lease_until=job.lease_until,
            seq=job.seq,
            cancel_requested_at=job.cancel_requested_at,
            pipeline_version=job.pipeline_version,
            created_by=job.created_by,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            version=job.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def save(self, ctx: ExecutionContext, job: IngestionJob) -> None:
        t = drawings_jobs
        stmt = (
            update(t)
            .where(
                t.c.id == job.id,
                t.c.workspace_id == ctx.workspace_id,
                t.c.version == job.version,
            )
            .values(
                status=job.status.value,
                stage=None if job.stage is None else job.stage.value,
                failure_code=job.failure_code,
                failure_detail=job.failure_detail,
                attempt=job.attempt,
                lease_until=job.lease_until,
                seq=job.seq,
                cancel_requested_at=job.cancel_requested_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
                version=t.c.version + 1,
            )
            .returning(t.c.version, t.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise _stale("job", job.id)
        job.version, job.updated_at = row

    async def open_for_document(
        self, ctx: ExecutionContext, document_id: UuidStr
    ) -> IngestionJob | None:
        t = drawings_jobs
        stmt = select(t).where(
            t.c.workspace_id == ctx.workspace_id,
            t.c.document_id == document_id,
            t.c.status.in_(_OPEN_JOB),
        )
        return await self._one(ctx, stmt)

    async def latest_for_document(
        self, ctx: ExecutionContext, document_id: UuidStr
    ) -> IngestionJob | None:
        t = drawings_jobs
        stmt = (
            select(t)
            .where(t.c.workspace_id == ctx.workspace_id, t.c.document_id == document_id)
            .order_by(t.c.created_at.desc(), t.c.id.desc())
            .limit(1)
        )
        return await self._one(ctx, stmt)

    async def open_count(self, ctx: ExecutionContext) -> int:
        t = drawings_jobs
        stmt = (
            select(func.count())
            .select_from(t)
            .where(t.c.workspace_id == ctx.workspace_id, t.c.status.in_(_OPEN_JOB))
        )
        return await self._scalar_int(ctx, stmt)

    async def queue_position(self, ctx: ExecutionContext, job: IngestionJob) -> int:
        """1 + the QUEUED jobs of the workspace created before this one (``ix_drawings_jobs_
        queue``); ties on ``created_at`` fall back to the id, as the fake does."""
        t = drawings_jobs
        stmt = (
            select(func.count())
            .select_from(t)
            .where(
                t.c.workspace_id == ctx.workspace_id,
                t.c.status == JobStatus.QUEUED.value,
                or_(
                    t.c.created_at < job.created_at,
                    and_(t.c.created_at == job.created_at, t.c.id < job.id),
                ),
            )
        )
        return await self._scalar_int(ctx, stmt) + 1

    async def recent_run_seconds(self, ctx: ExecutionContext, *, limit: int) -> Sequence[float]:
        t = drawings_jobs
        stmt = (
            select(extract("epoch", t.c.finished_at - t.c.started_at))
            .where(
                t.c.workspace_id == ctx.workspace_id,
                t.c.status == JobStatus.SUCCEEDED.value,
                t.c.started_at.is_not(None),
                t.c.finished_at.is_not(None),
            )
            .order_by(t.c.finished_at.desc())
            .limit(limit)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [float(seconds) for (seconds,) in rows]

    async def _one(self, ctx: ExecutionContext, stmt: Any) -> IngestionJob | None:
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _job(row)

    async def _scalar_int(self, ctx: ExecutionContext, stmt: Any) -> int:
        try:
            async with self._tenant_session(ctx) as session:
                return int((await session.execute(stmt)).scalar_one())
        except DBAPIError as exc:
            raise _translate(exc) from exc


# --------------------------------------------------------------------------------------
# pages and their stages
# --------------------------------------------------------------------------------------
def _abridged_signals(signals: PageSignals) -> dict[str, Any]:
    """What is worth keeping of a page's signals (<= 16 KiB by CHECK): the numbers, the fonts
    (<= 32) and nothing of the token sample, which can echo drawing text."""
    return {
        "width_pt": signals.width_pt,
        "height_pt": signals.height_pt,
        "rotation": signals.rotation,
        "char_count": signals.char_count,
        "unmapped_char_count": signals.unmapped_char_count,
        "pua_char_count": signals.pua_char_count,
        "cjk_char_count": signals.cjk_char_count,
        "latin_char_count": signals.latin_char_count,
        "word_count": signals.word_count,
        "vector_path_count": signals.vector_path_count,
        "short_curve_path_count": signals.short_curve_path_count,
        "image_count": signals.image_count,
        "image_area_ratio": signals.image_area_ratio,
        "link_count": signals.link_count,
        "fonts": [
            {
                "name": font.name[:96],
                "font_type": font.font_type[:_FONT_TYPE_CHARS],
                "cid": font.cid,
                "embedded": font.embedded,
                "has_tounicode": font.has_tounicode,
            }
            for font in signals.fonts[:32]
        ],
    }


def _tally_json(tally: ConventionTally) -> dict[str, Any]:
    return {
        "conventions": {c.value: n for c, n in tally.conventions.items()},
        "plc_families": {f.value: n for f, n in tally.plc_families.items()},
        "cross_reference_formats": dict(tally.cross_reference_formats),
        "identifiers": tally.identifiers,
        "tokens": tally.tokens,
    }


def _tally(stored: Mapping[str, Any]) -> ConventionTally:
    return ConventionTally(
        conventions=_by_enum(stored.get("conventions", {}), Convention),
        plc_families=_by_enum(stored.get("plc_families", {}), PlcFamily),
        cross_reference_formats={
            key: int(n) for key, n in sorted(stored.get("cross_reference_formats", {}).items())
        },
        identifiers=int(stored.get("identifiers", 0)),
        tokens=int(stored.get("tokens", 0)),
    )


def _signals(page_no: int, stored: Mapping[str, Any]) -> PageSignals:
    """The abridged signals read back: the numbers and the fonts, no token sample."""
    return PageSignals(
        page_no=page_no,
        width_pt=float(stored["width_pt"]),
        height_pt=float(stored["height_pt"]),
        rotation=int(stored["rotation"]),
        char_count=int(stored["char_count"]),
        unmapped_char_count=int(stored["unmapped_char_count"]),
        pua_char_count=int(stored["pua_char_count"]),
        cjk_char_count=int(stored["cjk_char_count"]),
        latin_char_count=int(stored["latin_char_count"]),
        word_count=int(stored["word_count"]),
        vector_path_count=int(stored["vector_path_count"]),
        short_curve_path_count=int(stored["short_curve_path_count"]),
        image_count=int(stored["image_count"]),
        image_area_ratio=float(stored["image_area_ratio"]),
        link_count=int(stored["link_count"]),
        fonts=tuple(
            FontSignal(
                name=font["name"],
                font_type=font["font_type"],
                cid=bool(font["cid"]),
                embedded=bool(font["embedded"]),
                has_tounicode=bool(font["has_tounicode"]),
            )
            for font in stored.get("fonts", ())
        ),
        tokens=(),
    )


def _stored_signals(result: PageResult) -> dict[str, Any]:
    stored = _abridged_signals(result.signals)
    if result.tally is not None:
        stored["tally"] = _tally_json(result.tally)
    if len(json.dumps(stored, ensure_ascii=False).encode()) > _SIGNALS_BUDGET_BYTES:
        # Unreachable with the fields clipped above and by ele-parse; kept so that a page that
        # still would not fit the column's CHECK (16 KiB) costs its font table, not the batch.
        stored["fonts"] = []
    return stored


def _classification(
    page_no: int,
    kind: str,
    language: str | None,
    needs_ocr: bool | None,
    status: str,
    reason: str | None,
) -> PageClassification:
    return PageClassification(
        page_no=page_no,
        kind=PageKind(kind),
        language=PageLanguage(language or PageLanguage.NONE.value),
        needs_ocr=bool(needs_ocr),
        status=StageStatus(status),
        reason=None if reason is None else StageReason(reason),
    )


class SqlPageRepository(_Base):
    async def seed(
        self,
        ctx: ExecutionContext,
        document_id: UuidStr,
        pages: Sequence[int],
        stage: Stage,
        pipeline_version: str,
    ) -> None:
        """Pending rows; ON CONFLICT DO NOTHING, so a resume re-seeds harmlessly. The page row
        goes first: a stage row references it."""
        if not pages:
            return
        ws = ctx.workspace_id
        try:
            async with self._tenant_session(ctx) as session:
                # asyncpg refuses a statement with more than 32,767 parameters (7 per stage
                # row): a document is seeded in chunks, in the one transaction.
                for at in range(0, len(pages), SEED_CHUNK_PAGES):
                    chunk = pages[at : at + SEED_CHUNK_PAGES]
                    await session.execute(self._page_insert(ws, document_id, chunk))
                    await session.execute(
                        self._stage_insert(ws, document_id, chunk, stage, pipeline_version)
                    )
        except DBAPIError as exc:
            raise _translate(exc) from exc

    @staticmethod
    def _page_insert(ws: UuidStr, document_id: UuidStr, pages: Sequence[int]) -> Any:
        return (
            pg_insert(drawings_pages)
            .values([{"workspace_id": ws, "document_id": document_id, "page_no": n} for n in pages])
            .on_conflict_do_nothing(index_elements=["workspace_id", "document_id", "page_no"])
        )

    @staticmethod
    def _stage_insert(
        ws: UuidStr,
        document_id: UuidStr,
        pages: Sequence[int],
        stage: Stage,
        pipeline_version: str,
    ) -> Any:
        return (
            pg_insert(drawings_page_stages)
            .values(
                [
                    {
                        "workspace_id": ws,
                        "document_id": document_id,
                        "page_no": n,
                        "stage": stage.value,
                        "status": StageStatus.PENDING.value,
                        "attempts": 0,
                        "pipeline_version": pipeline_version,
                    }
                    for n in pages
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["workspace_id", "document_id", "page_no", "stage"]
            )
        )

    async def record(
        self, ctx: ExecutionContext, document_id: UuidStr, results: Sequence[PageResult]
    ) -> None:
        """Upsert each page and its CLASSIFY stage row; one transaction for the batch."""
        if not results:
            return
        ws = ctx.workspace_id
        try:
            async with self._tenant_session(ctx) as session:
                for result in results:
                    c = result.classification
                    page = pg_insert(drawings_pages).values(
                        workspace_id=ws,
                        document_id=document_id,
                        page_no=c.page_no,
                        kind=c.kind.value,
                        language=c.language.value,
                        needs_ocr=c.needs_ocr,
                        signals=_stored_signals(result),
                    )
                    await session.execute(
                        page.on_conflict_do_update(
                            index_elements=["workspace_id", "document_id", "page_no"],
                            set_={
                                "kind": page.excluded.kind,
                                "language": page.excluded.language,
                                "needs_ocr": page.excluded.needs_ocr,
                                "signals": page.excluded.signals,
                                "updated_at": func.now(),
                            },
                        )
                    )
                    stage = pg_insert(drawings_page_stages).values(
                        workspace_id=ws,
                        document_id=document_id,
                        page_no=c.page_no,
                        stage=Stage.CLASSIFY.value,
                        status=c.status.value,
                        reason=None if c.reason is None else c.reason.value,
                        attempts=result.attempts,
                        pipeline_version=PIPELINE_VERSION,
                    )
                    await session.execute(
                        stage.on_conflict_do_update(
                            index_elements=["workspace_id", "document_id", "page_no", "stage"],
                            set_={
                                "status": stage.excluded.status,
                                "reason": stage.excluded.reason,
                                "attempts": stage.excluded.attempts,
                                "pipeline_version": stage.excluded.pipeline_version,
                                "updated_at": func.now(),
                            },
                        )
                    )
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def unfinished(
        self, ctx: ExecutionContext, document_id: UuidStr, stage: Stage
    ) -> Sequence[int]:
        s = drawings_page_stages
        stmt = (
            select(s.c.page_no)
            .where(
                s.c.workspace_id == ctx.workspace_id,
                s.c.document_id == document_id,
                s.c.stage == stage.value,
                s.c.status.in_(_UNFINISHED_STAGE),
            )
            .order_by(s.c.page_no)
        )
        try:
            async with self._tenant_session(ctx) as session:
                return [n for (n,) in (await session.execute(stmt)).all()]
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def stage_counts(
        self, ctx: ExecutionContext, document_id: UuidStr, stage: Stage
    ) -> Mapping[StageStatus, int]:
        s = drawings_page_stages
        stmt = (
            select(s.c.status, func.count())
            .where(
                s.c.workspace_id == ctx.workspace_id,
                s.c.document_id == document_id,
                s.c.stage == stage.value,
            )
            .group_by(s.c.status)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return {StageStatus(status): int(count) for status, count in rows}

    async def reset(
        self,
        ctx: ExecutionContext,
        document_id: UuidStr,
        pages: Sequence[int] | None,
        stage: Stage,
    ) -> int:
        s = drawings_page_stages
        conditions = [
            s.c.workspace_id == ctx.workspace_id,
            s.c.document_id == document_id,
            s.c.stage == stage.value,
            s.c.status != StageStatus.PENDING.value,
        ]
        if pages is not None:
            conditions.append(s.c.page_no.in_(list(pages)))
        stmt = (
            update(s)
            .where(*conditions)
            .values(
                status=StageStatus.PENDING.value, reason=None, attempts=0, updated_at=func.now()
            )
            .returning(s.c.page_no)
        )
        try:
            async with self._tenant_session(ctx) as session:
                return len((await session.execute(stmt)).all())
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def evidence(self, ctx: ExecutionContext, document_id: UuidStr) -> Sequence[PageEvidence]:
        p, s = drawings_pages, drawings_page_stages
        stmt = (
            select(
                p.c.page_no,
                p.c.kind,
                p.c.language,
                p.c.needs_ocr,
                s.c.status,
                s.c.reason,
                p.c.signals,
            )
            .join(
                s,
                and_(
                    s.c.workspace_id == p.c.workspace_id,
                    s.c.document_id == p.c.document_id,
                    s.c.page_no == p.c.page_no,
                    s.c.stage == Stage.CLASSIFY.value,
                ),
            )
            .where(
                p.c.workspace_id == ctx.workspace_id,
                p.c.document_id == document_id,
                p.c.kind.is_not(None),
                p.c.signals.is_not(None),
                s.c.status.in_((StageStatus.SUCCEEDED.value, StageStatus.NEEDS_REVIEW.value)),
            )
            .order_by(p.c.page_no)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [
            PageEvidence(
                classification=_classification(*row[:6]),
                signals=_signals(row[0], row[6]),
                tally=_tally(row[6]["tally"]) if "tally" in row[6] else None,
            )
            for row in rows
        ]

    async def list(
        self, ctx: ExecutionContext, document_id: UuidStr, *, limit: int, cursor: str | None
    ) -> Page[PageRow]:
        """Pages in page order (``page_no`` keyset), each with its stages' states."""
        p, s = drawings_pages, drawings_page_stages
        conditions = [p.c.workspace_id == ctx.workspace_id, p.c.document_id == document_id]
        if cursor is not None:
            conditions.append(p.c.page_no > decode_seq_cursor(cursor, maximum=_INT4_MAX))
        page_stmt = select(p).where(*conditions).order_by(p.c.page_no).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                pages = (await session.execute(page_stmt)).mappings().all()
                shown = pages[:limit]
                stages: list[RowMapping] = []
                if shown:
                    stage_stmt = select(s).where(
                        s.c.workspace_id == ctx.workspace_id,
                        s.c.document_id == document_id,
                        s.c.page_no.in_([row["page_no"] for row in shown]),
                    )
                    stages = list((await session.execute(stage_stmt)).mappings().all())
        except DBAPIError as exc:
            raise _translate(exc) from exc
        by_page: dict[int, list[RowMapping]] = {}
        for row in stages:
            by_page.setdefault(row["page_no"], []).append(row)
        data: list[PageRow] = []
        for row in shown:
            rows = by_page.get(row["page_no"], [])
            classify = next((r for r in rows if r["stage"] == Stage.CLASSIFY.value), None)
            classification = (
                _classification(
                    row["page_no"],
                    row["kind"],
                    row["language"],
                    row["needs_ocr"],
                    classify["status"],
                    classify["reason"],
                )
                if row["kind"] is not None and classify is not None
                else None
            )
            data.append(
                PageRow(
                    page_no=row["page_no"],
                    classification=classification,
                    stages={Stage(r["stage"]): StageStatus(r["status"]) for r in rows},
                    attempts=max((r["attempts"] for r in rows), default=0),
                )
            )
        next_cursor = (
            encode_seq_cursor(shown[-1]["page_no"]) if len(pages) > limit and shown else None
        )
        return Page(data=data, next_cursor=next_cursor, limit=limit)


# --------------------------------------------------------------------------------------
# characterizations
# --------------------------------------------------------------------------------------
def _by_enum[E: StrEnum](stored: Mapping[str, int], kind: type[E]) -> dict[E, int]:
    """Counts keyed by enum, in the enum's order (jsonb does not keep key order)."""
    return {member: int(stored[member.value]) for member in kind if member.value in stored}


def _characterization_report(item: Characterization) -> dict[str, Any]:
    return {
        "content_sha256": item.content_sha256,
        "producer_family": item.producer_family,
        "creator": item.creator,
        "producer": item.producer,
        "page_count": item.page_count,
        "text_kinds": {k.value: v for k, v in item.text_kinds.items()},
        "page_languages": {k.value: v for k, v in item.page_languages.items()},
        "document_language": item.document_language,
        "conventions": {k.value: v for k, v in item.conventions.items()},
        "symbol_standard_guess": item.symbol_standard_guess.value,
        "symbol_standard_basis": list(item.symbol_standard_basis),
        "plc_families": {k.value: v for k, v in item.plc_families.items()},
        "cross_reference_formats": dict(item.cross_reference_formats),
        "ocr_needed_pages": item.ocr_needed_pages,
        "cid_fonts_without_tounicode": item.cid_fonts_without_tounicode,
        "mixed_standards": item.mixed_standards,
    }


def _characterization(row: RowMapping) -> Characterization:
    report: Mapping[str, Any] = row["report"]
    return Characterization(
        document_id=row["document_id"],
        content_sha256=report["content_sha256"],
        producer_family=report["producer_family"],
        creator=report["creator"],
        producer=report["producer"],
        page_count=report["page_count"],
        text_kinds=_by_enum(report["text_kinds"], PageKind),
        page_languages=_by_enum(report["page_languages"], PageLanguage),
        document_language=report["document_language"],
        conventions=_by_enum(report["conventions"], Convention),
        symbol_standard_guess=SymbolStandard(report["symbol_standard_guess"]),
        symbol_standard_basis=tuple(report["symbol_standard_basis"]),
        plc_families=_by_enum(report["plc_families"], PlcFamily),
        cross_reference_formats={
            key: int(report["cross_reference_formats"][key])
            for key in sorted(report["cross_reference_formats"])
        },
        ocr_needed_pages=report["ocr_needed_pages"],
        cid_fonts_without_tounicode=report["cid_fonts_without_tounicode"],
        pipeline_version=row["pipeline_version"],
        shared_version=row["shared_version"],
        rules_version=row["rules_version"],
        mixed_standards=bool(report.get("mixed_standards", False)),
    )


class SqlCharacterizationRepository(_Base):
    async def put(
        self, ctx: ExecutionContext, item: Characterization, *, project_id: UuidStr
    ) -> None:
        """One record per document: a re-run replaces it (and its ``created_at``)."""
        stmt = pg_insert(drawings_characterizations).values(
            workspace_id=ctx.workspace_id,
            document_id=item.document_id,
            project_id=project_id,
            report=_characterization_report(item),
            pipeline_version=item.pipeline_version,
            shared_version=item.shared_version,
            rules_version=item.rules_version,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["workspace_id", "document_id"],
            set_={
                "project_id": stmt.excluded.project_id,
                "report": stmt.excluded.report,
                "pipeline_version": stmt.excluded.pipeline_version,
                "shared_version": stmt.excluded.shared_version,
                "rules_version": stmt.excluded.rules_version,
                "created_at": func.now(),
            },
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def get(self, ctx: ExecutionContext, document_id: UuidStr) -> Characterization | None:
        if not _is_uuid(document_id):
            return None
        t = drawings_characterizations
        stmt = select(t).where(t.c.workspace_id == ctx.workspace_id, t.c.document_id == document_id)
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _characterization(row)

    async def for_project(
        self, ctx: ExecutionContext, project_id: UuidStr
    ) -> Sequence[Characterization]:
        """ACTIVE documents only: an excluded or purging document is out of every read."""
        t, d = drawings_characterizations, drawings_documents
        stmt = (
            select(t)
            .join(d, d.c.id == t.c.document_id)
            .where(
                t.c.workspace_id == ctx.workspace_id,
                t.c.project_id == project_id,
                d.c.workspace_id == ctx.workspace_id,
                d.c.status == DocumentStatus.ACTIVE.value,
            )
            .order_by(t.c.document_id)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_characterization(row) for row in rows]


# --------------------------------------------------------------------------------------
# the purge sweep
# --------------------------------------------------------------------------------------
class SqlDrawingsSweep(_Base):
    async def sweep_document(self, ctx: ExecutionContext, document_id: UuidStr) -> int:
        """Delete a PURGING document and everything under it, in FK order, in ONE transaction:
        stages, pages, characterization, jobs, document profile versions, the document. Returns
        the rows deleted; ``0`` (nothing written) when the document is gone or is not PURGING.
        The ``FOR UPDATE`` on the document row serialises two sweepers."""
        ws = ctx.workspace_id
        d = drawings_documents
        guard = (
            select(d.c.id)
            .where(
                d.c.id == document_id,
                d.c.workspace_id == ws,
                d.c.status == DocumentStatus.PURGING.value,
            )
            .with_for_update()
        )
        total = 0
        try:
            async with self._tenant_session(ctx) as session:
                if (await session.execute(guard)).first() is None:
                    return 0
                for table in (
                    drawings_page_stages,
                    drawings_pages,
                    drawings_characterizations,
                    drawings_jobs,
                    drawings_profile_versions,
                ):
                    stmt = (
                        delete(table)
                        .where(table.c.workspace_id == ws, table.c.document_id == document_id)
                        .returning(table.c.workspace_id)
                    )
                    total += len((await session.execute(stmt)).all())
                last = (
                    delete(d).where(d.c.id == document_id, d.c.workspace_id == ws).returning(d.c.id)
                )
                total += len((await session.execute(last)).all())
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return total
