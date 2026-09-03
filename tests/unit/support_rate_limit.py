"""An in-memory ``RateLimiter`` for the hermetic suite (capacity-plan 1.2).

Not a ``test_*`` module, so pytest never collects it — the
``support_integrations`` precedent: both the policy tests
(``test_rate_limiter.py``) and the end-to-end route tests
(``test_api_auth.py``) need the same fake, and a per-file copy is how copies
drift.

**It is a MODEL of the Lua script, deliberately faithful on the three
properties the port declares**, because a fake that was merely permissive
would let every test pass while the real limiter refused nothing:

* the window is a sliding LOG (one entry per admitted request, evicted by
  age), not a fixed period — so "the 121st request inside a minute" means here
  exactly what it means in Redis;
* consumption is ALL-OR-NOTHING across buckets, checked in one pass and
  applied in another;
* buckets are evaluated IN ORDER and the first full one is the one reported.

What it cannot model is atomicity across processes, which is the whole reason
the real one is a Lua script — that half is proven against a real server in
``tests/integration/test_rate_limiter_live.py``.

**Time is a field, not a clock.** ``now_ms`` is advanced by the test, so a
window's expiry is asserted rather than slept through; the live suite is where
a real server-side clock is exercised.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.framework.ports.rate_limiter import ALLOWED, RateBucket, RateVerdict


class InMemoryRateLimiter:
    """A structural ``RateLimiter`` over one dict of arrival logs."""

    def __init__(self) -> None:
        self._log: dict[str, list[float]] = {}
        # The fake clock, in milliseconds. Tests move it forward.
        self.now_ms: float = 0.0
        # Every call's buckets, so a test can assert WHICH ceilings the policy
        # composed rather than only what the verdict was.
        self.calls: list[tuple[RateBucket, ...]] = []
        # Set to make the port raise, which is the only way the caller's
        # fail-open branch is reachable.
        self.failure: Exception | None = None

    async def try_consume(self, buckets: Sequence[RateBucket]) -> RateVerdict:
        self.calls.append(tuple(buckets))
        if self.failure is not None:
            raise self.failure
        for bucket in buckets:
            live = [at for at in self._log.get(bucket.key, []) if at > self.now_ms - _ms(bucket)]
            self._log[bucket.key] = live
            if len(live) >= bucket.limit:
                retry_ms = live[0] + _ms(bucket) - self.now_ms
                return RateVerdict(
                    allowed=False,
                    scope=bucket.scope,
                    retry_after_s=max(1, math.ceil(retry_ms / 1000)),
                )
        for bucket in buckets:
            self._log[bucket.key].append(self.now_ms)
        return ALLOWED

    def count(self, key: str) -> int:
        """Entries currently held for ``key`` — how a test proves that a
        refused request consumed nothing."""
        return len(self._log.get(key, []))


def _ms(bucket: RateBucket) -> float:
    return bucket.window_s * 1000
