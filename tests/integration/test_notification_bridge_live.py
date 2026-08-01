"""Live-Redis end-to-end test for the notification bridge (5.3-د · 03-api-spec
§3.2 · 04-event-catalog §2/§4).

``live_redis`` only. Proves the whole ``cg.notify`` path against REAL Redis:
a global worker-result event XADDed onto a stream is read by the generic 5.1
``StreamConsumer``, dispatched to ``make_notification_handler``, and pushed to
the live ``ConnectionHub`` session of exactly the event's workspace — the
bridge that closes AC-11's streaming story (SSE over a real orchestrator +
the WS endpoint + this fan-out to live sockets).

The handler is wired via ``make_notification_handler`` into a ``Subscription``
naming a UNIQUE stream + a unique ``cg.notify.test.<uuid>`` group (the
``test_stream_consumer_live``/``test_e2e_outbox_to_worker`` precedent, R6),
rather than the production ``build_notification_consumer`` topology, so the
test never touches shared ``stream.knowledge``/``stream.media`` and cleans up
its own group + stream in ``finally``. ``build_notification_consumer``'s real
topology (which streams, which types) is pinned hermetically in
``tests/unit/test_notifications.py``.
"""

from __future__ import annotations

import json

import pytest
from redis.asyncio import Redis

from app.framework.clock import utc_now
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.streaming import ConnectionHub, make_notification_handler
from app.framework.types import Json
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer
from tests.unit.support_streaming import InMemoryWsConnectionRegistry

pytestmark = [pytest.mark.live_redis]

_CE = b"ce"


class _Session:
    def __init__(self) -> None:
        self.received: list[Json] = []

    async def send_json(self, payload: Json) -> None:
        self.received.append(payload)


async def _cleanup(client: Redis, stream: str, group: str) -> None:
    await client.xgroup_destroy(stream, group)
    await client.delete(stream)


def _consumer(redis_client: Redis) -> StreamConsumer:
    return StreamConsumer(
        RedisStreamsConsumer(redis_client),
        consumer_name="notify.test",
        block_ms=200,
        batch_count=16,
        max_deliveries=5,
    )


@pytest.mark.anyio
async def test_an_indexed_event_reaches_only_its_workspace_session(
    redis_client: Redis,
) -> None:
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.notify.test.{new_uuid7()}"
    mine_ws = new_uuid7()
    other_ws = new_uuid7()
    document_id = new_uuid7()

    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    mine, theirs = _Session(), _Session()
    await hub.try_register(workspace_id=mine_ws, user_id=new_uuid7(), session=mine)
    await hub.try_register(workspace_id=other_ws, user_id=new_uuid7(), session=theirs)

    handler = make_notification_handler(hub)
    subscription = Subscription(
        stream=stream, group=group, handlers={"knowledge.document.indexed.v1": handler}
    )
    consumer = _consumer(redis_client)

    envelope = build_envelope(
        event_id=new_uuid7(),
        source="knowledge",
        event_type="knowledge.document.indexed.v1",
        subject=document_id,
        occurred_at=utc_now(),
        workspace_id=mine_ws,
        data={"document_id": document_id, "chunk_count": 3},
    )
    try:
        await consumer.setup([subscription])

        await redis_client.xadd(stream, {_CE: json.dumps(envelope).encode()})

        handled = await consumer.run_once([subscription])

        assert handled == 1
        # Routed to the event's workspace ONLY (tenant isolation IS the routing).
        assert mine.received == [
            {
                "type": "notification",
                "event": "knowledge.document.indexed.v1",
                "data": {"document_id": document_id, "chunk_count": 3},
            }
        ]
        assert theirs.received == []

        # The entry was XACKed (a successful push) -- a second pass finds
        # nothing pending, so no duplicate notification.
        again = await consumer.run_once([subscription])
        assert again == 0
        assert len(mine.received) == 1
    finally:
        await _cleanup(redis_client, stream, group)


@pytest.mark.anyio
async def test_a_media_generated_event_pushes_the_job_result(
    redis_client: Redis,
) -> None:
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.notify.test.{new_uuid7()}"
    workspace_id = new_uuid7()
    job_id = new_uuid7()
    result_file_id = new_uuid7()

    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    session = _Session()
    await hub.try_register(workspace_id=workspace_id, user_id=new_uuid7(), session=session)

    handler = make_notification_handler(hub)
    subscription = Subscription(
        stream=stream, group=group, handlers={"media.job.generated.v1": handler}
    )
    consumer = _consumer(redis_client)

    envelope = build_envelope(
        event_id=new_uuid7(),
        source="media",
        event_type="media.job.generated.v1",
        subject=job_id,
        occurred_at=utc_now(),
        workspace_id=workspace_id,
        data={"job_id": job_id, "result_file_id": result_file_id},
    )
    try:
        await consumer.setup([subscription])

        await redis_client.xadd(stream, {_CE: json.dumps(envelope).encode()})

        handled = await consumer.run_once([subscription])

        assert handled == 1
        assert session.received == [
            {
                "type": "notification",
                "event": "media.job.generated.v1",
                "data": {"job_id": job_id, "result_file_id": result_file_id},
            }
        ]
    finally:
        await _cleanup(redis_client, stream, group)


@pytest.mark.anyio
async def test_an_event_with_nobody_connected_is_acked_not_stuck(
    redis_client: Redis,
) -> None:
    """A notification for a workspace with no live session is a successful
    no-op push (the durable record lives in the producing module's tables),
    XACKed and never redelivered -- not poison, not a leak."""
    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.notify.test.{new_uuid7()}"
    workspace_id = new_uuid7()

    hub = ConnectionHub(
        max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()
    )  # nobody registered
    handler = make_notification_handler(hub)
    subscription = Subscription(
        stream=stream, group=group, handlers={"media.job.failed.v1": handler}
    )
    consumer = _consumer(redis_client)

    envelope = build_envelope(
        event_id=new_uuid7(),
        source="media",
        event_type="media.job.failed.v1",
        subject=new_uuid7(),
        occurred_at=utc_now(),
        workspace_id=workspace_id,
        data={"job_id": new_uuid7(), "reason": "generation timed out"},
    )
    try:
        await consumer.setup([subscription])

        await redis_client.xadd(stream, {_CE: json.dumps(envelope).encode()})

        handled = await consumer.run_once([subscription])
        assert handled == 1  # a no-op push is still a success → XACK

        again = await consumer.run_once([subscription])
        assert again == 0  # acked, nothing pending
    finally:
        await _cleanup(redis_client, stream, group)
