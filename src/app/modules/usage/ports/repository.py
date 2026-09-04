"""Usage persistence port (02-port-contracts §2 pattern + 06-domain-models
§10 use-cases ``SetLimits``/``GetUsageSummary``, which need two methods
beyond the three shown in 02 §2's illustrative fragment — ``replace_limits``
and ``list_rollups``; 02 §2's closing note says this port "repeats the same
pattern" as every other module's, not that its fragment is exhaustive).

Every method takes ``ExecutionContext`` first so the SQL adapter can apply
the RLS guard (``SET LOCAL app.workspace_id``) and the ``WHERE
workspace_id`` filter (DD-04) — the files/knowledge/media port precedent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.modules.usage.domain.entities import Reservation, UsageLimit
from app.modules.usage.domain.read_models import UsageRollup
from app.modules.usage.ports.inbound import UsageCharge


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """Aggregated totals for one rollup bucket + period (``rollup``'s return
    shape) — deliberately not the same shape as ``read_models.UsageRollup``:
    no ``workspace_id``/``agent_key``/``provider``/``period_start``/
    ``updated_at`` identity, just the two sums ``enforcement.LimitCheck
    .current`` needs."""

    tokens: int
    cost_micros: int


class UsageLedgerRepository(Protocol):
    """Tenant-scoped persistence for the append-only usage ledger + its
    rollups + configured limits (02 §2, INV-U1)."""

    async def append(self, ctx: ExecutionContext, charge: UsageCharge) -> bool:
        """Append one usage charge, idempotently, in a single transaction
        (INV-U1):

        1. ``INSERT INTO usage.usage_records (...) VALUES (...) ON CONFLICT
           (workspace_id, operation_id) DO NOTHING`` — the adapter mints
           ``id`` (UUIDv7) and ``created_at`` itself; neither is a field on
           ``UsageCharge`` (an approved, documented deviation from every
           other module's ``add``, which receives an already-identified
           aggregate — see ``domain.entities.UsageRecord``'s docstring).
        2. Only if a row was actually inserted: upsert
           ``domain.periods.rollup_buckets(charge.agent, charge.provider)``
           x each configured rollup period, keyed by
           ``domain.periods.period_start(period, now)``, via ``ON CONFLICT
           (workspace_id, agent_key, provider, period, period_start) DO
           UPDATE SET tokens_sum = usage_rollups.tokens_sum +
           EXCLUDED.tokens_sum, cost_micros_sum =
           usage_rollups.cost_micros_sum + EXCLUDED.cost_micros_sum``.
        3. Returns ``True`` iff step 1 inserted a row; ``False`` on a
           ``UNIQUE(workspace_id, operation_id)`` conflict, in which case
           rollups are left untouched (INV-U1: a duplicate capture never
           double-counts).
        """
        ...

    async def rollup(
        self, ctx: ExecutionContext, agent: str, provider: str, period: str
    ) -> UsageTotals:
        """Read the current totals for one ``(agent, provider, period)``
        bucket as of "now" (``domain.periods.period_start``) — ``UsageTotals
        (0, 0)`` if no rollup row exists yet for this bucket."""
        ...

    async def reserved(self, ctx: ExecutionContext, agent: str, provider: str) -> UsageTotals:
        """The totals still RESERVED but not yet charged for one rollup
        bucket — capacity-plan 2.7's other half of ``current``.

        A live reservation contributes to exactly the buckets
        ``domain.periods.rollup_buckets`` fans its ``(agent, provider)`` out
        to, so ``('*','*')`` sums every live row, ``(agent,'*')`` sums that
        agent's and ``('*',provider)`` that provider's.

        Deliberately NOT keyed by period, unlike ``rollup``: a reservation is
        in flight NOW, so it belongs to every period bucket that contains now
        — the day's and the month's alike — and asking for one would mean
        answering the same question twice with the same rows.

        Rows whose ``expires_at`` has passed are excluded, so an abandoned
        reservation stops costing a tenant headroom the moment it expires,
        whether or not anything has deleted it yet.
        """
        ...

    async def reserve(self, ctx: ExecutionContext, reservation: Reservation) -> None:
        """Insert one reservation. Called ONLY inside a unit of work that
        already holds the workspace's quota lock and has just read the totals
        above — the insert is the second half of an atomic admission, and on
        its own it decides nothing."""
        ...

    async def release(self, ctx: ExecutionContext, reservation_id: str) -> None:
        """Drop a reservation, by id. Silent when the row is not there: it may
        have expired and been swept, and a request that is trying to give its
        slot back must never fail for having been beaten to it."""
        ...

    async def get_limits(self, ctx: ExecutionContext) -> list[UsageLimit]:
        """All configured limits for this workspace (any scope)."""
        ...

    async def replace_limits(self, ctx: ExecutionContext, limits: Sequence[UsageLimit]) -> None:
        """Replace this workspace's ENTIRE configured limit set with
        ``limits`` in one transaction — whole-set replacement semantics
        (``SetLimits``'s contract), not a per-row upsert: a limit configured
        previously but absent from ``limits`` is deleted."""
        ...

    async def list_rollups(self, ctx: ExecutionContext, period: str) -> Sequence[UsageRollup]:
        """Every rollup row for this workspace + ``period`` (all buckets:
        workspace-wide, per-agent, per-provider) — ``GetUsageSummary``'s
        source data."""
        ...
