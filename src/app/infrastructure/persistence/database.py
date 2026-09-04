"""Async SQLAlchemy engine/sessionmaker factories (OPS-02).

The engine/sessionmaker themselves are infrastructure (this module lives
under ``app.infrastructure`` and is wired exclusively by the Composition
Root, per import-linter contract 6); RLS machinery (``rls.py``) and module
SQL adapters receive an already-built ``async_sessionmaker`` rather than
importing this module directly.

``statement_cache_size``/``prepared_statement_cache_size`` are pinned to
``0`` on *every* engine (OPS-02): the app talks to Postgres through PgBouncer
in transaction-pooling mode, which is incompatible with asyncpg's client-side
statement caching / named prepared statements.

**Capacity step 2.6 — the per-transaction budget, and why it is issued the
way it is.** ``pool_pre_ping`` was the only timeout-shaped thing here; a
statement that hung hung forever, holding one of the pooler's 25 ``app_rw``
server slots (``ح-3``) for as long as it liked. Measured on the live stack
before the fix: ``SELECT pg_sleep(8)`` on the request path returned after
**8.17 s**, having been cancelled by nothing.

⚠️ **THE OBVIOUS IMPLEMENTATION IS THE DANGEROUS ONE.** Setting the budget
once per connection -- a plain ``SET statement_timeout``, or asyncpg's
``server_settings`` -- leaks it onto a SHARED server connection under
transaction pooling: PgBouncer only runs ``server_reset_query`` in session
mode, so whatever the last client set stays. Measured on the live stack: one
client issued ``SET statement_timeout = '1234ms'``, and the next **six**
unrelated clients read back ``1234ms``. That is one tenant's budget silently
applied to another's queries, and it is the same family of hazard as the
``statement_cache_size`` note above. The only correct mechanism here is a
TRANSACTION-LOCAL one, so the budget is issued as ``set_config(..., is_local
=> true)`` from a Core ``begin`` listener -- inside the transaction, gone at
commit, and never inherited by the next client on that server connection.

It is one listener on the ENGINE rather than a line in each of ``rls.py``'s
five session factories, because the property worth having is that a
transaction opened by code written later cannot forget it: the ``begin``
event covers every ORM session AND every ``engine.begin()`` a Core caller
opens. The price is one extra round trip per transaction, measured against
the live pooler at **+0.94 ms p50** (1.75 -> 2.69 ms for an empty
transaction) -- 0.6% of the 150 ms p95 read budget of ``07 §2``. If that ever
stops being cheap, folding the two ``set_config`` calls into ``rls.py``'s
existing ``set_tenant`` statement recovers all of it for the tenant paths and
leaves the relay's sessions to a listener; it is not done today because two
mechanisms for one guarantee is how a guarantee acquires a hole.

**What this does NOT bound, and where that bound lives instead.** The wait
for a PgBouncer *server slot* happens before any backend has seen the query,
so ``statement_timeout`` cannot cover it: with all 25 ``app_rw`` slots busy,
a 26th client carrying a 5 s statement budget was measured waiting **7.85 s**
and then SUCCEEDING. That phase is bounded by the pooler's own
``query_wait_timeout`` (``docker-compose.yml``, and 120 s by default -- see
that service's comment). asyncpg's ``command_timeout`` would bound it
per-path instead, and was rejected after measuring what it raises: a bare,
message-less ``asyncio.TimeoutError`` that is NOT a ``DBAPIError``, so it
walks straight through every adapter's ``except DBAPIError`` translation
(R6) and out of the domain as an untranslated driver exception.
``query_wait_timeout`` fires as a ``DBAPIError`` (asyncpg
``ConnectionDoesNotExistError``, measured at 3.31 s against a deliberately
lowered ceiling), which the adapters already handle.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, Pool

from app.framework.settings.settings import DatabaseSettings

_CONNECT_ARGS = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}


def _budget_sql(db: DatabaseSettings) -> str | None:
    """The ONE statement that installs this path's transaction budget, or
    ``None`` when the path asked for neither half (``app.ops.*``, and every
    ``DatabaseSettings`` built without the 2.6 fields).

    Both GUCs are set ``is_local => true`` -- see this module's docstring for
    the measured leak that rules out every non-local form -- and both are
    formatted from ``int``\\ s that Pydantic has already validated, so there
    is no interpolation surface here. They ride in a SINGLE ``SELECT`` because
    two ``set_config`` calls in one statement cost one round trip and two
    statements cost two; asyncpg refuses multi-statement text outright.
    """
    statement_ms = int(db.statement_timeout_ms)
    idle_ms = int(db.idle_in_transaction_timeout_ms)
    if statement_ms <= 0 and idle_ms <= 0:
        return None
    return (
        f"SELECT set_config('statement_timeout', '{statement_ms}', true), "
        f"set_config('idle_in_transaction_session_timeout', '{idle_ms}', true)"
    )


def _install_transaction_budget(engine: AsyncEngine, db: DatabaseSettings) -> None:
    """Register the ``begin`` listener that issues ``sql`` as the first
    statement of every transaction on ``engine``.

    Listens on ``engine.sync_engine``: the async engine is a facade, and Core
    events are emitted by the sync one underneath it. The handler runs inside
    the greenlet the async driver is already executing in, which is what makes
    a synchronous ``exec_driver_sql`` here legal rather than a deadlock.
    """
    sql = _budget_sql(db)
    if sql is None:
        return

    @event.listens_for(engine.sync_engine, "begin")
    def _set_transaction_budget(conn: Connection) -> None:
        conn.exec_driver_sql(sql)


def create_engine(db: DatabaseSettings, *, poolclass: type[Pool] | None = None) -> AsyncEngine:
    """Build the async engine for ``db`` (OPS-02 connect args on every engine).

    ``poolclass=None`` (the default, used by the app at runtime) pools
    connections via ``AsyncAdaptedQueuePool``, sized from
    ``db.pool_size``/``db.max_overflow`` with health-checked checkouts
    (``pool_pre_ping``) and the two 2.6 pool bounds -- ``pool_timeout``
    (how long a checkout may queue) and ``pool_recycle`` (how long a
    connection may live). Tests instead inject ``poolclass=NullPool`` (no
    pooling across short-lived test connections/transactions) -- ``NullPool``
    *rejects* ``pool_size``/``max_overflow``/``pool_timeout`` keyword
    arguments, so those are only passed in the default-pool branch.

    The transaction budget is installed in BOTH branches: it is a property of
    the path, not of how that path pools, and a ``NullPool`` engine that
    opened a fresh connection per statement would otherwise be the one place
    an unbounded statement could still hide.
    """
    if poolclass is None:
        engine = create_async_engine(
            db.url,
            connect_args=_CONNECT_ARGS,
            poolclass=AsyncAdaptedQueuePool,
            pool_pre_ping=True,
            pool_size=db.pool_size,
            max_overflow=db.max_overflow,
            pool_timeout=db.pool_timeout_s,
            pool_recycle=db.pool_recycle_s,
        )
    else:
        engine = create_async_engine(
            db.url,
            connect_args=_CONNECT_ARGS,
            poolclass=poolclass,
        )
    _install_transaction_budget(engine, db)
    return engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``.

    ``expire_on_commit=False``: an aggregate hydrated from a query stays
    usable after its enclosing transaction commits -- ``TenantSessionFactory``
    (``rls.py``) commits on clean scope exit, and callers keep reading
    already-loaded attributes off the returned domain objects afterwards.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
