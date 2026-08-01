"""Unit tests for ``RedisStreamsPublisher`` (5.1-ب, 02-port-contracts §1.8).

Hermetic: a fake stands in for ``redis.asyncio.Redis`` recording every
``xadd`` call, so these tests never touch a real Redis. ``docs/log/3.44.md``'s
mutation-battery lesson applies here too: the expected wire field is
hardcoded as the literal ``b"ce"`` rather than imported from the module under
test, so a mutation that renames the module's own ``_CE_FIELD`` constant
cannot silently drag this test's expectation along with it.
"""

from __future__ import annotations

import json

import pytest
from redis import RedisError

from app.framework.errors import AppError
from app.framework.ports.event_publisher import StreamEvent
from app.infrastructure.messaging.redis_streams import RedisStreamsPublisher

# Hardcoded on purpose -- see the module docstring.
_CE_FIELD = b"ce"


class _FakeRedisClient:
    """Emulates the ONE method ``RedisStreamsPublisher`` touches: ``xadd``.
    Always hands back ``bytes`` (``decode_responses=False`` is the project's
    fixed client contract, ``redis_streams.py``'s own docstring)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[bytes, str]]] = []
        # Trim arguments recorded SEPARATELY from the positional pair (7.3):
        # an assertion on the whole kwargs dict catches a stray extra
        # argument, which an assertion on  alone would not.
        self.kwargs: list[dict[str, object]] = []
        self._next_seq = 1
        self._fail_on_call: int | None = None
        self._error: RedisError = RedisError("boom")

    def fail_on_call(self, call_number: int, exc: RedisError | None = None) -> None:
        """1-indexed: ``fail_on_call(1)`` fails the very first ``xadd``;
        ``fail_on_call(2)`` lets the first succeed and fails the second."""
        self._fail_on_call = call_number
        if exc is not None:
            self._error = exc

    async def xadd(self, name: str, fields: dict[bytes, str], **kwargs: object) -> bytes:
        attempt = len(self.calls) + 1
        if attempt == self._fail_on_call:
            raise self._error
        self.calls.append((name, fields))
        self.kwargs.append(dict(kwargs))
        entry_id = f"{self._next_seq}-0".encode()
        self._next_seq += 1
        return entry_id


# --------------------------------------------------------------------------- #
# publish -- wire shape                                                       #
# --------------------------------------------------------------------------- #
async def test_publish_sends_the_ce_field_with_compact_ascii_preserving_json() -> None:
    """Mutation-battery target #4: a publisher writing any other field name
    (e.g. ``b"payload"``) must fail exactly this assertion."""
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]
    event = {"id": "evt-1", "data": {"prompt": "a cat wearing sunglasses مرحبا"}}

    await publisher.publish("stream.media", event)

    (stream, fields) = client.calls[0]
    assert stream == "stream.media"
    assert list(fields.keys()) == [_CE_FIELD]
    # Compact separators (no spaces) AND non-ASCII preserved verbatim, not
    # `\uXXXX`-escaped -- see the module docstring's "Serialization" note.
    assert fields[_CE_FIELD] == json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    assert ", " not in fields[_CE_FIELD]
    assert "\\u" not in fields[_CE_FIELD]


async def test_publish_does_not_mutate_or_rebuild_the_envelope() -> None:
    """The envelope was already built/validated at produce time -- ``publish``
    republishes it VERBATIM, so round-tripping through JSON must reproduce
    the exact same structure with nothing added, removed, or reordered away."""
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]
    event = {"specversion": "1.0", "id": "evt-2", "data": {"job_id": "job-1"}}

    await publisher.publish("stream.media", event)

    (_, fields) = client.calls[0]
    assert json.loads(fields[_CE_FIELD]) == event


# --------------------------------------------------------------------------- #
# publish -- entry id                                                         #
# --------------------------------------------------------------------------- #
async def test_publish_returns_the_decoded_entry_id() -> None:
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]

    entry_id = await publisher.publish("stream.media", {"id": "evt-1"})

    assert entry_id == "1-0"
    assert isinstance(entry_id, str)


# --------------------------------------------------------------------------- #
# publish -- error translation                                                #
# --------------------------------------------------------------------------- #
async def test_a_redis_failure_is_translated_to_apperror_common_internal() -> None:
    """R6: no ``redis``-package exception type escapes this adapter."""
    client = _FakeRedisClient()
    client.fail_on_call(1, RedisError("connection refused"))
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]

    with pytest.raises(AppError) as exc_info:
        await publisher.publish("stream.media", {"id": "evt-1"})

    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, RedisError)


async def test_the_publisher_never_retries_a_failed_publish() -> None:
    """No retry logic inside the publisher -- that is `OutboxRelay`'s job
    alone (the module docstring's error-policy paragraph). One failure must
    raise immediately, never re-attempt the same XADD silently."""
    client = _FakeRedisClient()
    client.fail_on_call(1, RedisError("connection refused"))
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]

    with pytest.raises(AppError):
        await publisher.publish("stream.media", {"id": "evt-1"})

    assert client.calls == []  # the one attempt failed and was not retried


# --------------------------------------------------------------------------- #
# publish_batch -- order + per-item attribution                               #
# --------------------------------------------------------------------------- #
async def test_publish_batch_preserves_order_and_returns_one_id_per_item() -> None:
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]
    items = [
        StreamEvent(stream="stream.media", event={"id": "evt-1"}),
        StreamEvent(stream="stream.files", event={"id": "evt-2"}),
        StreamEvent(stream="stream.memory", event={"id": "evt-3"}),
    ]

    ids = await publisher.publish_batch(items)

    assert ids == ["1-0", "2-0", "3-0"]
    assert [stream for stream, _ in client.calls] == [
        "stream.media",
        "stream.files",
        "stream.memory",
    ]


async def test_publish_batch_attributes_a_partial_failure_to_its_own_item() -> None:
    """Sequential publishing (not a pipeline): when the SECOND item's XADD
    fails, the first one's already independently landed and is attributable
    -- the whole point of not batching this into one pipelined command."""
    client = _FakeRedisClient()
    client.fail_on_call(2, RedisError("boom"))
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]
    items = [
        StreamEvent(stream="stream.media", event={"id": "evt-1"}),
        StreamEvent(stream="stream.files", event={"id": "evt-2"}),
    ]

    with pytest.raises(AppError):
        await publisher.publish_batch(items)

    assert len(client.calls) == 1
    assert client.calls[0][0] == "stream.media"


# --------------------------------------------------------------------------- #
# publish -- stream trimming (7.3)                                            #
# --------------------------------------------------------------------------- #
# Redis Streams are append-only: XACK clears a group's pending list, it does
# NOT delete the entry. So `stream.<module>` grows forever with every worker
# running and acking perfectly -- not only while 7.2 leaves them unbootable.
# The live stack measured `maxmemory 0` / `noeviction`: no backstop anywhere.
async def test_a_publisher_without_a_cap_sends_no_trim_arguments_at_all() -> None:
    """The pre-7.3 wire bytes, preserved exactly. ``maxlen=None`` must OMIT
    the argument rather than pass ``None`` through -- redis-py would encode a
    literal ``None`` into the command and Redis would reject it."""
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client)  # type: ignore[arg-type]

    await publisher.publish("stream.media", {"id": "evt-1"})

    assert client.kwargs[0] == {}


async def test_a_capped_publisher_trims_approximately_on_every_publish() -> None:
    """``MAXLEN ~ n``. ``approximate`` is not a detail: exact trimming makes
    every XADD on the platform's hot path pay an O(n) deletion to enforce a
    bound whose precise value nobody has an opinion about."""
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client, maxlen=100_000)  # type: ignore[arg-type]

    await publisher.publish("stream.media", {"id": "evt-1"})

    assert client.kwargs[0] == {"maxlen": 100_000, "approximate": True}


async def test_every_entry_in_a_batch_carries_the_cap() -> None:
    """`publish_batch` delegates to `publish`, so the cap must not be a
    property of the first call only -- a batch is where unbounded growth
    actually happens."""
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client, maxlen=500)  # type: ignore[arg-type]

    await publisher.publish_batch(
        [
            StreamEvent(stream="stream.media", event={"id": "evt-1"}),
            StreamEvent(stream="stream.knowledge", event={"id": "evt-2"}),
        ]
    )

    assert client.kwargs == [
        {"maxlen": 500, "approximate": True},
        {"maxlen": 500, "approximate": True},
    ]


async def test_trimming_does_not_disturb_the_field_name_or_the_entry_id() -> None:
    """The cap is a retention argument, not a wire-format change: `ce` and the
    decoded entry id must be exactly what they were before 7.3."""
    client = _FakeRedisClient()
    publisher = RedisStreamsPublisher(client, maxlen=10)  # type: ignore[arg-type]

    entry_id = await publisher.publish("stream.media", {"id": "evt-1"})

    (stream, fields) = client.calls[0]
    assert stream == "stream.media"
    assert list(fields.keys()) == [_CE_FIELD]
    assert entry_id == "1-0"
