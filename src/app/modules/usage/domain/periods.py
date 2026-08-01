"""Pure period/rollup-bucket helpers (06-domain-models §10, 01-data-model
§2.10). No I/O, no framework imports.

``period_start`` and ``rollup_buckets`` are exactly the two computations the
SQL adapter's ``append`` MUST perform per its binding docstring
(``ports/repository.py``); they live in the domain because they are pure and
module-authored. ``period_reset_at`` (3.79) is the one helper the application
slice DOES call directly: it turns a denial's binding period into the
``Retry-After`` hint the inbound port now carries, and it belongs beside
``period_start`` because it is the same calendar rule read forwards.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.modules.usage.domain.value_objects import Period

_WILDCARD = "*"
_DECEMBER = 12


def period_start(period: Period, now: datetime) -> date:
    """The rollup-bucket start date for ``period`` at ``now``, normalized to
    UTC first (DD-03): ``MONTH`` -> the 1st of ``now``'s UTC month; ``DAY``
    -> ``now``'s UTC calendar date."""
    normalized = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    if period is Period.MONTH:
        return date(normalized.year, normalized.month, 1)
    return normalized.date()


def period_reset_at(period: Period, now: datetime) -> datetime:
    """The UTC instant ``period``'s bucket rolls over — i.e. the first moment
    a workspace denied on that bucket has fresh headroom (3.79).

    This is the honest producer 03 §4 said ``Retry-After`` was missing: the
    header was withheld in v1 precisely because ``LimitDecision`` carried no
    reset time and any value would have been invented. There is nothing to
    invent here — a rollup bucket is keyed by ``period_start``, so its end is
    the NEXT bucket's start, computed from the same calendar rule.

    ``MONTH`` rolls at 00:00 UTC on the 1st of the following month; ``DAY`` at
    the next UTC midnight. Month arithmetic is done on ``(year, month)``
    integers rather than by adding 31 days, which would skip February
    entirely and land a January denial in March.
    """
    start = period_start(period, now)
    if period is Period.MONTH:
        rolls_over = start.month == _DECEMBER
        year, month = (start.year + 1, 1) if rolls_over else (start.year, start.month + 1)
        return datetime(year, month, 1, tzinfo=UTC)
    following = start + timedelta(days=1)
    return datetime(following.year, following.month, following.day, tzinfo=UTC)


def rollup_buckets(agent: str, provider: str) -> tuple[tuple[str, str], ...]:
    """The (up to 3) rollup dimension keys a charge against ``(agent,
    provider)`` updates: workspace-wide (``*``, ``*``), per-agent (``agent``,
    ``*``), per-provider (``*``, ``provider``) — order-preserving,
    de-duplicated. Critical when ``agent``/``provider`` is itself ``'*'``:
    the workspace-wide bucket must never be double-counted."""
    candidates = ((_WILDCARD, _WILDCARD), (agent, _WILDCARD), (_WILDCARD, provider))
    buckets: list[tuple[str, str]] = []
    for bucket in candidates:
        if bucket not in buckets:
            buckets.append(bucket)
    return tuple(buckets)
