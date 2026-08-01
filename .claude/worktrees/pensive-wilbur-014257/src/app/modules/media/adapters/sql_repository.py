"""SQL adapter for ``MediaJobRepository`` (02-port-contracts §2; 01-data-model
§2.8; ``migrations/versions/media/0001_media.py``).

Declares its own local Core ``Table`` against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04): Layer 1 (``SET LOCAL``/``set_config``
``app.workspace_id``) is applied by the injected ``tenant_session`` provider
*before* this adapter's code runs at all — the adapter itself never touches
the GUC, i.e. it is GUC-agnostic. Layer 2 (``WHERE workspace_id = :ws``) is
applied explicitly in every statement below, as defence in depth alongside
Postgres RLS.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.types import Uuid as UuidStr
from app.modules.media.domain.entities import MediaJob
from app.modules.media.domain.value_objects import AgentKey, GenParams, JobStatus, MediaKind

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

media_jobs = Table(
    "media_jobs",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("agent_key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("prompt", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("result_file_id", _uuid_col, nullable=True),
    Column("error", Text, nullable=True),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("version", Integer, nullable=False),
    schema="media",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlMediaJobRepository:
    """SQL ``MediaJobRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, R2) — there is no cross-call unit of work yet. Wiring a
    request-scoped session in later (once the Outbox makes "aggregate write +
    event" atomic) only means changing what ``tenant_session`` resolves to;
    this class's public shape does not change.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(self, ctx: ExecutionContext, job_id: UuidStr) -> MediaJob | None:
        stmt = select(media_jobs).where(
            media_jobs.c.id == job_id, media_jobs.c.workspace_id == ctx.workspace_id
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate(row)

    async def add(self, ctx: ExecutionContext, job: MediaJob) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched job.workspace_id is then rejected by the RLS
        # WITH CHECK clause rather than silently persisted under ctx's tenant.
        stmt = insert(media_jobs).values(
            id=job.id,
            workspace_id=job.workspace_id,
            agent_key=job.agent_key.value,
            kind=job.kind.value,
            prompt=job.prompt,
            params=job.params.as_dict(),
            status=job.status.value,
            result_file_id=job.result_file_id,
            error=job.error,
            created_by=job.created_by,
            created_at=job.created_at,
            updated_at=job.updated_at,
            version=job.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def save(self, ctx: ExecutionContext, job: MediaJob) -> None:
        # Optimistic lock: only a row still at `job.version` is updated.
        stmt = (
            update(media_jobs)
            .where(
                media_jobs.c.id == job.id,
                media_jobs.c.workspace_id == ctx.workspace_id,
                media_jobs.c.version == job.version,
            )
            .values(
                status=job.status.value,
                result_file_id=job.result_file_id,
                error=job.error,
                version=media_jobs.c.version + 1,
            )
            .returning(media_jobs.c.version, media_jobs.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(f"media job {job.id} was modified concurrently (stale version)")
        # The domain never advances `version`/`updated_at` itself (they are
        # repository-owned, 02-port-contracts §2) -- write the fresh values
        # back onto the aggregate so a second save() in the same call (e.g.
        # RunMediaJob's start_running-save then complete-save) targets the
        # version this adapter just wrote, not the one the caller minted.
        job.version, job.updated_at = row


def _hydrate(row: RowMapping) -> MediaJob:
    kind = MediaKind(row["kind"])
    return MediaJob(
        id=row["id"],
        workspace_id=row["workspace_id"],
        agent_key=AgentKey(row["agent_key"]),
        kind=kind,
        prompt=row["prompt"],
        params=GenParams.from_dict(kind, row["params"]),
        status=JobStatus(row["status"]),
        result_file_id=row["result_file_id"],
        error=row["error"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6).

    ``23505`` (``unique_violation``) -- lost a uniqueness race (e.g. a
    duplicate ``id``) -- ``ConflictError`` (409, ``common.conflict``).
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``job.workspace_id`` on
    ``add``) -- an internal/500-class error (``common.internal``): a
    well-behaved caller can never trigger this, so it is not a normal 4xx.
    Anything else is an unexpected database failure, folded into the same
    500-class error rather than leaking the driver exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("media job write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError("media job write rejected by row-level security", code="common.internal")
    return AppError(
        "unexpected database error while persisting a media job", code="common.internal"
    )
