"""Live-Postgres proofs for capacity step 2.9 (``ح-18``) against real
``aizzak_test``.

Four things no source-level guard can establish, and each one was a MEASURED
failure of this repository before the step landed:

1. **Three replicas provisioning at once all succeed.** Measured before the
   lock, on a database with NOTHING left to migrate -- the ordinary
   rolling-deploy case -- one of three died in ``apply_grants`` with
   ``tuple concurrently updated`` (``XX000``), an internal error no retry rule
   would classify as retryable. Since ``migrate`` is a
   ``service_completed_successfully`` dependency, that replica never starts.
2. **The lock is really taken, and a second holder really waits.** A guard
   that reads ``pg_advisory_lock`` out of the source proves the call is
   written, not that it locks anything.
3. **The holder is idle, not idle-in-transaction.** A lock held inside an open
   transaction pins the ``xmin`` horizon for the whole deploy, which is exactly
   what stops autovacuum from removing dead tuples -- the defect step 2.8 just
   finished removing from these same tables.
4. **``lock_timeout`` turns a blocked DDL into a failure instead of a freeze,
   and ``create_index_concurrently`` produces a VALID index.** The second half
   matters more than it looks: measured, ``CREATE INDEX CONCURRENTLY IF NOT
   EXISTS`` against an INVALID namesake leaves it invalid and reports success,
   so the retry that looks like a fix leaves the table maintaining an index the
   planner will never use.

Every test here works on scratch objects in ``public`` or on the advisory-lock
namespace, and none of them migrates ``aizzak_test`` -- the session fixture has
already done that, and a second migration mid-session is how the 2.8 run lost
two measurements.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings, MigrationSettings
from app.infrastructure.persistence.database import create_engine
from app.ops.online_ddl import backfill_in_batches, create_index_concurrently
from app.ops.provision import provision, provision_lock
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_PROBE_TABLE = "public.zdm_probe"
_PROBE_INDEX = "ix_zdm_probe_new"
_PROBE_ROWS = 12_000

# Short enough that a test that would otherwise hang fails in seconds, long
# enough that it cannot expire before the contended lock is even requested.
_SHORT_WAIT = MigrationSettings(provision_lock_wait_ms=1_500)


@pytest.fixture
async def owner_engine(live_db: LiveDbDsns) -> AsyncEngine:
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _lock_holders(conn: AsyncConnection, key: str) -> list[tuple[int, str, bool]]:
    """Every backend holding or waiting for the provisioning lock, with the
    state of its session -- which is what test (3) is about."""
    rows = await conn.execute(
        text(
            "SELECT a.pid, a.state, l.granted "
            "FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
            "WHERE l.locktype = 'advisory' "
            "AND (l.classid::bigint << 32) | (l.objid::bigint & 4294967295) "
            "    = hashtextextended(:key, 0)"
        ),
        {"key": key},
    )
    return [(row[0], row[1], row[2]) for row in rows]


# ───────────────────────── (1) the race, as it happens ────────────────────


async def test_three_provisioners_at_once_all_succeed(live_db: LiveDbDsns) -> None:
    """The step's own acceptance criterion, on a database already at head --
    which is the shape a rolling deploy actually meets, and the shape that
    used to fail on the GRANTs rather than on the migrations.

    Threads, not tasks: ``provision`` is synchronous and drives Alembic with
    its own ``asyncio.run``, exactly as the ``migrate`` container does. Each
    call builds its own engine and its own connection, so the three sessions
    contend for the lock for real.
    """
    settings = MigrationSettings()
    results = await asyncio.gather(
        *(asyncio.to_thread(provision, live_db.owner, settings) for _ in range(3)),
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, f"concurrent provisioning failed: {failures}"


async def test_the_second_provisioner_waits_rather_than_racing(
    live_db: LiveDbDsns, owner_engine: AsyncEngine
) -> None:
    """Held by somebody else, the lock makes a second provisioner WAIT --
    and, when the wait is capped, die with a message naming the key rather
    than proceeding to migrate alongside the holder."""
    settings = MigrationSettings()
    async with provision_lock(live_db.owner, settings):
        started = time.monotonic()
        with pytest.raises(SystemExit) as exc:
            await asyncio.to_thread(provision, live_db.owner, _SHORT_WAIT)
        waited_ms = (time.monotonic() - started) * 1000.0

    assert settings.provision_lock_key in str(exc.value)
    assert waited_ms >= _SHORT_WAIT.provision_lock_wait_ms, (
        "the loser returned before its wait budget -- it did not actually queue"
    )


async def test_the_lock_is_released_when_the_holder_disconnects(
    live_db: LiveDbDsns, owner_engine: AsyncEngine
) -> None:
    """The recovery path, and the reason there is no lease to expire: a
    ``migrate`` container killed mid-run drops its connection, and the backend
    exit frees a session-level lock. If it did not, a single crashed deploy
    would wedge every later one."""
    settings = MigrationSettings()
    async with (
        provision_lock(live_db.owner, settings),
        owner_engine.connect() as observer,
    ):
        assert await _lock_holders(observer, settings.provision_lock_key)

    async with owner_engine.connect() as observer:
        assert not await _lock_holders(observer, settings.provision_lock_key)


async def test_the_lock_holder_is_idle_and_not_idle_in_transaction(
    live_db: LiveDbDsns, owner_engine: AsyncEngine
) -> None:
    """``idle in transaction`` for the length of a deploy holds back the
    ``xmin`` horizon, so no autovacuum anywhere in the database can remove a
    tuple that died after the lock was taken. Step 2.8 spent its whole
    measurement budget on exactly that class of defect."""
    settings = MigrationSettings()
    async with (
        provision_lock(live_db.owner, settings),
        owner_engine.connect() as observer,
    ):
        holders = await _lock_holders(observer, settings.provision_lock_key)
    assert holders, "the lock was not held at all"
    states = {state for _, state, granted in holders if granted}
    assert states == {"idle"}, f"the provisioning lock is held by a session in state {states}"


# ──────────────────── (2) lock_timeout: fail, do not freeze ───────────────


async def _hold_access_exclusive(dsn: str, table: str, seconds: float) -> None:
    engine = create_engine(DatabaseSettings(url=dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
            await asyncio.sleep(seconds)
    finally:
        await engine.dispose()


async def test_a_blocked_ddl_fails_at_its_budget_instead_of_waiting(
    live_db: LiveDbDsns, owner_engine: AsyncEngine
) -> None:
    """The mechanism ``migrations/env.py`` installs, proven on a connection
    carrying the same ``server_settings`` it uses.

    Without it the DDL waits for the contender and every later reader queues
    behind its lock REQUEST -- measured off-test at 6.57 s of blocked
    ``SELECT`` against an 8-second holder. With it the migration fails loudly
    at its own budget and the queue drains.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_PROBE_TABLE}"))
        await conn.execute(text(f"CREATE TABLE {_PROBE_TABLE} (id bigserial primary key)"))

    # Built exactly as `migrations/env.py` builds its own engine -- the point
    # is the startup-packet GUC, so the engine must be made the same way.
    budget_ms = 800
    guarded = create_async_engine(
        live_db.owner,
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "server_settings": {"lock_timeout": str(budget_ms)},
        },
    )
    holder = asyncio.create_task(_hold_access_exclusive(live_db.owner, _PROBE_TABLE, 6.0))
    await asyncio.sleep(0.5)
    try:
        started = time.monotonic()
        with pytest.raises(DBAPIError) as exc:
            async with guarded.begin() as conn:
                await conn.execute(text(f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN probe int"))
        elapsed_ms = (time.monotonic() - started) * 1000.0
    finally:
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
        await guarded.dispose()

    assert getattr(exc.value.orig, "sqlstate", None) == "55P03"
    assert elapsed_ms < 6_000, "the DDL outlived the holder -- lock_timeout was not in force"

    async with owner_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_PROBE_TABLE}"))


# ─────────────────── (3) expand/contract's two mechanisms ─────────────────


def _expand(conn: object) -> int:
    """Run both helpers the way a revision would: inside an Alembic
    ``MigrationContext``, through the ``alembic.op`` proxy."""
    context = MigrationContext.configure(connection=conn)  # type: ignore[arg-type]
    with Operations.context(context), context.begin_transaction():
        create_index_concurrently(_PROBE_INDEX, _PROBE_TABLE, "(new_v) WHERE new_v IS NOT NULL")
        return backfill_in_batches(
            _PROBE_TABLE, "new_v = t.old_v * 2", "new_v IS NULL", batch_size=2_500
        )


@pytest.fixture
async def probe_table(owner_engine: AsyncEngine) -> AsyncEngine:
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_PROBE_TABLE}"))
        await conn.execute(
            text(
                f"CREATE TABLE {_PROBE_TABLE} "
                "(id bigserial primary key, old_v int not null, new_v int)"
            )
        )
        await conn.execute(
            text(f"INSERT INTO {_PROBE_TABLE} (old_v) SELECT g FROM generate_series(1, :n) g"),
            {"n": _PROBE_ROWS},
        )
    try:
        yield owner_engine
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_PROBE_TABLE}"))


async def _index_is_valid(engine: AsyncEngine) -> bool | None:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                    "WHERE c.relname = :name"
                ),
                {"name": _PROBE_INDEX},
            )
        ).scalar()


async def test_the_expand_phase_builds_a_valid_index_and_fills_every_row(
    probe_table: AsyncEngine,
) -> None:
    """``CONCURRENTLY`` cannot run inside a transaction and Alembic wraps every
    revision in one; without ``autocommit_block`` this raises rather than
    building anything. ``knowledge/0009_hot_path_indexes.py`` had to take the
    blocking form for want of this path, and measured 2.1 s of blocked writes."""
    async with probe_table.connect() as conn:
        filled = await conn.run_sync(_expand)

    assert filled == _PROBE_ROWS
    assert await _index_is_valid(probe_table) is True
    async with probe_table.connect() as conn:
        remaining = (
            await conn.execute(text(f"SELECT count(*) FROM {_PROBE_TABLE} WHERE new_v IS NULL"))
        ).scalar()
        doubled = (
            await conn.execute(text(f"SELECT bool_and(new_v = old_v * 2) FROM {_PROBE_TABLE}"))
        ).scalar()
    assert remaining == 0
    assert doubled is True


async def test_both_phases_are_re_runnable(probe_table: AsyncEngine) -> None:
    """An autocommit block is not atomic, so a revision that uses one WILL be
    re-run after an interrupted deploy. The predicate is what makes the second
    run free rather than a second full rewrite."""
    async with probe_table.connect() as conn:
        await conn.run_sync(_expand)
    async with probe_table.connect() as conn:
        again = await conn.run_sync(_expand)
    assert again == 0
    assert await _index_is_valid(probe_table) is True


async def test_an_invalid_index_is_rebuilt_and_not_silently_kept(
    probe_table: AsyncEngine,
) -> None:
    """The measured trap, reproduced the deterministic way.

    An interrupted ``CREATE INDEX CONCURRENTLY`` leaves an index with
    ``indisvalid = false``: the planner will not use it, and every write still
    maintains it. A ``CREATE UNIQUE INDEX CONCURRENTLY`` that meets a duplicate
    leaves exactly the same wreckage, on purpose rather than on a race, so that
    is how this test makes one.

    Then both halves are shown on the same wreckage: the obvious retry --
    ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` -- SEES the invalid index and
    reports success without building anything, and the helper rebuilds it.
    """
    async with probe_table.begin() as conn:
        await conn.execute(text(f"UPDATE {_PROBE_TABLE} SET new_v = 1"))
        await conn.execute(text(f"INSERT INTO {_PROBE_TABLE} (old_v, new_v) VALUES (1, 1)"))

    autocommit = probe_table.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit.connect() as conn:
        with pytest.raises(DBAPIError) as exc:
            await conn.execute(
                text(f"CREATE UNIQUE INDEX CONCURRENTLY {_PROBE_INDEX} ON {_PROBE_TABLE} (new_v)")
            )
    assert getattr(exc.value.orig, "sqlstate", None) == "23505"
    assert await _index_is_valid(probe_table) is False, (
        "a failed CONCURRENTLY build did not leave an invalid index -- "
        "the trap this test is about did not occur"
    )

    async with autocommit.connect() as conn:
        await conn.execute(
            text(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_PROBE_INDEX} "
                f"ON {_PROBE_TABLE} (new_v) WHERE new_v IS NOT NULL"
            )
        )
    assert await _index_is_valid(probe_table) is False, (
        "IF NOT EXISTS rebuilt the invalid index -- the helper's whole reason "
        "for dropping it first would be gone"
    )

    async with probe_table.connect() as conn:
        await conn.run_sync(_expand)
    assert await _index_is_valid(probe_table) is True

    async with probe_table.connect() as conn:
        copies = (
            await conn.execute(
                text("SELECT count(*) FROM pg_class WHERE relname = :name"), {"name": _PROBE_INDEX}
            )
        ).scalar()
    assert copies == 1, "the rebuild left a second index behind"
