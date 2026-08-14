"""The platform directory's search filter, read off the emitted SQL.

The adapter is exercised through a session that records statements instead of
running them: what matters here is which predicate reaches which statement, and
that is decided before PostgreSQL ever sees it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects import postgresql

from app.modules.admin.adapters.sql_directory import SqlPlatformUserDirectory

_ROW = {
    "id": "018f0000-0000-7000-8000-000000000001",
    "workspace_id": "018f0000-0000-7000-8000-0000000000f1",
    "email": "someone@example.com",
    "display_name": None,
    "status": "active",
    "last_seen_at": None,
    "online": False,
    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    "roles": ["member"],
}
_TOTALS = {"total": 1, "active": 1, "disabled": 0, "online": 0}


class _RecordingSession:
    """Capture every statement and answer with one fixed row."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return _Result()


class _Result:
    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return [_ROW]

    def one(self) -> dict[str, Any]:
        return _TOTALS


def _directory() -> tuple[SqlPlatformUserDirectory, _RecordingSession]:
    session = _RecordingSession()

    @asynccontextmanager
    async def provider() -> Any:
        yield session

    return SqlPlatformUserDirectory(provider), session


def _params(statement: Any) -> list[Any]:
    return list(statement.compile(dialect=postgresql.dialect()).params.values())


async def test_the_search_predicate_reaches_the_page_and_the_totals() -> None:
    directory, session = _directory()

    await directory.list_users(limit=20, offset=0, search="ali")

    page_sql, totals_sql = (
        str(s.compile(dialect=postgresql.dialect())) for s in session.statements
    )
    # Counting the unfiltered platform beside a filtered page would advertise a
    # next offset for rows the caller can never be served.
    assert "ILIKE" in page_sql.upper()
    assert "ILIKE" in totals_sql.upper()
    assert "%ali%" in _params(session.statements[0])
    assert "%ali%" in _params(session.statements[1])


async def test_wildcards_typed_by_the_operator_are_matched_literally() -> None:
    directory, session = _directory()

    await directory.list_users(limit=20, offset=0, search="100%_a\\b")

    # A stray % would otherwise return the whole platform under a search that
    # reads, to the operator, like a narrow one.
    assert "%100\\%\\_a\\\\b%" in _params(session.statements[0])


async def test_no_search_leaves_both_statements_unfiltered() -> None:
    directory, session = _directory()

    page = await directory.list_users(limit=20, offset=0)

    assert page.total == 1
    for statement in session.statements:
        assert "ILIKE" not in str(statement.compile(dialect=postgresql.dialect())).upper()


async def test_tombstones_are_absent_from_the_page_and_from_its_counts() -> None:
    directory, session = _directory()

    await directory.list_users(limit=20, offset=0)

    # A deleted account has no identity fields left to list, and counting it
    # would inflate the total the operator pages through.
    for statement in session.statements:
        assert "deleted" in _params(statement)
