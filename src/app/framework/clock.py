"""Time source. All timestamps are timezone-aware UTC (DD-03, DAT-06)."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(UTC)
