"""usage module: usage_records (append-only ledger) + usage_rollups
(aggregation) + limits (quotas/budgets) — metering, quotas, budgets
(FR-130...134).

Creates ``usage.usage_records``/``usage.usage_rollups``/``usage.limits``
(01-data-model §2.10), their RLS tenant-isolation policies (01 §3), and the
``touch_updated_at`` trigger on ``usage_rollups``/``limits`` (``usage_records``
is append-only -- no ``updated_at`` column, so no trigger there, the
``knowledge.chunks`` precedent).

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same reason
as ``knowledge/0001_knowledge.py``/``media/0001_media.py`` -- each module
chain's applied revisions are recorded in its OWN ``version_table_schema``
(DAT-03, run via ``-x vts=usage``), so a single ``alembic upgrade``
invocation has no way to inspect whether another chain's revision has
already been applied against this database; a cross-chain ``depends_on``
would be unenforceable here. The platform baseline chain MUST be applied
first as an operational runbook step (08-local-runbook), not something
Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=usage upgrade usage@head

Revision ID: 0001_usage
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_usage"
down_revision = None
branch_labels = ("usage",)
depends_on = None


def upgrade() -> None:
    # Append-only ledger (DAT-07: no updated_at/deleted_at/version columns).
    op.execute(
        """
        CREATE TABLE usage.usage_records (
          id            uuid PRIMARY KEY,
          workspace_id  uuid NOT NULL,
          agent_key     text NOT NULL,
          provider      text NOT NULL,
          tokens        bigint NOT NULL DEFAULT 0 CHECK (tokens >= 0),
          cost_micros   bigint NOT NULL DEFAULT 0 CHECK (cost_micros >= 0),
          operation_id  uuid NOT NULL,
          created_at    timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_usage_op UNIQUE (workspace_id, operation_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX ix_usage_ws_agent_prov
            ON usage.usage_records(workspace_id, agent_key, provider, created_at);
        """
    )

    # Internal rollup/aggregation table -- fast-path enforcement reads.
    op.execute(
        """
        CREATE TABLE usage.usage_rollups (
          workspace_id    uuid NOT NULL,
          agent_key       text NOT NULL DEFAULT '*',
          provider        text NOT NULL DEFAULT '*',
          period          text NOT NULL CHECK (period IN ('day','month')),
          period_start    date NOT NULL,
          tokens_sum      bigint NOT NULL DEFAULT 0,
          cost_micros_sum bigint NOT NULL DEFAULT 0,
          updated_at      timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (workspace_id, agent_key, provider, period, period_start)
        );
        """
    )

    # Configurable quotas/budgets/limits.
    op.execute(
        """
        CREATE TABLE usage.limits (
          id            uuid PRIMARY KEY,
          workspace_id  uuid NOT NULL,
          scope         text NOT NULL CHECK (scope IN ('workspace','agent','provider')),
          scope_key     text NOT NULL DEFAULT '*',
          metric        text NOT NULL CHECK (metric IN ('tokens','cost_micros')),
          period        text NOT NULL CHECK (period IN ('day','month')),
          limit_value   bigint NOT NULL CHECK (limit_value >= 0),
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),
          version       integer NOT NULL DEFAULT 1,
          CONSTRAINT uq_limit UNIQUE (workspace_id, scope, scope_key, metric, period)
        );
        """
    )
    op.execute("CREATE INDEX ix_limits_ws ON usage.limits(workspace_id);")

    # touch triggers -- usage_rollups + limits only (01 §0); usage_records is
    # append-only (no updated_at column, so no trigger there).
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON usage.usage_rollups
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON usage.limits
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3, verbatim pattern) on all three tenant-scoped tables.
    op.execute("ALTER TABLE usage.usage_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE usage.usage_records FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON usage.usage_records
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )

    op.execute("ALTER TABLE usage.usage_rollups ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE usage.usage_rollups FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON usage.usage_rollups
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )

    op.execute("ALTER TABLE usage.limits ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE usage.limits FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON usage.limits
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON usage.limits")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON usage.usage_rollups")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON usage.usage_records")

    op.execute("DROP TRIGGER IF EXISTS trg_touch ON usage.limits")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON usage.usage_rollups")

    op.execute("DROP INDEX IF EXISTS usage.ix_limits_ws")
    op.execute("DROP TABLE IF EXISTS usage.limits")

    op.execute("DROP TABLE IF EXISTS usage.usage_rollups")

    op.execute("DROP INDEX IF EXISTS usage.ix_usage_ws_agent_prov")
    op.execute("DROP TABLE IF EXISTS usage.usage_records")
