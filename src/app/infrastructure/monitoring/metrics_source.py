"""``MetricsSource`` over Postgres (the Outbox age) + Redis (DLQ depth) —
P1-3, ``docs/p1-hardening-plan.md`` §3 step 10.

**Where each number actually lives, and why this one adapter reads both.**
``outbox-relay`` (``infrastructure/messaging/outbox.py``) is the ONE process
that stamps ``published_at`` — it OWNS the Outbox's true state — but it runs
no HTTP server at all (``docker-compose.yml``'s own comment: "SINGLE
instance", started as ``python -m app.workers.outbox_relay``, nothing else).
``app`` is the one process with an HTTP listener, but it holds no read
access to ``platform.outbox`` at all: ``app_rw`` is deliberately
INSERT-only there (``app.ops.provision``'s own docstring). So *measuring*
this metric happens over a connection ``app`` did not otherwise have any
reason to hold — a FIFTH least-privilege role, ``metrics_reader``
(``app.ops.provision.METRICS_ROLE``), granted ``SELECT`` on
``platform.outbox`` and nothing else, exactly the ``outbox_relay``/
``retention_sweeper`` precedent applied to a fourth distinct job. *Exposing*
it, correctly, stays where the one HTTP listener already is.

The DLQ half has no such asymmetry: ``dead_letter``
(``infrastructure/messaging/redis_streams.py``) writes to ``<stream>.dlq``
over the SAME shared Redis client every process in this codebase already
holds (no ACL split on the Redis side, unlike Postgres's role model), so
``app``'s own ``redis_client`` (``composition_root.py``) reads it directly —
no new connection, no new credential.

**Every value here is computed FRESH on every call — see the port's own
docstring for why that is what makes this correct under gunicorn's
multi-worker default without ``PROMETHEUS_MULTIPROC_DIR``.**
"""

from __future__ import annotations

from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# 04 §2's topology table: the two source streams this platform's workers
# consume today. Hardcoded rather than imported from each module's own
# `event_mapping.STREAM` -- `app.infrastructure` may not import
# `app.modules` (import-linter contract 6 runs the other direction too: only
# the Composition Root may reach across layers), and there is no single
# canonical stream registry to import instead -- the SAME literal pair
# `composition_root.py`'s own `_NOTIFY_STREAMS` already hardcodes, for the
# identical reason.
DLQ_SOURCE_STREAMS: tuple[str, ...] = ("stream.knowledge", "stream.media")


class SqlRedisMetricsSource:
    """Structural ``MetricsSource`` (Protocol match, no inheritance -- the
    ``RedisCache``/every adapter-over-a-port precedent in this codebase)."""

    def __init__(self, engine: AsyncEngine, redis_client: Redis) -> None:
        self._engine = engine
        self._redis = redis_client

    async def outbox_oldest_unpublished_age_seconds(self) -> float:
        """``min(created_at)`` over the unpublished half of ``platform.outbox``
        -- the exact partial index ``ix_outbox_unpublished`` (``created_at``
        ``WHERE published_at IS NULL``, ``0001_baseline_platform.py``) exists
        for, so this is an index scan for the single oldest row, never a
        sequential scan of the whole (unbounded) table.

        ``0.0`` when nothing is waiting -- the healthy, common case, not an
        error: an outbox with zero unpublished rows has no "age" to report,
        and ``0.0`` is exactly the value a scrape during that state should
        show (below every SLO threshold in ``07-nfr-slo`` §2, because there
        is genuinely nothing in flight).
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT min(created_at) FROM platform.outbox WHERE published_at IS NULL")
            )
            oldest: datetime | None = result.scalar_one_or_none()
        if oldest is None:
            return 0.0
        # `max(0.0, ...)` guards only against clock skew between this
        # process and Postgres (a negative age is never a real value) --
        # never against the ordinary case, which is always non-negative.
        return max(0.0, (datetime.now(UTC) - oldest).total_seconds())

    async def dlq_depths(self) -> dict[str, int]:
        """``XLEN`` of ``<stream>.dlq`` for every entry in
        ``DLQ_SOURCE_STREAMS``, keyed by the SOURCE stream name -- the
        ``ops.dlq`` module's own convention (never the ``.dlq`` suffix as the
        key, so a caller need not know the derivation to read the result)."""
        return {
            stream: int(await self._redis.xlen(f"{stream}.dlq")) for stream in DLQ_SOURCE_STREAMS
        }
