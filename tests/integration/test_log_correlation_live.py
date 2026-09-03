"""The worker half of capacity step 0.6, against real Redis: an event that
fails carries the ids that tie it back to the request that produced it.

**Why this needs to be live.** The hermetic tests
(``tests/unit/test_log_correlation.py``) prove the formatter writes what is
bound and that ``consumers/engine.py`` contains the binding. Neither can prove
the thing that actually breaks: that the id survives the round trip through a
real ``XADD``/``XREADGROUP``, arrives on the envelope where the engine looks
for it, and is still bound when the handler's failure is logged several
``await``\\ s later, in a coroutine the engine did not write. Context variables
and asynchronous frameworks are exactly where a "surely it propagates"
assumption is wrong, and the failure is silent -- a log line with one field
missing.

**And why the DEAD-LETTER path specifically.** ``handler_failed`` and
``dead_lettered`` are the two lines an operator goes looking for when an event
produced no effect. They are also the two emitted furthest from the binding:
after the handler raised, inside the ``except``, and (for the second) inside a
different method entirely. If the binding survives to there it survives
everywhere.

``live_redis`` only -- no database is involved. Everything targets a FRESH,
UNIQUE ``stream.test.<uuid>``/``cg.test.<uuid>`` pair (R6), so this never
touches a production stream or the DLQ an operator is reading.

⚠️ The full three-tier join (edge → app → worker in ONE Loki query) is NOT
tested here: Loki is ``expose``-only, so reaching it needs a container on the
``aizzak_default`` network -- the ``RUN_P1_6_LOAD_TEST`` shape, which is
deliberately never auto-run. That join was measured by hand instead and the
result is recorded in ``docs/capacity-status.md``; what a test can hold cheaply
and forever is the agreement between the config files (the unit module's drift
guards) and this, the one hop where propagation can silently stop.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from redis.asyncio import Redis

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.observability.logging import WORKSPACE_FIELD, JsonFormatter
from app.framework.observability.pseudonymity import pseudonymous_id
from app.framework.types import Json
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer

pytestmark = [pytest.mark.live_redis, pytest.mark.anyio]

_EVENT_TYPE = "memory.item.stored.v1"
# `max_deliveries=1` so ONE `run_once` reaches the dead-letter branch: the
# point here is the two log lines, not the retry budget (which
# `test_dlq_ops_live.py` owns).
_MAX_DELIVERIES = 1


class _CapturingHandler(logging.Handler):
    """Collects the REAL formatter's output, parsed back from JSON.

    Deliberately the production ``JsonFormatter`` and not a mock: what is under
    test is the line an operator would find in Loki, and a handler that
    recorded ``record.msg`` would pass while the formatter dropped every id.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))

    def of(self, message: str) -> list[dict[str, Any]]:
        return [line for line in self.lines if line.get("message") == message]


@pytest.fixture
def captured_engine_log() -> AsyncIterator[_CapturingHandler]:
    """Attach to the engine's OWN logger for the duration of one test."""
    logger = logging.getLogger("app.infrastructure.messaging.consumers.engine")
    handler = _CapturingHandler()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@pytest.fixture
async def isolated_stream(redis_client: Redis) -> AsyncIterator[tuple[str, str]]:
    """A fresh ``(stream, group)`` pair, destroyed afterwards (R6)."""
    suffix = new_uuid7()
    stream = f"stream.test.{suffix}"
    group = f"cg.test.{suffix}"
    await RedisStreamsConsumer(redis_client).ensure_group(stream, group)
    try:
        yield stream, group
    finally:
        with contextlib.suppress(Exception):
            await redis_client.delete(stream, f"{stream}.dlq")


def _envelope(*, workspace_id: str, correlation_id: str | None) -> Json:
    return build_envelope(
        event_id=new_uuid7(),
        source="urn:aizzak:test:capacity-0.6",
        event_type=_EVENT_TYPE,
        subject=new_uuid7(),
        occurred_at=datetime.now(UTC),
        workspace_id=workspace_id,
        data={"item_id": new_uuid7()},
        correlation_id=correlation_id,
    )


async def _dispatch_one_failure(
    redis_client: Redis, stream: str, group: str, envelope: Json
) -> None:
    """Publish ``envelope`` and let a real consumer take it to the DLQ."""

    async def always_fails(_ctx: ExecutionContext, _envelope: Json) -> None:
        raise RuntimeError("deliberate handler failure -- capacity 0.6 probe")

    await redis_client.xadd(stream, {"ce": json.dumps(envelope)})
    consumer = StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="capacity-0.6-probe",
        block_ms=500,
        batch_count=10,
        max_deliveries=_MAX_DELIVERIES,
    )
    subscription = Subscription(stream=stream, group=group, handlers={_EVENT_TYPE: always_fails})
    await consumer.run_once([subscription])


async def test_a_failed_event_is_findable_by_the_requests_correlation_id(
    redis_client: Redis,
    isolated_stream: tuple[str, str],
    captured_engine_log: _CapturingHandler,
) -> None:
    """The step's whole purpose, at the hop where it can silently stop.

    The correlation id here is the one the API bound onto its own lines and
    wrote into the outbox row; by the time it reaches this assertion it has
    crossed a JSON encode, an ``XADD``, an ``XREADGROUP`` in another
    coroutine, and a raised exception.
    """
    stream, group = isolated_stream
    correlation_id = new_uuid7()
    workspace_id = new_uuid7()
    envelope = _envelope(workspace_id=workspace_id, correlation_id=correlation_id)

    await _dispatch_one_failure(redis_client, stream, group, envelope)

    failures = captured_engine_log.of("handler_failed")
    assert len(failures) == 1, f"expected one handler_failed line, got {captured_engine_log.lines}"
    line = failures[0]
    assert line["correlation_id"] == correlation_id
    assert line["event_id"] == envelope["id"]
    assert line["level"] == "ERROR"


async def test_the_dead_letter_line_carries_them_too(
    redis_client: Redis,
    isolated_stream: tuple[str, str],
    captured_engine_log: _CapturingHandler,
) -> None:
    """``dead_lettered`` is emitted from a DIFFERENT METHOD than the one that
    opened the binding -- the furthest point in the engine from
    ``log_context``. An id that survives to here survives anywhere in it."""
    stream, group = isolated_stream
    correlation_id = new_uuid7()
    envelope = _envelope(workspace_id=new_uuid7(), correlation_id=correlation_id)

    await _dispatch_one_failure(redis_client, stream, group, envelope)

    transfers = captured_engine_log.of("dead_lettered")
    assert len(transfers) == 1
    assert transfers[0]["correlation_id"] == correlation_id
    assert transfers[0]["event_id"] == envelope["id"]
    assert transfers[0]["dlq"] == f"{stream}.dlq"


async def test_the_tenant_reaches_the_line_pseudonymised(
    redis_client: Redis,
    isolated_stream: tuple[str, str],
    captured_engine_log: _CapturingHandler,
) -> None:
    """«``workspace_id`` مموّهاً» (0.6), proven on the path that actually
    writes it rather than on a synthetic record. The raw id is on the envelope
    and in the ``ExecutionContext``; it must not be in the line."""
    stream, group = isolated_stream
    workspace_id = new_uuid7()
    envelope = _envelope(workspace_id=workspace_id, correlation_id=new_uuid7())

    await _dispatch_one_failure(redis_client, stream, group, envelope)

    line = captured_engine_log.of("handler_failed")[0]
    assert line[WORKSPACE_FIELD] == pseudonymous_id(workspace_id)
    assert workspace_id not in json.dumps(line, ensure_ascii=False)


async def test_an_envelope_without_a_correlation_id_falls_back_to_the_event_id(
    redis_client: Redis,
    isolated_stream: tuple[str, str],
    captured_engine_log: _CapturingHandler,
) -> None:
    """The engine's own long-standing fallback, now visible in the log for the
    first time. It matters because a line with NO id is unjoinable, while a
    line whose correlation id IS the event id still groups every attempt at
    that one event together."""
    stream, group = isolated_stream
    envelope = _envelope(workspace_id=new_uuid7(), correlation_id=None)

    await _dispatch_one_failure(redis_client, stream, group, envelope)

    line = captured_engine_log.of("handler_failed")[0]
    assert line["correlation_id"] == envelope["id"] == line["event_id"]


async def test_the_binding_does_not_outlive_the_message(
    redis_client: Redis,
    isolated_stream: tuple[str, str],
    captured_engine_log: _CapturingHandler,
) -> None:
    """The reason the engine uses the RESTORING helper. One long-lived task
    handles message after message, so a bare ``set()`` would stamp the first
    message's ids onto the second's lines -- and the second event would be
    investigated as part of the first request's incident."""
    stream, group = isolated_stream
    first = _envelope(workspace_id=new_uuid7(), correlation_id=new_uuid7())
    second = _envelope(workspace_id=new_uuid7(), correlation_id=new_uuid7())

    await _dispatch_one_failure(redis_client, stream, group, first)
    await _dispatch_one_failure(redis_client, stream, group, second)

    correlations = [line["correlation_id"] for line in captured_engine_log.of("handler_failed")]
    assert correlations == [first["correlationid"], second["correlationid"]], (
        "the second message's line carries the first message's correlation id -- the "
        "binding leaked across deliveries"
    )
