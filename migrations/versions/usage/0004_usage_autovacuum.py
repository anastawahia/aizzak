"""usage: per-table autovacuum settings + ``fillfactor`` for the metering
tables (capacity step 2.8, ``ح-16`` — ``docs/capacity-plan.md`` §5, Wave 2).

The sibling of ``platform/0005_ledger_autovacuum.py``, which carries the full
derivation of the numbers and of why step 2.8's "relative thresholds" is
implemented as its opposite. This chain applies the same shape to the third
table 2.8 names — ``usage`` — and adds one thing the platform ledgers cannot
use.

**Two tables, two different defects.**

``usage.usage_records`` is the append-only ledger (DAT-07: no ``updated_at``,
no ``deleted_at``, no ``version``). It takes one row per metered model call and
nothing ever updates or deletes it, so the *dead-tuple* trigger never moves at
all and only PostgreSQL 13+'s ``autovacuum_vacuum_insert_*`` pair fires — with
the same proportional defect (``1000 + 0.2 * n_live``), so a ledger that has
reached ten million rows waits for two million more before its
freeze/visibility-map maintenance runs. It is also the table with no retention
policy of any kind until this step; ``app.ops.retention`` gains it as a fourth
sweep target.

``usage.usage_rollups`` is the opposite and is the more interesting one: it is
**small and updated constantly**. Every charge upserts ``domain.periods.
rollup_buckets`` (up to 3 dimension keys) x every ``Period`` member (day,
month) — up to **six UPDATEs per metered call** against a table holding only a
handful of rows per workspace. It is also on the enforcement read path: step
2.7's ``reserve`` reads it inside the workspace's advisory lock, so bloat here
is paid by every admission decision, under a lock, while other tenants wait.

**``fillfactor = 70`` on the rollups, and NOT on anything else.** A HOT update
rewrites a tuple inside its own page and never touches an index, and it is
possible only when (a) no *indexed* column changes and (b) the page has room.
``usage_rollups``'s upsert changes ``tokens_sum``/``cost_micros_sum``/
``updated_at``, none of which are in its primary key — condition (a) holds
here and holds nowhere else in 2.8's scope. Condition (b) is what
``fillfactor`` buys: at the default 100 a freshly packed page has no free space
at all, so the first update of every row must go to another page and write both
index entries. Measured on ``platform.outbox``, where condition (a) is FALSE
(``published_at`` is indexed by both partial indexes), HOT was already
impossible: ``n_tup_hot_upd = 3`` out of 500,000 updates. That is why the
platform ledgers get no ``fillfactor`` and this table does.

30% reserved is the standard figure for an update-in-place counter table and
costs ~40% more heap for a table whose whole size is measured in megabytes —
paid once, against six index updates per charge saved. It applies to pages
written *after* this migration; existing pages keep their packing until they
are rewritten, so the effect appears gradually rather than at deploy time.

``usage.limits`` and ``usage.reservations`` are deliberately untouched.
``limits`` is configuration, written by an operator and read on the hot path —
its churn is not measured in rows per second. ``reservations`` is bounded by
construction (2.7: every row is deleted by its own commit, and an abandoned one
is swept opportunistically by the next reserver of the same workspace under the
lock it already holds), so it has no backlog to bloat behind.

**The retention half — why this migration also adds two policies.** Step 2.8
asks for "a retention policy executed by step 5.7", and ``usage`` is the one
of its three named tables that has no sweep at all: ``app.ops.retention``
covers the three ``platform`` ledgers and nothing else. Extending it to
``usage.usage_records`` is not a matter of adding a ``DELETE`` — every
``usage`` table is under ``FORCE ROW LEVEL SECURITY`` with a
``tenant_isolation`` policy carrying no ``TO`` clause, so it applies to
``retention_sweeper`` too and confines an age-based cross-tenant sweep to
whatever ``app.workspace_id`` its session has set: nothing. This migration
therefore adds the same role-scoped carve-out
``platform/0003_retention_sweep.py`` added for ``platform.idempotency_keys``
— ``retention_sweeper_select``/``retention_sweeper_delete``, ``USING (true)``,
``TO retention_sweeper`` alone, OR-combined with ``tenant_isolation`` so
``app_rw``'s own confinement is untouched. The role's actual reach stays
bounded by its GRANT (``app.ops.provision.RETENTION_GRANTS``: SELECT and
DELETE, never INSERT or UPDATE) and by the sweep's own ``created_at <
cutoff`` predicate.

``usage_rollups`` gets NO such policy and is NOT swept, deliberately. It is
the aggregate the ledger rows roll up into and the table the quota check
actually reads (2.7's ``reserve``); deleting a rollup row would not reclaim
history, it would silently hand a workspace back the headroom it had already
spent. The ledger is the detail and can age out; the rollup is the balance and
cannot. ``usage_records(created_at)`` gets the index the sweep's predicate
needs -- ``ix_usage_ws_agent_prov`` leads with ``workspace_id``, which a
cross-tenant age sweep does not have.

Applied with the per-module chain (DAT-03)::

    alembic -x vts=usage upgrade usage@head

Revision ID: 0004_usage_autovacuum
Revises: 0003_usage_reservations
"""

from __future__ import annotations

from alembic import op

revision = "0004_usage_autovacuum"
down_revision = "0003_usage_reservations"
branch_labels = None
depends_on = None

# The platform ledgers' numbers, re-derived nowhere: one autovacuum naptime of
# peak dead-tuple production. Kept as literals rather than imported because a
# migration must keep applying the value it was written with even if a later
# revision changes the constant -- the platform chain's own docstring carries
# the derivation.
VACUUM_THRESHOLD = 50_000
ANALYZE_THRESHOLD = 25_000

#: The rollups' upsert is HOT-able (module docstring); 30% reserved is what
#: makes it actually happen.
ROLLUPS_FILLFACTOR = 70

# Literal copy of `app.ops.provision.RETENTION_ROLE`, the
# `0003_retention_sweep.py` precedent: `migrations/` is not importable
# application code, and this only needs to name the role a `CREATE POLICY ...
# TO` clause validates.
RETENTION_ROLE = "retention_sweeper"

_METERING_SETTINGS: tuple[str, ...] = (
    "autovacuum_vacuum_scale_factor = 0",
    f"autovacuum_vacuum_threshold = {VACUUM_THRESHOLD}",
    "autovacuum_analyze_scale_factor = 0",
    f"autovacuum_analyze_threshold = {ANALYZE_THRESHOLD}",
    "autovacuum_vacuum_insert_scale_factor = 0",
    f"autovacuum_vacuum_insert_threshold = {VACUUM_THRESHOLD}",
    "autovacuum_vacuum_cost_delay = 0",
)

METERING_TABLES: tuple[str, ...] = ("usage.usage_records", "usage.usage_rollups")

_DEFAULTS: tuple[str, ...] = tuple(s.split(" = ")[0] for s in _METERING_SETTINGS)


def upgrade() -> None:
    settings = ", ".join(_METERING_SETTINGS)
    for table in METERING_TABLES:
        op.execute(f"ALTER TABLE {table} SET ({settings})")
    op.execute(f"ALTER TABLE usage.usage_rollups SET (fillfactor = {ROLLUPS_FILLFACTOR})")

    # The sweep's own predicate is `created_at < cutoff` across every tenant;
    # `ix_usage_ws_agent_prov` leads with `workspace_id` and cannot serve it.
    op.execute("CREATE INDEX ix_usage_records_created_at ON usage.usage_records(created_at);")

    # Cross-tenant reach for the sweep role ONLY (module docstring).
    op.execute(
        f"CREATE POLICY retention_sweeper_select ON usage.usage_records "
        f"FOR SELECT TO {RETENTION_ROLE} USING (true);"
    )
    op.execute(
        f"CREATE POLICY retention_sweeper_delete ON usage.usage_records "
        f"FOR DELETE TO {RETENTION_ROLE} USING (true);"
    )


def downgrade() -> None:
    resets = ", ".join(_DEFAULTS)
    for table in METERING_TABLES:
        op.execute(f"ALTER TABLE {table} RESET ({resets})")
    op.execute("ALTER TABLE usage.usage_rollups RESET (fillfactor)")
    op.execute("DROP POLICY IF EXISTS retention_sweeper_delete ON usage.usage_records")
    op.execute("DROP POLICY IF EXISTS retention_sweeper_select ON usage.usage_records")
    op.execute("DROP INDEX IF EXISTS usage.ix_usage_records_created_at")
