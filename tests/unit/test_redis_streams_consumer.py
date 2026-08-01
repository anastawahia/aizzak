"""Unit tests for ``RedisStreamsConsumer`` (5.1-ج, ``infrastructure/messaging/
redis_streams.py``).

Hermetic: a fake stands in for ``redis.asyncio.Redis`` recording every
``xgroup_create``/``xreadgroup``/``xack`` call, so these tests never touch a
real Redis -- the ``test_redis_streams_publisher.py`` precedent, applied to
the consumer side. ``tests/integration/test_stream_consumer_live.py`` proves
the same adapter end to end against real Redis.
"""

from __future__ import annotations

import pytest
from redis import RedisError, ResponseError

from app.framework.errors import AppError
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, StreamMessage


class _FakePipeline:
    """Stands in for ``redis.asyncio.client.Pipeline`` exactly as
    ``dead_letter`` drives it: async context manager, sync command
    queueing, one awaited ``execute``."""

    def __init__(self, client: _FakeRedisClient, transaction: bool) -> None:
        self._client = client
        self.transaction = transaction
        self.commands: list[tuple[object, ...]] = []

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def xadd(self, name: str, fields: dict[bytes, bytes]) -> _FakePipeline:
        self.commands.append(("xadd", name, dict(fields)))
        return self

    def xack(self, name: str, groupname: str, entry_id: str) -> _FakePipeline:
        self.commands.append(("xack", name, groupname, entry_id))
        return self

    async def execute(self) -> list[object]:
        if self._client.pipeline_error is not None:
            raise self._client.pipeline_error
        self._client.pipelines_executed.append((self.transaction, list(self.commands)))
        return [object()] * len(self.commands)


class _FakeRedisClient:
    """Emulates the methods ``RedisStreamsConsumer`` touches."""

    def __init__(self) -> None:
        self.xgroup_create_calls: list[tuple[str, str, str, bool]] = []
        self.xgroup_destroy_calls: list[tuple[str, str]] = []
        self.xinfo_groups_calls: list[str] = []
        self.xreadgroup_calls: list[tuple[str, str, dict[str, str], int | None, int | None]] = []
        self.xack_calls: list[tuple[str, str, str]] = []
        self.xpending_range_calls: list[tuple[str, str, str, str, int, str | None]] = []
        self.pipelines_executed: list[tuple[bool, list[tuple[object, ...]]]] = []
        self.pipeline_error: Exception | None = None
        self._xgroup_create_error: Exception | None = None
        self._xgroup_destroy_error: Exception | None = None
        self._xinfo_groups_error: Exception | None = None
        self._xinfo_groups_response: list[dict[str, object]] = []
        self._xreadgroup_responses: list[list[object]] = []
        self._xreadgroup_error: Exception | None = None
        self._xack_error: Exception | None = None
        self._xpending_responses: list[list[dict[str, object]]] = []

    def fail_xgroup_create(self, exc: Exception) -> None:
        self._xgroup_create_error = exc

    def fail_xgroup_destroy(self, exc: Exception) -> None:
        self._xgroup_destroy_error = exc

    def fail_xinfo_groups(self, exc: Exception) -> None:
        self._xinfo_groups_error = exc

    def queue_xinfo_groups_response(self, response: list[dict[str, object]]) -> None:
        self._xinfo_groups_response = response

    def queue_xreadgroup_response(self, response: list[object]) -> None:
        self._xreadgroup_responses.append(response)

    def queue_xpending_response(self, response: list[dict[str, object]]) -> None:
        self._xpending_responses.append(response)

    def fail_xreadgroup(self, exc: Exception) -> None:
        self._xreadgroup_error = exc

    def fail_xack(self, exc: Exception) -> None:
        self._xack_error = exc

    async def xgroup_create(
        self, name: str, groupname: str, id: str = "$", mkstream: bool = False
    ) -> bool:
        self.xgroup_create_calls.append((name, groupname, id, mkstream))
        if self._xgroup_create_error is not None:
            raise self._xgroup_create_error
        return True

    async def xgroup_destroy(self, name: str, groupname: str) -> int:
        self.xgroup_destroy_calls.append((name, groupname))
        if self._xgroup_destroy_error is not None:
            raise self._xgroup_destroy_error
        return 1

    async def xinfo_groups(self, name: str) -> list[dict[str, object]]:
        self.xinfo_groups_calls.append(name)
        if self._xinfo_groups_error is not None:
            raise self._xinfo_groups_error
        return self._xinfo_groups_response

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[object]:
        self.xreadgroup_calls.append((groupname, consumername, dict(streams), count, block))
        if self._xreadgroup_error is not None:
            raise self._xreadgroup_error
        if self._xreadgroup_responses:
            return self._xreadgroup_responses.pop(0)
        return []

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        self.xack_calls.append((name, groupname, ids[0]))
        if self._xack_error is not None:
            raise self._xack_error
        return len(ids)

    async def xpending_range(
        self,
        name: str,
        groupname: str,
        min: str,
        max: str,
        count: int,
        consumername: str | None = None,
    ) -> list[dict[str, object]]:
        self.xpending_range_calls.append((name, groupname, min, max, count, consumername))
        if self._xpending_responses:
            return self._xpending_responses.pop(0)
        return []

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self, transaction)


# --------------------------------------------------------------------------- #
# ensure_group                                                                #
# --------------------------------------------------------------------------- #
async def test_ensure_group_issues_xgroup_create_with_tail_id_and_mkstream() -> None:
    client = _FakeRedisClient()
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.ensure_group("stream.media", "cg.media")

    assert client.xgroup_create_calls == [("stream.media", "cg.media", "$", True)]


async def test_ensure_group_swallows_busygroup() -> None:
    client = _FakeRedisClient()
    client.fail_xgroup_create(ResponseError("BUSYGROUP Consumer Group name already exists"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.ensure_group("stream.media", "cg.media")  # must not raise


async def test_ensure_group_translates_a_non_busygroup_response_error() -> None:
    client = _FakeRedisClient()
    client.fail_xgroup_create(ResponseError("WRONGTYPE some other failure"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        await consumer.ensure_group("stream.media", "cg.media")
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, RedisError)


async def test_ensure_group_translates_a_connection_level_redis_error() -> None:
    client = _FakeRedisClient()
    client.fail_xgroup_create(RedisError("connection refused"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError):
        await consumer.ensure_group("stream.media", "cg.media")


# --------------------------------------------------------------------------- #
# destroy_group / list_groups (§3.81 -- the per-process notify group's        #
# lifecycle: destroy on clean shutdown, list for the startup orphan sweep)    #
# --------------------------------------------------------------------------- #
async def test_destroy_group_issues_xgroup_destroy() -> None:
    client = _FakeRedisClient()
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.destroy_group("stream.media", "cg.notify.host.123")

    assert client.xgroup_destroy_calls == [("stream.media", "cg.notify.host.123")]


async def test_destroy_group_swallows_a_missing_stream() -> None:
    """A stream nobody ever ``ensure_group``\\ d against on this host has
    nothing to destroy -- verified live against Redis 8.0.1
    (``XGROUP DESTROY`` on an absent key raises "...requires the key to
    exist...", the ``XGROUP CREATE`` sibling minus ``MKSTREAM``)."""
    client = _FakeRedisClient()
    client.fail_xgroup_destroy(
        ResponseError(
            "The XGROUP subcommand requires the key to exist. Note that for CREATE "
            "you may want to use the MKSTREAM option to create an empty stream "
            "automatically."
        )
    )
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.destroy_group("stream.media", "cg.notify.host.123")  # must not raise


async def test_destroy_group_translates_a_non_missing_key_response_error() -> None:
    client = _FakeRedisClient()
    client.fail_xgroup_destroy(ResponseError("WRONGTYPE some other failure"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        await consumer.destroy_group("stream.media", "cg.notify.host.123")
    assert exc_info.value.code == "common.internal"


async def test_destroy_group_translates_a_connection_level_redis_error() -> None:
    client = _FakeRedisClient()
    client.fail_xgroup_destroy(RedisError("connection refused"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError):
        await consumer.destroy_group("stream.media", "cg.notify.host.123")


async def test_list_groups_returns_decoded_names() -> None:
    client = _FakeRedisClient()
    client.queue_xinfo_groups_response(
        [
            {"name": b"cg.notify.host.111", "consumers": 0, "pending": 0},
            {"name": b"cg.notify.host.222", "consumers": 1, "pending": 0},
        ]
    )
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    names = await consumer.list_groups("stream.media")

    assert names == ["cg.notify.host.111", "cg.notify.host.222"]
    assert client.xinfo_groups_calls == ["stream.media"]


async def test_list_groups_returns_empty_for_a_missing_stream() -> None:
    """Verified live: ``XINFO GROUPS`` on a stream that was never created
    raises ``"no such key"`` rather than answering an empty list -- folded to
    ``[]`` here so a caller never special-cases "never created" versus
    "created, nothing left"."""
    client = _FakeRedisClient()
    client.fail_xinfo_groups(ResponseError("no such key"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    assert await consumer.list_groups("stream.media") == []


async def test_list_groups_translates_a_non_missing_key_response_error() -> None:
    client = _FakeRedisClient()
    client.fail_xinfo_groups(ResponseError("WRONGTYPE some other failure"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        await consumer.list_groups("stream.media")
    assert exc_info.value.code == "common.internal"


async def test_list_groups_translates_a_connection_level_redis_error() -> None:
    client = _FakeRedisClient()
    client.fail_xinfo_groups(RedisError("connection refused"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError):
        await consumer.list_groups("stream.media")


# --------------------------------------------------------------------------- #
# read -- the two-pass (recovery then fresh) deviation                        #
# --------------------------------------------------------------------------- #
async def test_read_issues_a_recovery_pass_then_a_fresh_pass_covering_every_stream() -> None:
    """The class docstring's "Deviation" paragraph: two ``XREADGROUP`` calls
    -- ``id="0"`` (recovery, never blocks) then ``id=">"`` (fresh, respects
    ``block_ms``) -- both covering every requested stream in one call each."""
    client = _FakeRedisClient()
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.read(
        streams=["stream.files", "stream.knowledge"],
        group="cg.knowledge",
        consumer="c1",
        count=10,
        block_ms=500,
    )

    assert len(client.xreadgroup_calls) == 2
    (group0, consumer0, streams0, count0, block0) = client.xreadgroup_calls[0]
    assert (group0, consumer0, streams0, count0) == (
        "cg.knowledge",
        "c1",
        {"stream.files": "0", "stream.knowledge": "0"},
        10,
    )
    assert block0 is None  # recovery pass never blocks (Redis ignores BLOCK on non-`>` reads)
    (group1, consumer1, streams1, count1, block1) = client.xreadgroup_calls[1]
    assert (group1, consumer1, streams1, count1, block1) == (
        "cg.knowledge",
        "c1",
        {"stream.files": ">", "stream.knowledge": ">"},
        10,
        500,
    )


async def test_read_merges_recovered_messages_before_fresh_ones() -> None:
    client = _FakeRedisClient()
    client.queue_xreadgroup_response([[b"stream.media", [(b"1-0", {b"ce": b"recovered"})]]])
    client.queue_xreadgroup_response([[b"stream.media", [(b"2-0", {b"ce": b"fresh"})]]])
    client.queue_xpending_response(
        [{"message_id": b"1-0", "consumer": b"c1", "time_since_delivered": 5, "times_delivered": 3}]
    )
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    messages = await consumer.read(
        streams=["stream.media"], group="cg.media", consumer="c1", count=10, block_ms=500
    )

    assert [(m.stream, m.entry_id, m.raw, m.delivery_count) for m in messages] == [
        ("stream.media", "1-0", b"recovered", 3),  # XPENDING's own counter
        ("stream.media", "2-0", b"fresh", 1),  # fresh == first delivery, by definition
    ]


async def test_read_asks_xpending_only_for_recovered_entries_and_filters_by_consumer() -> None:
    """The 5.2-ب enrichment's two economy/correctness properties in one:
    the ``XPENDING RANGE`` is bounded to exactly the recovered ids and
    consumer-filtered (recovered entries are by construction this
    consumer's own PEL)."""
    client = _FakeRedisClient()
    client.queue_xreadgroup_response(
        [[b"stream.media", [(b"1-0", {b"ce": b"a"}), (b"3-0", {b"ce": b"b"})]]]
    )
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.read(
        streams=["stream.media"], group="cg.media", consumer="c1", count=10, block_ms=500
    )

    assert client.xpending_range_calls == [("stream.media", "cg.media", "1-0", "3-0", 2, "c1")]


async def test_read_skips_xpending_entirely_when_the_recovery_pass_is_empty() -> None:
    """The idle-loop common case must cost exactly what it cost before
    5.2-ب: no recovered entries -> zero ``XPENDING`` round trips."""
    client = _FakeRedisClient()
    client.queue_xreadgroup_response([])
    client.queue_xreadgroup_response([[b"stream.media", [(b"2-0", {b"ce": b"fresh"})]]])
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    messages = await consumer.read(
        streams=["stream.media"], group="cg.media", consumer="c1", count=10, block_ms=500
    )

    assert client.xpending_range_calls == []
    assert [m.delivery_count for m in messages] == [1]


async def test_read_carries_none_when_the_ce_field_is_absent() -> None:
    client = _FakeRedisClient()
    client.queue_xreadgroup_response([])
    client.queue_xreadgroup_response([[b"stream.media", [(b"1-0", {b"other": b"x"})]]])
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    messages = await consumer.read(
        streams=["stream.media"], group="cg.media", consumer="c1", count=10, block_ms=500
    )

    assert messages == [StreamMessage(stream="stream.media", entry_id="1-0", raw=None)]


async def test_read_returns_an_empty_list_when_nothing_is_available() -> None:
    client = _FakeRedisClient()
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    messages = await consumer.read(
        streams=["stream.media"], group="cg.media", consumer="c1", count=10, block_ms=500
    )

    assert messages == []


async def test_read_translates_a_redis_failure() -> None:
    client = _FakeRedisClient()
    client.fail_xreadgroup(RedisError("boom"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        await consumer.read(
            streams=["stream.media"], group="cg.media", consumer="c1", count=10, block_ms=500
        )
    assert exc_info.value.code == "common.internal"


# --------------------------------------------------------------------------- #
# ack                                                                         #
# --------------------------------------------------------------------------- #
async def test_ack_issues_xack_for_exactly_the_named_entry() -> None:
    client = _FakeRedisClient()
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.ack("stream.media", "cg.media", "1-0")

    assert client.xack_calls == [("stream.media", "cg.media", "1-0")]


async def test_ack_translates_a_redis_failure() -> None:
    client = _FakeRedisClient()
    client.fail_xack(RedisError("boom"))
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        await consumer.ack("stream.media", "cg.media", "1-0")
    assert exc_info.value.code == "common.internal"


# --------------------------------------------------------------------------- #
# dead_letter (5.2-ب)                                                         #
# --------------------------------------------------------------------------- #
async def test_dead_letter_xadds_to_the_dlq_and_xacks_in_one_transaction() -> None:
    """04 §3's ``stream.<m>.dlq`` naming, the verbatim ``ce`` bytes, the
    bookkeeping fields, and the MULTI (transaction=True) -- all in one
    observable shape."""
    client = _FakeRedisClient()
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.dead_letter(
        stream="stream.media",
        group="cg.media",
        entry_id="7-0",
        raw=b'{"the":"envelope"}',
        reason="handler_failed: RuntimeError: boom",
        delivery_count=5,
    )

    assert len(client.pipelines_executed) == 1
    transactional, commands = client.pipelines_executed[0]
    assert transactional is True
    assert commands == [
        (
            "xadd",
            "stream.media.dlq",
            {
                b"reason": b"handler_failed: RuntimeError: boom",
                b"source_stream": b"stream.media",
                b"source_entry_id": b"7-0",
                b"consumer_group": b"cg.media",
                b"deliveries": b"5",
                b"ce": b'{"the":"envelope"}',
            },
        ),
        ("xack", "stream.media", "cg.media", "7-0"),
    ]


async def test_dead_letter_omits_the_ce_field_when_the_entry_had_none() -> None:
    """A malformed entry with no ``ce`` field at all dead-letters as
    bookkeeping-only -- there are no bytes to preserve, and inventing an
    empty one would make the DLQ entry look re-injectable when it is not."""
    client = _FakeRedisClient()
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    await consumer.dead_letter(
        stream="stream.media",
        group="cg.media",
        entry_id="7-0",
        raw=None,
        reason="malformed_envelope",
        delivery_count=1,
    )

    (_, commands) = client.pipelines_executed[0]
    xadd_fields = commands[0][2]
    assert isinstance(xadd_fields, dict)
    assert b"ce" not in xadd_fields
    assert xadd_fields[b"reason"] == b"malformed_envelope"


async def test_dead_letter_translates_a_redis_failure() -> None:
    client = _FakeRedisClient()
    client.pipeline_error = RedisError("boom")
    consumer = RedisStreamsConsumer(client)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        await consumer.dead_letter(
            stream="stream.media",
            group="cg.media",
            entry_id="7-0",
            raw=b"x",
            reason="r",
            delivery_count=5,
        )
    assert exc_info.value.code == "common.internal"
