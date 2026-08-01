"""WsConnectionRegistry driven port — the CROSS-PROCESS side of the 07 §4
per-user WebSocket connection cap (P1-8, ``docs/p1-hardening-plan.md`` §3
step 11).

Until this port, ``framework/streaming/hub.py`` counted a user's live sockets
in a plain ``dict`` on its own heap. That is the SAME defect root as P0-2
(``docs/log/3.81.md``): both deployment paths default gunicorn to
``WEB_CONCURRENCY=2``, so "the API" is at least two SIBLING OS processes, each
with its own hub and therefore its own private count — the announced ceiling
of 5 was really *5 x workers x replicas*, and one user could open five sockets
per worker without a single refusal. A cap that only holds inside one process
is not a cap.

**The counter therefore lives where every sibling can see it, and this port is
how the kernel reaches it.** ``app.framework`` may not import
``app.infrastructure`` (import-linter contract 6), so the hub takes a
``WsConnectionRegistry`` and the Composition Root binds the Redis adapter
(``infrastructure.streaming.redis_connection_registry.RedisWsConnectionRegistry``)
— the step-10 ``MetricsSource`` shape, applied to a second cross-process fact.

**Two properties an implementation MUST provide, or the port is a lie:**

1. **``try_acquire`` is ATOMIC.** The in-process version was atomic for free —
   ``hub.try_register`` was synchronous, so no ``await`` sat between the check
   and the change and cooperative scheduling could not interleave. Moving the
   count out of the process spends that guarantee, and it has to be bought
   back on the far side: "read the count, then add if there is room" over two
   round-trips lets two simultaneous connections both observe ``count < max``
   and both be admitted, which overshoots exactly the ceiling this exists to
   hold. The Redis adapter buys it back with ONE Lua script (Redis executes a
   script atomically end to end); any other implementation owes the same
   guarantee by some other means.
2. **An INDIVIDUAL entry ages out (``ttl_s``).** A shared counter leaks: a
   process that is SIGKILLed never runs ``release``, so its share of a user's
   sockets stays counted against a session that no longer exists — and, with a
   ceiling of 5, a handful of crashes locks a real user out permanently. An
   expiry on the whole KEY does not fix this: the key is renewed by every new
   connection, so an actively-connecting user's key never expires and the dead
   entry inside it is counted forever. So ``ttl_s`` bounds the life of ONE
   entry, and a live connection keeps its own entry young by calling ``renew``
   on a period strictly shorter than ``ttl_s`` (``ConnectionHub``'s renewal
   loop owns that schedule — see its docstring for who renews and when).

``ttl_s`` is a PARAMETER of every method rather than adapter configuration on
purpose: the ceiling, the entry lifetime and the renewal period are one
coherent policy, and ``ConnectionHub`` owns all three so they cannot silently
drift apart across two constructors.

Implemented by ``infrastructure.streaming.redis_connection_registry
.RedisWsConnectionRegistry`` (the Composition Root's only caller); an
in-memory double with the same semantics substitutes it in
``tests/unit/support_streaming.py`` so hub/endpoint unit tests keep exercising
real admission control without a live Redis.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.types import Uuid


class WsConnectionRegistry(Protocol):
    async def try_acquire(
        self, *, user_id: Uuid, connection_id: Uuid, max_connections: int, ttl_s: int
    ) -> bool:
        """Claim one of ``user_id``'s ``max_connections`` slots for
        ``connection_id``, atomically. ``False`` = the user is already at the
        cap (an expected admission outcome, never an error). Entries older
        than ``ttl_s`` are evicted first, so a crashed process's share never
        blocks a live user beyond that bound."""
        ...

    async def renew(self, *, user_id: Uuid, connection_ids: Sequence[Uuid], ttl_s: int) -> None:
        """Reset the age of the named STILL-HELD entries, evicting anything
        already older than ``ttl_s`` first. An id that is no longer registered
        — released, or aged out — is not resurrected: renewal keeps a live
        entry young, it never re-admits a lost one past the cap.

        Evicting here is what makes renewal double as the SWEEP: nothing else
        in this system runs periodically over these entries, so a crashed
        process's leftovers are reclaimed on the next tick of any live
        caller's loop rather than lingering until someone happens to acquire
        or count."""
        ...

    async def release(self, *, user_id: Uuid, connection_id: Uuid) -> None:
        """Free the slot. Idempotent: releasing an unknown/already-released
        id is a no-op, because the caller is a WebSocket endpoint's
        ``finally`` and a double call must not raise during teardown."""
        ...

    async def count(self, *, user_id: Uuid, ttl_s: int) -> int:
        """Slots held across ALL processes, aged-out entries excluded — the
        cross-process truth ``ConnectionHub.user_connection_count`` (which
        only ever sees this process's own sessions) cannot answer."""
        ...
