"""The two mechanisms expand/contract needs and Alembic does not give it
(capacity step 2.9, ``ح-18`` — ``docs/capacity-plan.md`` §5, Wave 2).

**What expand/contract is, in this repository's own terms.** A rolling deploy
(step 7.2) has a window in which two code versions run against ONE schema. A
schema change survives that window only if it is additive in both directions:
old code must keep working after the change, and new code must keep working
before it. The four phases are

1. **expand** — add the compatible thing: a NULLable column, a new index, a
   new table. Nothing reads it yet, so nothing breaks if the deploy rolls back.
2. **dual-read/dual-write** — ship code that tolerates both shapes.
3. **backfill** — fill the new shape in batches, each batch its own
   transaction (``backfill_in_batches`` below).
4. **contract** — in a LATER release, once no running code reads the old
   shape: ``SET NOT NULL``, ``DROP COLUMN``, drop the old index.

This repository already practises phases 1-3 by hand and says so:
``files/0002_file_space.py`` gives its column a NULLable birth and moves the
``SET NOT NULL`` to its own later step, for exactly the reason above. What it
lacked was the mechanism for the two phases that take locks, and any guard
that a future migration would do the same. ``tests/unit/
test_zero_downtime_migrations.py`` is the guard; this module is the mechanism.

**Why these are here and not in ``infrastructure/persistence``.** Nothing on
the request path may reach for them: both functions open autocommit blocks and
run for as long as a table is big, which is the opposite of every rule that
governs a request. They are deploy-time operator machinery, so they sit beside
``app.ops.provision``, which is the only process that ever drives them.
"""

from __future__ import annotations

import logging
import re

from alembic import op
from sqlalchemy import text

_logger = logging.getLogger(__name__)

#: One batch of a backfill. Small enough that the row locks it holds are
#: released inside a request budget (2.6's 5 s), large enough that a million
#: rows is 200 statements and not 200,000.
DEFAULT_BATCH_SIZE = 5_000

#: A ceiling on the loop, not on the data: 10,000 batches is fifty million
#: rows at the default size. It exists because the loop's only other exit is
#: "the predicate stopped matching", and a predicate that the assignments do
#: not falsify (``WHERE true``) would spin forever -- during a deploy, holding
#: the provisioning lock, with `migrate` never exiting and therefore no
#: service starting. Failing loudly at an absurd number beats that.
MAX_BATCHES = 10_000

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


def _require_identifier(name: str, what: str) -> str:
    """Migration source is trusted code, not user input -- this check is not
    an injection guard, it is a typo guard. Both helpers interpolate their
    arguments into SQL because a table or column name cannot be a bind
    parameter, and a mistyped one should fail here with the name in the
    message rather than three statements later inside a batch loop."""
    if not _IDENTIFIER.match(name):
        raise ValueError(
            f"{what} must be a bare (optionally schema-qualified) identifier: {name!r}"
        )
    return name


def create_index_concurrently(index: str, table: str, definition: str) -> None:
    """``CREATE INDEX CONCURRENTLY`` from inside an Alembic revision.

    **Why this needs a helper at all.** ``CONCURRENTLY`` cannot run inside a
    transaction block, and Alembic wraps every revision in one -- which is why
    ``knowledge/0009_hot_path_indexes.py`` had to take a plain ``CREATE
    INDEX`` and record the cost it could not avoid (measured there: **2.1 s**
    of blocked writes for two indexes on a million chunks). The way out is
    ``MigrationContext.autocommit_block()``, which commits the revision's
    transaction, runs the body outside one, and opens a fresh transaction
    after.

    **And that is a real price, stated rather than hidden: inside the block
    the revision is no longer atomic.** If the statement fails, the DDL that
    ran before it in the same revision stays committed and Alembic has not
    stamped the version -- so the revision will be re-run. Every migration
    that uses this must therefore be re-runnable, which is why this function
    is written to be: it drops a leftover invalid index before building.

    **The leftover invalid index is not hypothetical, and ``IF NOT EXISTS`` is
    the trap.** A ``CREATE INDEX CONCURRENTLY`` that is interrupted leaves
    behind an index marked ``indisvalid = false``: the planner will not use
    it, but every INSERT and UPDATE still maintains it. ``CREATE INDEX
    CONCURRENTLY IF NOT EXISTS`` then SEES that index and skips the build --
    so the retry silently succeeds and leaves the table paying for an index
    nobody can use. This helper drops an invalid namesake first, and only an
    invalid one: a valid index of the same name means the work is already
    done.
    """
    _require_identifier(table, "table name")
    if "." in index:
        # The index lands in the TABLE's schema; a qualified name here would
        # read as a second, silently ignored, opinion about where it goes.
        raise ValueError(f"index name must be bare, not schema-qualified: {index!r}")
    _require_identifier(index, "index name")
    schema = table.partition(".")[0] if "." in table else "public"
    bare = index.rpartition(".")[2]

    with op.get_context().autocommit_block():
        conn = op.get_bind()
        invalid = conn.execute(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE n.nspname = :schema AND c.relname = :index AND NOT i.indisvalid"
            ),
            {"schema": schema, "index": bare},
        ).scalar()
        if invalid is not None:
            _logger.warning(
                "online_ddl.dropping_invalid_index",
                extra={"index": f"{schema}.{bare}"},
            )
            conn.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {schema}.{bare}"))
        conn.execute(
            text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {bare} ON {table} {definition}")
        )


def backfill_in_batches(
    table: str,
    assignments: str,
    predicate: str,
    key: str = "id",
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = MAX_BATCHES,
) -> int:
    """Fill an expanded column a batch at a time, committing between batches.

    **The shape this replaces.** The backfills already in this tree
    (``files/0002_file_space.py`` and its two siblings) issue ONE ``UPDATE``
    per workspace inside the revision's single transaction. That is correct
    today because those tables were small when the migration ran, and it does
    not stay correct: one statement over a million rows holds a row lock on
    every row it has touched until the revision commits, and the writes that
    collide with it wait the whole time. Batching bounds that wait to one
    batch, and bounds the WAL a single transaction must hold too.

    **It runs in an autocommit block, so it is not atomic, so it must be
    re-runnable.** ``predicate`` is what makes it so: it has to select rows
    that still need the work (``space_id IS NULL``, not ``true``), so a
    re-run after an interrupted deploy picks up where the last one stopped
    instead of rewriting every row again.

    **It does not turn RLS off, and cannot be made to.** Every tenant table
    here is ``FORCE ROW LEVEL SECURITY`` and ``aizzak_owner`` is neither
    superuser nor ``BYPASSRLS``, so a caller that has not set
    ``app.workspace_id`` sees zero rows and this returns ``0`` -- the same
    silent no-op ``files/0002``'s docstring warns about. The caller loops over
    ``workspace.workspaces`` (the one table with no RLS) exactly as that
    migration does; making this function do it for them would hide which
    tables are tenant-scoped and which are not.

    **The loop's only natural exit is the predicate**, so an assignment that
    does not falsify it never terminates -- and it would not terminate while
    holding the provisioning lock, with ``migrate`` never exiting and no
    service ever starting behind it. ``max_batches`` is the ceiling that turns
    that mistake into a message instead of a hung deploy.

    Returns the number of rows updated.
    """
    _require_identifier(table, "table name")
    _require_identifier(key, "key column")
    if batch_size < 1:
        raise ValueError(f"batch_size must be positive: {batch_size}")

    statement = text(
        f"WITH batch AS ("
        f"  SELECT {key} FROM {table} WHERE {predicate} ORDER BY {key} LIMIT {batch_size}"
        f") "
        f"UPDATE {table} AS t SET {assignments} "
        f"FROM batch AS b WHERE t.{key} = b.{key}"
    )

    updated = 0
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        for batch in range(max_batches):
            done = conn.execute(statement).rowcount
            if done <= 0:
                return updated
            updated += done
            _logger.info(
                "online_ddl.backfill_batch",
                extra={"table": table, "batch": batch + 1, "rows": done, "total": updated},
            )
    raise RuntimeError(
        f"backfill of {table} did not finish in {max_batches} batches ({updated} rows). "
        f"The predicate {predicate!r} is still matching rows the assignments were meant "
        "to exclude -- a backfill whose predicate its own SET does not falsify never ends."
    )
