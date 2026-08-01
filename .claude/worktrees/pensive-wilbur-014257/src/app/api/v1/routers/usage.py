"""The Usage router — ``/api/v1/usage`` (03-api-spec §1 · FR-130…134) — Phase
6.1-و-1.

Three routes over ``UsageUseCases``: the period summary, and the configured
limit set (read + whole-set replace).

**Display and configuration only, by contract.** 03 §1 states it outright and
INV-U4 enforces it structurally: enforcement (before an operation) and
capture (after) are INTERNAL inbound ports the ORCHESTRATOR calls
(FR-131/132) — they are not in the bundle this router holds, so no request
can charge the ledger, clear a counter, or ask for a decision. The numbers
here are read out of the same rollups enforcement reads, never written
through this door.

**``GET /usage/limits`` answers what the workspace CONFIGURED**, not what
will be enforced. When a ``(metric, period)`` has no configured row,
enforcement falls back to the platform guardrail (``UsageSettings
.default_limits``) — see ``ListLimits``'s docstring for why folding those
into this response would be actively harmful rather than merely verbose.

**``PUT`` replaces the whole set** (``SetLimits``'s semantics): a limit that
was configured before and is absent from the body is DELETED, so a client
must send the complete set it wants, not a delta. The response is the SAVED
set — freshly minted ids and all — which is why a plain re-``PUT`` of a
``GET`` body is safe and never accumulates rows. A duplicate
scope/scope_key/metric/period key inside one body is a ``ConflictError``/409
rather than a last-one-wins silent merge.

Both limit routes wear the API-04 envelope with ``next_cursor: null`` — a
bounded, unpaginated collection, exactly as 03 §2 lists ``getUsageLimits``/
``putUsageLimits`` among the wrapped-but-unpaginated endpoints.

**Auth on every route** via the router-level ``current_principal`` dependency
(03 §0); the owner/admin RBAC guard ``PUT /limits`` deserves is 6.4's.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.pagination import Page, PageMeta
from app.api.v1.dto.usage import UsageLimitOut, UsageLimitsPutIn, UsageSummaryOut
from app.modules.access.domain.value_objects import Permission
from app.modules.usage.application.use_cases import LimitSpec
from app.modules.usage.domain.entities import UsageLimit
from app.modules.usage.domain.read_models import DimensionUsage, UsageSummary
from app.modules.usage.domain.value_objects import LimitScope, Metric, Period

# Belt and braces, knowingly (the §3.56 precedent): every route builds `ctx`.
router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(current_principal)])

# The default window a summary covers when the caller names none. `month` is
# the period 07-nfr-slo §4's guardrails are written in, so an unqualified
# "how much have I used?" answers in the same units the quota is set in.
_DEFAULT_PERIOD: Literal["day", "month"] = "month"


def _to_dimension(dimension: DimensionUsage) -> dict[str, object]:
    """The one place a breakdown entry's key spelling lives (03 §2 types
    these as open ``dict``s)."""
    return {
        "key": dimension.key,
        "tokens": dimension.tokens,
        "cost_micros": dimension.cost_micros,
    }


def _to_limit_out(limit: UsageLimit) -> UsageLimitOut:
    return UsageLimitOut(
        id=limit.id,
        scope=limit.scope.value,
        scope_key=limit.scope_key,
        metric=limit.metric.value,
        period=limit.period.value,
        limit_value=limit.limit_value,
    )


def _to_summary_out(workspace_id: str, summary: UsageSummary) -> UsageSummaryOut:
    return UsageSummaryOut(
        workspace_id=workspace_id,
        period=summary.period.value,
        tokens=summary.tokens,
        cost_micros=summary.cost_micros,
        by_agent=[_to_dimension(row) for row in summary.by_agent],
        by_provider=[_to_dimension(row) for row in summary.by_provider],
    )


def _limits_page(limits: tuple[UsageLimit, ...]) -> Page[UsageLimitOut]:
    data = [_to_limit_out(limit) for limit in limits]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.get("", dependencies=[Depends(require(Permission.USAGE_READ))])
async def get_usage_summary(
    services: Services,
    ctx: Context,
    period: Annotated[Literal["day", "month"], Query()] = _DEFAULT_PERIOD,
) -> UsageSummaryOut:
    """Totals for the current window plus the per-agent/per-provider
    breakdown. A single resource — returned bare, envelope-free — even though
    it CONTAINS two lists: the breakdowns are fields of one summary, not
    collections the caller pages through.

    ``workspace_id`` is echoed from ``ctx``, never read from a rollup row: the
    summary is by construction the caller's own tenant's.
    """
    summary = await services.usage.summary.execute(ctx, period=period)
    return _to_summary_out(ctx.workspace_id, summary)


@router.get("/limits", dependencies=[Depends(require(Permission.USAGE_READ))])
async def get_usage_limits(services: Services, ctx: Context) -> Page[UsageLimitOut]:
    """The configured set, as stored. An empty page means "nothing
    configured" — which is NOT "no limits": the platform guardrail still
    applies (see the module docstring)."""
    return _limits_page(await services.usage.list_limits.execute(ctx))


@router.put("/limits", dependencies=[Depends(require(Permission.USAGE_MANAGE))])
async def put_usage_limits(
    body: UsageLimitsPutIn, services: Services, ctx: Context
) -> Page[UsageLimitOut]:
    """Replace the configured set wholesale and answer with what was saved.

    The DTO's ``Literal``s have already narrowed every enum-valued field, so
    these three constructions cannot fail; the coupling rules that CAN fail
    (a workspace-scoped limit must use ``scope_key='*'``, an agent/provider
    one must not — and ``limit_value >= 0``) belong to ``LimitRule`` and
    surface from the use-case as 422. An incoming ``id`` is dropped here,
    deliberately: identity is the server's (see the DTO module).
    """
    specs = [
        LimitSpec(
            scope=LimitScope(item.scope),
            scope_key=item.scope_key,
            metric=Metric(item.metric),
            period=Period(item.period),
            limit_value=item.limit_value,
        )
        for item in body.limits
    ]
    return _limits_page(await services.usage.set_limits.execute(ctx, limits=specs))
