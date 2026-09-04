"""usage module: the in-flight reservation the quota check takes out before a
request runs (capacity-plan step 2.7, `FR-132`'s declared reserve/commit
extension point — 01-data-model §2.10's "جدول حجوزات لاحقاً", 02 §2's
``reservation_id``, INV-U3).

**Why a table exists at all.** Until now enforcement was a READ
(``usage_rollups`` vs ``limits``) followed, one model call later, by a WRITE
(the ledger row). Nothing stood between them, so every request that started
while another was still running saw a total that did not include it. Measured
on the live stack before this migration: a workspace with **one** token of
headroom left admitted **46 of 100** concurrent requests and finished at 55
tokens against a limit of 10 — and the 46 was not a property of the quota but
of the connection pool, since the same run with ``pool_size=5`` admitted
exactly 5 and with ``pool_size=30`` admitted 29. A ceiling enforced at the
width of the pool is not a ceiling.

A row here is the admission itself: taken under the workspace's advisory lock
in the same transaction that reads the totals, counted by every later reader
until it is committed or expires. That is what makes "exactly one of a hundred
gets in" a property of the database rather than of timing.

**``expires_at`` is a backstop, not a policy.** A committed reservation is
DELETEd by the commit; this column exists for the request that never comes
back — a killed worker, a severed stream, a process that died between reserve
and charge. Its value is the caller's own ``Limits.stream_max_duration_s``
(600 s), passed in by the Composition Root rather than written here: a request
cannot outlive the deadline that already bounds it, so a reservation older
than that belongs to nobody. Readers filter on it, so an abandoned row stops
costing headroom the moment it expires whether or not anything has deleted it
yet; the delete is opportunistic housekeeping done by the next reserver of the
same workspace, which is why this chain adds no sweeper and no new job.

``tokens``/``cost_micros`` are what was RESERVED, never what was spent —
the spend is the ledger's, and the two never live in the same row.

Applied with the per-module chain (DAT-03)::

    alembic -x vts=usage upgrade usage@head

Revision ID: 0003_usage_reservations
Revises: 0002_usage_estimated
"""

from __future__ import annotations

from alembic import op

revision = "0003_usage_reservations"
down_revision = "0002_usage_estimated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE usage.reservations (
          id            uuid PRIMARY KEY,
          workspace_id  uuid NOT NULL,
          agent_key     text NOT NULL,
          provider      text NOT NULL,
          tokens        bigint NOT NULL DEFAULT 0 CHECK (tokens >= 0),
          cost_micros   bigint NOT NULL DEFAULT 0 CHECK (cost_micros >= 0),
          created_at    timestamptz NOT NULL DEFAULT now(),
          expires_at    timestamptz NOT NULL
        );
        """
    )
    # The one read this table serves: "what is still in flight for this
    # workspace, right now". `expires_at` trails the tenant so the filter that
    # drops abandoned rows is an index condition rather than a filter over
    # every row the tenant ever reserved.
    op.execute(
        """
        CREATE INDEX ix_usage_reservations_ws_live
            ON usage.reservations(workspace_id, expires_at);
        """
    )

    # No `trg_touch`: like `usage_records` this table has no `updated_at`. A
    # reservation is never edited -- it is created, then either deleted by its
    # commit or left to expire.
    op.execute("ALTER TABLE usage.reservations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE usage.reservations FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON usage.reservations
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON usage.reservations")
    op.execute("DROP INDEX IF EXISTS usage.ix_usage_reservations_ws_live")
    op.execute("DROP TABLE IF EXISTS usage.reservations")
