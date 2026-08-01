"""Live-Postgres proof of the 5.1-أ request-scoped unit of work: the
aggregate write and the Outbox append are now ATOMIC, positive and negative
case, both against a real PostgreSQL 16.

``MediaRequestService`` composed exactly as the Composition Root composes it
— real ``SqlMediaJobRepository`` + real ``SqlEventOutbox`` over one
``TenantSessionFactory`` acting as the ``uow`` too (§5's "one object, two
roles"). Auto-skips via ``live_db``.

What only a live run can prove: **a REAL repository write rolls back when a
LATER step in the same call fails.** Every hermetic test in
``test_media_outbox_seam.py``/``test_files_use_cases.py``/
``test_memory_use_cases.py`` uses fake ports with no real transaction
semantics, so none of them can tell whether ``SqlMediaJobRepository.add``'s
own INSERT actually survives a Postgres ``ROLLBACK``. Before 5.1-أ this
class of failure was structurally impossible to prevent: the aggregate write
and the append were two separate transactions, so a failing append could
never have undone an already-committed job row.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import DatabaseSettings, Limits
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.media.application.use_cases import MediaRequestService, RequestMedia
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


def _ctx(workspace_id: str, *, correlation_id: str | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=correlation_id or new_uuid7(),
        roles=frozenset({"member"}),
    )


class _OutboxThatFailsAfterTheAggregateWrite:
    """A REAL-shaped ``EventOutbox`` that always raises. Injected in place of
    ``SqlEventOutbox`` so the aggregate write still goes to a real,
    RLS-scoped Postgres transaction (via the real ``SqlMediaJobRepository``)
    while the FAILURE is synthetic -- proving the rollback is a property of
    the unit of work, not of any particular adapter's own error handling.
    """

    async def append(self, ctx: ExecutionContext, records: list[OutboxRecord]) -> None:
        raise RuntimeError("synthetic outbox failure")


async def _read_outbox(owner_dsn: str, aggregate_id: str) -> list[RowMapping]:
    """Read back as the OWNER -- ``app_rw`` has INSERT only on the outbox."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM platform.outbox WHERE aggregate_id = :id"),
                {"id": aggregate_id},
            )
            return list(result.mappings())
    finally:
        await engine.dispose()


async def _count_media_jobs(owner_dsn: str, workspace_id: str) -> int:
    """Read back as the OWNER -- but ``media.media_jobs`` uses ``FORCE ROW
    LEVEL SECURITY`` (``migrations/versions/media/0001_media.py``,
    ``test_media_repository_rls.py``'s own ``_fetch_row_as_owner`` precedent),
    so even the owner is policy-subject and must set the GUC first, on the
    SAME connection/transaction, before this count means anything. Skipping
    this would make the assertion a tautological ``0`` regardless of what is
    actually in the table -- the exact trap this helper exists to avoid.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT count(*) FROM media.media_jobs WHERE workspace_id = :ws"),
                {"ws": workspace_id},
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


def _real_service(tenant_session: TenantSessionFactory) -> MediaRequestService:
    """Composed exactly as ``CompositionRoot.from_env`` composes it."""
    return MediaRequestService(
        RequestMedia(SqlMediaJobRepository(tenant_session), Limits()),
        SqlEventOutbox(tenant_session),
        tenant_session,
    )


@pytest.mark.anyio
async def test_the_job_row_and_its_outbox_row_commit_together(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """The positive case: ``request()`` runs entirely inside ``uow.begin(ctx)``
    and leaves BOTH rows behind -- from a caller (``MediaRequestService``)
    that never itself opens a session or knows a unit of work exists."""
    workspace_id = new_uuid7()
    ctx = _ctx(workspace_id)

    view = await _real_service(tenant_session).request(
        ctx,
        agent_key="image-agent",
        kind="image",
        prompt="a cat wearing sunglasses",
        params={"width": 512, "height": 512},
    )

    stored = await SqlMediaJobRepository(tenant_session).get(ctx, view.job_id)
    assert stored is not None, "the job must be persisted"
    assert stored.status.value == "queued"

    rows = await _read_outbox(live_db.owner, view.job_id)
    assert len(rows) == 1, "the outbox row must be persisted alongside the job"
    assert rows[0]["event_type"] == "media.job.requested.v1"
    # The read-back goes through raw SQL (no typed Table), so asyncpg hands
    # back `uuid.UUID` objects rather than the adapter's `as_uuid=False` text
    # (the `test_outbox.py` precedent).
    assert str(rows[0]["workspace_id"]) == workspace_id


@pytest.mark.anyio
async def test_an_outbox_failure_rolls_back_the_aggregate_write_too(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """The negative case -- the whole point of 5.1-أ. Before this sub-step, the
    aggregate write (``SqlMediaJobRepository.add``) and the outbox append were
    two independent transactions, so a failing append could never have undone
    an already-committed job row. Now they share ONE transaction, so a
    failure anywhere inside ``uow.begin(ctx)`` must roll BOTH back."""
    workspace_id = new_uuid7()
    ctx = _ctx(workspace_id)
    service = MediaRequestService(
        RequestMedia(SqlMediaJobRepository(tenant_session), Limits()),
        _OutboxThatFailsAfterTheAggregateWrite(),
        tenant_session,
    )

    with pytest.raises(RuntimeError, match="synthetic outbox failure"):
        await service.request(
            ctx,
            agent_key="image-agent",
            kind="image",
            prompt="a cat wearing sunglasses",
            params={"width": 512, "height": 512},
        )

    assert await _count_media_jobs(live_db.owner, workspace_id) == 0, (
        "the aggregate write must not have survived the outbox failure"
    )
