"""Live-Postgres proof of the 4.7-d-2 media seam end to end (09-testing-strategy §3).

``MediaRequestService`` composed exactly as the Composition Root composes it —
real ``SqlMediaJobRepository`` + real ``SqlEventOutbox`` over one
``TenantSessionFactory`` — against a real PostgreSQL 16. Auto-skips via
``live_db``.

What only a live run can prove: **one call leaves BOTH rows behind.** Every
hermetic test in ``test_media_outbox_seam.py`` uses a fake outbox, so it cannot
tell whether the two adapters agree about the job id, whether ``app_rw``
actually holds the grants both writes need, or whether the envelope survives a
``jsonb`` round trip. Before 4.7-d-1 the second row did not exist at all: the
job was queued and the event was dropped, so nothing could ever pick the job up.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.identifiers import new_uuid7
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


def _service(tenant_session: TenantSessionFactory) -> MediaRequestService:
    """Composed exactly as ``CompositionRoot.from_env`` composes it — the SAME
    ``tenant_session`` instance backs the repository, the outbox, AND the
    unit of work (5.1-أ)."""
    return MediaRequestService(
        RequestMedia(SqlMediaJobRepository(tenant_session), Limits()),
        SqlEventOutbox(tenant_session),
        tenant_session,
    )


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


@pytest.mark.anyio
async def test_one_request_leaves_both_the_job_row_and_its_event(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    workspace_id = new_uuid7()
    ctx = _ctx(workspace_id)

    view = await _service(tenant_session).request(
        ctx,
        agent_key="image-agent",
        kind="image",
        prompt="a cat wearing sunglasses",
        params={"width": 512, "height": 512},
    )

    stored = await SqlMediaJobRepository(tenant_session).get(ctx, view.job_id)
    assert stored is not None, "the job must be persisted"
    assert stored.status.value == "queued"

    (event_row,) = await _read_outbox(live_db.owner, view.job_id)
    assert event_row["event_type"] == "media.job.requested.v1"
    assert event_row["stream"] == "stream.media"
    assert event_row["published_at"] is None, "the relay has not run yet"


@pytest.mark.anyio
async def test_the_stored_envelope_describes_the_stored_job(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """The two writes must agree. A ``subject``/``job_id`` that disagreed with
    the persisted row would send the Phase-5 worker after a job that is not
    there — a failure no unit test with a fake outbox can see."""
    workspace_id = new_uuid7()
    correlation_id = new_uuid7()
    ctx = _ctx(workspace_id, correlation_id=correlation_id)

    view = await _service(tenant_session).request(
        ctx,
        agent_key="video-agent",
        kind="video",
        prompt="a river at dawn",
        params={"duration_seconds": 6},
    )

    (event_row,) = await _read_outbox(live_db.owner, view.job_id)
    payload = event_row["payload"]

    assert payload["subject"] == view.job_id
    assert payload["data"]["job_id"] == view.job_id
    assert payload["data"]["kind"] == "video"
    assert payload["data"]["params"] == {"duration_seconds": 6}
    assert payload["workspaceid"] == workspace_id
    assert payload["correlationid"] == correlation_id
    # The envelope's own id is the row's primary key (DD-09 idempotency key).
    assert payload["id"] == str(event_row["id"])


@pytest.mark.anyio
async def test_a_rejected_request_writes_neither_row(
    live_db: LiveDbDsns, tenant_session: TenantSessionFactory
) -> None:
    """INV-MJ3: the ceiling is enforced before anything is persisted, so an
    over-limit request must not leave an orphan job OR a stray event."""
    ctx = _ctx(new_uuid7())
    service = MediaRequestService(
        RequestMedia(SqlMediaJobRepository(tenant_session), Limits(max_image_dim=256)),
        SqlEventOutbox(tenant_session),
        tenant_session,
    )

    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            before = (await conn.execute(text("SELECT count(*) FROM platform.outbox"))).scalar_one()
    finally:
        await engine.dispose()

    with pytest.raises(ValidationError):
        await service.request(
            ctx,
            agent_key="image-agent",
            kind="image",
            prompt="a cat",
            params={"width": 4096, "height": 4096},
        )

    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            after = (await conn.execute(text("SELECT count(*) FROM platform.outbox"))).scalar_one()
    finally:
        await engine.dispose()
    assert after == before
