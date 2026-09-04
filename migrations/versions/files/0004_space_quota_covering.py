"""files: the space index carries the byte the quota reads, and the redundant one goes.

Capacity step 2.4 (``docs/capacity-plan.md`` §5, Wave 2). Two changes, and
together they take ``files.files`` from five indexes to **four**:

* ``ix_files_space_name`` gains ``INCLUDE (size_bytes)``. Same three key
  columns, same partial predicate, one payload column.
* ``ix_files_space`` (``space_id`` alone, partial on ``deleted_at IS NULL``,
  added by ``0002_file_space``) is dropped.

**What it fixes: the space quota is an aggregate that holds a row lock.**
``framework/di/space_quota.py`` runs, on EVERY upload registration, inside one
transaction: ``SELECT … FOR UPDATE`` on the space row, then
``SqlFileRepository.bytes_in_space`` — ``sum(size_bytes)`` over that space's
live files — then the insert. So the sum's duration is **lock-hold time**, and
every concurrent upload into the same space waits behind it. Measured on the
Wave-0 seed (``app_rw``, largest tenant, 8,506 files in the space), before::

    Aggregate  (actual time=17.515..17.517 rows=1)
      Buffers: shared hit=609 read=145
      ->  Bitmap Heap Scan on files  (rows=8506)
            Heap Blocks: exact=609
            ->  Bitmap Index Scan on ix_files_space_name  (rows=8506)

The index already narrowed to the space; every one of the 8,506 rows was then
fetched from the heap for one column, because ``size_bytes`` lived in no index
on this table — 754 buffers to produce one number. After, with the column
carried as a payload, the same index answers the aggregate without the heap::

    Aggregate  (actual time=…)
      ->  Index Only Scan using ix_files_space_name on files  (rows=8506)
            Index Cond: ((workspace_id = …) AND (space_id = …))
            Heap Fetches: 18
      Buffers: shared hit=6 read=88

94 buffers instead of 754 — **8x** — and the lock is held for proportionally
less time.

It is still O(files in the space): an ``INCLUDE`` makes the read cheap, it does
not make it constant. The constant-time shape is a maintained counter, which is
a data-model change (a second place for a space's volume to be recorded, and to
drift) and belongs to whoever takes that decision, not to an index review.
Recorded in ``docs/capacity-status.md`` rather than smuggled in here.

**⚠️ Why the payload rides on THIS index and not on a new narrow one — the
first shape of this fix was wrong, and an existing test caught it.** The
obvious move is a separate ``ix_files_space_bytes(workspace_id, space_id)
INCLUDE (size_bytes)``: narrower, and measured genuinely cheaper for the
aggregate alone (5.8 MB against 8.5 MB; 67 buffers against 94). It was written
that way first, and
``tests/integration/test_file_namesakes_live.py::
test_the_name_key_is_an_index_condition_and_not_a_filter`` failed on it::

    ->  Index Scan using ix_files_space_bytes on files
          Index Cond: ((workspace_id = …) AND (space_id = …))
          Filter: (name_key = 'report.pdf'::text)

Two indexes now shared the same ``(workspace_id, space_id)`` prefix, so the
planner was free to take the narrower one for the NAMESAKE lookup too — and
that one needs the third key column. ``name_key`` fell out of ``Index Cond``
and into ``Filter``, which is the exact plan ``0003_file_name_lookup`` exists to
prevent: a seek straight to the name becomes a scan of the whole space filtered
by it, on the module's hottest write path (every upload completion and every
rename, against a ``max_files_per_workspace`` of 10,000).

On the seeded database the planner still preferred the three-key index (real
statistics, a real distribution), so this would have shipped green and degraded
later — which is precisely why the guard asserts the plan under ``enable_seqscan
= off`` rather than a timing. One index carrying both answers removes the choice
instead of betting on the planner making it.

**Why dropping ``ix_files_space`` loses nothing.** Every predicate on
``files.files`` in every adapter carries ``workspace_id`` beside ``space_id``
— ``list``, ``bytes_in_space``, ``totals_by_space``, ``storage_keys_in_space``,
``purge_space``, ``live_namesakes`` — because DD-04 layer 2 makes that a rule
rather than a habit. ``ix_files_space_name``'s own ``(workspace_id, space_id)``
prefix therefore serves every one of them, and serves them better: the tenant's
entries are contiguous instead of interleaved with 199 others'. There is no
query in this repository that filters ``space_id`` without its workspace.

**``INCLUDE`` and not a fourth key column.** ``size_bytes`` is never a search
key and never an ordering here; putting it in the key would widen every
internal page for nothing. As a payload it rides in the leaf pages only, which
is exactly what an index-only scan needs — and it leaves ``name_key`` as the
last key column, where the namesake seek needs it.

**An index-only scan is only as good as the visibility map.** ``Heap Fetches:
18`` above is not zero — a row on a page ``autovacuum`` has not yet marked
all-visible is still fetched — so a table under heavy write churn gives back
part of this win until it is vacuumed. That is the same dependency ``files
.count``'s existing ``Index Only Scan`` already lives on (measured: 37 heap
fetches in 17,012 rows), and the ``autovacuum`` tuning that keeps it healthy is
capacity step **2.8**, not this one.

**Lock cost.** ``CREATE INDEX``/``DROP INDEX`` (not ``CONCURRENTLY``) take
locks that block writes to ``files.files`` for their duration — sub-second on
100,004 rows. Both creates run BEFORE either drop, so no window exists in which
a predicate has no index to use. ``CONCURRENTLY`` cannot run inside Alembic's
transaction; the online path is capacity step 2.9, which has not landed.

Revision ID: 0004_space_quota_covering
Revises: 0003_file_name_lookup
"""

from __future__ import annotations

from alembic import op

revision = "0004_space_quota_covering"
down_revision = "0003_file_name_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Build the replacement under a temporary name, then swap: an index cannot
    # be altered to add a payload column, and dropping first would leave the
    # namesake predicate with no index for the length of a rebuild.
    op.execute(
        """
        CREATE INDEX ix_files_space_name_covering
          ON files.files(workspace_id, space_id, name_key)
          INCLUDE (size_bytes)
          WHERE deleted_at IS NULL;
        """
    )
    op.execute("DROP INDEX files.ix_files_space_name")
    op.execute("ALTER INDEX files.ix_files_space_name_covering RENAME TO ix_files_space_name")
    # Redundant once the index above leads with the tenant (docstring).
    op.execute("DROP INDEX IF EXISTS files.ix_files_space")


def downgrade() -> None:
    # Restores `0002_file_space`'s and `0003_file_name_lookup`'s definitions
    # verbatim -- a downgrade that left either index shaped differently from
    # the way the revisions below it created it would make the chain's history
    # untrue.
    op.execute("CREATE INDEX ix_files_space ON files.files(space_id) WHERE deleted_at IS NULL;")
    op.execute(
        """
        CREATE INDEX ix_files_space_name_plain
          ON files.files(workspace_id, space_id, name_key)
          WHERE deleted_at IS NULL;
        """
    )
    op.execute("DROP INDEX files.ix_files_space_name")
    op.execute("ALTER INDEX files.ix_files_space_name_plain RENAME TO ix_files_space_name")
