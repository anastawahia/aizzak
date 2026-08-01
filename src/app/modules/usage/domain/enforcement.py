"""Pure limit-evaluation engine (06-domain-models §10 VO ``Decision``,
INV-U3/U6). No I/O, no framework imports — ``application.use_cases
.EnforceLimit`` is the only caller, after resolving every relevant
``LimitCheck.current`` from the ledger.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.modules.usage.domain.value_objects import Decision, DenyReason, LimitScope, Metric, Period

_METRIC_RANK: dict[Metric, int] = {Metric.TOKENS: 0, Metric.COST_MICROS: 1}
_SCOPE_RANK: dict[LimitScope, int] = {
    LimitScope.WORKSPACE: 0,
    LimitScope.AGENT: 1,
    LimitScope.PROVIDER: 2,
}


@dataclass(frozen=True, slots=True)
class LimitCheck:
    """One resolved limit + its current usage, ready to evaluate."""

    scope: LimitScope
    metric: Metric
    period: Period
    limit_value: int
    current: int


@dataclass(frozen=True, slots=True)
class Evaluation:
    """``evaluate()``'s full result: the ``Decision`` plus, on denial, the
    specific ``LimitCheck`` that caused it (``binding``); ``None`` when
    allowed."""

    decision: Decision
    binding: LimitCheck | None


def evaluate(checks: Sequence[LimitCheck], *, estimated_tokens: int | None = None) -> Evaluation:
    """Evaluate every relevant ``LimitCheck`` and return one ``Decision``.

    1. No checks at all ⇒ allow, ``remaining=None`` (nothing to be near).
    2. A ``TOKENS`` check is violated when already at/over its cap
       (``current >= limit_value``) OR when the request's
       ``estimated_tokens`` would push it strictly over
       (``current + estimated_tokens > limit_value`` — landing exactly on
       the cap is allowed). A ``COST_MICROS`` check is only ever violated by
       the first rule (there is no "estimated cost" input in v1).
    3. ``remaining`` is the smallest headroom (``max(limit - current, 0)``)
       across ``TOKENS`` checks only — ``None`` if there are none — computed
       independently of whether the request is ultimately allowed or
       denied (a denial via the ``estimated_tokens`` overshoot rule can
       still report positive headroom).
    4. On denial, ``binding`` is the violated check with the lowest
       ``(metric_rank, scope_rank)`` — tokens before cost, workspace before
       agent before provider (INV-U6 precedence); ties (e.g. two violated
       periods on the same scope+metric) fall back to ``checks``' input
       order.
    """
    if not checks:
        return Evaluation(Decision(allowed=True), None)

    remaining = _remaining_tokens(checks)
    violated = [check for check in checks if _violates(check, estimated_tokens)]
    if not violated:
        return Evaluation(Decision(allowed=True, remaining=remaining), None)

    binding = min(violated, key=_rank)
    reason = (
        DenyReason.QUOTA_EXCEEDED if binding.metric is Metric.TOKENS else DenyReason.BUDGET_EXCEEDED
    )
    return Evaluation(Decision(allowed=False, reason=reason, remaining=remaining), binding)


def _violates(check: LimitCheck, estimated_tokens: int | None) -> bool:
    if check.metric is Metric.TOKENS:
        over_current = check.current >= check.limit_value
        over_estimate = (
            estimated_tokens is not None and check.current + estimated_tokens > check.limit_value
        )
        return over_current or over_estimate
    return check.current >= check.limit_value


def _rank(check: LimitCheck) -> tuple[int, int]:
    return (_METRIC_RANK[check.metric], _SCOPE_RANK[check.scope])


def _remaining_tokens(checks: Sequence[LimitCheck]) -> int | None:
    token_checks = [check for check in checks if check.metric is Metric.TOKENS]
    if not token_checks:
        return None
    return min(max(check.limit_value - check.current, 0) for check in token_checks)
