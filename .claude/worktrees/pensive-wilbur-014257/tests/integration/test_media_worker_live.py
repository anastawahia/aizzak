"""Live-Postgres + live-Redis test for the media worker's REAL run handler
(5.1-ج), a ``FakeMediaGenerator`` injected in place of the still-nonexistent
real adapter (``media/ports/generation.py``'s own "deferred, Phase-5 infra
adapter" note) -- ``build_media_worker``'s own parametrized-dependencies
shape exists precisely for this.

Same chain as ``test_e2e_outbox_to_worker.py``: a real ``RequestMedia`` call
persists a queued job (``app_rw``), its mapped ``media.job.requested.v1``
event is appended (retargeted at a UNIQUE test stream, R6) → the real relay
(5.1-ب, ``outbox_relay`` role) → ``build_media_run_handler`` (``app_rw``)
runs the (fake) generator and persists the job's terminal state + its
mapped follow-on event, atomically (D5's own documented shape).
"""

from __future__ import annotations

import contextlib
from dataclasses import replace

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings, Limits
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.media.adapters.sql_repository import SqlMediaJobRepository
from app.modules.media.application.event_mapping import to_outbox_record
from app.modules.media.application.use_cases import RequestMedia
from app.modules.media.domain.value_objects import GenParams, MediaKind
from app.workers.bootstrap import build_media_run_handler
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_redis]


class FakeMediaGenerator:
    """A ``MediaGenerator`` fake -- ``build_media_worker``'s own injection
    seam for the gap ``media/ports/generation.py``'s docstring names."""

    def __init__(self, result_file_id: str | None = None) -> None:
        self.result_file_id = result_file_id or new_uuid7()
        self.calls: list[tuple[MediaKind, str]] = []

    async def generate(
        self, ctx: ExecutionContext, *, kind: MediaKind, prompt: str, params: GenParams
    ) -> str:
        self.calls.append((kind, prompt))
        return self.result_file_id


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


async def _read_outbox_as_owner(
    owner_dsn: str, *, aggregate_id: str, event_type: str
) -> list[RowMapping]:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM platform.outbox WHERE aggregate_id = :id AND event_type = :et"),
                {"id": aggregate_id, "et": event_type},
            )
            return list(result.mappings())
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_a_real_requested_job_flows_through_the_relay_to_the_fake_generator_run_handler(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    ctx = _ctx(new_uuid7())

    jobs = SqlMediaJobRepository(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    generator = FakeMediaGenerator()
    handler = build_media_run_handler(
        jobs,
        generator,
        outbox,
        tenant_session,
        SqlProcessedEventLedger(tenant_session),
        # The ledger key must be THE group that owns this subscription
        # (production builders feed both from one constant); this test's
        # group is unique per run, so it is passed explicitly.
        consumer_group=group,
    )
    subscription = Subscription(
        stream=stream, group=group, handlers={"media.job.requested.v1": handler}
    )
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="e2e-media-test",
        block_ms=500,
        batch_count=10,
        max_deliveries=5,
    )

    try:
        # 0) setup() FIRST -- ensure_group's own `$` (tail-start) semantics
        #    mean a group created AFTER an entry already exists on the
        #    stream never sees that entry (a real worker's setup() always
        #    runs at process boot, before it consumes anything).
        await consumer.setup([subscription])

        # 1) The real RequestMedia use-case persists a queued job (app_rw).
        job, events = await RequestMedia(jobs, Limits()).execute(
            ctx,
            agent_key="image-agent",
            kind="image",
            prompt="a cat wearing sunglasses",
            params={"width": 64, "height": 64},
        )
        # 2) Its mapped event, retargeted at a UNIQUE test stream (R6) --
        #    the real event_mapping.py shape, real data, only .stream differs.
        record = replace(to_outbox_record(ctx, events[0]), stream=stream)
        await outbox.append(ctx, [record])

        # 3) The real relay (5.1-ب), as the real outbox_relay role.
        relay = OutboxRelay(
            SqlOutboxRelayStore(relay_sessionmaker),
            RedisStreamsPublisher(redis_client),
            batch_size=10,
            poll_interval_ms=100,
            max_backoff_ms=1000,
        )
        published = await relay.run_once()
        assert published == 1

        # 4) The real run handler, fake generator, on app_rw.
        handled = await consumer.run_once([subscription])
        assert handled == 1

        # -- Assertions --------------------------------------------------- #
        stored = await jobs.get(ctx, job.id)
        assert stored is not None
        assert stored.status.value == "succeeded"
        assert stored.result_file_id == generator.result_file_id
        assert generator.calls == [(MediaKind.IMAGE, "a cat wearing sunglasses")]

        follow_on = await _read_outbox_as_owner(
            live_db.owner, aggregate_id=job.id, event_type="media.job.generated.v1"
        )
        assert len(follow_on) == 1
        assert str(follow_on[0]["workspace_id"]) == ctx.workspace_id

        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0

        second = await consumer.run_once([subscription])
        assert second == 0
    finally:
        with contextlib.suppress(Exception):
            await redis_client.xgroup_destroy(stream, group)
        await redis_client.delete(stream)
