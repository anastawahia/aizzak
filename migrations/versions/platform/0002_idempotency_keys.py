"""platform: idempotency_keys (the ``Idempotency-Key`` ledger, 3.79).

Creates ``platform.idempotency_keys`` — the durable store behind 03-api-spec
§0's "`Idempotency-Key` ⇒ إعادة المحاولة آمنة" and the header ``openapi.yaml``
declares on ``registerFile``/``createMediaJob``/``runWorkflow``.

**Why this chain and not a module's.** One table serves three endpoints owned
by three different modules (files, media, workflows). Putting it in any one
module's chain would make the other two depend on that module's schema —
exactly the cross-module coupling DD-01/12 §3 forbids — so it lives beside
``platform.processed_events``, the DD-09 ledger it is modelled on: shared
bookkeeping, owned by the platform, referenced by no module's domain.

**RLS, unlike ``processed_events``.** That table is keyed by consumer group
and carries no tenant column, so it carries no policy. This one is keyed by
workspace: a stored response body is tenant data, and a client that guessed
another tenant's key would otherwise read back their created resource. Born
hardened with the ``NULLIF`` form (rls-empty-string-hardening): a pooled
connection that never had ``app.workspace_id`` SET LOCAL reads back ``''``,
not NULL, and a raw ``current_setting(...)::uuid`` cast on that empty string
raises ``22P02`` instead of failing safe to zero rows. ``NULLIF(..., '')``
makes the cast yield NULL, so the comparison is simply false.

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this is a linear successor inside the ``platform`` chain, applied by the same
step that applies the baseline:

    alembic upgrade platform@head

Revision ID: 0002_idempotency_keys
Revises: 0001_baseline_platform
"""

from __future__ import annotations

from alembic import op

revision = "0002_idempotency_keys"
down_revision = "0001_baseline_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `response_body`/`completed_at` are NULLABLE on purpose: a row is inserted
    # BEFORE the operation runs (that INSERT's 23505 is what makes "only one
    # caller runs" true under concurrency), and filled in after. `completed_at
    # IS NULL` therefore means "claimed, still in flight" -- a state the adapter
    # must be able to tell apart from a completed one, which a NOT NULL column
    # with a placeholder could not express.
    #
    # No `response_status` column: a replay returns through the same route,
    # whose `status_code` is declared once on the decorator, so the status is
    # reproduced by construction and a stored copy would be a column nothing
    # reads. `request_hash` is a digest, never the body -- the ledger has no
    # business keeping a second copy of a tenant's prompt or filename.
    op.execute(
        """
        CREATE TABLE platform.idempotency_keys (
          workspace_id    uuid NOT NULL,
          endpoint        text NOT NULL,
          idempotency_key text NOT NULL,
          request_hash    text NOT NULL,
          response_body   jsonb,
          created_at      timestamptz NOT NULL DEFAULT now(),
          completed_at    timestamptz,
          PRIMARY KEY (workspace_id, endpoint, idempotency_key)
        );
        """
    )
    # The retention sweep's index (01 §4.2's `processed_events` precedent: the
    # ledger grows without bound and `created_at` is the column an ops-side
    # prune would work on). Created with the table rather than "when it hurts":
    # a DELETE ... WHERE created_at < now() - interval on an unindexed ledger
    # is a sequential scan over every tenant's rows.
    op.execute("CREATE INDEX ix_idempotency_keys_created ON platform.idempotency_keys(created_at);")

    # RLS (01 §3 pattern, NULLIF-hardened -- see the module docstring).
    op.execute("ALTER TABLE platform.idempotency_keys ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE platform.idempotency_keys FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON platform.idempotency_keys
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON platform.idempotency_keys")
    op.execute("DROP INDEX IF EXISTS platform.ix_idempotency_keys_created")
    op.execute("DROP TABLE IF EXISTS platform.idempotency_keys")
