"""Live-Redis proof for the fix in ``stream-topology-plan.md`` §3, "الخطوة 1":
a blocking ``XREADGROUP ... BLOCK <block_ms>`` read on a QUIET stream must
outlive the socket's own read timeout, or a wait that is behaving exactly as
asked is cut mid-way as ``redis.exceptions.TimeoutError`` -- surfaced here as
``AppError("event consume failed")`` (``redis_streams.py::_translate_consume``)
-- deterministically, every cycle, on every quiet stream (§1-أ-1, measured
live against the ``knowledge``/``memory`` workers on 2026-08-03).

Deliberately builds its OWN client via ``create_redis_client`` rather than
reusing the ``redis_client`` fixture (``tests/integration/conftest.py:628``
builds from the SAME factory with the request-path default,
``_SOCKET_TIMEOUT_S`` == 2.0s) -- a test that inherited that client would
fail after the fix exactly as it failed before it, which is precisely the
trap the plan calls out by name (§3, item 1's warning under test 6).

Both tests below are read-only against a brand-new, uniquely-named stream +
group, cleaned up in ``finally`` -- no ``FLUSH``, the ``test_redis_cache.py``
precedent.
"""

from __future__ import annotations

import time

import pytest
from redis.asyncio import Redis

from app.framework.errors import AppError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import RedisSettings
from app.infrastructure.cache.redis_cache import blocking_read_timeout_s, create_redis_client
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer

pytestmark = [pytest.mark.live_redis]

# Deliberately > `_SOCKET_TIMEOUT_S` (2.0s) -- the exact gap §1-أ-1 measures:
# a consumer that asks Redis to wait `_BLOCK_MS` must get a socket that
# tolerates at least that long.
_BLOCK_MS = 3_000


async def _cleanup(client: Redis, stream: str, group: str) -> None:
    await client.xgroup_destroy(stream, group)
    await client.delete(stream)


async def test_blocking_read_on_a_quiet_stream_survives_with_the_derived_timeout(
    live_redis: str,
) -> None:
    """The fix (item 1+2): a client built with
    ``blocking_read_timeout_s(block_ms)`` reads past ``block_ms`` on a truly
    quiet, brand-new stream/group and returns cleanly -- no messages
    published, no exception raised, and the call actually waited out the
    full block window (proving it did not merely fail fast and slip through
    by luck)."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    client = create_redis_client(
        RedisSettings(url=live_redis), read_timeout_s=blocking_read_timeout_s(_BLOCK_MS)
    )
    consumer = RedisStreamsConsumer(client)
    try:
        await consumer.ensure_group(stream, group)

        started = time.monotonic()
        messages = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=_BLOCK_MS
        )
        elapsed_s = time.monotonic() - started

        assert messages == []
        assert elapsed_s >= _BLOCK_MS / 1000
    finally:
        await _cleanup(client, stream, group)
        await client.aclose()


async def test_blocking_read_on_a_quiet_stream_times_out_with_the_pre_fix_default(
    live_redis: str,
) -> None:
    """The proof of failure (plan §0 rule 3): the IDENTICAL read, over a
    client built the way every call site built it before this step
    (``create_redis_client(settings)``, no ``read_timeout_s`` -- still the
    default any caller gets today unless it opts in) crashes with the real
    defect mechanism, not a substitute: ``_SOCKET_TIMEOUT_S`` (2.0s) is
    shorter than ``_BLOCK_MS`` (3.0s), so the read is cut mid-wait and
    surfaces as ``AppError("event consume failed", code="common.internal")``
    -- ``redis_streams.py``'s own translation of the raw ``redis.exceptions.
    TimeoutError`` (§1-أ-1's measured message, reproduced here on demand)."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    client = create_redis_client(
        RedisSettings(url=live_redis)
    )  # no read_timeout_s -- the old default
    consumer = RedisStreamsConsumer(client)
    try:
        await consumer.ensure_group(stream, group)

        with pytest.raises(AppError) as excinfo:
            await consumer.read(
                streams=[stream], group=group, consumer="c1", count=10, block_ms=_BLOCK_MS
            )
        assert excinfo.value.code == "common.internal"
    finally:
        await _cleanup(client, stream, group)
        await client.aclose()
