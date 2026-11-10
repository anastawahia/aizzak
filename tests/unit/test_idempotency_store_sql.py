"""The SQL ``SqlIdempotencyStore`` runs (gap th-3), no database.

A recording session stands in for ``tenant_session``; the statements it is
handed are compiled with the asyncpg dialect (the app driver). Live behaviour
(the DB clock, the primary-key race) is in
``tests/integration/test_idempotency_live.py`` (CI only).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.exc import IntegrityError

from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.idempotency_store import ClaimOutcome
from app.infrastructure.persistence.idempotency import SqlIdempotencyStore

_PG = asyncpg.dialect()
_WS = "0190cccc-0000-7000-8000-000000000001"
_EP = "POST /conversations/c/messages"


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_WS, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


class _Orig(Exception):
    sqlstate = "23505"


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row


class _Nested:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Session:
    def __init__(self, sink: list[Any], row: dict[str, Any] | None, conflict: bool) -> None:
        self.sink = sink
        self.row = row
        self.conflict = conflict

    def begin_nested(self) -> _Nested:
        return _Nested(self)

    async def execute(self, statement: Any) -> _Result:
        self.sink.append(statement)
        if self.conflict and len(self.sink) == 1:
            raise IntegrityError("insert", {}, _Orig())
        return _Result(self.row)


def _store(
    sink: list[Any], row: dict[str, Any] | None = None, *, conflict: bool = False
) -> SqlIdempotencyStore:
    @asynccontextmanager
    async def tenant_session(_ctx: ExecutionContext) -> AsyncIterator[_Session]:
        yield _Session(sink, row, conflict)

    return SqlIdempotencyStore(tenant_session)  # type: ignore[arg-type]


def _sql(statement: Any) -> str:
    return " ".join(str(statement.compile(dialect=_PG)).split())


async def test_checkpoint_is_one_update_that_leaves_completed_at_alone() -> None:
    sink: list[Any] = []

    await _store(sink).checkpoint(_ctx(), endpoint=_EP, key="k", response_body={"outcome": "x"})

    assert len(sink) == 1
    sql = _sql(sink[0])
    assert sql.startswith("UPDATE platform.idempotency_keys SET response_body=")
    assert "completed_at=" not in sql.replace("completed_at IS NULL", "")
    assert "completed_at IS NULL" in sql
    assert "workspace_id" in sql


async def test_complete_is_one_update_that_sets_completed_at() -> None:
    sink: list[Any] = []

    await _store(sink).complete(_ctx(), endpoint=_EP, key="k", response_body={"outcome": "x"})

    assert len(sink) == 1
    assert "completed_at=now()" in _sql(sink[0])


async def test_release_only_deletes_a_claim_with_no_body_and_no_completion() -> None:
    sink: list[Any] = []

    await _store(sink).release(_ctx(), endpoint=_EP, key="k")

    assert len(sink) == 1
    sql = _sql(sink[0])
    assert sql.startswith("DELETE FROM platform.idempotency_keys")
    assert "completed_at IS NULL" in sql
    assert "response_body IS NULL" in sql


async def test_inspect_selects_the_age_on_the_database_clock() -> None:
    sink: list[Any] = []
    row = {
        "request_hash": "h",
        "response_body": {"outcome": "accepted"},
        "completed_at": None,
        "age_s": 12,
    }

    claim = await _store(sink, row, conflict=True).claim(
        _ctx(), endpoint=_EP, key="k", request_hash="h"
    )

    assert len(sink) == 2  # the INSERT that conflicted, then ONE follow-up SELECT
    select_sql = _sql(sink[1])
    assert "EXTRACT(epoch FROM now() - platform.idempotency_keys.created_at) AS age_s" in select_sql
    assert claim.outcome is ClaimOutcome.IN_PROGRESS
    assert claim.response_body == {"outcome": "accepted"}
    assert claim.age_s == 12.0


async def test_a_completed_row_replays_and_a_foreign_hash_mismatches() -> None:
    done = {"request_hash": "h", "response_body": {"a": 1}, "completed_at": "t", "age_s": 5}

    replay = await _store([], done, conflict=True).claim(
        _ctx(), endpoint=_EP, key="k", request_hash="h"
    )
    mismatch = await _store([], done, conflict=True).claim(
        _ctx(), endpoint=_EP, key="k", request_hash="other"
    )

    assert replay.outcome is ClaimOutcome.REPLAY
    assert replay.response_body == {"a": 1}
    assert replay.age_s is None
    assert mismatch.outcome is ClaimOutcome.MISMATCH
    assert mismatch.response_body is None


async def test_a_vanished_row_is_in_progress_without_an_age() -> None:
    claim = await _store([], None, conflict=True).claim(
        _ctx(), endpoint=_EP, key="k", request_hash="h"
    )

    assert claim.outcome is ClaimOutcome.IN_PROGRESS
    assert claim.age_s is None
    assert claim.response_body is None
