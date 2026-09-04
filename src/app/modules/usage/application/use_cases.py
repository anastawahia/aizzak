"""Usage use-cases (06-domain-models §10).

Thin application services that coordinate the pure domain over injected
ports. They own identity/time (framework ``new_uuid7``/``utc_now``) and
translate domain-rule violations into the shared framework error hierarchy
at this boundary — the domain itself stays framework-free (media/knowledge
precedent).

``EnforceLimit``/``CaptureUsage`` are the pure-slice use-cases behind
02-port-contracts §2's ``UsageEnforcement``/``UsageCapture`` inbound ports;
``ReserveQuota``/``CommitReservation``/``ReleaseReservation`` (capacity-plan
2.7) are the reserve/commit flow the same section declared as an extension
point — the answer to the fact that ``EnforceLimit`` is a READ and a hundred
simultaneous readers all see the same headroom (measured: one token of
headroom admitted 46 of 100 concurrent requests, and 46 was the size of the
connection pool);
``UsageEnforcementService``/``UsageCaptureService`` below implement those
ports over them (the ``KnowledgeRetrievalService`` precedent for an
inbound-port implementation living alongside the other use-cases). Both
ports are called **only by the orchestrator** (INV-U4); no other module
imports this one.

Guardrail fallback (coordinator decision): when a workspace has no
configured ``UsageLimit`` row for a given ``(metric, period)``,
``EnforceLimit`` falls back to ``UsageSettings.default_limits`` (07-nfr-slo
§4's platform-wide quota/budget, approved OQ-02) as an implicit
workspace-scoped rule — "deny by guardrail" rather than "allow unbounded"
when configuration is absent. Enforcement reads ``UsageSettings
.default_limits`` exclusively; ``Limits.usage_tokens_quota_month``/
``usage_cost_micros_month`` (07-nfr-slo §4's same numbers, also mirrored
onto ``Limits`` for other callers) are deliberately NOT consulted here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.quota_lock import QuotaLock
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.settings.settings import UsageSettings
from app.modules.usage.domain.enforcement import LimitCheck, evaluate
from app.modules.usage.domain.entities import Reservation, UsageLimit
from app.modules.usage.domain.errors import UsageError
from app.modules.usage.domain.events import LimitExceeded, UsageEvent, UsageRecorded
from app.modules.usage.domain.periods import period_reset_at
from app.modules.usage.domain.read_models import DimensionUsage, UsageSummary
from app.modules.usage.domain.value_objects import Decision, LimitRule, LimitScope, Metric, Period
from app.modules.usage.ports.inbound import LimitDecision, UsageCharge
from app.modules.usage.ports.repository import UsageLedgerRepository

_WILDCARD = "*"

# The ``QuotaLock`` name for the token/cost quota (capacity-plan 2.7). A
# constant rather than a literal because the string IS the lock's identity --
# two spellings are two locks, and two locks serialise nobody.
USAGE_QUOTA_CEILING = "usage.workspace_quota"

# What ``ReserveQuota`` holds for a caller that supplied no estimate.
#
# ⚠️ NOT a guess about the request's cost, and the distinction is the reason
# this number is allowed to exist at all. ``orchestrator._enforce`` argued --
# correctly -- that inventing a per-request token figure would silently shrink
# every workspace's headroom by that invention. One token invents nothing: it
# is the smallest true statement about a request that runs, it is replaced by
# the measured charge at ``commit``, and it is what turns "a hundred callers
# and one token of headroom" into one admission instead of a hundred.
_MIN_RESERVED_TOKENS = 1

# ⚠️ Reservations carry NO cost estimate, and the ``COST_MICROS`` limit is
# therefore admission-controlled by the ledger alone. That is not an oversight
# to be fixed by inventing a price: v1 charges ``_V1_COST_MICROS = 0`` for
# every operation (``agents/orchestrator.py``), so a cost reservation would be
# a number with no producer on either side of it. The moment a real price
# exists, this is the line that changes.
_RESERVED_COST_MICROS = 0


@dataclass(frozen=True, slots=True)
class LimitSpec:
    """One requested limit in a ``SetLimits`` call — plain input, validated
    into a ``LimitRule`` (and then a persisted ``UsageLimit``) by the
    use-case."""

    scope: LimitScope
    scope_key: str
    metric: Metric
    period: Period
    limit_value: int


class EnforceLimit:
    """The pure-slice implementation behind ``UsageEnforcement.check`` (02
    §2) — see ``UsageEnforcementService`` below for the port adapter."""

    def __init__(self, ledger: UsageLedgerRepository, usage_settings: UsageSettings) -> None:
        self._ledger = ledger
        self._usage_settings = usage_settings

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> tuple[Decision, tuple[UsageEvent, ...]]:
        rows = await self._ledger.get_limits(ctx)
        configured = [row.rule for row in rows]
        rules = _with_defaults(configured, self._usage_settings)
        relevant = [rule for rule in rules if _governs(rule, agent, provider)]

        totals: dict[tuple[str, str, str], tuple[int, int]] = {}
        # In-flight totals are NOT period-keyed, and the separate cache is
        # what says so: a reservation is live NOW, so it belongs to the day's
        # bucket and the month's alike, and re-asking per period would be the
        # same rows counted twice under two names.
        held: dict[tuple[str, str], tuple[int, int]] = {}
        checks: list[LimitCheck] = []
        for rule in relevant:
            bucket = _bucket_for(rule.scope, agent, provider)
            totals_key = (bucket[0], bucket[1], rule.period.value)
            if totals_key not in totals:
                result = await self._ledger.rollup(ctx, bucket[0], bucket[1], rule.period.value)
                totals[totals_key] = (result.tokens, result.cost_micros)
            if bucket not in held:
                # capacity-plan 2.7 -- what is ALREADY admitted and not yet
                # charged. Counted by `check` as well as by `reserve`, and
                # deliberately: "is this workspace over?" has one answer, and a
                # reader that ignored in-flight work would give the optimistic
                # one to the workflow run-level gate while every step of that
                # run reserved against the honest one.
                in_flight = await self._ledger.reserved(ctx, bucket[0], bucket[1])
                held[bucket] = (in_flight.tokens, in_flight.cost_micros)
            tokens_total, cost_total = totals[totals_key]
            tokens_held, cost_held = held[bucket]
            current = (
                tokens_total + tokens_held
                if rule.metric is Metric.TOKENS
                else cost_total + cost_held
            )
            checks.append(
                LimitCheck(rule.scope, rule.metric, rule.period, rule.limit_value, current)
            )

        evaluation = evaluate(checks, estimated_tokens=estimated_tokens)
        if evaluation.binding is None:
            return evaluation.decision, ()

        # ONE `now` for both the event and the retry hint (3.79): reading the
        # clock twice would let a denial's `LimitExceeded` timestamp and its
        # `Retry-After` disagree about which period they belong to, which is
        # exactly the kind of one-second window that is unreproducible.
        now = utc_now()
        binding = evaluation.binding
        decision = replace(
            evaluation.decision,
            retry_after_s=_seconds_until(period_reset_at(binding.period, now), now),
        )
        events: tuple[UsageEvent, ...] = (
            LimitExceeded(ctx.workspace_id, binding.scope.value, binding.metric.value, now),
        )
        return decision, events


class ReserveQuota:
    """Admit one operation against the workspace's quota and hold its slot —
    the atomic half of `FR-132` that `02 §2` declared as an extension point and
    capacity-plan 2.7 built (`INV-U3`).

    **Why ``EnforceLimit`` alone could not do this.** Its body is a READ. Under
    concurrency every caller that arrives before the first charge lands reads a
    total that excludes every other caller, so all of them are told yes.
    Measured on the live stack against a workspace with **one** token of
    headroom: 46 of 100 concurrent requests admitted, ledger at 55 against a
    limit of 10 — and the 46 was the width of the connection pool, not a
    property of the quota (``pool_size=5`` admitted 5).

    **What makes this one atomic.** The whole body is ONE unit of work holding
    the workspace's ``QuotaLock``, so the totals read and the reservation
    written cannot be interleaved by a second admission. Contention is scoped
    to one ceiling of one workspace: two tenants never wait for each other,
    and neither does a file registration waiting on its own lock.

    **The reservation is counted by the very next reader**, because
    ``EnforceLimit`` adds ``reserved`` into ``current``. That is the entire
    mechanism — no counter, no decrement, no number that can drift from the
    rows it summarises.
    """

    def __init__(
        self,
        enforce: EnforceLimit,
        ledger: UsageLedgerRepository,
        uow: UnitOfWork,
        lock: QuotaLock,
        ttl_s: int,
    ) -> None:
        self._enforce = enforce
        self._ledger = ledger
        self._uow = uow
        self._lock = lock
        self._ttl_s = ttl_s

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> tuple[Decision, tuple[UsageEvent, ...]]:
        tokens = max(estimated_tokens or 0, _MIN_RESERVED_TOKENS)
        async with self._uow.begin(ctx):
            await self._lock.hold(ctx, USAGE_QUOTA_CEILING)
            # `estimated_tokens=tokens` is what makes the request count
            # against itself: `evaluate`'s overshoot rule denies a check whose
            # `current + estimated` would pass the cap, so the last token of
            # headroom is taken by the first caller and refused to the rest.
            decision, events = await self._enforce.execute(
                ctx, agent=agent, provider=provider, estimated_tokens=tokens
            )
            if not decision.allowed:
                return decision, events

            now = utc_now()
            reservation = Reservation(
                id=new_uuid7(),
                workspace_id=ctx.workspace_id,
                agent_key=agent,
                provider=provider,
                tokens=tokens,
                cost_micros=_RESERVED_COST_MICROS,
                created_at=now,
                expires_at=now + timedelta(seconds=self._ttl_s),
            )
            await self._ledger.reserve(ctx, reservation)
            return replace(decision, reservation_id=reservation.id), events


class CommitReservation:
    """Replace a held reservation with what the operation actually spent.

    ONE unit of work for the release and the ledger append: a workspace must
    never be briefly charged twice for one request (slot still held, charge
    already landed) nor briefly charged for neither. The append keeps its own
    idempotency (`INV-U1`) — a replayed ``operation_id`` still writes nothing,
    and the reservation is still given back, which is the right outcome for a
    retry of a capture that already succeeded.
    """

    def __init__(
        self, ledger: UsageLedgerRepository, capture: CaptureUsage, uow: UnitOfWork
    ) -> None:
        self._ledger = ledger
        self._capture = capture
        self._uow = uow

    async def execute(
        self, ctx: ExecutionContext, reservation_id: str, charge: UsageCharge
    ) -> tuple[bool, tuple[UsageEvent, ...]]:
        async with self._uow.begin(ctx):
            await self._ledger.release(ctx, reservation_id)
            return await self._capture.execute(ctx, charge)


class ReleaseReservation:
    """Hand a slot back, charging nothing — the admitted operation that
    consumed no tokens at all (a media agent, `D-04`), and the one that died
    before it could. No unit of work: a single DELETE is already atomic, and
    wrapping it would only add a transaction to say so."""

    def __init__(self, ledger: UsageLedgerRepository) -> None:
        self._ledger = ledger

    async def execute(self, ctx: ExecutionContext, reservation_id: str) -> None:
        await self._ledger.release(ctx, reservation_id)


class UsageEnforcementService:
    """Implements ``UsageEnforcement`` (02 §2) over ``EnforceLimit``. Domain
    events returned by ``EnforceLimit`` are intentionally dropped here:
    nothing in this pure slice publishes them (no ``EventPublisher``
    dependency — INV-U5), and the inbound port's contract returns only a
    ``LimitDecision``.
    """

    def __init__(
        self,
        enforce: EnforceLimit,
        reserve: ReserveQuota,
        commit: CommitReservation,
        release: ReleaseReservation,
    ) -> None:
        self._enforce = enforce
        self._reserve = reserve
        self._commit = commit
        self._release = release

    async def check(
        self,
        ctx: ExecutionContext,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> LimitDecision:
        decision, _events = await self._enforce.execute(
            ctx, agent=agent, provider=provider, estimated_tokens=estimated_tokens
        )
        # `reservation_id=None` is a STATEMENT here, not a leftover: `check`
        # holds nothing, so a caller must never be able to mistake its answer
        # for an admission it owes a `commit` or a `release` for.
        return _decision_out(decision, reservation_id=None)

    async def reserve(
        self,
        ctx: ExecutionContext,
        agent: str,
        provider: str,
        estimated_tokens: int | None = None,
    ) -> LimitDecision:
        decision, _events = await self._reserve.execute(
            ctx, agent=agent, provider=provider, estimated_tokens=estimated_tokens
        )
        return _decision_out(decision, reservation_id=decision.reservation_id)

    async def commit(self, ctx: ExecutionContext, reservation_id: str, charge: UsageCharge) -> None:
        await self._commit.execute(ctx, reservation_id, charge)

    async def release(self, ctx: ExecutionContext, reservation_id: str) -> None:
        await self._release.execute(ctx, reservation_id)


class CaptureUsage:
    """The pure-slice implementation behind ``UsageCapture.record`` (02 §2)
    — see ``UsageCaptureService`` below for the port adapter."""

    def __init__(self, ledger: UsageLedgerRepository) -> None:
        self._ledger = ledger

    async def execute(
        self, ctx: ExecutionContext, charge: UsageCharge
    ) -> tuple[bool, tuple[UsageEvent, ...]]:
        if not charge.agent.strip():
            raise ValidationError("agent must not be empty")
        if not charge.provider.strip():
            raise ValidationError("provider must not be empty")
        if charge.tokens < 0:
            raise ValidationError("tokens must be >= 0")
        if charge.cost_micros < 0:
            raise ValidationError("cost_micros must be >= 0")

        recorded = await self._ledger.append(ctx, charge)
        if not recorded:
            return False, ()

        event = UsageRecorded(
            charge.operation_id, ctx.workspace_id, charge.agent, charge.provider, utc_now()
        )
        return True, (event,)


class UsageCaptureService:
    """Implements ``UsageCapture.record`` (02 §2) over ``CaptureUsage``: a
    duplicate ``operation_id`` is silently ignored (INV-U1), matching the
    inbound port's ``-> None`` contract — there is nothing meaningful to
    return either way, and no ``EventPublisher`` is wired here (INV-U5).
    """

    def __init__(self, capture: CaptureUsage) -> None:
        self._capture = capture

    async def record(self, ctx: ExecutionContext, charge: UsageCharge) -> None:
        await self._capture.execute(ctx, charge)


class SetLimits:
    """Replace this workspace's entire configured limit set (06 §10
    ``SetLimits``) — whole-set replacement, not a per-row upsert (see
    ``UsageLedgerRepository.replace_limits``'s docstring)."""

    def __init__(self, ledger: UsageLedgerRepository) -> None:
        self._ledger = ledger

    async def execute(
        self, ctx: ExecutionContext, *, limits: Sequence[LimitSpec]
    ) -> tuple[UsageLimit, ...]:
        seen: set[tuple[LimitScope, str, Metric, Period]] = set()
        now = utc_now()
        built: list[UsageLimit] = []
        for spec in limits:
            try:
                rule = LimitRule(
                    spec.scope, spec.scope_key, spec.metric, spec.period, spec.limit_value
                )
            except UsageError as exc:
                raise ValidationError(str(exc)) from exc
            key = (rule.scope, rule.scope_key, rule.metric, rule.period)
            if key in seen:
                raise ConflictError(
                    "duplicate limit key in payload: "
                    f"{rule.scope.value}/{rule.scope_key}/{rule.metric.value}/{rule.period.value}"
                )
            seen.add(key)
            built.append(
                UsageLimit(
                    id=new_uuid7(),
                    workspace_id=ctx.workspace_id,
                    scope=rule.scope,
                    scope_key=rule.scope_key,
                    metric=rule.metric,
                    period=rule.period,
                    limit_value=rule.limit_value,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
            )
        await self._ledger.replace_limits(ctx, built)
        return tuple(built)


class ListLimits:
    """This workspace's CONFIGURED limits, exactly as stored (``GET
    /api/v1/usage/limits``).

    Deliberately NOT ``_with_defaults``: the guardrail defaults
    ``EnforceLimit`` falls back on are platform CONFIGURATION
    (``UsageSettings.default_limits``), not rows this workspace owns, and the
    only honest answer to "what have I configured?" is the stored set —
    otherwise a ``PUT`` of what a ``GET`` returned would silently
    materialise the platform defaults as workspace-scoped rows and freeze
    today's numbers into the tenant forever.
    """

    def __init__(self, ledger: UsageLedgerRepository) -> None:
        self._ledger = ledger

    async def execute(self, ctx: ExecutionContext) -> tuple[UsageLimit, ...]:
        return tuple(await self._ledger.get_limits(ctx))


class GetUsageSummary:
    """Assemble a ``UsageSummary`` from persisted rollups (06 §10
    ``GetUsageSummary``)."""

    def __init__(self, ledger: UsageLedgerRepository) -> None:
        self._ledger = ledger

    async def execute(self, ctx: ExecutionContext, *, period: str = "month") -> UsageSummary:
        try:
            period_vo = Period(period)
        except ValueError as exc:
            raise ValidationError(f"invalid period: {period!r}") from exc

        rows = await self._ledger.list_rollups(ctx, period_vo.value)

        tokens = 0
        cost_micros = 0
        by_agent: list[DimensionUsage] = []
        by_provider: list[DimensionUsage] = []
        for row in rows:
            if row.agent_key == _WILDCARD and row.provider == _WILDCARD:
                tokens = row.tokens_sum
                cost_micros = row.cost_micros_sum
            elif row.provider == _WILDCARD and row.agent_key != _WILDCARD:
                by_agent.append(DimensionUsage(row.agent_key, row.tokens_sum, row.cost_micros_sum))
            elif row.agent_key == _WILDCARD and row.provider != _WILDCARD:
                by_provider.append(
                    DimensionUsage(row.provider, row.tokens_sum, row.cost_micros_sum)
                )

        return UsageSummary(
            period=period_vo,
            tokens=tokens,
            cost_micros=cost_micros,
            by_agent=tuple(by_agent),
            by_provider=tuple(by_provider),
        )


@dataclass(frozen=True, slots=True)
class UsageUseCases:
    """The module's API-facing bundle — what ``/api/v1/usage`` delegates to.

    The three READ/CONFIGURE use-cases only. ``UsageEnforcementService``/
    ``UsageCaptureService`` are pointedly absent: 03 §1 says so in as many
    words ("الفرض والالتقاط منفذان واردان داخليّان … ليسا API عاماً",
    FR-131/132), and INV-U4 hands those two ports to the orchestrator alone.
    Putting them in a bundle the API layer holds would make metering
    client-reachable — a workspace could charge or clear its own ledger.
    """

    summary: GetUsageSummary
    list_limits: ListLimits
    set_limits: SetLimits


def _decision_out(decision: Decision, *, reservation_id: str | None) -> LimitDecision:
    """Project the domain ``Decision`` onto the inbound port's
    ``LimitDecision``. One function so ``check`` and ``reserve`` can never
    drift in what they translate — the difference between them is the
    ``reservation_id`` argument and nothing else."""
    return LimitDecision(
        allowed=decision.allowed,
        reason=decision.reason.value if decision.reason is not None else None,
        remaining=decision.remaining,
        reservation_id=reservation_id,
        retry_after_s=decision.retry_after_s,
    )


def _seconds_until(reset_at: datetime, now: datetime) -> int:
    """Whole seconds from ``now`` to ``reset_at``, for ``Retry-After`` (3.79).

    Rounded UP and floored at 1. RFC 9110 defines the delay-seconds form as a
    non-negative integer, and truncating 0.4s to ``0`` would tell a client to
    retry immediately — straight back into the same denial, at the exact
    moment the platform is trying to shed load. One second late is free; one
    second early is a retry storm.
    """
    return max(1, math.ceil((reset_at - now).total_seconds()))


def _governs(rule: LimitRule, agent: str, provider: str) -> bool:
    """Which configured/default rules actually apply to THIS call: workspace
    rules always; agent/provider rules only when their ``scope_key`` names
    the call's actual agent/provider."""
    if rule.scope is LimitScope.WORKSPACE:
        return True
    if rule.scope is LimitScope.AGENT:
        return rule.scope_key == agent
    return rule.scope_key == provider


def _bucket_for(scope: LimitScope, agent: str, provider: str) -> tuple[str, str]:
    """The single rollup bucket a given rule's scope reads from: WORKSPACE ->
    ``('*', '*')``, AGENT -> ``(agent, '*')``, PROVIDER -> ``('*',
    provider)``."""
    if scope is LimitScope.WORKSPACE:
        return (_WILDCARD, _WILDCARD)
    if scope is LimitScope.AGENT:
        return (agent, _WILDCARD)
    return (_WILDCARD, provider)


def _with_defaults(configured: list[LimitRule], usage_settings: UsageSettings) -> list[LimitRule]:
    """Add a workspace-scoped default rule from ``UsageSettings
    .default_limits`` for every ``(metric, period)`` NOT already covered by a
    configured workspace-scoped rule (coordinator decision: deny-by-guardrail
    fallback). ``Metric()``/``Period()``/``int()`` are validated here, at the
    point of consumption, and nowhere else."""
    existing_workspace_keys = {
        (rule.metric, rule.period) for rule in configured if rule.scope is LimitScope.WORKSPACE
    }
    rules = list(configured)
    for metric_key, by_period in usage_settings.default_limits.items():
        metric = Metric(metric_key)
        for period_key, limit_value in by_period.items():
            period = Period(period_key)
            if (metric, period) in existing_workspace_keys:
                continue
            rules.append(
                LimitRule(LimitScope.WORKSPACE, _WILDCARD, metric, period, int(limit_value))
            )
    return rules
