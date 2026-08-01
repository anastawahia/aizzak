"""Unit tests for the usage module: value objects (``LimitRule``/enums), the
pure ``enforcement.evaluate``/``periods`` helpers, and the
``EnforceLimit``/``CaptureUsage``/``SetLimits``/``GetUsageSummary`` use-cases
(+ their ``UsageEnforcement``/``UsageCapture`` port-adapter services) over a
local in-memory ``UsageLedgerRepository`` fake (``_FakeUsageLedger``) — none
imported from any other test module (the ``test_media_module.py``
precedent).

Unlike media/knowledge, ``usage``'s domain events (``UsageRecorded``/
``LimitExceeded``) are internal-only with no published wire JSON Schema
(04-event-catalog §5), so there is no schema-conformance section here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import UsageSettings
from app.modules.usage.application.use_cases import (
    CaptureUsage,
    EnforceLimit,
    GetUsageSummary,
    LimitSpec,
    SetLimits,
    UsageCaptureService,
    UsageEnforcementService,
)
from app.modules.usage.domain.enforcement import LimitCheck, evaluate
from app.modules.usage.domain.entities import UsageLimit, UsageRecord
from app.modules.usage.domain.errors import InvalidUsageInput
from app.modules.usage.domain.events import LimitExceeded, UsageRecorded
from app.modules.usage.domain.periods import period_reset_at, period_start, rollup_buckets
from app.modules.usage.domain.read_models import UsageRollup
from app.modules.usage.domain.value_objects import (
    Decision,
    DenyReason,
    LimitRule,
    LimitScope,
    Metric,
    Period,
)
from app.modules.usage.ports.inbound import LimitDecision, UsageCharge
from app.modules.usage.ports.repository import UsageTotals


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str = "ws1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _usage_limit(
    *,
    workspace_id: str = "ws1",
    scope: LimitScope,
    scope_key: str = "*",
    metric: Metric,
    period: Period = Period.MONTH,
    limit_value: int,
) -> UsageLimit:
    now = utc_now()
    return UsageLimit(
        id=new_uuid7(),
        workspace_id=workspace_id,
        scope=scope,
        scope_key=scope_key,
        metric=metric,
        period=period,
        limit_value=limit_value,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _charge(
    *,
    agent: str = "agent-1",
    provider: str = "openai",
    tokens: int = 100,
    cost_micros: int = 50,
    operation_id: str = "op-1",
) -> UsageCharge:
    return UsageCharge(
        agent=agent,
        provider=provider,
        tokens=tokens,
        cost_micros=cost_micros,
        operation_id=operation_id,
    )


def test_usage_charge_defaults_to_measured_not_estimated() -> None:
    """4.7-c-1: omitting ``estimated`` must mean "this count was MEASURED".

    The default is a contract decision, not a formality: flipping it would
    silently relabel every exact, provider-reported charge in the ledger as a
    guess, and no existing test would notice — the live suite's own helper
    always passes the flag explicitly, so the default is never exercised
    there. (Added after the 4.7-c-1 mutation battery reported exactly that
    flip as SURVIVED.)
    """
    charge = UsageCharge(agent="a", provider="p", tokens=1, cost_micros=0, operation_id="op")

    assert charge.estimated is False


# --------------------------------------------------------------------------- #
# Fake (UsageLedgerRepository) -- local to this module.                       #
# --------------------------------------------------------------------------- #
class _FakeUsageLedger:
    """In-memory ``UsageLedgerRepository``. ``append`` updates rollup buckets
    via ``rollup_buckets``/``period_start`` (the future SQL adapter's
    documented behaviour -- ``ports/repository.py``'s ``append`` docstring)
    so ``EnforceLimit``/``CaptureUsage`` tests exercise realistic rollup
    reads. ``rollup_calls`` records every ``rollup()`` invocation so tests
    can assert the "read once per distinct (bucket, period)" caching
    contract (06-domain-models §10 ``EnforceLimit`` step 4).
    """

    def __init__(
        self, *, now: datetime | None = None, rollup_periods: tuple[str, ...] = ("day", "month")
    ) -> None:
        self.now = now or utc_now()
        self.rollup_periods = rollup_periods
        self.records: dict[tuple[str, str], UsageCharge] = {}
        self.rollups: dict[tuple[str, str, str, str, date], tuple[int, int]] = {}
        self.limits: dict[str, list[UsageLimit]] = {}
        self.rollup_calls: list[tuple[str, str, str, str]] = []

    async def append(self, ctx: ExecutionContext, charge: UsageCharge) -> bool:
        key = (ctx.workspace_id, charge.operation_id)
        if key in self.records:
            return False
        self.records[key] = charge
        for period in self.rollup_periods:
            bucket_start = period_start(Period(period), self.now)
            for agent_bucket, provider_bucket in rollup_buckets(charge.agent, charge.provider):
                rkey = (ctx.workspace_id, agent_bucket, provider_bucket, period, bucket_start)
                tokens, cost = self.rollups.get(rkey, (0, 0))
                self.rollups[rkey] = (tokens + charge.tokens, cost + charge.cost_micros)
        return True

    async def rollup(
        self, ctx: ExecutionContext, agent: str, provider: str, period: str
    ) -> UsageTotals:
        self.rollup_calls.append((ctx.workspace_id, agent, provider, period))
        bucket_start = period_start(Period(period), self.now)
        rkey = (ctx.workspace_id, agent, provider, period, bucket_start)
        tokens, cost = self.rollups.get(rkey, (0, 0))
        return UsageTotals(tokens, cost)

    async def get_limits(self, ctx: ExecutionContext) -> list[UsageLimit]:
        return list(self.limits.get(ctx.workspace_id, []))

    async def replace_limits(self, ctx: ExecutionContext, limits: list[UsageLimit]) -> None:
        self.limits[ctx.workspace_id] = list(limits)

    async def list_rollups(self, ctx: ExecutionContext, period: str) -> list[UsageRollup]:
        rows: list[UsageRollup] = []
        for (ws, agent, provider, p, bucket_start), (tokens, cost) in self.rollups.items():
            if ws == ctx.workspace_id and p == period:
                rows.append(
                    UsageRollup(
                        ws, agent, provider, Period(p), bucket_start, tokens, cost, self.now
                    )
                )
        return rows

    def seed_rollup(
        self,
        workspace_id: str,
        agent: str,
        provider: str,
        period: str,
        *,
        tokens: int = 0,
        cost_micros: int = 0,
    ) -> None:
        """Directly set one rollup bucket's totals, bypassing ``append`` --
        for tests that want a specific pre-existing usage level without
        simulating N captures."""
        bucket_start = period_start(Period(period), self.now)
        self.rollups[(workspace_id, agent, provider, period, bucket_start)] = (tokens, cost_micros)


# --------------------------------------------------------------------------- #
# 1) ENUMs + LimitRule coherence                                              #
# --------------------------------------------------------------------------- #
def test_limit_scope_values() -> None:
    assert [scope.value for scope in LimitScope] == ["workspace", "agent", "provider"]


def test_metric_values() -> None:
    assert [metric.value for metric in Metric] == ["tokens", "cost_micros"]


def test_period_values() -> None:
    assert [period.value for period in Period] == ["day", "month"]


def test_deny_reason_values() -> None:
    assert [reason.value for reason in DenyReason] == ["quota_exceeded", "budget_exceeded"]


def test_limit_rule_valid_workspace_rule() -> None:
    rule = LimitRule(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, 1000)
    assert rule.scope_key == "*"
    assert rule.limit_value == 1000


def test_limit_rule_valid_agent_rule() -> None:
    rule = LimitRule(LimitScope.AGENT, "agent-1", Metric.TOKENS, Period.DAY, 500)
    assert rule.scope_key == "agent-1"


def test_limit_rule_valid_provider_rule() -> None:
    rule = LimitRule(LimitScope.PROVIDER, "openai", Metric.COST_MICROS, Period.MONTH, 500)
    assert rule.scope_key == "openai"


def test_limit_rule_rejects_non_wildcard_workspace_scope_key() -> None:
    """Rejection case 1: WORKSPACE requires ``scope_key == '*'``."""
    with pytest.raises(InvalidUsageInput):
        LimitRule(LimitScope.WORKSPACE, "agent-1", Metric.TOKENS, Period.MONTH, 1000)


@pytest.mark.parametrize("scope", [LimitScope.AGENT, LimitScope.PROVIDER])
def test_limit_rule_rejects_wildcard_scope_key_for_agent_or_provider(scope: LimitScope) -> None:
    """Rejection case 2: AGENT/PROVIDER must name a specific, non-'*' key."""
    with pytest.raises(InvalidUsageInput):
        LimitRule(scope, "*", Metric.TOKENS, Period.MONTH, 1000)


@pytest.mark.parametrize("scope", [LimitScope.AGENT, LimitScope.PROVIDER])
def test_limit_rule_rejects_blank_scope_key_for_agent_or_provider(scope: LimitScope) -> None:
    with pytest.raises(InvalidUsageInput):
        LimitRule(scope, "   ", Metric.TOKENS, Period.MONTH, 1000)


def test_limit_rule_rejects_negative_limit_value() -> None:
    """Rejection case 3: ``limit_value`` must be >= 0."""
    with pytest.raises(InvalidUsageInput):
        LimitRule(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, -1)


def test_limit_rule_accepts_zero_limit_value() -> None:
    rule = LimitRule(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, 0)
    assert rule.limit_value == 0


# --------------------------------------------------------------------------- #
# Domain entities (UsageRecord / UsageLimit) -- supplemental to the eight     #
# groups below: entities.py's own validation is not otherwise exercised by    #
# any use-case in this pure slice (CaptureUsage validates UsageCharge fields  #
# directly rather than constructing a UsageRecord -- see its docstring).     #
# --------------------------------------------------------------------------- #
def test_usage_record_valid_construction() -> None:
    record = UsageRecord(
        id="rec-1",
        workspace_id="ws1",
        agent_key="agent-1",
        provider="openai",
        tokens=100,
        cost_micros=200,
        operation_id="op-1",
        created_at=utc_now(),
    )
    assert (record.tokens, record.cost_micros) == (100, 200)


def test_usage_record_rejects_negative_tokens() -> None:
    with pytest.raises(InvalidUsageInput):
        UsageRecord(
            id="rec-1",
            workspace_id="ws1",
            agent_key="agent-1",
            provider="openai",
            tokens=-1,
            cost_micros=0,
            operation_id="op-1",
            created_at=utc_now(),
        )


def test_usage_record_rejects_negative_cost_micros() -> None:
    with pytest.raises(InvalidUsageInput):
        UsageRecord(
            id="rec-1",
            workspace_id="ws1",
            agent_key="agent-1",
            provider="openai",
            tokens=0,
            cost_micros=-1,
            operation_id="op-1",
            created_at=utc_now(),
        )


def test_usage_limit_rule_property_projects_to_limit_rule() -> None:
    limit = _usage_limit(
        scope=LimitScope.AGENT, scope_key="agent-1", metric=Metric.TOKENS, limit_value=10
    )
    assert limit.rule == LimitRule(LimitScope.AGENT, "agent-1", Metric.TOKENS, Period.MONTH, 10)


def test_usage_limit_rejects_inconsistent_scope_via_post_init() -> None:
    with pytest.raises(InvalidUsageInput):
        _usage_limit(scope=LimitScope.AGENT, scope_key="*", metric=Metric.TOKENS, limit_value=10)


# --------------------------------------------------------------------------- #
# 2) period_start -- UTC edges                                               #
# --------------------------------------------------------------------------- #
def test_period_start_day_returns_calendar_date() -> None:
    now = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
    assert period_start(Period.DAY, now) == date(2026, 7, 14)


def test_period_start_month_returns_first_of_month() -> None:
    now = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
    assert period_start(Period.MONTH, now) == date(2026, 7, 1)


def test_period_start_end_of_day_boundary() -> None:
    now = datetime(2026, 7, 14, 23, 59, 59, tzinfo=UTC)
    assert period_start(Period.DAY, now) == date(2026, 7, 14)


def test_period_start_start_of_day_boundary() -> None:
    now = datetime(2026, 7, 14, 0, 0, 0, tzinfo=UTC)
    assert period_start(Period.DAY, now) == date(2026, 7, 14)


def test_period_start_month_boundary() -> None:
    end_of_month = datetime(2026, 7, 31, 23, 0, tzinfo=UTC)
    start_of_next_month = datetime(2026, 8, 1, 0, 30, tzinfo=UTC)
    assert period_start(Period.MONTH, end_of_month) == date(2026, 7, 1)
    assert period_start(Period.MONTH, start_of_next_month) == date(2026, 8, 1)


def test_period_start_normalizes_non_utc_timezone_across_day_boundary() -> None:
    """2026-07-15T02:00 in UTC+5 is 2026-07-14T21:00 UTC -- a different day;
    the non-UTC input must be normalized to UTC before deriving the date."""
    plus_five = timezone(timedelta(hours=5))
    now = datetime(2026, 7, 15, 2, 0, tzinfo=plus_five)
    assert period_start(Period.DAY, now) == date(2026, 7, 14)


# --------------------------------------------------------------------------- #
# 2b) period_reset_at -- the Retry-After producer (3.79)                      #
# --------------------------------------------------------------------------- #
def test_period_reset_at_day_is_the_next_utc_midnight() -> None:
    now = datetime(2026, 7, 14, 23, 59, 59, tzinfo=UTC)
    assert period_reset_at(Period.DAY, now) == datetime(2026, 7, 15, 0, 0, tzinfo=UTC)


def test_period_reset_at_month_is_the_first_of_the_following_month() -> None:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    assert period_reset_at(Period.MONTH, now) == datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def test_period_reset_at_month_rolls_the_year_over_in_december() -> None:
    """The one case naive month arithmetic gets wrong: month 12 + 1 is not
    month 13."""
    now = datetime(2026, 12, 31, 18, 0, tzinfo=UTC)
    assert period_reset_at(Period.MONTH, now) == datetime(2027, 1, 1, 0, 0, tzinfo=UTC)


def test_period_reset_at_month_does_not_add_a_fixed_number_of_days() -> None:
    """Adding 31 days to a January bucket would land in early March, skipping
    February entirely and telling a tenant to wait a month too long."""
    now = datetime(2026, 1, 20, 0, 0, tzinfo=UTC)
    assert period_reset_at(Period.MONTH, now) == datetime(2026, 2, 1, 0, 0, tzinfo=UTC)


def test_period_reset_at_normalizes_a_non_utc_input() -> None:
    """Inherited from ``period_start``: 2026-07-15T02:00+05:00 is still the
    14th in UTC, so the day bucket resets at the 15th's UTC midnight."""
    plus_five = timezone(timedelta(hours=5))
    now = datetime(2026, 7, 15, 2, 0, tzinfo=plus_five)
    assert period_reset_at(Period.DAY, now) == datetime(2026, 7, 15, 0, 0, tzinfo=UTC)


def test_period_reset_at_is_always_strictly_after_now() -> None:
    """The invariant ``Retry-After`` depends on: a reset that is already in
    the past would produce a non-positive delay, i.e. "retry immediately"
    straight back into the same denial."""
    for now in (
        datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 14, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
    ):
        assert period_reset_at(Period.DAY, now) > now
        assert period_reset_at(Period.MONTH, now) > now


# --------------------------------------------------------------------------- #
# 3) rollup_buckets + de-duplication at '*'                                  #
# --------------------------------------------------------------------------- #
def test_rollup_buckets_distinct_agent_and_provider() -> None:
    assert rollup_buckets("agent-1", "openai") == (("*", "*"), ("agent-1", "*"), ("*", "openai"))


def test_rollup_buckets_dedup_when_agent_is_wildcard() -> None:
    assert rollup_buckets("*", "openai") == (("*", "*"), ("*", "openai"))


def test_rollup_buckets_dedup_when_provider_is_wildcard() -> None:
    assert rollup_buckets("agent-1", "*") == (("*", "*"), ("agent-1", "*"))


def test_rollup_buckets_dedup_when_both_wildcard() -> None:
    assert rollup_buckets("*", "*") == (("*", "*"),)


# --------------------------------------------------------------------------- #
# 4) enforcement.evaluate (pure)                                              #
# --------------------------------------------------------------------------- #
def test_evaluate_empty_checks_allows() -> None:
    evaluation = evaluate([])
    assert evaluation.decision == Decision(allowed=True)
    assert evaluation.binding is None


def test_evaluate_allows_and_computes_remaining() -> None:
    checks = [LimitCheck(LimitScope.WORKSPACE, Metric.TOKENS, Period.MONTH, 1000, 400)]
    evaluation = evaluate(checks)
    assert evaluation.decision.allowed is True
    assert evaluation.decision.remaining == 600
    assert evaluation.binding is None


def test_evaluate_denies_quota_exceeded_when_current_at_or_over_limit() -> None:
    checks = [LimitCheck(LimitScope.WORKSPACE, Metric.TOKENS, Period.MONTH, 1000, 1000)]
    evaluation = evaluate(checks)
    assert evaluation.decision.allowed is False
    assert evaluation.decision.reason is DenyReason.QUOTA_EXCEEDED
    assert evaluation.decision.remaining == 0
    assert evaluation.binding == checks[0]


def test_evaluate_denies_budget_exceeded() -> None:
    checks = [LimitCheck(LimitScope.WORKSPACE, Metric.COST_MICROS, Period.MONTH, 5000, 5000)]
    evaluation = evaluate(checks)
    assert evaluation.decision.allowed is False
    assert evaluation.decision.reason is DenyReason.BUDGET_EXCEEDED


def test_evaluate_estimated_tokens_overshoot_denies() -> None:
    checks = [LimitCheck(LimitScope.WORKSPACE, Metric.TOKENS, Period.MONTH, 1000, 990)]
    evaluation = evaluate(checks, estimated_tokens=11)
    assert evaluation.decision.allowed is False
    assert evaluation.decision.reason is DenyReason.QUOTA_EXCEEDED


def test_evaluate_estimated_tokens_exact_match_allows() -> None:
    checks = [LimitCheck(LimitScope.WORKSPACE, Metric.TOKENS, Period.MONTH, 1000, 990)]
    evaluation = evaluate(checks, estimated_tokens=10)
    assert evaluation.decision.allowed is True


def test_evaluate_multiple_violations_tokens_workspace_precedes_cost_provider() -> None:
    tokens_workspace = LimitCheck(LimitScope.WORKSPACE, Metric.TOKENS, Period.MONTH, 100, 100)
    cost_provider = LimitCheck(LimitScope.PROVIDER, Metric.COST_MICROS, Period.MONTH, 100, 100)
    evaluation = evaluate([cost_provider, tokens_workspace])
    assert evaluation.binding == tokens_workspace
    assert evaluation.decision.reason is DenyReason.QUOTA_EXCEEDED


def test_evaluate_multiple_violations_agent_tokens_precedes_workspace_cost() -> None:
    """Metric rank dominates scope rank: tokens/agent (0,1) beats
    cost/workspace (1,0)."""
    agent_tokens = LimitCheck(LimitScope.AGENT, Metric.TOKENS, Period.MONTH, 100, 100)
    workspace_cost = LimitCheck(LimitScope.WORKSPACE, Metric.COST_MICROS, Period.MONTH, 100, 100)
    evaluation = evaluate([workspace_cost, agent_tokens])
    assert evaluation.binding == agent_tokens


def test_evaluate_reservation_id_always_none() -> None:
    allow = evaluate([LimitCheck(LimitScope.WORKSPACE, Metric.TOKENS, Period.MONTH, 100, 0)])
    deny = evaluate([LimitCheck(LimitScope.WORKSPACE, Metric.TOKENS, Period.MONTH, 100, 100)])
    assert allow.decision.reservation_id is None
    assert deny.decision.reservation_id is None


# --------------------------------------------------------------------------- #
# 5) CaptureUsage / UsageCaptureService (AC-16)                               #
# --------------------------------------------------------------------------- #
async def test_capture_usage_first_time_records_and_emits_event_and_updates_rollups() -> None:
    ledger = _FakeUsageLedger()
    ctx = _ctx("ws1")
    charge = _charge(tokens=100, cost_micros=50, operation_id="op-1")

    recorded, events = await CaptureUsage(ledger).execute(ctx, charge)

    assert recorded is True
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, UsageRecorded)
    assert event.operation_id == "op-1"
    assert event.workspace_id == "ws1"
    assert event.agent_key == "agent-1"
    assert event.provider == "openai"

    totals = await ledger.rollup(ctx, "*", "*", "month")
    assert (totals.tokens, totals.cost_micros) == (100, 50)


async def test_capture_usage_duplicate_operation_returns_false_rollups_unchanged() -> None:
    ledger = _FakeUsageLedger()
    ctx = _ctx("ws1")
    charge = _charge(tokens=100, cost_micros=50, operation_id="op-1")
    await CaptureUsage(ledger).execute(ctx, charge)

    recorded, events = await CaptureUsage(ledger).execute(ctx, charge)

    assert recorded is False
    assert events == ()
    totals = await ledger.rollup(ctx, "*", "*", "month")
    assert (totals.tokens, totals.cost_micros) == (100, 50)  # unchanged, not doubled


@pytest.mark.parametrize("tokens,cost_micros", [(-1, 0), (0, -1)])
async def test_capture_usage_rejects_negative_values(tokens: int, cost_micros: int) -> None:
    ledger = _FakeUsageLedger()
    charge = _charge(tokens=tokens, cost_micros=cost_micros)
    with pytest.raises(ValidationError):
        await CaptureUsage(ledger).execute(_ctx(), charge)


@pytest.mark.parametrize("agent,provider", [("", "p"), ("a", ""), ("   ", "p")])
async def test_capture_usage_rejects_blank_agent_or_provider(agent: str, provider: str) -> None:
    ledger = _FakeUsageLedger()
    charge = _charge(agent=agent, provider=provider)
    with pytest.raises(ValidationError):
        await CaptureUsage(ledger).execute(_ctx(), charge)


async def test_usage_capture_service_record_returns_none_on_first_capture() -> None:
    ledger = _FakeUsageLedger()
    result = await UsageCaptureService(CaptureUsage(ledger)).record(_ctx(), _charge())
    assert result is None


async def test_usage_capture_service_record_returns_none_on_duplicate() -> None:
    ledger = _FakeUsageLedger()
    charge = _charge()
    service = UsageCaptureService(CaptureUsage(ledger))
    await service.record(_ctx(), charge)
    result = await service.record(_ctx(), charge)
    assert result is None


# --------------------------------------------------------------------------- #
# 6) EnforceLimit / UsageEnforcementService                                   #
# --------------------------------------------------------------------------- #
async def test_enforce_limit_allows_when_under_all_limits() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=1000),
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.COST_MICROS, limit_value=1000),
    ]
    decision, events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is True
    assert events == ()


async def test_enforce_limit_denies_quota_exceeded() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=1000)
    ]
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=1000)
    decision, events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is False
    assert decision.reason is DenyReason.QUOTA_EXCEEDED
    assert len(events) == 1
    assert isinstance(events[0], LimitExceeded)


async def test_enforce_limit_denies_budget_exceeded() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.COST_MICROS, limit_value=500)
    ]
    ledger.seed_rollup("ws1", "*", "*", "month", cost_micros=500)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is False
    assert decision.reason is DenyReason.BUDGET_EXCEEDED


async def test_enforce_limit_agent_scope_denies_where_workspace_allows() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=1_000_000),
        _usage_limit(
            scope=LimitScope.AGENT, scope_key="agent-1", metric=Metric.TOKENS, limit_value=100
        ),
    ]
    ledger.seed_rollup("ws1", "agent-1", "*", "month", tokens=100)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is False


async def test_enforce_limit_provider_scope_denies_where_workspace_allows() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=1_000_000),
        _usage_limit(
            scope=LimitScope.PROVIDER, scope_key="openai", metric=Metric.TOKENS, limit_value=100
        ),
    ]
    ledger.seed_rollup("ws1", "*", "openai", "month", tokens=100)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is False


async def test_enforce_limit_agent_limit_for_other_agent_does_not_apply() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(
            scope=LimitScope.AGENT, scope_key="agent-other", metric=Metric.TOKENS, limit_value=1
        )
    ]
    ledger.seed_rollup("ws1", "agent-other", "*", "month", tokens=1)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is True  # only the (untripped) default workspace rule applies


async def test_enforce_limit_custom_workspace_limit_narrower_than_default_denies() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=100)
    ]
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=100)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is False  # the 5,000,000 default would have allowed 100 tokens


async def test_enforce_limit_custom_workspace_limit_wider_than_default_allows() -> None:
    ledger = _FakeUsageLedger()
    default_tokens = UsageSettings().default_limits["tokens"]["month"]
    ledger.limits["ws1"] = [
        _usage_limit(
            scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=default_tokens + 1
        )
    ]
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=default_tokens)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is True  # the default would have denied at this usage level


async def test_enforce_limit_no_rows_uses_default_tokens_quota() -> None:
    """07-nfr-slo §4: no configured rows -> the 5,000,000 tokens/month
    default guardrail applies."""
    ledger = _FakeUsageLedger()
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=5_000_000)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is False
    assert decision.reason is DenyReason.QUOTA_EXCEEDED


async def test_enforce_limit_no_rows_uses_default_cost_budget() -> None:
    """07-nfr-slo §4: no configured rows -> the 50,000,000 micros/month
    default guardrail applies."""
    ledger = _FakeUsageLedger()
    ledger.seed_rollup("ws1", "*", "*", "month", cost_micros=50_000_000)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert decision.allowed is False
    assert decision.reason is DenyReason.BUDGET_EXCEEDED


async def test_enforce_limit_reads_rollup_once_per_distinct_bucket_and_period() -> None:
    """Both default rules (tokens/month, cost_micros/month) are
    workspace-scoped and share the same (bucket, period) -- exactly one
    ``rollup()`` call must serve both."""
    ledger = _FakeUsageLedger()
    await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert ledger.rollup_calls == [("ws1", "*", "*", "month")]


async def test_usage_enforcement_service_threads_estimated_tokens() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=100)
    ]
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=95)
    service = UsageEnforcementService(EnforceLimit(ledger, UsageSettings()))

    allowed = await service.check(_ctx("ws1"), "agent-1", "openai", estimated_tokens=5)
    assert allowed.allowed is True

    denied = await service.check(_ctx("ws1"), "agent-1", "openai", estimated_tokens=6)
    assert denied.allowed is False


async def test_usage_enforcement_service_converts_decision_to_limit_decision() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=10)
    ]
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=10)
    service = UsageEnforcementService(EnforceLimit(ledger, UsageSettings()))

    result = await service.check(_ctx("ws1"), "agent-1", "openai")

    assert isinstance(result, LimitDecision)
    assert result.allowed is False
    assert result.reason == "quota_exceeded"
    assert result.remaining == 0
    assert result.reservation_id is None
    # 3.79: the reset hint crosses the port too, or the API can emit no header.
    assert result.retry_after_s is not None
    assert result.retry_after_s > 0


async def test_a_denial_carries_a_retry_hint_bounded_by_its_own_period() -> None:
    """3.79: ``retry_after_s`` is the wait until the BINDING limit's period
    rolls over — so a monthly quota and a daily one cannot report the same
    number, and neither can exceed its own window."""
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(
            scope=LimitScope.WORKSPACE,
            metric=Metric.TOKENS,
            period=Period.DAY,
            limit_value=10,
        )
    ]
    ledger.seed_rollup("ws1", "*", "*", "day", tokens=10)
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )

    assert decision.allowed is False
    assert decision.retry_after_s is not None
    # Strictly positive (never "retry now") and never longer than the window
    # it is waiting out.
    assert 0 < decision.retry_after_s <= 24 * 60 * 60


async def test_an_allowed_decision_carries_no_retry_hint() -> None:
    """There is nothing to wait for on an allow, and an ``AppError`` is never
    raised — a number here could only ever reach a client by accident."""
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=1000)
    ]
    decision, _events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )

    assert decision.allowed is True
    assert decision.retry_after_s is None


async def test_enforce_limit_emits_no_event_when_allowed() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(scope=LimitScope.WORKSPACE, metric=Metric.TOKENS, limit_value=1000)
    ]
    _decision, events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert events == ()


async def test_enforce_limit_emits_limit_exceeded_event_with_binding_scope_and_metric() -> None:
    ledger = _FakeUsageLedger()
    ledger.limits["ws1"] = [
        _usage_limit(
            scope=LimitScope.AGENT, scope_key="agent-1", metric=Metric.TOKENS, limit_value=10
        )
    ]
    ledger.seed_rollup("ws1", "agent-1", "*", "month", tokens=10)
    _decision, events = await EnforceLimit(ledger, UsageSettings()).execute(
        _ctx("ws1"), agent="agent-1", provider="openai"
    )
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, LimitExceeded)
    assert event.workspace_id == "ws1"
    assert event.scope == "agent"
    assert event.metric == "tokens"


# --------------------------------------------------------------------------- #
# 7) SetLimits                                                                #
# --------------------------------------------------------------------------- #
async def test_set_limits_valid_batch_builds_usage_limits_and_replaces() -> None:
    ledger = _FakeUsageLedger()
    ctx = _ctx("ws1")
    specs = [
        LimitSpec(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, 1000),
        LimitSpec(LimitScope.AGENT, "agent-1", Metric.COST_MICROS, Period.DAY, 500),
    ]

    result = await SetLimits(ledger).execute(ctx, limits=specs)

    assert len(result) == 2
    for limit in result:
        assert limit.version == 1
        assert limit.workspace_id == "ws1"
    assert len({limit.id for limit in result}) == 2  # each minted its own uuid7
    assert ledger.limits["ws1"] == list(result)


async def test_set_limits_rejects_invalid_spec() -> None:
    ledger = _FakeUsageLedger()
    specs = [LimitSpec(LimitScope.AGENT, "*", Metric.TOKENS, Period.MONTH, 100)]
    with pytest.raises(ValidationError):
        await SetLimits(ledger).execute(_ctx(), limits=specs)


async def test_set_limits_rejects_duplicate_key_in_payload() -> None:
    ledger = _FakeUsageLedger()
    specs = [
        LimitSpec(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, 100),
        LimitSpec(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, 200),
    ]
    with pytest.raises(ConflictError):
        await SetLimits(ledger).execute(_ctx(), limits=specs)


async def test_set_limits_rejects_negative_limit_value() -> None:
    ledger = _FakeUsageLedger()
    specs = [LimitSpec(LimitScope.WORKSPACE, "*", Metric.TOKENS, Period.MONTH, -1)]
    with pytest.raises(ValidationError):
        await SetLimits(ledger).execute(_ctx(), limits=specs)


# --------------------------------------------------------------------------- #
# 8) GetUsageSummary                                                          #
# --------------------------------------------------------------------------- #
async def test_get_usage_summary_builds_total_by_agent_by_provider() -> None:
    ledger = _FakeUsageLedger()
    ctx = _ctx("ws1")
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=1000, cost_micros=2000)
    ledger.seed_rollup("ws1", "agent-1", "*", "month", tokens=600, cost_micros=1200)
    ledger.seed_rollup("ws1", "agent-2", "*", "month", tokens=400, cost_micros=800)
    ledger.seed_rollup("ws1", "*", "openai", "month", tokens=700, cost_micros=1400)
    ledger.seed_rollup("ws1", "*", "claude", "month", tokens=300, cost_micros=600)

    summary = await GetUsageSummary(ledger).execute(ctx, period="month")

    assert summary.period is Period.MONTH
    assert summary.tokens == 1000
    assert summary.cost_micros == 2000
    assert {d.key: (d.tokens, d.cost_micros) for d in summary.by_agent} == {
        "agent-1": (600, 1200),
        "agent-2": (400, 800),
    }
    assert {d.key: (d.tokens, d.cost_micros) for d in summary.by_provider} == {
        "openai": (700, 1400),
        "claude": (300, 600),
    }


async def test_get_usage_summary_rejects_invalid_period() -> None:
    ledger = _FakeUsageLedger()
    with pytest.raises(ValidationError):
        await GetUsageSummary(ledger).execute(_ctx(), period="year")


async def test_get_usage_summary_defaults_to_month() -> None:
    ledger = _FakeUsageLedger()
    ledger.seed_rollup("ws1", "*", "*", "month", tokens=42)
    summary = await GetUsageSummary(ledger).execute(_ctx("ws1"))
    assert summary.tokens == 42
    assert summary.period is Period.MONTH
