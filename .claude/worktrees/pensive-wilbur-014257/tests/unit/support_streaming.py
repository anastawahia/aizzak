"""Shared test double for the P1-8 cross-process WS connection registry
(``app.framework.ports.ws_connection_registry.WsConnectionRegistry``).

Lives in ``tests/`` and NOT in ``src/`` deliberately. An in-process
implementation of this port shipped as production code would be a loaded gun:
the whole point of step 11 is that a per-process counter cannot enforce a
per-user cap under gunicorn's ``WEB_CONCURRENCY=2``, so a wiring mistake that
picked an in-memory registry would silently reintroduce the exact defect —
and it would pass every unit test, because a single-process test cannot tell
the two apart. Only the Redis adapter is importable from the Composition
Root; this double is importable only from tests
(the ``support_access``/``support_conversations`` convention).

It is a FAITHFUL double, not a stub: it evicts by ``ttl_s`` on the same
boundary the Lua script does, ``renew`` refuses to resurrect a released id
(``ZADD XX``'s semantics), and ``release`` is silently idempotent. Unit tests
of the hub and the WebSocket endpoint therefore exercise real admission
control — a stub that always admitted would turn every cap test into
theatre.

Time is read from ``time.monotonic`` rather than the wall clock so a test
that manipulates ages does so through the ``now`` seam below instead of
sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from app.framework.errors import AppError
from app.framework.types import Uuid


class InMemoryWsConnectionRegistry:
    """Structural ``WsConnectionRegistry`` match (no inheritance, the codebase's
    Protocol convention)."""

    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._now = now if now is not None else time.monotonic
        self._entries: dict[Uuid, dict[Uuid, float]] = {}
        # Flipped by a test to prove the hub's Redis-failure policy (fail
        # closed on admission, swallow on release) with the port's own error.
        self.fail_with: AppError | None = None

    def _live(self, user_id: Uuid, ttl_s: int) -> dict[Uuid, float]:
        held = self._entries.setdefault(user_id, {})
        cutoff = self._now() - ttl_s
        for connection_id in [cid for cid, stamp in held.items() if stamp <= cutoff]:
            del held[connection_id]
        return held

    def _guard(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def try_acquire(
        self, *, user_id: Uuid, connection_id: Uuid, max_connections: int, ttl_s: int
    ) -> bool:
        self._guard()
        held = self._live(user_id, ttl_s)
        if len(held) >= max_connections:
            return False
        held[connection_id] = self._now()
        return True

    async def renew(self, *, user_id: Uuid, connection_ids: Sequence[Uuid], ttl_s: int) -> None:
        self._guard()
        held = self._live(user_id, ttl_s)
        stamp = self._now()
        for connection_id in connection_ids:
            if connection_id in held:  # `ZADD XX`: never resurrect a released id
                held[connection_id] = stamp

    async def release(self, *, user_id: Uuid, connection_id: Uuid) -> None:
        self._guard()
        self._entries.get(user_id, {}).pop(connection_id, None)

    async def count(self, *, user_id: Uuid, ttl_s: int) -> int:
        self._guard()
        return len(self._live(user_id, ttl_s))
