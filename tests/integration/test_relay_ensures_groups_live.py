"""Live-Redis proof for ``stream-topology-plan.md`` §3, "الخطوة 4": the FIX
step 3 shipped -- ``build_relay_from_env`` provisions every static consumer
group at the stream's tail (``ensure_topology``) BEFORE the relay's first
publish -- through the REAL closure, on a REAL Redis server, and the OLD
defect it replaces, reproduced by the real mechanism (a group born AFTER the
entries it should have seen never delivers them, ``redis_streams.py:235``'s
``xgroup_create(..., id="$", mkstream=True)`` -- §1-ب of the plan, unchanged).

**Why this file builds ``REDIS_URL`` into the environment before calling
``build_relay_from_env()``.** That function resolves its Redis client
through ``load_settings()`` (``env_settings.py``'s single ``.env`` reader),
and this repo's checked-in ``.env``/``.env.example`` pin ``REDIS_URL`` to the
Docker-only hostname ``redis://redis:6379/0`` -- verified directly against
this test environment (``load_settings().redis.url == "redis://redis:6379/0"``,
unreachable outside the Compose network). The ``live_redis`` fixture, by
contrast, hands out ``TEST_REDIS_URL`` or ``redis://127.0.0.1:6379/0``
(``tests/integration/conftest.py:621``). Those are two different servers by
default. Every test below therefore does
``monkeypatch.setenv("REDIS_URL", live_redis)`` -- **before** the first call
to ``build_relay_from_env()`` -- so the relay's own client and this test's
own read-back both land on the SAME Redis the fixture just proved reachable.
Skipping this line would let a test provision groups on one server and read
from another: it could only pass by accident (an unrelated local Redis
happening to also be quiet), never because the fix under test actually ran.

Follows the ``test_blocking_read_timeout_live.py`` precedent: unique
``new_uuid7()``-suffixed stream/group names, cleanup in ``finally``
(``xgroup_destroy`` + ``delete``), no ``FLUSH`` -- the ``live_redis`` fixture
docstring's rule (``tests/integration/conftest.py:613``): the server may
hold unrelated data.

``STATIC_CONSUMER_TOPOLOGY`` is monkeypatched at
``app.workers.bootstrap.STATIC_CONSUMER_TOPOLOGY`` -- where
``build_relay_from_env`` actually looks it up (a plain ``from ... import``
binds a NEW name into that module's namespace; patching
``app.framework.events.topology.STATIC_CONSUMER_TOPOLOGY`` instead would
leave ``bootstrap.py``'s already-bound reference untouched) -- to a tuple
holding exactly ONE brand-new binding, so this test never touches the four
real production pairs (``stream.files``/``stream.knowledge``/
``stream.media``/``stream.memory``) or their real consumer groups.

The mutation proof for §0 rule 3 / acceptance criterion 6 (test 1 must
detect the shipped fix's removal, not just describe the old defect by
construction) is carried out by hand against real Redis and its captured
transcript lives in ``docs/log/3.108.md`` §3 -- not reproduced as code here,
since it requires temporarily mutating ``src/app/workers/bootstrap.py``,
which this test file must not do permanently.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from redis.asyncio import Redis

from app.framework.di.lifecycle import Disposable, dispose_all
from app.framework.events.topology import ConsumerBinding
from app.framework.identifiers import new_uuid7
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer
from app.workers.bootstrap import build_relay_from_env

pytestmark = [pytest.mark.live_redis]

_CE_FIELD = b"ce"


def _redis_client_of(disposables: Sequence[Disposable]) -> Redis:
    """The ``test_relay_topology_provisioning.py`` lookup, restated here so
    this file stays self-contained: pick the one ``Redis`` client out of a
    builder's disposables list by bound-method ownership (``__self__``)."""
    for dispose in disposables:
        owner = getattr(dispose, "__self__", None)
        if isinstance(owner, Redis):
            return owner
    raise AssertionError("no Redis client found among the disposables")


async def test_ensure_topology_then_publish_delivers_all_three_entries(
    live_redis: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix (plan §3 step 4, items 1-3): ``ensure_topology()`` -- built
    from the REAL ``build_relay_from_env()``, with ``STATIC_CONSUMER_TOPOLOGY``
    monkeypatched to one brand-new pair -- runs BEFORE three ``XADD``\\ s, so
    a consumer joining that group for the very first time reads all three,
    with the exact payloads published (not merely "non-empty")."""
    monkeypatch.setenv("REDIS_URL", live_redis)

    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    monkeypatch.setattr(
        "app.workers.bootstrap.STATIC_CONSUMER_TOPOLOGY",
        (ConsumerBinding(stream=stream, group=group),),
    )

    _, ensure_topology, disposables = build_relay_from_env()
    client = _redis_client_of(disposables)
    payloads = [f"payload-{i}".encode() for i in range(3)]
    try:
        await ensure_topology()

        for payload in payloads:
            await client.xadd(stream, {_CE_FIELD: payload})

        consumer = RedisStreamsConsumer(client)
        messages = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )

        assert len(messages) == 3
        assert [message.raw for message in messages] == payloads
    finally:
        await client.xgroup_destroy(stream, group)
        await client.delete(stream)
        await dispose_all(disposables)


async def test_publish_then_ensure_topology_delivers_nothing(
    live_redis: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old defect (plan §3 step 4, item 4), by its real mechanism: three
    entries published BEFORE the group is created at ``$`` (the stream's
    tail, ``redis_streams.py:235``) are never delivered to a consumer that
    joins that group afterwards -- exactly zero entries read.

    Guards the guard: also asserts ``XLEN`` == 3 on the stream, so "read
    nothing" is proven to mean "the group was born past three real entries",
    not "nothing was ever published" (an empty stream would pass this
    assertion for the wrong reason)."""
    monkeypatch.setenv("REDIS_URL", live_redis)

    stream = f"stream.test.{new_uuid7()}"
    group = f"cg.test.{new_uuid7()}"
    monkeypatch.setattr(
        "app.workers.bootstrap.STATIC_CONSUMER_TOPOLOGY",
        (ConsumerBinding(stream=stream, group=group),),
    )

    _, ensure_topology, disposables = build_relay_from_env()
    client = _redis_client_of(disposables)
    payloads = [f"payload-{i}".encode() for i in range(3)]
    try:
        for payload in payloads:
            await client.xadd(stream, {_CE_FIELD: payload})

        # Guard the guard: prove the three entries actually landed BEFORE
        # asserting the read below sees none of them.
        assert await client.xlen(stream) == 3

        await ensure_topology()

        consumer = RedisStreamsConsumer(client)
        messages = await consumer.read(
            streams=[stream], group=group, consumer="c1", count=10, block_ms=200
        )

        assert messages == []
    finally:
        await client.xgroup_destroy(stream, group)
        await client.delete(stream)
        await dispose_all(disposables)
