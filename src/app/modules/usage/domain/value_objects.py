"""Usage value objects (pure — 06-domain-models §10).

Frozen, self-validating primitives for quota/budget enforcement. Enum values
mirror the closed vocabularies in 01-data-model §2.10 (CHECK constraints) and
02-port-contracts §2 / 03-api-spec §4 (``LimitDecision.reason`` string
values) verbatim, so a persisted row / port-level ``reason`` string
round-trips through these enums without translation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.usage.domain.errors import InvalidUsageInput

_WILDCARD = "*"


class LimitScope(StrEnum):
    """What a ``UsageLimit``/``LimitRule`` governs (01 §2.10 ``scope`` CHECK)."""

    WORKSPACE = "workspace"
    AGENT = "agent"
    PROVIDER = "provider"


class Metric(StrEnum):
    """What is being metered (01 §2.10 ``metric`` CHECK)."""

    TOKENS = "tokens"
    COST_MICROS = "cost_micros"


class Period(StrEnum):
    """The rollup/limit window (01 §2.10 ``period`` CHECK)."""

    DAY = "day"
    MONTH = "month"


class DenyReason(StrEnum):
    """Why ``enforcement.evaluate`` denied a request (02-port-contracts §2
    ``LimitDecision.reason`` / 03-api-spec §4, verbatim string values)."""

    QUOTA_EXCEEDED = "quota_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True, slots=True)
class LimitRule:
    """A single configured (or default) limit, decoupled from the
    ``UsageLimit`` aggregate's identity/bookkeeping columns — the shape that
    ``enforcement.evaluate`` actually reasons about (via ``LimitCheck``).

    ``__post_init__`` enforces the scope/scope_key coupling (01 §2.10):
    ``WORKSPACE`` limits always apply tenant-wide (``scope_key == '*'``);
    ``AGENT``/``PROVIDER`` limits must name a specific, non-wildcard key.
    """

    scope: LimitScope
    scope_key: str
    metric: Metric
    period: Period
    limit_value: int

    def __post_init__(self) -> None:
        if self.scope is LimitScope.WORKSPACE:
            if self.scope_key != _WILDCARD:
                raise InvalidUsageInput("workspace-scoped limits must use scope_key='*'")
        elif not self.scope_key.strip() or self.scope_key == _WILDCARD:
            raise InvalidUsageInput(
                f"{self.scope.value}-scoped limits require a specific, non-wildcard scope_key"
            )
        if self.limit_value < 0:
            raise InvalidUsageInput("limit_value must be >= 0")


@dataclass(frozen=True, slots=True)
class Decision:
    """The enforcement outcome (06 §10 VO ``Decision``) — an object, never a
    bare ``bool`` (INV-U3). ``reservation_id`` stays ``None`` in v1: a
    forward-compatible field for a future reserve/commit flow (FR-132),
    never populated by ``enforcement.evaluate``.

    ``retry_after_s`` (3.79) is the seconds until the BINDING limit's period
    rolls over — the honest ``Retry-After`` value 03 §4 recorded as missing.
    ``evaluate`` cannot fill it and deliberately does not try: it is
    clockless by design, and "how long until midnight" is not a question a
    pure comparison of counters against caps can answer. ``EnforceLimit``
    fills it from ``periods.period_reset_at`` and the same ``now`` it already
    stamps ``LimitExceeded`` with, so the number is consistent with the event
    emitted for the very same denial.
    """

    allowed: bool
    reason: DenyReason | None = None
    remaining: int | None = None
    reservation_id: str | None = None
    retry_after_s: int | None = None
