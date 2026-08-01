"""Unit tests for the shared teardown seam (``framework/di/lifecycle.py``,
3.79).

``dispose_all`` exists for exactly one behaviour a bare ``for``-loop cannot
give: a client whose ``close`` RAISES must not strand the clients behind it.
A shutdown path is the worst place for fail-fast — the process is going away
regardless, and the only thing at stake is whether the OTHER sockets get closed
— so these tests pin isolation, ordering, and the one exception the function
deliberately does NOT swallow.
"""

from __future__ import annotations

import asyncio

import pytest

from app.framework.di.lifecycle import dispose_all


async def test_disposes_every_thunk_in_order() -> None:
    closed: list[str] = []

    async def close(name: str) -> None:
        closed.append(name)

    await dispose_all(
        [
            lambda: close("engine"),
            lambda: close("redis"),
            lambda: close("qdrant"),
        ]
    )

    assert closed == ["engine", "redis", "qdrant"]


async def test_a_failing_disposal_does_not_strand_the_rest() -> None:
    """The whole point: one client refusing to close leaves every other
    connection pool still returned to the OS."""
    closed: list[str] = []

    async def ok(name: str) -> None:
        closed.append(name)

    async def boom() -> None:
        raise RuntimeError("connection reset while closing")

    await dispose_all([lambda: ok("engine"), boom, lambda: ok("redis")])

    assert closed == ["engine", "redis"]


async def test_cancellation_is_deferred_then_re_raised() -> None:
    """Teardown often runs inside a task that is itself being cancelled. A
    ``CancelledError`` escaping the FIRST client would skip every client after
    it (the exact leak this function prevents), and swallowing it outright
    would break cooperative cancellation — so it is remembered and re-raised
    once the whole list has been attempted."""
    closed: list[str] = []

    async def ok(name: str) -> None:
        closed.append(name)

    async def cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await dispose_all([cancelled, lambda: ok("redis"), lambda: ok("qdrant")])

    assert closed == ["redis", "qdrant"]


async def test_base_exceptions_other_than_cancellation_propagate() -> None:
    """``KeyboardInterrupt`` is deliberately NOT caught: an operator
    interrupting a shutdown wants out now."""
    closed: list[str] = []

    async def interrupt() -> None:
        raise KeyboardInterrupt

    async def ok() -> None:
        closed.append("redis")

    with pytest.raises(KeyboardInterrupt):
        await dispose_all([interrupt, ok])

    assert closed == []


async def test_empty_list_is_a_no_op() -> None:
    await dispose_all([])
