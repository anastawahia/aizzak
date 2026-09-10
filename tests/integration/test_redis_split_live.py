"""The acceptance criterion of capacity step 5.2, against both live servers.

The criterion, verbatim: «ملءُ الذاكرة المخبّأة حتّى سقفها لا يفقد رسالةَ مجرًى
واحدة ولا مدخلَ اتصالٍ واحداً» -- filling the cache to its ceiling loses no
stream message and no connection entry.

⚠️ **A test of that sentence is worthless unless it can fail**, and against
two separate servers it cannot: of course filling instance B leaves instance A
alone. So this module asserts three things the sentence does not say, and each
one is what stops it from passing vacuously:

1. **The fill actually reached the ceiling.** ``evicted_keys`` on the cache
   instance must MOVE. Without this the test passes on a fill that never
   filled anything.
2. **The stream instance still ACCEPTS a write afterwards.** This is the half
   the criterion omits, and it is the half that fails on the other
   single-instance configuration. MEASURED on a throwaway 16 MB Redis at
   ``maxmemory-policy noeviction`` with cache-shaped values only:

       XADD stream.knowledge        -> OOM command not allowed ...
       HSET ws:conn:user-1 (WS reg) -> OOM command not allowed ...
       ZADD rate:user:user-1 (1.2)  -> OOM command not allowed ...
       SET auth:revoked:<sub>       -> OOM command not allowed ...

   i.e. on ONE instance a flood of disposable query vectors makes it
   impossible to publish an event, register a WebSocket session, or REVOKE A
   COMPROMISED SESSION. "No data lost" is true there and is not enough.
3. **The stream instance evicted nothing.** ``noeviction`` is what the whole
   split rests on, and a policy is a runtime value, not a file.

**And the other single-instance option, measured the same way** -- one 16 MB
Redis at ``allkeys-lru``, seeded with 2,000 stream entries, 300 ``ws:conn``
hashes, 300 rate windows and one ``auth:revoked:`` entry, then filled with
``embed:v1:``-shaped 1,536-byte vectors:

    stream entries      2000 -> 0        <- the whole KEY went, not entries
    ws:conn keys         300 -> 9
    rate: windows        300 -> 15
    auth:revoked entry     1 -> 0        <- the revoked token works again
    evicted_keys        5375   errors: 0

Zero errors and no log line anywhere. That is ``ح-10``'s "عطب صامت" as a
number, and note the first row: LRU evicts KEYS, so one eviction decision took
two thousand events at once.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.framework.identifiers import new_uuid7

pytestmark = [pytest.mark.live_redis, pytest.mark.live_redis_cache]

# Small enough that the fill is a second of work, large enough that Redis's own
# baseline (~1 MB of allocator and buffers) is not the whole budget.
_TEST_MAXMEMORY = 8 * 1024 * 1024
# `caching_embedding.py` packs float32: 384 dimensions -> 1,536 bytes. Using
# the real width matters -- a fill made of tiny values reaches the ceiling
# through key overhead instead of through values, which is not the shape the
# ceiling was sized for.
_VECTOR_BYTES = b"\x00" * 1536
_STREAM_ENTRIES = 500
_CONNECTIONS = 50


async def _info_field(client: Redis, section: str, field: str) -> int:
    info = await client.info(section)
    return int(info[field])


@pytest.fixture
async def seeded_stream_keys(redis_client: Redis) -> AsyncIterator[dict[str, str]]:
    """One of each correctness-bearing population on the ``noeviction``
    instance, under a unique prefix (the ``live_redis`` fixture's own rule:
    the server holds unrelated data, so a test owns only what it named).

    All four shapes are here rather than just the two the criterion names,
    because the criterion names the two that are easiest to think of and the
    other two are the ones that hurt: an evicted rate-limit window hands a
    tenant free quota, and an evicted ``auth:revoked:`` entry re-validates a
    revoked token.
    """
    unique = str(new_uuid7())
    keys = {
        "stream": f"stream.test.{unique}",
        "connection": f"ws:conn:test-{unique}",
        "rate": f"rate:user:test-{unique}",
        "revoked": f"auth:revoked:test-{unique}",
    }
    for index in range(_STREAM_ENTRIES):
        await redis_client.xadd(keys["stream"], {b"ce": f'{{"seq":{index}}}'.encode()})
    await redis_client.hset(keys["connection"], mapping={b"tab-a": b"1", b"tab-b": b"1"})
    await redis_client.zadd(keys["rate"], {b"member": 1757500000000})
    await redis_client.set(keys["revoked"], b"1", ex=3660)
    try:
        yield keys
    finally:
        await redis_client.delete(*keys.values())


async def _fill_past_ceiling(client: Redis, marker: str, *, evicted_before: int) -> int:
    """Write cache-shaped values until the server starts evicting, and return
    how many were written.

    ⚠️ **The loop watches ``evicted_keys``, NOT ``used_memory``, and the first
    version of this function got that wrong.** Under ``allkeys-lru`` Redis
    frees memory to STAY UNDER ``maxmemory``, so ``used_memory`` converges on
    the ceiling and never passes it: a `while used_memory < maxmemory` loop
    runs forever on a server that is already evicting, which is precisely the
    state the loop was waiting for. The eviction counter is the only
    unambiguous signal that the ceiling was reached -- and it is the same
    number the test then asserts on, so the fill and its witness cannot
    disagree.

    Batched through a pipeline rather than issued one at a time: this has to
    move megabytes, and the point of the test is what the OTHER server does
    while it happens, not how fast this one accepts writes.
    """
    written = 0
    while await _info_field(client, "stats", "evicted_keys") <= evicted_before:
        pipeline = client.pipeline(transaction=False)
        for index in range(written, written + 500):
            pipeline.set(f"embed:v1:{marker}:{index}", _VECTOR_BYTES, ex=600)
        await pipeline.execute()
        written += 500
        assert written < 100_000, (
            f"wrote {written} values of {len(_VECTOR_BYTES)} bytes against a "
            f"{_TEST_MAXMEMORY}-byte ceiling and the cache instance still evicted nothing"
        )
    return written


async def test_filling_the_cache_to_its_ceiling_costs_the_stream_instance_nothing(
    redis_client: Redis, cache_redis_client: Redis, seeded_stream_keys: dict[str, str]
) -> None:
    """5.2's acceptance criterion, plus the three assertions that make it
    falsifiable.

    ⚠️ **This lowers ``maxmemory`` on the cache instance and puts it back.**
    Mutating a shared server's runtime config from a test is not something to
    do lightly, and the reason it is acceptable here is itself a consequence
    of the step: this instance persists nothing, holds only reconstructible
    values, and is the one server in the topology whose entire contents may be
    thrown away at any moment. Doing the same to ``redis-stream`` would be
    unacceptable, and nothing here touches it.
    """
    original = (await cache_redis_client.config_get("maxmemory"))["maxmemory"]
    policy = (await cache_redis_client.config_get("maxmemory-policy"))["maxmemory-policy"]
    assert policy == "allkeys-lru", (
        f"the cache instance is running {policy!r}, not `allkeys-lru` -- this test would "
        "measure a server that is not the one 5.2 describes"
    )

    marker = str(new_uuid7())
    evicted_before = await _info_field(cache_redis_client, "stats", "evicted_keys")
    stream_evicted_before = await _info_field(redis_client, "stats", "evicted_keys")
    await cache_redis_client.config_set("maxmemory", _TEST_MAXMEMORY)
    try:
        await _fill_past_ceiling(cache_redis_client, marker, evicted_before=evicted_before)
        evicted_after = await _info_field(cache_redis_client, "stats", "evicted_keys")
    finally:
        await cache_redis_client.config_set("maxmemory", original)
        cursor, batch = 0, []
        while True:
            cursor, found = await cache_redis_client.scan(
                cursor, match=f"embed:v1:{marker}:*", count=1000
            )
            batch.extend(found)
            if cursor == 0:
                break
        if batch:
            await cache_redis_client.delete(*batch)

    # (1) The fill was real. Without this the rest passes on an empty gesture.
    assert evicted_after > evicted_before, (
        "the cache instance evicted nothing, so it never reached its ceiling and this test "
        "proved nothing about what happens when it does"
    )

    # The criterion itself: not one stream entry, not one connection record.
    assert await redis_client.xlen(seeded_stream_keys["stream"]) == _STREAM_ENTRIES
    assert await redis_client.hlen(seeded_stream_keys["connection"]) == 2
    assert await redis_client.zcard(seeded_stream_keys["rate"]) == 1
    # And the entry whose loss is a security defect rather than a performance one.
    assert await redis_client.exists(seeded_stream_keys["revoked"]) == 1, (
        "the session denylist entry is gone. A miss reads as 'not revoked' "
        "(framework/auth/revocation.py), so this is a revoked token working again"
    )

    # (2) The half the criterion does not state -- and the half that fails when
    # one `noeviction` instance serves both roles.
    entry_id = await redis_client.xadd(seeded_stream_keys["stream"], {b"ce": b'{"seq":"after"}'})
    assert entry_id, "the stream instance refused a write while the cache instance was full"
    await redis_client.xdel(seeded_stream_keys["stream"], entry_id)

    # (3) The policy is a runtime value, not a line in a file.
    assert await _info_field(redis_client, "stats", "evicted_keys") == stream_evicted_before, (
        "the stream instance evicted a key -- its `maxmemory-policy` is not `noeviction`, "
        "and everything on it is now silently discardable"
    )


async def test_the_two_instances_are_actually_two_servers(
    redis_client: Redis, cache_redis_client: Redis
) -> None:
    """The premise the module above rests on, checked rather than assumed.

    ``CACHE_REDIS_URL`` collapsing onto ``REDIS_URL`` is a SUPPORTED
    configuration (the ``م-8`` off switch), and under it every assertion in
    this file would be measuring one server twice -- passing the eviction
    check and then failing the survival checks, for a reason that has nothing
    to do with a defect. Naming the premise turns that into one honest failure
    with the cause in its message.
    """
    stream_id = (await redis_client.info("server"))["run_id"]
    cache_id = (await cache_redis_client.info("server"))["run_id"]
    assert stream_id != cache_id, (
        "TEST_REDIS_URL and TEST_CACHE_REDIS_URL point at the SAME server "
        f"(run_id {stream_id}). 5.2's acceptance criterion cannot be measured on one "
        "instance -- `maxmemory` and `maxmemory-policy` are server-wide"
    )
    assert (await redis_client.config_get("maxmemory-policy"))["maxmemory-policy"] == "noeviction"
    assert int((await redis_client.config_get("maxmemory"))["maxmemory"]) > 0, (
        "the stream instance has no `maxmemory`. `noeviction` without a ceiling is not a "
        "bound at all -- it is the pre-5.2 state ح-10 describes"
    )
