"""Usage inbound ports (02-port-contracts §2, verbatim contract).

Called **only by the orchestrator** (the agents layer, INV-U4) — never by
another business module (12-module-authoring-guide §3). Both are
synchronous, no Redis Streams (INV-U5, FR-131/132):
``UsageEnforcement.check`` runs before an operation, ``UsageCapture.record``
appends after it, idempotently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class UsageCharge:
    """What the orchestrator supplies after an operation completes (FR-134)."""

    agent: str
    provider: str
    tokens: int
    cost_micros: int
    operation_id: Uuid
    # Phase 4.7-c-1 (additive, defaults to the honest "measured"): was
    # ``tokens`` REPORTED by the provider, or estimated by the orchestrator?
    #
    # 4.7-a gave ``LlmChunk`` real ``prompt_tokens``/``completion_tokens`` and
    # defined ``None`` there as "the provider reported nothing, estimate" —
    # and the user's billing decision was explicitly "exact-when-available
    # **plus a marker**". This is that marker, carried all the way into the
    # ledger (column added in ``0002_usage_estimated``): without it a measured
    # and a guessed row are byte-identical, and nobody auditing a bill can
    # tell which is which. Enforcement deliberately does NOT branch on it —
    # an estimated charge still counts against the quota, because the
    # alternative is a free tier for every provider that omits usage.
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class LimitDecision:
    """A decision object — never a bare ``bool`` (FR-132)."""

    allowed: bool
    reason: str | None = None
    remaining: int | None = None
    reservation_id: Uuid | None = None
    # Phase 3.79 (additive, defaults to "no hint"): seconds until the limit
    # that caused THIS denial resets.
    #
    # 03 §4 withheld `Retry-After` from v1 as a deliberate refusal rather than
    # an oversight — the reasoning was "لا مُنتِج له": this port carried
    # `remaining` and no reset time, so any header value would have been
    # invented, and an invented `Retry-After` is worse than none (a client
    # backs off for a number that means nothing). That same paragraph names
    # the fix — "حقل إعادة تعيين على `LimitDecision`" — and this is it.
    #
    # `None` on an ALLOW, always: there is nothing to wait for. `None` is also
    # the honest answer for a denial whose cause has no period (none exist
    # today; a future reserve/commit denial would be one), which is why the
    # field is optional rather than "0 means unknown" — a client cannot tell
    # an unknown 0 from an immediate retry.
    retry_after_s: int | None = None


class UsageEnforcement(Protocol):
    """Called before an operation.

    **Two questions, not one (capacity-plan 2.7).** ``check`` asks "is this
    workspace already over?" and changes nothing; ``reserve`` asks "admit me"
    and takes a slot. The distinction is the whole fix: a pure read cannot
    stop a hundred simultaneous callers from each seeing the same headroom and
    all being told yes. Measured on the live stack before ``reserve`` existed,
    on a workspace with **one** token of headroom: 46 of 100 concurrent
    requests were admitted and the ledger finished at 55 against a limit of 10.

    ``reserve``/``commit`` are the extension points 02 §2 declared and
    01 §2.10 named a table for ("جدول حجوزات لاحقاً"); ``release`` is their
    necessary third, for the admitted request that ends up spending nothing.
    ``check`` is unchanged and still the right call wherever no charge will
    follow — the workflow RUN-level pre-flight gate, where every step reserves
    under its own agent and provider a moment later.
    """

    async def check(
        self,
        ctx: ExecutionContext,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> LimitDecision: ...

    async def reserve(
        self,
        ctx: ExecutionContext,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> LimitDecision:
        """Admit this operation and hold its slot, or deny it.

        On an ALLOW the returned ``LimitDecision.reservation_id`` is not
        ``None``, and the caller owes exactly one ``commit`` (it spent) or
        ``release`` (it did not). A caller that does neither is not a
        correctness problem — the reservation expires — but it costs the
        workspace one slot's headroom until it does.

        ``estimated_tokens`` is the caller's own worst case when it has one.
        When it has none the reservation is ONE token, which is not a guess
        about the request's cost but the smallest true statement about it: a
        request that runs spends at least one token. That floor is what turns
        "a hundred callers, one token of headroom" into one admission, and it
        is released or replaced by the real charge within the request's own
        deadline, so it never shrinks a workspace's headroom by more than the
        number of requests genuinely in flight.
        """
        ...

    async def commit(
        self, ctx: ExecutionContext, reservation_id: Uuid, charge: UsageCharge
    ) -> None:
        """Replace a held reservation with what the operation actually spent —
        the release and the ledger append in ONE transaction, so a workspace is
        never briefly charged twice for the same request nor briefly charged
        for neither."""
        ...

    async def release(self, ctx: ExecutionContext, reservation_id: Uuid) -> None:
        """Give a reservation back, charging nothing. The admitted request
        that consumed no tokens at all (a media agent, D-04) and the one that
        died before it could — both hand the slot straight back rather than
        making the next caller wait out the expiry."""
        ...


class UsageCapture(Protocol):
    """Called after an operation — synchronous, idempotent append."""

    async def record(self, ctx: ExecutionContext, charge: UsageCharge) -> None: ...

    # A duplicate operation_id is silently ignored.
