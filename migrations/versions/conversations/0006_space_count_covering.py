"""conversations: the space index gains the tenant it always carries.

Capacity step 2.4 (``docs/capacity-plan.md`` §5, Wave 2). ``ix_conv_space``
(``space_id`` alone, partial on ``deleted_at IS NULL``, added by
``0004_conversation_space``) becomes ``ix_conv_ws_space`` — ``(workspace_id,
space_id)``, same partial predicate. A replacement, not an addition; the
argument is the one ``files/0004_space_quota_covering.py`` makes about its own
table, and the two were found by the same run.

**What it fixes.** ``SqlConversationRepository.counts_by_space`` puts the thread
count beside each space in a space listing — one ``GROUP BY`` for the whole
page, read on nearly every screen that shows the switcher. With only
``(space_id)`` indexed, the ``workspace_id`` half of the predicate could not be
a search key, so every matching row had to be fetched from the heap to be
rechecked. Measured on the Wave-0 seed (``app_rw``, largest tenant, 4,253
threads in the space), before::

    GroupAggregate  (actual time=2.062..2.063 rows=1)
      Buffers: shared hit=168 read=5
      ->  Bitmap Heap Scan on conversations  (rows=4253)
            Recheck Cond: ((workspace_id = …) AND (space_id = …) AND (deleted_at IS NULL))
            Heap Blocks: exact=168

After::

    GroupAggregate  (actual time=…)
      ->  Index Only Scan   (rows=4254, Heap Fetches: 29)
      Buffers: shared hit=9

173 buffers to 9 — 19x — and the plan stops touching the heap at all for rows
the visibility map already vouches for.

**Why ``(space_id)`` alone loses nothing.** Every predicate on this table in
this adapter carries ``workspace_id`` beside ``space_id``: ``list_by_agent``'s
optional narrowing, ``counts_by_space``, and ``purge_space``. DD-04 layer 2
makes that a rule, not a coincidence, so a key that leads with the tenant
serves all three and serves them better.

**What this does NOT fix, and is not trying to.** ``list_by_agent`` reads 838
rows to return 21 (39.9x) on the same tenant, and the excess is not the index
-- it is the correlated ``message_count`` subquery, which runs once per row of
the page and reads that thread's messages through ``uq_msg_seq``. That is a
different shape with a different remedy (a lateral join, or a stored counter),
it sits under the 50x allowance ``app.ops.explain_hot_paths`` applies, and an
index review is the wrong step to decide it in. Recorded in
``docs/capacity-status.md`` as the statement nearest the line.

**Lock cost.** ``CREATE INDEX``/``DROP INDEX`` (not ``CONCURRENTLY``) block
writes to ``conversations.conversations`` for their duration — sub-second on
50,095 rows. Create before drop, so no window exists without an index on the
space column. The online path is capacity step 2.9, which has not landed.

Revision ID: 0006_space_count_covering
Revises: 0005_conversation_clarification
"""

from __future__ import annotations

from alembic import op

revision = "0006_space_count_covering"
down_revision = "0005_conversation_clarification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_conv_ws_space
          ON conversations.conversations(workspace_id, space_id)
          WHERE deleted_at IS NULL;
        """
    )
    op.execute("DROP INDEX IF EXISTS conversations.ix_conv_space")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX ix_conv_space ON conversations.conversations(space_id) "
        "WHERE deleted_at IS NULL;"
    )
    op.execute("DROP INDEX IF EXISTS conversations.ix_conv_ws_space")
