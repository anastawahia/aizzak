"""Usage DTOs — the wire shapes of ``/api/v1/usage`` (03-api-spec §2, Phase
6.1-و-1).

Display and configuration only: 03 §1 is explicit that enforcement and
capture are INTERNAL inbound ports the orchestrator calls (FR-131/132,
INV-U4), so no DTO here can charge the ledger or move a counter.

``by_agent``/``by_provider`` keep the spec's ``list[dict[str, Any]]``
verbatim. Each entry is one ``DimensionUsage`` rendered as ``{"key": …,
"tokens": …, "cost_micros": …}`` — the router is the single place that
spelling exists, so the shape stays consistent across the two breakdowns.

**``UsageLimitIn`` vs the spec's ``limits: list[UsageLimitOut]``.** The spec
writes the PUT body as a list of the OUT model to say that a limit set
round-trips: what ``GET /usage/limits`` returns can be sent straight back.
That literal round-trip works here — every ``UsageLimitOut`` field is
accepted — but ``id`` is optional on the way IN and **ignored**, because
identity is the server's (DD-02) and ``SetLimits`` is whole-set replacement:
it mints a fresh UUIDv7 per row rather than patching the ids a client names.
Requiring an ``id`` would otherwise force a client to invent one for a limit
that has never existed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class UsageSummaryOut(BaseModel):
    workspace_id: str
    period: Literal["day", "month"]
    tokens: int
    cost_micros: int
    by_agent: list[dict[str, Any]]
    by_provider: list[dict[str, Any]]


class UsageLimitOut(BaseModel):
    id: str
    scope: Literal["workspace", "agent", "provider"]
    scope_key: str
    metric: Literal["tokens", "cost_micros"]
    period: Literal["day", "month"]
    limit_value: int


class UsageLimitIn(BaseModel):
    """One requested limit. ``id`` is accepted (so a ``GET`` body PUTs back
    unchanged) and ignored — see the module docstring."""

    id: str | None = None
    scope: Literal["workspace", "agent", "provider"]
    scope_key: str
    metric: Literal["tokens", "cost_micros"]
    period: Literal["day", "month"]
    limit_value: int


class UsageLimitsPutIn(BaseModel):
    limits: list[UsageLimitIn]
