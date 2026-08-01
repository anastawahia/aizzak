"""platform: retention-sweep support (P1-5, docs/p1-hardening-plan.md §3 step 8).

**What this adds, and why a migration and not just a Python script.**
``platform.processed_events`` (01-data-model §4.2 names it as carrying no
retention policy at all), ``platform.idempotency_keys`` (01 §4.2-ب: "لا سياسة
احتفاظٍ في v1؛ ``created_at`` مفهرسٌ لأنّ كنسةً تشغيليّةً ستعمل عليه" — an
operational sweep this migration is the SCHEMA half of) and published
``platform.outbox`` rows all grow monotonically, forever. ``app.ops.
provision``'s own docstring names the reason a sweep cannot simply be granted
to ``app_rw``: INSERT-only on ``outbox``/``processed_events`` is deliberate —
"a role that can only INSERT can neither enumerate the ledger nor un-process
an event to force a replay". Granting ``app_rw`` DELETE there to close this
gap would undo exactly that guarantee. So this migration does NOT touch
``app_rw``'s grant at all (grants never live in a migration anyway — 01 §6,
``provision.py``'s own docstring); instead it prepares the schema for a
FOURTH role, ``retention_sweeper`` (``app.ops.retention``, sibling of
``outbox_relay``'s own separate-role precedent), whose actual grants are
added in ``app.ops.provision.RETENTION_GRANTS`` — a runbook/deploy step, same
as every other role's grants.

**Two additions, no new tables:**

1. Indexes the sweep's own ``WHERE`` clauses need, so a slow-growing table
   does not become a sequential scan the one day it finally matters:
   ``platform.processed_events(processed_at)`` (there is no index on this
   column at all in ``0001_baseline_platform.py``) and a partial
   ``platform.outbox(published_at) WHERE published_at IS NOT NULL`` — the
   mirror image of the existing ``ix_outbox_unpublished`` partial index
   (which covers the OPPOSITE half of that same column, ``published_at IS
   NULL``, for the relay's own poll query).

2. ``platform.idempotency_keys`` is the ONE ``platform`` table under RLS
   (``0002_idempotency_keys.py``), and its ``tenant_isolation`` policy has no
   ``TO`` clause — it applies to EVERY role, ``retention_sweeper`` included,
   confining it to whatever ``app.workspace_id`` its session happens to have
   set (nothing, for a role that runs one age-based sweep across every
   tenant at once). A retention sweep is cross-tenant by nature, so this
   migration adds TWO permissive policies scoped ``TO retention_sweeper``
   alone — ``retention_sweeper_select``/``retention_sweeper_delete``, both
   ``USING (true)`` — exactly the "OR-combined permissive policy" mechanism
   01 §3.1 already uses for ``platform_credentials_read``, here scoped to a
   single named role instead of PUBLIC so ``app_rw``'s own tenant confinement
   is completely untouched. ``retention_sweeper``'s ACTUAL reach stays
   bounded by its own GRANT (SELECT, DELETE only — ``provision.py``) and by
   the sweep's own ``WHERE created_at < cutoff`` predicate, never by an
   unbounded ``DELETE FROM``.

**Role must pre-exist.** ``CREATE POLICY ... TO retention_sweeper`` validates
the role at DDL time (unlike a bare ``USING``/``WITH CHECK`` clause, which
never names a role) — so, unlike every prior migration in this chain, this
one has a genuine prerequisite the earlier ones did not: ``retention_sweeper``
must already be a login role before this migration runs.
``app.ops.provision.provision()`` already fails fast on a missing role BEFORE
running any migration (``_require_roles``); this revision adds
``RETENTION_ROLE`` to that check, so the ordering constraint is enforced in
code, not merely documented here.

Revision ID: 0003_retention_sweep
Revises: 0002_idempotency_keys
"""

from __future__ import annotations

from alembic import op

revision = "0003_retention_sweep"
down_revision = "0002_idempotency_keys"
branch_labels = None
depends_on = None

# Literal copy of `app.ops.provision.RETENTION_ROLE` — `migrations/` is not
# importable application code (the `0002_idempotency_keys.py`/conftest
# precedent for schema literals), so this only needs to name the role a
# `CREATE POLICY ... TO` clause validates, not share behaviour with it.
RETENTION_ROLE = "retention_sweeper"


def upgrade() -> None:
    # (1) The sweep's own indexes — see module docstring point 1.
    op.execute(
        "CREATE INDEX ix_processed_events_processed_at ON platform.processed_events(processed_at);"
    )
    op.execute(
        "CREATE INDEX ix_outbox_published ON platform.outbox(published_at) "
        "WHERE published_at IS NOT NULL;"
    )

    # (2) Cross-tenant reach for the sweep role ONLY (module docstring point
    # 2) — OR-combines with `tenant_isolation` (no `TO` clause, applies to
    # every role) rather than replacing it, so `app_rw`'s own confinement is
    # untouched.
    op.execute(
        f"""
        CREATE POLICY retention_sweeper_select ON platform.idempotency_keys
          FOR SELECT
          TO {RETENTION_ROLE}
          USING (true);
        """
    )
    op.execute(
        f"""
        CREATE POLICY retention_sweeper_delete ON platform.idempotency_keys
          FOR DELETE
          TO {RETENTION_ROLE}
          USING (true);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS retention_sweeper_delete ON platform.idempotency_keys")
    op.execute("DROP POLICY IF EXISTS retention_sweeper_select ON platform.idempotency_keys")
    op.execute("DROP INDEX IF EXISTS platform.ix_outbox_published")
    op.execute("DROP INDEX IF EXISTS platform.ix_processed_events_processed_at")
