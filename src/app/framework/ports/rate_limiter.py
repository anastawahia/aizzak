"""RateLimiter driven port — the CROSS-PROCESS side of capacity-plan step 1.2.

``Limits.api_rate_per_min = 120`` has been declared since 07 §4 and read by
nothing. This port is where it starts being enforced, and the reason it is a
port at all is the reason ``ws_connection_registry`` is one: a counter that
lives on one process's heap is not a limit. Both deployment paths default
gunicorn to ``WEB_CONCURRENCY=2``, and the plan's road to 300 rps is replicas
— so an in-process window would announce 120/min and permit *120 x workers x
replicas*.

**A bucket is (scope, key, limit, window).** The CALLER composes them, which
is what lets one mechanism serve step 1.2's two buckets (per user, per
workspace) and step 1.3's third (heavy jobs) without the limiter knowing what
a workspace or a job is. ``scope`` is a short label the caller chooses; it
comes back on a refusal so the caller can say WHICH ceiling bound, and it is
the only part of a bucket that may be used as a metric label — a key carries
a tenant id and would mint one time series per user.

**Two properties an implementation MUST provide, or the port is a lie:**

1. **``try_consume`` is ATOMIC, across every bucket at once.** Reading a count
   over one round trip and adding over another lets two simultaneous requests
   both observe ``count < limit`` and both be admitted — the same overshoot
   ``WsConnectionRegistry`` spells out, and here it is the difference between
   a ceiling and a suggestion. The Redis adapter buys it back with ONE Lua
   script (Redis runs a script atomically end to end); any other
   implementation owes the same guarantee by some other means.

2. **Consumption is ALL-OR-NOTHING.** A request refused by the workspace
   bucket must not have spent the user's own allowance. Otherwise a tenant at
   its ceiling silently burns down every one of its users' budgets, and the
   moment the tenant window reopens its users are still refused — a limit
   whose refusals compound is a limit that never lets go.

**The window is a sliding LOG, not a fixed-period counter, and the acceptance
criterion is why.** 1.2 asks for "the 121st request in a minute answers 429".
A fixed calendar minute admits 120 at 11:00:59 and 120 more at 11:01:00; a
bucketed approximation answers "about the 121st". Only one entry per request,
scored with the time it arrived, makes the number in the criterion the number
the platform actually enforces — and it is also the only shape from which an
exact ``Retry-After`` can be computed, since the oldest entry's score plus the
window IS the instant capacity returns.

**Failure policy belongs to the CALLER, and 1.2 states it explicitly: fail
OPEN.** An implementation reports failure by raising (the adapters translate
their driver errors into ``AppError``); it never decides on its own that a
Redis outage means everybody is refused. ``api/middleware/rate_limit.py``
makes that call, and its docstring says why the answer here is the opposite of
``ConnectionHub``'s.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateBucket:
    """One ceiling: at most ``limit`` consumptions of ``key`` per ``window_s``.

    ``scope`` names the ceiling ("user", "workspace", "heavy"), ``key`` names
    the thing being counted. They are separate fields rather than one string
    because they have opposite cardinalities: ``scope`` is a handful of
    compile-time constants and is safe to label a metric with, ``key`` embeds
    a tenant or user id and never is.
    """

    scope: str
    key: str
    limit: int
    window_s: int


@dataclass(frozen=True, slots=True)
class RateVerdict:
    """Allowed, or refused by exactly one bucket.

    ``scope`` and ``retry_after_s`` are meaningful only when ``allowed`` is
    false. ``retry_after_s`` is a whole number of seconds ≥ 1, in the shape
    RFC 9110's ``Retry-After`` delay-seconds takes and
    ``RateLimitedError.retry_after_s`` accepts — a computed instant, never a
    plausible-looking guess (that error drops anything non-positive rather
    than clamp it).
    """

    allowed: bool
    scope: str = ""
    retry_after_s: int = 0


# The verdict every allowed request gets. One shared instance because it is
# frozen and carries no request-specific fact — the common path by an enormous
# margin, and it should allocate nothing.
ALLOWED = RateVerdict(allowed=True)


class RateLimiter(Protocol):
    """Consume one unit from every bucket, or from none of them."""

    async def try_consume(self, buckets: Sequence[RateBucket]) -> RateVerdict:
        """Admit this request and record it, or refuse it and record nothing.

        Buckets are evaluated IN ORDER, and the first one that is full is the
        one reported. That order is the caller's editorial choice, not an
        implementation detail: 1.2 puts the per-user bucket first so a single
        abusive client is told it is over ITS limit, rather than being handed
        a tenant-wide refusal that its well-behaved colleagues also see.
        """
        ...
