"""Usage entities (pure — 06-domain-models §10).

``UsageRecord`` is the append-only ledger row (INV-U2: no ``version``/
``deleted_at`` — a mistaken record is corrected via an offsetting entry,
never an update, matching ``01-data-model`` §2.10's literal DDL). ``UsageLimit``
is the mutable aggregate root for one configured limit; its ``rule`` property
projects it onto the pure ``LimitRule`` value object that
``domain.enforcement.evaluate`` actually reasons over, keeping the AR's
identity/bookkeeping columns (``id``, ``created_at``, ``updated_at``,
``version``) out of the pure evaluation path.

Deviation from the literal ``UsageLedgerRepository.append(ctx, charge:
UsageCharge) -> bool`` port contract (02-port-contracts §2, coordinator
decision): that signature carries no ``id``/``created_at``, so a
``UsageRecord``'s ``id``/``created_at`` are minted by the (deferred) SQL
adapter at insert time, not by the application layer — unlike every other
aggregate in this codebase, whose application layer mints identity before
calling ``add``/``save``. ``CaptureUsage`` therefore never constructs a
``UsageRecord`` itself; the type exists here as the pure, testable shape of
one ledger row (06 §10 AR), for the adapter (and any future direct domain
use) to build.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.usage.domain.errors import InvalidUsageInput
from app.modules.usage.domain.value_objects import LimitRule, LimitScope, Metric, Period


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One append-only usage ledger entry (06 §10 AR ``UsageRecord``,
    INV-U2). No ``version``/``deleted_at`` — see the module docstring."""

    id: str
    workspace_id: str
    agent_key: str
    provider: str
    tokens: int
    cost_micros: int
    operation_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.tokens < 0:
            raise InvalidUsageInput("tokens must be >= 0")
        if self.cost_micros < 0:
            raise InvalidUsageInput("cost_micros must be >= 0")


@dataclass(slots=True)
class UsageLimit:
    """A configured quota/budget limit (06 §10 AR ``UsageLimit``).

    ``SetLimits`` (application layer) always rebuilds this aggregate from
    scratch (new ``id``, ``version=1``) rather than patching an existing
    row — whole-set replacement semantics, so there is no in-place mutation
    method here.
    """

    id: str
    workspace_id: str
    scope: LimitScope
    scope_key: str
    metric: Metric
    period: Period
    limit_value: int
    created_at: datetime
    updated_at: datetime
    version: int

    def __post_init__(self) -> None:
        # Delegates the scope/scope_key/limit_value invariant to LimitRule
        # (single source of truth for that shape check) instead of
        # duplicating it here.
        LimitRule(self.scope, self.scope_key, self.metric, self.period, self.limit_value)

    @property
    def rule(self) -> LimitRule:
        """Project this aggregate onto the pure ``LimitRule`` that
        ``domain.enforcement.evaluate`` reasons over (identity/bookkeeping
        columns dropped)."""
        return LimitRule(self.scope, self.scope_key, self.metric, self.period, self.limit_value)
