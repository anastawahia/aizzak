"""Live proof of capacity step 2.6's acceptance line — «`pg_sleep` اصطناعيٌّ
على مسار الطلب يُنهى بخطأٍ خلال 5s ويُحرَّر اتصالُه» — plus the three claims
around it that only a real server can settle.

WHAT THIS REPLACES. Before 2.6 the request path had no statement budget at
all: measured on the live stack, `SELECT pg_sleep(8)` returned after **8.17
seconds**, cancelled by nothing, having held one of the pooler's 25 `app_rw`
server connections (`ح-3`) for the whole of it. That is how a slow query in
one tenant becomes a stall for every tenant.

FOUR CLAIMS, and each one is a thing a unit test cannot reach:

1. **The budget fires, and the connection comes back.** A `pg_sleep` far past
   the budget ends as SQLSTATE `57014` inside it, and the SAME pool — sized to
   exactly one connection, so a leaked one would make the next checkout hang —
   serves the next query immediately.
2. **It is transaction-LOCAL.** The GUC is gone the moment the transaction
   ends, and an engine built WITHOUT the budget on the same server sees the
   server's own default. This is the whole reason the budget is issued as
   `set_config(..., is_local => true)` rather than as a connection-level `SET`:
   under PgBouncer's transaction pooling a non-local `SET` leaks onto a shared
   server connection, and it was measured doing exactly that — one client set
   `1234ms`, the next six unrelated clients read `1234ms` back.
3. **Idle inside a transaction is bounded too.** A transaction that opens and
   then holds its server connection doing nothing is `ح-3`'s other way to lose
   a slot, and `statement_timeout` says nothing about it (no statement is
   running).
4. **A background engine gets the looser budget, not the request one.** The
   value `_worker_db` installs is read back off the server, so
   `workers/bootstrap.py`'s constants are proven to ARRIVE rather than merely
   to exist.

`live_db` (never the pooler): these tests build their own engines on the
`app_rw` DSN rather than taking the `app_engine` fixture, because the fixture
is deliberately `NullPool` with no budget — inheriting it would test the
default this step exists to change. The `test_blocking_read_timeout_live.py`
precedent, on the Postgres side.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncEngine

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.workers.bootstrap import _BACKGROUND_STATEMENT_TIMEOUT_MS, _worker_db
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]

# The shipped request-path budget (`.env.example` / `docker-compose.yml`), not
# a number invented for the test: what is being proven is the deployment's own
# value, and `tests/unit/test_connection_timeouts.py` is what keeps the three
# copies of it equal.
_REQUEST_STATEMENT_TIMEOUT_MS = 5_000
# Deliberately far past it -- a sleep that could plausibly finish on its own
# would prove nothing about the budget.
_SLEEP_S = 30
# One connection, no overflow: a connection that the failed statement leaked
# would leave the pool empty, and the follow-up checkout would block until
# `pool_timeout` instead of answering. That is the "ويُحرَّر اتصالُه" half of
# the acceptance line, and it is why the pool is sized this way.
_ONE = {"pool_size": 1, "max_overflow": 0}


@asynccontextmanager
async def _engine(dsn: str, **kwargs: object) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(DatabaseSettings(url=dsn, **kwargs))  # type: ignore[arg-type]
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_a_runaway_statement_dies_inside_the_budget_and_frees_its_connection(
    live_db: LiveDbDsns,
) -> None:
    """The acceptance line itself."""
    async with _engine(
        live_db.app,
        statement_timeout_ms=_REQUEST_STATEMENT_TIMEOUT_MS,
        pool_timeout_s=2.0,
        **_ONE,
    ) as engine:
        started = time.perf_counter()
        with pytest.raises(DBAPIError) as caught:
            async with engine.begin() as conn:
                await conn.execute(text(f"SELECT pg_sleep({_SLEEP_S})"))
        elapsed = time.perf_counter() - started

        # `57014` (query_canceled) and not a connection drop: the statement was
        # cancelled by the server, which is what lets every adapter's
        # `_translate` (R6) see a database error rather than a socket one.
        assert getattr(caught.value.orig, "sqlstate", None) == "57014", caught.value
        budget_s = _REQUEST_STATEMENT_TIMEOUT_MS / 1000
        assert elapsed < budget_s + 1.5, f"took {elapsed:.2f}s for a {budget_s}s budget"
        assert elapsed >= budget_s - 0.5, (
            f"failed after only {elapsed:.2f}s -- something other than the budget "
            "ended this statement, and the test would pass for the wrong reason"
        )

        # The pool holds exactly one connection. If the cancelled statement had
        # kept it, this would block for `pool_timeout` and raise instead.
        async with engine.begin() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1


async def test_the_budget_is_transaction_local_and_leaks_to_nobody(
    live_db: LiveDbDsns,
) -> None:
    """Claim 2 -- the reason for `set_config(..., is_local => true)`.

    ⚠️ `SHOW statement_timeout` in a LATER transaction cannot prove this and
    the first draft of this test wrongly asked it to: the listener runs at
    every `begin`, so the value is `5s` there too and a leak would look
    identical to correct behaviour. `pg_settings.reset_val` is what actually
    distinguishes them -- it is the value the SESSION returns to when the
    transaction ends, so `setting = 5s` with `reset_val = 0` is precisely the
    statement "this budget dies with this transaction and is inherited by
    nobody".
    """
    async with _engine(
        live_db.app, statement_timeout_ms=_REQUEST_STATEMENT_TIMEOUT_MS, **_ONE
    ) as budgeted:
        async with budgeted.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT setting, reset_val FROM pg_settings "
                        "WHERE name = 'statement_timeout'"
                    )
                )
            ).one()
        # `pg_settings` reports the raw unit (milliseconds); `SHOW` renders
        # the same value as `5s`. Comparing against the constant keeps this
        # honest if the deployment's number ever moves.
        assert row.setting == str(_REQUEST_STATEMENT_TIMEOUT_MS), (
            "the budget never reached the server"
        )
        assert row.reset_val == "0", (
            "the budget outlives its transaction -- it was issued session-wide, "
            "which under PgBouncer's transaction pooling means one client's "
            "budget silently applies to another's queries"
        )

    # And an engine that never asked for a budget gets none, on the same
    # server -- no listener, and nothing inherited from the engine above.
    async with _engine(live_db.app, **_ONE) as bare, bare.begin() as conn:
        assert (await conn.execute(text("SHOW statement_timeout"))).scalar() == "0"


async def test_a_transaction_left_idle_loses_its_connection_too(
    live_db: LiveDbDsns,
) -> None:
    """Claim 3 -- `ح-3`'s other way to lose one of the 25 slots. A short
    budget rather than the shipped 10s, because what is under test is that the
    GUC arrives and bites, not how long the deployment chose to wait.

    ⚠️ AND IT DOES NOT ARRIVE AS A SQLSTATE, which is the difference between
    this bound and its neighbour. `statement_timeout` cancels a statement and
    the backend stays, so the client gets `57014` and every adapter's
    `_translate` (R6) can name it. `idle_in_transaction_session_timeout`
    TERMINATES THE SESSION: measured here, the follow-up statement raises
    asyncpg's `InterfaceError("connection is closed")` with `sqlstate = None`.
    The slot is still released -- which is the whole reason the bound exists --
    but an operator reading the log sees a dropped connection, not a named
    timeout. The first draft of this test asserted `25P03` and was wrong.
    """
    async with _engine(
        live_db.app,
        statement_timeout_ms=_REQUEST_STATEMENT_TIMEOUT_MS,
        idle_in_transaction_timeout_ms=1_000,
        **_ONE,
    ) as engine:
        with pytest.raises(DBAPIError) as caught:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
                await asyncio.sleep(2.5)
                await conn.execute(text("SELECT 1"))
        assert getattr(caught.value.orig, "sqlstate", None) is None, (
            "this bound now reports a SQLSTATE -- if the server stopped ending "
            "the session, say so here and let the adapters translate it"
        )
        assert "closed" in str(caught.value.orig)

        # The killed session did not take the pool with it: `pool_pre_ping`
        # discards the dead connection and the next checkout opens a new one.
        async with engine.begin() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1


async def test_a_background_engine_carries_the_looser_budget(
    live_db: LiveDbDsns,
) -> None:
    """Claim 4 -- `_worker_db`'s constants read back off the server. Proves
    they ARRIVE, which is the half a unit test cannot see."""
    worker_db = _worker_db(DatabaseSettings(url=live_db.app))
    async with (
        _engine(
            worker_db.url,
            pool_size=worker_db.pool_size,
            max_overflow=worker_db.max_overflow,
            statement_timeout_ms=worker_db.statement_timeout_ms,
            idle_in_transaction_timeout_ms=worker_db.idle_in_transaction_timeout_ms,
        ) as engine,
        engine.begin() as conn,
    ):
        seen = (await conn.execute(text("SHOW statement_timeout"))).scalar()
    assert seen == f"{_BACKGROUND_STATEMENT_TIMEOUT_MS // 1000}s"


async def test_a_checkout_that_cannot_be_served_fails_instead_of_waiting(
    live_db: LiveDbDsns,
) -> None:
    """`pool_timeout` -- the first of the five phases, and the only one that
    had a value before this step: SQLAlchemy's undeclared 30s, which is 120x
    the p95 write budget of `07 §2`. A caller that has waited that long is
    gone; failing is the honest answer (`ق-6`)."""
    async with (
        _engine(live_db.app, pool_timeout_s=1.0, **_ONE) as engine,
        engine.begin() as held,
    ):
        await held.execute(text("SELECT 1"))
        started = time.perf_counter()
        with pytest.raises(PoolTimeout):
            async with engine.begin() as second:
                await second.execute(text("SELECT 1"))
        elapsed = time.perf_counter() - started
    assert elapsed < 3.0, f"waited {elapsed:.2f}s for a 1s pool_timeout"
