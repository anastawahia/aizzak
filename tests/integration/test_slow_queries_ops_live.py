"""Live-Postgres proof for the slowest-query report (``app.ops.slow_queries``,
capacity step 0.4) against the real ``aizzak_test`` database.

The exit criterion this file exists for: ``pg_stat_statements`` really
answers this module's SQL, and a statement this test just ran really comes
back in the ranking -- neither of which a stub can show, because both are
properties of the extension rather than of the code that reads it. SQL shape,
warning conditions, output rendering and CLI refusals need no database and
live in ``tests/unit/test_ops_slow_queries.py``.

**``reset`` is deliberately never called here.** ``pg_stat_statements`` keeps
ONE hash table for the whole server, so a suite that reset it would erase
whatever an operator was accumulating on the same cluster -- including, on a
developer machine, the load-run baseline this very step exists to produce.
``deploy/postgres/testdb/20-test-database.sh`` withholds the ``EXECUTE``
grant for that reason, so this is enforced by the cluster and not only by
this file's restraint.

The probe statements below carry a fixed column alias rather than a unique
one: identifiers are NOT normalised by ``pg_stat_statements``, so an alias is
findable, and a FIXED alias means repeated runs of this suite aggregate into
one entry instead of leaking a new entry per run into a table with a bounded
size.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.ops.slow_queries import QueryStat, extension_installed, stats_info, top_queries
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db

_PROBE_ALIAS = "aizzak_slow_queries_live_probe"
_NORMALISATION_ALIAS = "aizzak_slow_queries_live_normalisation"
_PROBE_SECONDS = 0.05
_SEARCH_LIMIT = 500


async def _find(conn: AsyncConnection, alias: str, *, order_by: str = "total") -> list[QueryStat]:
    stats = await top_queries(conn, limit=_SEARCH_LIMIT, order_by=order_by)
    return [stat for stat in stats if alias in stat.query]


@pytest.fixture
async def owner_conn(live_db: LiveDbDsns) -> AsyncIterator[AsyncConnection]:
    """A direct ``aizzak_owner`` connection to ``aizzak_test`` -- the footing
    the tool documents (one role per process, and never through the pooler
    while the pooler is what a load run is measuring)."""
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            yield conn
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_the_extension_exists_on_the_provisioned_cluster(owner_conn: AsyncConnection) -> None:
    """Not a tautology: this fails loudly on a cluster whose volume predates
    ``deploy/postgres/initdb/20-extensions.sh`` or whose operator has not
    re-run ``20-test-database.sh`` -- which is exactly the situation the
    tool's own missing-extension message describes, caught here instead of in
    the middle of a load run."""
    assert await extension_installed(owner_conn) is True


@pytest.mark.anyio
async def test_the_ranking_contains_a_statement_this_test_just_ran(
    owner_conn: AsyncConnection,
) -> None:
    """The whole point of the step, end to end: something costs time, and the
    report names it. ``pg_sleep`` makes the cost deterministic instead of
    hoping the probe out-ranks real work."""
    for _ in range(2):
        await owner_conn.execute(text(f"SELECT pg_sleep({_PROBE_SECONDS}) AS {_PROBE_ALIAS}"))

    found = await _find(owner_conn, _PROBE_ALIAS, order_by="mean")

    assert len(found) == 1, "one statement, one aggregated row"
    (probe,) = found
    assert probe.calls >= 2
    assert probe.mean_exec_ms >= _PROBE_SECONDS * 1000 * 0.8
    assert probe.role == "aizzak_owner"
    assert probe.database == "aizzak_test"


@pytest.mark.anyio
async def test_constants_are_normalised_so_no_literal_reaches_the_report(
    owner_conn: AsyncConnection,
) -> None:
    """Pitfall 3, proven rather than assumed: two executions differing only
    in their literal aggregate into ONE row whose text carries ``$1``. That
    is what makes it safe to print a tenant-facing statement in an operator
    report -- and equally why 2.4 still has to run ``EXPLAIN`` under a real
    ``app.workspace_id`` to see a tenant's actual plan.

    The probe sleeps for the same reason the one above it does, and the
    reason was measured: run as a bare ``SELECT length(...)`` it cost 0.01ms
    and fell outside the top 500 rows once the whole suite had run before it,
    so the test passed alone and failed in the suite. A probe that has to
    out-rank real work must cost something real."""
    for literal in ("workspace-alpha", "workspace-beta"):
        await owner_conn.execute(
            text(
                f"SELECT pg_sleep({_PROBE_SECONDS}), length('{literal}') AS {_NORMALISATION_ALIAS}"
            )
        )

    found = await _find(owner_conn, _NORMALISATION_ALIAS, order_by="mean")

    assert len(found) == 1
    (row,) = found
    assert "workspace-alpha" not in row.query
    assert "workspace-beta" not in row.query
    assert "$1" in row.query


@pytest.mark.anyio
async def test_the_default_scope_returns_only_the_connected_databases_rows(
    owner_conn: AsyncConnection,
) -> None:
    """The cluster carries ``aizzak`` and ``postgres`` beside this database,
    and their statements are in the SAME shared hash table -- a report that
    mixed them would rank a migration or a smoke test as production cost."""
    stats = await top_queries(owner_conn, limit=_SEARCH_LIMIT)

    assert stats, "the suite has run enough SQL by now for the view to be non-empty"
    assert {stat.database for stat in stats} == {"aizzak_test"}


@pytest.mark.anyio
async def test_stats_info_reports_the_two_numbers_that_qualify_a_report(
    owner_conn: AsyncConnection,
) -> None:
    """``dealloc``/``stats_reset`` come from ``pg_stat_statements_info``,
    which exists only from PostgreSQL 14 -- this asserts the view is really
    there on the pinned 16 image rather than trusting the version note."""
    info = await stats_info(owner_conn)

    assert info.dealloc >= 0
    assert info.stats_reset is not None
