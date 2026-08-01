"""Live-Redis proof that the notification bridge's consumer group must be
PER-PROCESS, not shared (P0-2 -- ``docs/pre-release-review.md`` §2 ·
``docs/log/3.81.md``).

``live_redis`` only. Reproduces the real production topology directly: TWO
independent bridge consumers (``build_notification_consumer``, the SAME
wiring function ``CompositionRoot.from_env`` calls once per process), each
with its OWN ``ConnectionHub`` and its OWN live session for the SAME
workspace, reading the SAME stream -- exactly what ``WEB_CONCURRENCY=2``'s
two sibling gunicorn workers are, both inside ONE API replica.

``_NOTIFY_STREAMS`` is monkeypatched to a private, uniquely-named test stream
(the ``test_notification_bridge_live``/``test_stream_consumer_live``
precedent, R6) so this file never touches shared ``stream.knowledge``/
``stream.media`` -- ``build_notification_consumer`` itself is exercised
unmodified, only the module-level stream→types map it reads is swapped.

**Fails against the pre-3.81 code, on purpose.** Before 3.81,
``build_notification_consumer`` had no ``group`` parameter at all -- every
call shared the hardcoded ``_CG_NOTIFY = "cg.notify"`` literal. Calling it
twice with two DIFFERENT ``group=`` values (exactly what two independent
sibling processes need) is therefore impossible to even EXPRESS against the
old signature: it raises ``TypeError: build_notification_consumer() got an
unexpected keyword argument 'group'`` before either consumer is built.
Verified explicitly (docs/log/3.81.md): stashing this fix and re-running
this file reproduces exactly that failure.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

import pytest
from redis.asyncio import Redis

from app.framework.clock import utc_now
from app.framework.di import composition_root as cr_module
from app.framework.di.composition_root import (
    _sweep_stale_notify_groups,
    build_notification_consumer,
)
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.streaming import ConnectionHub
from app.framework.types import Json
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer
from tests.unit.support_streaming import InMemoryWsConnectionRegistry

pytestmark = [pytest.mark.live_redis]

_CE = b"ce"
_EVENT_TYPE = "knowledge.document.indexed.v1"


class _Session:
    def __init__(self) -> None:
        self.received: list[Json] = []

    async def send_json(self, payload: Json) -> None:
        self.received.append(payload)


async def _cleanup(client: Redis, stream: str, groups: list[str]) -> None:
    for group in groups:
        await client.xgroup_destroy(stream, group)
    await client.delete(stream)


def _envelope(*, workspace_id: str, document_id: str) -> Json:
    return build_envelope(
        event_id=new_uuid7(),
        source="knowledge",
        event_type=_EVENT_TYPE,
        subject=document_id,
        occurred_at=utc_now(),
        workspace_id=workspace_id,
        data={"document_id": document_id, "chunk_count": 1},
    )


@pytest.mark.anyio
async def test_two_sibling_bridge_processes_each_receive_the_notification(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix's own regression guard: two independent per-process groups on
    ONE stream each deliver their own copy, so BOTH sibling hubs -- and
    therefore both live sessions of the SAME workspace, one per sibling --
    receive the push."""
    stream = f"stream.test.{new_uuid7()}"
    monkeypatch.setattr(cr_module, "_NOTIFY_STREAMS", {stream: (_EVENT_TYPE,)})

    workspace_id = new_uuid7()
    document_id = new_uuid7()
    hub_a = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    hub_b = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    session_a, session_b = _Session(), _Session()
    await hub_a.try_register(workspace_id=workspace_id, user_id=new_uuid7(), session=session_a)
    await hub_b.try_register(workspace_id=workspace_id, user_id=new_uuid7(), session=session_b)

    hostname = socket.gethostname()
    # Two DISTINCT synthetic "sibling process" tags -- the exact shape
    # `CompositionRoot.from_env` derives from `f"{hostname}.{os.getpid()}"`,
    # faked here since both calls run inside this ONE test process.
    tag_a, tag_b = f"{hostname}.simA-{new_uuid7()}", f"{hostname}.simB-{new_uuid7()}"
    group_a, group_b = f"cg.notify.{tag_a}", f"cg.notify.{tag_b}"

    consumer_a, subs_a = build_notification_consumer(
        hub_a,
        redis_client,
        group=group_a,
        consumer_name=f"notify.{tag_a}",
        block_ms=200,
        batch_count=16,
        max_deliveries=5,
    )
    consumer_b, subs_b = build_notification_consumer(
        hub_b,
        redis_client,
        group=group_b,
        consumer_name=f"notify.{tag_b}",
        block_ms=200,
        batch_count=16,
        max_deliveries=5,
    )

    envelope = _envelope(workspace_id=workspace_id, document_id=document_id)
    try:
        await consumer_a.setup(subs_a)
        await consumer_b.setup(subs_b)

        await redis_client.xadd(stream, {_CE: json.dumps(envelope).encode()})

        handled_a = await consumer_a.run_once(subs_a)
        handled_b = await consumer_b.run_once(subs_b)

        assert handled_a == 1
        assert handled_b == 1
        expected = [
            {
                "type": "notification",
                "event": _EVENT_TYPE,
                "data": {"document_id": document_id, "chunk_count": 1},
            }
        ]
        # BOTH sibling processes' hubs got the push -- the whole point.
        assert session_a.received == expected
        assert session_b.received == expected
    finally:
        await _cleanup(redis_client, stream, [group_a, group_b])


@pytest.mark.anyio
async def test_a_group_shared_between_two_processes_only_delivers_to_one(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DEFECT's own shape, reproduced directly at the mechanism level
    (not merely "the function signature changed"): if two sibling processes
    were still forced onto ONE shared group -- the pre-3.81 hardcoded
    ``cg.notify`` -- Redis Streams hands the entry to exactly ONE member.
    Whichever sibling's hub does NOT win never sees the push, however many
    live sessions it holds for the workspace -- exactly what
    ``docs/pre-release-review.md`` P0-2 measured live as "roughly half of
    every notification never reaches the client"."""
    stream = f"stream.test.{new_uuid7()}"
    monkeypatch.setattr(cr_module, "_NOTIFY_STREAMS", {stream: (_EVENT_TYPE,)})

    workspace_id = new_uuid7()
    document_id = new_uuid7()
    hub_a = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    hub_b = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    session_a, session_b = _Session(), _Session()
    await hub_a.try_register(workspace_id=workspace_id, user_id=new_uuid7(), session=session_a)
    await hub_b.try_register(workspace_id=workspace_id, user_id=new_uuid7(), session=session_b)

    shared_group = f"cg.notify.test.{new_uuid7()}"  # the pre-3.81 shape: ONE name, both processes
    consumer_a, subs_a = build_notification_consumer(
        hub_a,
        redis_client,
        group=shared_group,
        consumer_name="notify.test.a",
        block_ms=200,
        batch_count=16,
        max_deliveries=5,
    )
    consumer_b, subs_b = build_notification_consumer(
        hub_b,
        redis_client,
        group=shared_group,
        consumer_name="notify.test.b",
        block_ms=200,
        batch_count=16,
        max_deliveries=5,
    )

    envelope = _envelope(workspace_id=workspace_id, document_id=document_id)
    try:
        await consumer_a.setup(
            subs_a
        )  # both `ensure_group` the SAME pair -- idempotent (BUSYGROUP)
        await consumer_b.setup(subs_b)

        await redis_client.xadd(stream, {_CE: json.dumps(envelope).encode()})

        handled_a = await consumer_a.run_once(subs_a)
        handled_b = await consumer_b.run_once(subs_b)

        # Exactly ONE of the two sibling processes gets the entry -- Redis
        # Streams hands each group entry to exactly one CONSUMER within that
        # group, and both processes share the group name here.
        assert handled_a + handled_b == 1
        assert (len(session_a.received), len(session_b.received)) in {(1, 0), (0, 1)}
    finally:
        await _cleanup(redis_client, stream, [shared_group])


@pytest.mark.anyio
async def test_teardown_destroys_the_bridges_own_group_leaving_none_behind(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.81's clean-shutdown counterpart
    (``CompositionRoot.teardown_notify_bridge`` -> ``StreamConsumer.
    teardown``): a graceful exit destroys THIS process's own group, verified
    against ``XINFO GROUPS`` directly -- not merely "no exception raised"."""
    stream = f"stream.test.{new_uuid7()}"
    monkeypatch.setattr(cr_module, "_NOTIFY_STREAMS", {stream: (_EVENT_TYPE,)})

    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    group = f"cg.notify.test.{new_uuid7()}"
    consumer, subs = build_notification_consumer(
        hub,
        redis_client,
        group=group,
        consumer_name="notify.test",
        block_ms=200,
        batch_count=16,
        max_deliveries=5,
    )
    try:
        await consumer.setup(subs)
        before = {g["name"].decode() for g in await redis_client.xinfo_groups(stream)}
        assert before == {group}

        await consumer.teardown(subs)

        after = await redis_client.xinfo_groups(stream)
        assert after == []
    finally:
        await _cleanup(redis_client, stream, [group])


@pytest.mark.anyio
async def test_sweep_stale_notify_groups_removes_a_dead_siblings_group_but_not_a_live_ones(
    redis_client: Redis,
) -> None:
    """The orphan-cleanup half of P0-2's fix, against REAL Redis and a REAL
    dead pid (not a fake liveness check): a group tagged with a pid that has
    genuinely exited is swept; a group tagged with THIS test process's own
    (very much alive) pid survives untouched."""
    stream = f"stream.test.{new_uuid7()}"
    hostname = socket.gethostname()
    consumer = RedisStreamsConsumer(redis_client)

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    dead_group = f"cg.notify.{hostname}.{dead.pid}"
    live_group = f"cg.notify.{hostname}.{os.getpid()}"
    try:
        await consumer.ensure_group(stream, dead_group)
        await consumer.ensure_group(stream, live_group)

        swept = await _sweep_stale_notify_groups(consumer, [stream], hostname=hostname)

        assert swept == [dead_group]
        remaining = {g["name"].decode() for g in await redis_client.xinfo_groups(stream)}
        assert remaining == {live_group}
    finally:
        await _cleanup(redis_client, stream, [dead_group, live_group])
