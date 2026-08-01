"""IdempotencyStore driven port — the ``Idempotency-Key`` ledger behind the
heavy create endpoints (03-api-spec §0 · ``openapi.yaml`` ``registerFile`` /
``createMediaJob`` / ``runWorkflow``, landed in 3.79).

03 §0 promises "`Idempotency-Key` ⇒ إعادة المحاولة آمنة" and ``openapi.yaml``
declares the header on exactly three operations. Until 3.79 no store existed,
so the header was accepted and silently dropped — the worst of the three
possible states, because a client that sent it believed a retry was safe on
paths that BILL (a media job, a workflow run) and it was not.

**Postgres, not Redis — a decided point, not a preference.** Redis is the
obvious cache-shaped fit and is wrong here: an eviction (maxmemory, a restart,
a key sampled out under pressure) silently reopens the duplicate window, and
it reopens it precisely when the platform is under load, i.e. exactly when a
client is most likely to be retrying. The ledger this port models is the same
DD-09 shape ``platform.processed_events`` already uses for consumer dedup, on
the same durable store, for the same reason.

**Claim FIRST, complete after** — the ordering is the whole contract:

* ``claim`` inserts the key row in its own committed transaction, so a
  concurrent second request with the same key SEES it. The 23505 on the
  primary key is what makes "only one of us runs" true, rather than a
  read-then-write both callers pass.
* ``complete`` fills in the response the first caller produced, which is what
  a later repeat replays.
* ``release`` removes an unfinished claim when the handler RAISED, so a failed
  attempt does not brick the key forever. Without it, one 500 would make every
  future retry of that key a conflict — turning a transient failure into a
  permanent one, on the same billable path this exists to protect.

The port is deliberately narrow on the response side: a JSON body, nothing
else. **No status code**, because there is nothing to reproduce — a replay
returns through the SAME route, whose ``status_code`` is declared once on the
decorator, so the status is identical by construction; a stored copy would be
a column nothing ever reads. **No headers**, because the two that matter
(``X-Correlation-Id``, and 3.79's ``Retry-After``) are per-ATTEMPT facts, and
replaying the first attempt's correlation id would point an operator at the
wrong log line.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json


class ClaimOutcome(Enum):
    """What ``IdempotencyStore.claim`` found. A closed vocabulary rather than
    a bool + optionals, because the four cases have four different correct
    behaviours at the API boundary and a caller that forgets one would silently
    fall into the wrong branch."""

    #: No row existed; THIS call inserted it. The caller runs the operation.
    CLAIMED = "claimed"
    #: A completed row with the SAME request. Its stored response is replayed.
    REPLAY = "replay"
    #: A row exists for this key with a DIFFERENT request body.
    MISMATCH = "mismatch"
    #: A row exists for this key, same request, still running (a concurrent
    #: duplicate, or a caller that crashed between `claim` and `complete`).
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """``claim``'s result. ``response_body`` is populated for ``REPLAY`` and is
    ``None`` for every other outcome — there is no stored response to give back
    yet, and inventing one would be a fabricated success."""

    outcome: ClaimOutcome
    response_body: Json | None = None


class IdempotencyStore(Protocol):
    """The tenant-scoped ``(endpoint, key)`` ledger.

    Every method takes the ``ExecutionContext`` because the key is scoped by
    WORKSPACE as well as endpoint: two tenants that happen to pick the same
    ``Idempotency-Key`` string (a client library defaulting to a request
    counter, say) must not collide, and one tenant must never be able to read
    the other's stored response by guessing a key.
    """

    async def claim(
        self, ctx: ExecutionContext, *, endpoint: str, key: str, request_hash: str
    ) -> IdempotencyClaim: ...

    async def complete(
        self, ctx: ExecutionContext, *, endpoint: str, key: str, response_body: Json
    ) -> None: ...

    async def release(self, ctx: ExecutionContext, *, endpoint: str, key: str) -> None: ...
