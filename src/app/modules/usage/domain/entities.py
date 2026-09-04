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


@dataclass(frozen=True, slots=True)
class Reservation:
    """One request's in-flight admission (capacity-plan 2.7 — the
    ``reserve``/``commit`` extension `FR-132`/INV-U3 declared and 01 §2.10
    named a table for).

    It is NOT a ledger row and must never be mistaken for one: ``tokens`` is
    what the request was ADMITTED against, the ledger's is what it actually
    spent, and the two never live in the same row. A reservation's whole life
    is between the check and the charge — deleted by ``commit``/``release``,
    and ignored by every reader once ``expires_at`` has passed.

    ``expires_at`` is a backstop for the request that never comes back (a
    killed worker, a severed stream), not a policy: its value is the caller's
    own stream deadline, so a reservation older than it belongs to a request
    that is already over by construction.
    """

    id: str
    workspace_id: str
    agent_key: str
    provider: str
    tokens: int
    cost_micros: int
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.tokens < 0:
            raise InvalidUsageInput("tokens must be >= 0")
        if self.cost_micros < 0:
            raise InvalidUsageInput("cost_micros must be >= 0")
        if self.expires_at <= self.created_at:
            # An already-expired reservation is counted by nobody, so a caller
            # that built one would be admitted against a ceiling it never
            # actually took a slot in -- silently, and only under load.
            raise InvalidUsageInput("expires_at must be after created_at")


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
