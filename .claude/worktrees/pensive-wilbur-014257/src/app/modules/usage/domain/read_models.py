"""Usage read-models (pure — 06-domain-models §10 RM ``UsageRollup``).

Both ``UsageRollup`` and ``UsageSummary``/``DimensionUsage`` are derived
projections with no independent identity (``RM``, not ``AR``/``E``):
``UsageRollup`` mirrors one row of ``usage.usage_rollups`` (01-data-model
§2.10, composite-keyed — no surrogate ``id``); ``UsageSummary`` is assembled
in-memory by ``GetUsageSummary`` from a set of ``UsageRollup`` rows and is
never itself persisted.

Deviation from the abbreviated inline task-spec field list (coordinator
resolution: canonical ``docs/design`` wins over the inline summary on
conflict) — ``UsageRollup`` includes ``updated_at``, present in both
01-data-model §2.10's DDL and 06-domain-models §10's RM definition but
omitted from the inline field list. No behaviour in this slice depends on
it, but dropping a column documented in two canonical design docs would
silently narrow this read-model's fidelity to the real ``usage.usage_rollups``
row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.modules.usage.domain.value_objects import Period


@dataclass(frozen=True, slots=True)
class UsageRollup:
    """One ``usage.usage_rollups`` row (01 §2.10; composite PK
    ``(workspace_id, agent_key, provider, period, period_start)`` — no
    surrogate ``id``, hence ``RM`` not ``AR``)."""

    workspace_id: str
    agent_key: str
    provider: str
    period: Period
    period_start: date
    tokens_sum: int
    cost_micros_sum: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DimensionUsage:
    """One dimension's totals within a ``UsageSummary`` (e.g. one agent's or
    one provider's tokens/cost for the period)."""

    key: str
    tokens: int
    cost_micros: int


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """``GetUsageSummary``'s result: workspace-wide totals for ``period``
    plus the per-agent/per-provider breakdown (06 §10 use-case
    ``GetUsageSummary``)."""

    period: Period
    tokens: int
    cost_micros: int
    by_agent: tuple[DimensionUsage, ...]
    by_provider: tuple[DimensionUsage, ...]
