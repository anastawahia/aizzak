"""Live-Postgres tests for ``SqlMediaJobRepository`` + RLS (09-testing-strategy
§3: "RLS: ضبط app.workspace_id يعزل الصفوف؛ غياب السياق ⇒ صفر صفوف؛ محاولة
عبور مستأجر ⇒ لا تسرّب").

Runs against a real, local PostgreSQL 16 (no Docker/testcontainers -- see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. A job builder mirrors ``tests/unit/test_media_module.py``'s
``_job()``, except every id is a *real* ``new_uuid7()`` string rather than an
arbitrary label like ``"ws1"``/``"job-1"`` -- the underlying Postgres columns
are actually typed ``uuid``, so a non-UUID string would fail at the driver
before RLS ever gets a say.
"""

from __future__ import annotations

from datetime import datetime

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
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.media.domain.entities import MediaJob
from app.modules.media.domain.value_objects import AgentKey, GenParams, JobStatus, MediaKind
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str, *, user_id: str | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id or new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _default_params(kind: MediaKind) -> GenParams:
    if kind is MediaKind.IMAGE:
        return GenParams.from_dict(
            MediaKind.IMAGE, {"width": 512, "height": 512, "model": "dalle-3"}
        )
    return GenParams.from_dict(MediaKind.VIDEO, {"duration_seconds": 5})


def _job(
    *,
    workspace_id: str,
    job_id: str | None = None,
    agent_key: str = "image-agent",
    kind: MediaKind = MediaKind.IMAGE,
    prompt: str = "a cat wearing sunglasses",
    params: GenParams | None = None,
    status: JobStatus = JobStatus.QUEUED,
    result_file_id: str | None = None,
    error: str | None = None,
    created_by: str | None = None,
    now: datetime | None = None,
) -> MediaJob:
    at = now or utc_now()
    return MediaJob(
        id=job_id or new_uuid7(),
        workspace_id=workspace_id,
        agent_key=AgentKey(agent_key),
        kind=kind,
        prompt=prompt,
        params=params or _default_params(kind),
        status=status,
        result_file_id=result_file_id,
        error=error,
        created_by=created_by or new_uuid7(),
        created_at=at,
        updated_at=at,
        version=1,
    )


async def _fetch_row_as_owner(owner_dsn: str, workspace_id: str, job_id: str) -> RowMapping | None:
    """Bypass the repository entirely: read the raw row as the table owner,
    for assertions that must be independent of ``repo.get``'s own
    bookkeeping. Still goes through RLS -- ``0001_media.py`` uses ``FORCE ROW
    LEVEL SECURITY``, so even the owner is policy-subject -- hence setting
    the GUC first, on the same connection/transaction, before reading.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT * FROM media.media_jobs WHERE id = :id"), {"id": job_id}
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1) round-trip add/get                                                      #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips_the_aggregate(repo: SqlMediaJobRepository) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    params = GenParams.from_dict(
        MediaKind.IMAGE, {"width": 1024, "height": 768, "model": "dalle-3"}
    )
    job = _job(workspace_id=ws, params=params)

    await repo.add(ctx, job)
    fetched = await repo.get(ctx, job.id)

    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.workspace_id == ws
    assert fetched.agent_key == job.agent_key
    assert fetched.kind is MediaKind.IMAGE
    assert fetched.prompt == job.prompt
    assert fetched.params == params
    assert fetched.params.as_dict() == {"width": 1024, "height": 768, "model": "dalle-3"}
    assert fetched.status is JobStatus.QUEUED
    assert fetched.result_file_id is None
    assert fetched.error is None
    assert fetched.created_by == job.created_by
    assert fetched.created_at == job.created_at
    assert fetched.updated_at == job.updated_at
    assert fetched.version == 1


async def test_get_missing_job_returns_none(repo: SqlMediaJobRepository) -> None:
    assert await repo.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (2) save() transitions: version/updated_at write-back                       #
# --------------------------------------------------------------------------- #
async def test_save_transitions_advance_version_and_write_back_to_the_aggregate(
    repo: SqlMediaJobRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    job = _job(workspace_id=ws)
    await repo.add(ctx, job)
    created_at = job.created_at

    job.start_running(utc_now())
    await repo.save(ctx, job)

    assert job.version == 2
    assert job.updated_at > created_at
    row = await _fetch_row_as_owner(live_db.owner, ws, job.id)
    assert row is not None
    assert row["version"] == 2
    assert row["status"] == "running"

    result_file_id = new_uuid7()
    job.complete(result_file_id, utc_now())
    await repo.save(ctx, job)

    assert job.version == 3
    assert job.status is JobStatus.SUCCEEDED
    assert job.result_file_id == result_file_id

    fetched = await repo.get(ctx, job.id)
    assert fetched is not None
    assert fetched.version == 3
    assert fetched.status is JobStatus.SUCCEEDED
    assert fetched.result_file_id == result_file_id


# --------------------------------------------------------------------------- #
# (3) optimistic conflict                                                     #
# --------------------------------------------------------------------------- #
async def test_save_with_stale_version_raises_conflict_and_leaves_the_row_unchanged(
    repo: SqlMediaJobRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    job = _job(workspace_id=ws)
    await repo.add(ctx, job)

    reader_a = await repo.get(ctx, job.id)
    reader_b = await repo.get(ctx, job.id)
    assert reader_a is not None
    assert reader_b is not None
    assert reader_a.version == reader_b.version == 1

    reader_a.start_running(utc_now())
    await repo.save(ctx, reader_a)
    assert reader_a.version == 2

    reader_b.start_running(utc_now())  # reader_b is still stuck at version==1
    with pytest.raises(ConflictError):
        await repo.save(ctx, reader_b)

    row = await _fetch_row_as_owner(live_db.owner, ws, job.id)
    assert row is not None
    assert row["version"] == 2
    assert row["status"] == "running"


# --------------------------------------------------------------------------- #
# (4) RLS: no context set at all ⇒ zero rows, no exception                    #
# --------------------------------------------------------------------------- #
async def test_rls_with_no_tenant_context_set_returns_zero_rows(
    repo: SqlMediaJobRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    ws = new_uuid7()
    await repo.add(_ctx(ws), _job(workspace_id=ws))

    # A brand new app_rw session/connection that never called set_config at
    # all: current_setting('app.workspace_id', true) reads back SQL NULL
    # (01-data-model §3's documented "no context ⇒ zero rows" case) --
    # already safe before this slice's NULLIF hardening, since NULL::uuid
    # never raises. Contrast with test 7's explicit empty-string case.
    async with sessionmaker_app() as session:
        count = (await session.execute(text("SELECT count(*) FROM media.media_jobs"))).scalar_one()

    assert count == 0


# --------------------------------------------------------------------------- #
# (5) RLS isolation between two tenants                                       #
# --------------------------------------------------------------------------- #
async def test_rls_isolates_rows_between_tenants(repo: SqlMediaJobRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a, ctx_b = _ctx(ws_a), _ctx(ws_b)
    job_a = _job(workspace_id=ws_a)
    await repo.add(ctx_a, job_a)

    assert await repo.get(ctx_b, job_a.id) is None
    assert await repo.get(ctx_a, job_a.id) is not None

    job_b = _job(workspace_id=ws_b)
    await repo.add(ctx_b, job_b)

    assert await repo.get(ctx_a, job_b.id) is None
    assert await repo.get(ctx_b, job_b.id) is not None
    # each tenant still only ever sees its own job:
    assert await repo.get(ctx_b, job_a.id) is None
    assert await repo.get(ctx_a, job_a.id) is not None


# --------------------------------------------------------------------------- #
# (6) forged cross-tenant write ⇒ RLS WITH CHECK rejects it                   #
# --------------------------------------------------------------------------- #
async def test_add_with_forged_workspace_id_is_rejected_by_rls_with_check(
    repo: SqlMediaJobRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    # The aggregate claims tenant B, but is (incorrectly) added under
    # tenant A's RLS context -- the adapter writes job.workspace_id as-is
    # (not ctx.workspace_id), so only the WITH CHECK policy stands between
    # this and a cross-tenant write.
    forged_job = _job(workspace_id=ws_b)

    with pytest.raises(AppError) as exc_info:
        await repo.add(ctx_a, forged_job)

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)

    assert await repo.get(_ctx(ws_b), forged_job.id) is None


# --------------------------------------------------------------------------- #
# (7) empty-string GUC discriminator -- what the NULLIF fix buys              #
# --------------------------------------------------------------------------- #
async def test_empty_string_guc_yields_zero_rows_not_an_exception(
    repo: SqlMediaJobRepository, sessionmaker_app: async_sessionmaker[AsyncSession]
) -> None:
    """Documents exactly what ``migrations/versions/media/0001_media.py``'s
    ``NULLIF(current_setting('app.workspace_id', true), '')::uuid`` predicate
    buys over the raw ``current_setting('app.workspace_id', true)::uuid``
    form still used (untouched, R8) by the other three v1 module chains
    (knowledge/integrations/usage): on a connection that reaches this state
    (e.g. a pooled connection whose previous transaction's ``SET LOCAL``
    reverted), the raw form raises ``22P02 invalid input syntax for type
    uuid`` casting ``''``; ``NULLIF('', '')`` is SQL ``NULL`` first, so the
    cast yields ``NULL`` and the row comparison is just false -- zero rows,
    no exception. Contrast: setting the GUC to the real tenant id ⇒ 1 row.
    """
    ws_a = new_uuid7()
    await repo.add(_ctx(ws_a), _job(workspace_id=ws_a))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        count = (await session.execute(text("SELECT count(*) FROM media.media_jobs"))).scalar_one()
        assert count == 0

        await session.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws_a}
        )
        count = (await session.execute(text("SELECT count(*) FROM media.media_jobs"))).scalar_one()
        assert count == 1
