"""Hermetic tests for the DLQ operator tool (``app.ops.dlq``, P1-4,
``docs/p1-hardening-plan.md`` §3 step 7).

Everything here runs over ``_StubRedis``, a minimal duck-typed stand-in for
``redis.asyncio.Redis`` implementing only the three commands this module
calls (``xrange``/``xadd``/``xdel``) -- the ``test_ops_revoke.py`` precedent
(stub the client, never touch a real Redis). The live round trip (a real
``dead_letter`` transfer, a real ``requeue``, and proof the copied entry
actually reaches a consumer reading the source stream) lives in
``tests/integration/test_dlq_ops_live.py``, marked ``live_redis``.

The one thing this file is FOR: proving the two named pitfalls (module
docstring) are handled, not merely described -- a ``ce``-less entry is
flagged ``reinjectable=False`` and ``requeue`` refuses it by name; a
``ce``-bearing entry copies verbatim; ``purge`` never runs without ``--yes``.
"""

from __future__ import annotations

import pytest

from app.ops import dlq as dlq_module
from app.ops.dlq import DlqEntry, peek, purge, requeue


class _StubRedis:
    """Just enough of ``redis.asyncio.Redis`` for this module: ``xrange``
    (oldest-first, optional ``min``/``max``/``count``), ``xadd`` (assigns a
    monotonically increasing fake id, mirroring real Streams ids never being
    reused), and ``xdel`` (removes named ids, returns the count actually
    removed -- ``XDEL``'s own idempotent-per-id contract)."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[bytes, dict[bytes, bytes]]]] = {}
        self._counter = 0

    def seed(self, stream: str, fields: dict[bytes, bytes], *, entry_id: str | None = None) -> str:
        self._counter += 1
        assigned = entry_id or f"{self._counter}-0"
        self.streams.setdefault(stream, []).append((assigned.encode(), dict(fields)))
        return assigned

    async def xrange(
        self, name: str, min: str = "-", max: str = "+", count: int | None = None
    ) -> list[tuple[bytes, dict[bytes, bytes]]]:
        entries = self.streams.get(name, [])
        if min != "-" or max != "+":
            entries = [e for e in entries if e[0] == min.encode()]
        if count is not None:
            entries = entries[:count]
        return entries

    async def xadd(self, name: str, fields: dict[bytes, bytes]) -> bytes:
        self._counter += 1
        entry_id = f"{self._counter}-0".encode()
        self.streams.setdefault(name, []).append((entry_id, dict(fields)))
        return entry_id

    async def xdel(self, name: str, *ids: str) -> int:
        wanted = {i.encode() if isinstance(i, str) else i for i in ids}
        before = self.streams.get(name, [])
        after = [e for e in before if e[0] not in wanted]
        self.streams[name] = after
        return len(before) - len(after)


def _reinjectable_fields(reason: str = "handler_failed: ValueError: boom") -> dict[bytes, bytes]:
    return {
        b"reason": reason.encode(),
        b"source_stream": b"stream.knowledge",
        b"source_entry_id": b"1-0",
        b"consumer_group": b"cg.knowledge",
        b"deliveries": b"5",
        b"ce": b'{"type":"knowledge.document.registered.v1"}',
    }


def _bookkeeping_only_fields() -> dict[bytes, bytes]:
    # No `ce` key at all -- the exact shape `dead_letter` writes when `raw`
    # was `None` (module docstring's Pitfall 1).
    return {
        b"reason": b"malformed_envelope",
        b"source_stream": b"stream.knowledge",
        b"source_entry_id": b"2-0",
        b"consumer_group": b"cg.knowledge",
        b"deliveries": b"1",
    }


async def test_peek_reads_without_consuming_and_marks_both_classes() -> None:
    redis = _StubRedis()
    redis.seed("stream.knowledge.dlq", _reinjectable_fields(), entry_id="10-0")
    redis.seed("stream.knowledge.dlq", _bookkeeping_only_fields(), entry_id="20-0")

    entries = await peek(redis, "stream.knowledge")

    assert [e.entry_id for e in entries] == ["10-0", "20-0"]
    reinjectable, bookkeeping = entries
    assert reinjectable.reinjectable is True
    assert reinjectable.reason.startswith("handler_failed")
    assert reinjectable.deliveries == 5
    assert bookkeeping.reinjectable is False
    assert bookkeeping.ce is None
    assert bookkeeping.reason == "malformed_envelope"

    # Nothing else was touched -- no group, no XACK, no consumption: the
    # entries are still exactly where `peek` found them.
    assert len(redis.streams["stream.knowledge.dlq"]) == 2


async def test_requeue_copies_the_ce_bytes_verbatim_onto_the_source_stream() -> None:
    redis = _StubRedis()
    redis.seed("stream.knowledge.dlq", _reinjectable_fields(), entry_id="10-0")

    new_entry_id = await requeue(redis, "stream.knowledge", "10-0")

    assert new_entry_id != "10-0"  # Streams ids are never reused
    [(landed_id, landed_fields)] = redis.streams["stream.knowledge"]
    assert landed_id.decode() == new_entry_id
    assert landed_fields[b"ce"] == _reinjectable_fields()[b"ce"]
    # The DLQ copy is untouched -- `requeue` never purges on its own.
    assert len(redis.streams["stream.knowledge.dlq"]) == 1


async def test_requeue_refuses_a_ce_less_bookkeeping_only_entry_by_name() -> None:
    """Pitfall 1: no bytes to preserve means no XADD at all -- and the
    operator is told which entry and why, not handed a `KeyError`."""
    redis = _StubRedis()
    redis.seed("stream.knowledge.dlq", _bookkeeping_only_fields(), entry_id="20-0")

    with pytest.raises(ValueError, match="bookkeeping-only") as raised:
        await requeue(redis, "stream.knowledge", "20-0")

    assert "20-0" in str(raised.value)
    assert "malformed_envelope" in str(raised.value)
    # Nothing was ever XADDed onto the source stream.
    assert "stream.knowledge" not in redis.streams


async def test_requeue_names_a_dlq_entry_id_that_does_not_exist() -> None:
    redis = _StubRedis()

    with pytest.raises(ValueError, match="no such entry"):
        await requeue(redis, "stream.knowledge", "999-0")


async def test_purge_deletes_only_the_named_entries() -> None:
    redis = _StubRedis()
    redis.seed("stream.knowledge.dlq", _reinjectable_fields(), entry_id="10-0")
    redis.seed("stream.knowledge.dlq", _bookkeeping_only_fields(), entry_id="20-0")

    deleted = await purge(redis, "stream.knowledge", ["10-0"])

    assert deleted == 1
    remaining = await peek(redis, "stream.knowledge")
    assert [e.entry_id for e in remaining] == ["20-0"]


async def test_purge_counts_fewer_than_requested_when_an_id_is_already_gone() -> None:
    redis = _StubRedis()
    redis.seed("stream.knowledge.dlq", _reinjectable_fields(), entry_id="10-0")

    deleted = await purge(redis, "stream.knowledge", ["10-0", "does-not-exist-0"])

    assert deleted == 1


def test_cli_refuses_purge_without_explicit_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.dlq", "purge", "stream.knowledge", "10-0"])

    with pytest.raises(SystemExit) as raised:
        dlq_module.main()

    assert "--yes" in str(raised.value)


def test_cli_requires_a_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.dlq"])

    with pytest.raises(SystemExit):
        dlq_module.main()


def test_dlq_entry_reinjectable_is_a_pure_projection_of_ce_presence() -> None:
    with_ce = DlqEntry(
        entry_id="1-0",
        reason="handler_failed",
        source_stream="stream.knowledge",
        source_entry_id="1-0",
        consumer_group="cg.knowledge",
        deliveries=5,
        ce=b"{}",
    )
    without_ce = DlqEntry(
        entry_id="2-0",
        reason="malformed_envelope",
        source_stream="stream.knowledge",
        source_entry_id="2-0",
        consumer_group="cg.knowledge",
        deliveries=1,
        ce=None,
    )
    assert with_ce.reinjectable is True
    assert without_ce.reinjectable is False
