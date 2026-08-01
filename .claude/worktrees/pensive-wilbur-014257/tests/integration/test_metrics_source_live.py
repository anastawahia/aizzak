"""Live-Postgres/Redis proof for ``SqlRedisMetricsSource`` (P1-3,
``docs/p1-hardening-plan.md`` §3 step 10) and for ``GET /metrics`` end to end.

The exit criterion this file exists for, per the plan's own wording: **prove
by measurement, not by reading** -- scrape the endpoint for real and show the
number changes when the reality underneath it changes (a stale unpublished
row ages ⇒ the age rises; a DLQ entry appears ⇒ the depth rises), and prove
the value is correct regardless of which of several sibling processes
computes it (the multi-worker pitfall the design brief names by name).

Four things proven here, each a thing a hermetic fake cannot:

1. ``outbox_oldest_unpublished_age_seconds`` reflects a REAL, seeded
   ``platform.outbox`` row's age, using real SQL comparison semantics on a
   real ``timestamptz`` column and the real partial index
   (``ix_outbox_unpublished``), not a stub's arithmetic.
2. ``dlq_depths`` reflects a REAL ``XADD``/``XDEL`` against
   ``stream.knowledge.dlq`` on the test-only local Redis (``127.0.0.1:6379``
   -- a NATIVE service distinct from the docker-compose stack's own Redis,
   which the ``live_redis`` fixture's own module docstring documents; this is
   never the live production DLQ). Delta-based assertions
   (``baseline`` → ``baseline + 1`` → ``baseline``) so a stray entry left by
   an unrelated run never produces a false pass or a false failure.
3. **The multi-worker pitfall, proven, not merely designed against**: TWO
   entirely independent ``SqlRedisMetricsSource`` instances -- each its OWN
   engine, its OWN Redis client, exactly what two sibling gunicorn workers
   under ``WEB_CONCURRENCY=2`` would each hold -- report the IDENTICAL value
   for the identical real state, because both re-read the same external
   source rather than any private, in-process memory.
4. **An actual HTTP scrape**, not a direct port call: a real ``GET /metrics``
   against a minimal ASGI app mounting the REAL router over the REAL adapter
   (``httpx`` over ``ASGITransport``, the ``test_sse.py`` precedent), read
   once, then the underlying row aged further, scraped AGAIN, and the number
   read off the wire is shown to have changed.

Never touches ``platform.outbox``/``stream.*.dlq`` on the live docker-compose
stack: Postgres is ``aizzak_test`` (``live_db``'s own topology), and Redis is
the native ``127.0.0.1:6379`` test instance, never the containerised one
(mapped to a different host port entirely, ``docker-compose.yml``'s
``HOST_PORT_REDIS``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.api.metrics import OUTBOX_AGE_METRIC, metrics_router
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings, RedisSettings
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.monitoring.metrics_source import SqlRedisMetricsSource
from app.infrastructure.persistence.database import create_engine
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_redis]

# One of `SqlRedisMetricsSource.DLQ_SOURCE_STREAMS` -- the module's own
# hardcoded pair (mirroring `composition_root.py`'s `_NOTIFY_STREAMS`, its own
# docstring explains why it is not parameterised). Using the real name on the
# test-only Redis is safe (see module docstring); every assertion below is
# delta-based against a measured baseline so a stray leftover never matters.
_DLQ_STREAM = "stream.knowledge"


async def _seed_outbox_row(
    owner_dsn: str, *, created_at: datetime, published_at: datetime | None
) -> str:
    event_id = new_uuid7()
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO platform.outbox
                        (id, aggregate_type, aggregate_id, event_type, stream, payload,
                         created_at, published_at)
                    VALUES
                        (:id, 'test', :id, 'test.metrics.v1', 'stream.test', '{}'::jsonb,
                         :created_at, :published_at)
                    """
                ),
                {"id": event_id, "created_at": created_at, "published_at": published_at},
            )
    finally:
        await engine.dispose()
    return event_id


@pytest.mark.anyio
async def test_outbox_age_is_zero_with_nothing_waiting_and_rises_with_a_seeded_row(
    live_db: LiveDbDsns, metrics_engine: AsyncEngine, redis_client: Redis
) -> None:
    source = SqlRedisMetricsSource(metrics_engine, redis_client)

    # `truncate_tables` (conftest, autouse) leaves platform.outbox empty at
    # the start of every test -- the healthy, common case (module docstring).
    assert await source.outbox_oldest_unpublished_age_seconds() == 0.0

    old_created_at = datetime.now(UTC) - timedelta(seconds=12)
    await _seed_outbox_row(live_db.owner, created_at=old_created_at, published_at=None)

    age = await source.outbox_oldest_unpublished_age_seconds()
    # >= 12s (the row really is that old), generous upper bound only to
    # absorb this test's own execution time -- never a fixed "should be
    # exactly 12".
    assert 12.0 <= age < 60.0


@pytest.mark.anyio
async def test_outbox_age_ignores_published_rows_regardless_of_how_old(
    live_db: LiveDbDsns, metrics_engine: AsyncEngine, redis_client: Redis
) -> None:
    """An unpublished row is never a candidate at any age (retention.py's own
    principle, restated for the read side): the metric must not mistake a
    long-settled published row for a stuck one."""
    source = SqlRedisMetricsSource(metrics_engine, redis_client)
    ancient = datetime.now(UTC) - timedelta(days=200)
    await _seed_outbox_row(live_db.owner, created_at=ancient, published_at=ancient)

    assert await source.outbox_oldest_unpublished_age_seconds() == 0.0


@pytest.mark.anyio
async def test_dlq_depth_reflects_a_real_xadd_and_xdel(
    metrics_engine: AsyncEngine, redis_client: Redis
) -> None:
    source = SqlRedisMetricsSource(metrics_engine, redis_client)
    dlq_key = f"{_DLQ_STREAM}.dlq"

    baseline = (await source.dlq_depths())[_DLQ_STREAM]

    entry_id = await redis_client.xadd(
        dlq_key,
        {
            "ce": b"test-bytes",
            "reason": b"handler_failed",
            "source_stream": _DLQ_STREAM.encode(),
            "source_entry_id": b"0-1",
            "consumer_group": b"cg.test",
            "deliveries": b"5",
        },
    )
    try:
        risen = (await source.dlq_depths())[_DLQ_STREAM]
        assert risen == baseline + 1, "a real DLQ entry must raise the depth by exactly one"
    finally:
        await redis_client.xdel(dlq_key, entry_id)

    settled = (await source.dlq_depths())[_DLQ_STREAM]
    assert settled == baseline, "removing the entry must restore the prior depth"


@pytest.mark.anyio
async def test_two_independent_instances_report_the_identical_value(
    live_db: LiveDbDsns, live_redis: str
) -> None:
    """The multi-worker pitfall, proven: two ENTIRELY separate engines and
    Redis clients -- exactly what two sibling gunicorn workers under
    ``WEB_CONCURRENCY=2`` would each hold -- must answer identically for the
    identical real state, because neither holds any private in-process
    memory the other cannot see (module docstring point 3)."""
    old_created_at = datetime.now(UTC) - timedelta(seconds=7)
    await _seed_outbox_row(live_db.owner, created_at=old_created_at, published_at=None)

    engine_a = create_engine(DatabaseSettings(url=live_db.metrics), poolclass=NullPool)
    engine_b = create_engine(DatabaseSettings(url=live_db.metrics), poolclass=NullPool)
    client_a = create_redis_client(RedisSettings(url=live_redis))
    client_b = create_redis_client(RedisSettings(url=live_redis))
    try:
        source_a = SqlRedisMetricsSource(engine_a, client_a)
        source_b = SqlRedisMetricsSource(engine_b, client_b)

        age_a = await source_a.outbox_oldest_unpublished_age_seconds()
        age_b = await source_b.outbox_oldest_unpublished_age_seconds()
        # Not bit-identical (each queries `now()` independently, a
        # microsecond apart) -- close enough to prove neither is reading a
        # STALE, privately-cached number a real drift would expose as a
        # multi-second gap.
        assert abs(age_a - age_b) < 1.0, (
            f"two independent sources disagree by {abs(age_a - age_b)}s on the SAME "
            "real state -- one of them is reading private, per-process memory"
        )

        depths_a = await source_a.dlq_depths()
        depths_b = await source_b.dlq_depths()
        assert depths_a == depths_b
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
        await client_a.aclose()
        await client_b.aclose()


def _parse_gauge(body: str, name: str) -> float:
    for line in body.splitlines():
        if line.startswith(name) and not line.startswith("#"):
            return float(line.split()[-1])
    raise AssertionError(f"metric {name!r} not found in:\n{body}")


@pytest.mark.anyio
async def test_a_real_http_scrape_reflects_a_change_in_the_underlying_row(
    live_db: LiveDbDsns, metrics_engine: AsyncEngine, redis_client: Redis
) -> None:
    """The literal exit criterion: scrape ``GET /metrics`` over real HTTP
    (ASGI), not a direct port call, and show the number on the wire changes
    when the row underneath it ages further -- "صفٌّ غير منشورٍ يشيخ ⇐ الزمن
    يرتفع" in the design brief's own words. Mounts ONLY `metrics_router` on a
    bare `FastAPI()` (the `test_api_metrics_router.py` precedent) -- the
    router touches nothing but `request.app.state.metrics_source`, so
    building the whole `ApiServices`/orchestrator stack `create_app` needs
    would prove nothing more about THIS endpoint.
    """
    source = SqlRedisMetricsSource(metrics_engine, redis_client)
    app = FastAPI()
    app.state.metrics_source = source
    app.include_router(metrics_router)

    old_created_at = datetime.now(UTC) - timedelta(seconds=5)
    await _seed_outbox_row(live_db.owner, created_at=old_created_at, published_at=None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/metrics")
        assert first.status_code == 200
        first_age = _parse_gauge(first.text, OUTBOX_AGE_METRIC)
        assert first_age >= 5.0

        # The reality underneath changes: the SAME row is now older still --
        # nothing here re-seeds or mutates the fixture, time alone did it.
        second = await client.get("/metrics")
        second_age = _parse_gauge(second.text, OUTBOX_AGE_METRIC)
        assert second_age > first_age, (
            "a second scrape, taken later, must read a STRICTLY larger age for the "
            "same still-unpublished row -- otherwise the value is frozen, not live"
        )
