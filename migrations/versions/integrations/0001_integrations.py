"""integrations module: connections + mcp_servers (OAuth connectors, remote MCP).

Creates ``integrations.connections`` and ``integrations.mcp_servers``
(01-data-model §2.9), their RLS tenant-isolation policies (01 §3), and the
``touch_updated_at`` triggers. No cross-schema FK: references to other
modules stay logical ids only (INV-I5, DAT-02).

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same reason
as ``knowledge/0001_knowledge.py`` -- each module chain's applied revisions
are recorded in its OWN ``version_table_schema`` (DAT-03, run via
``-x vts=integrations``), so a single ``alembic upgrade`` invocation has no
way to inspect whether another chain's revision has already been applied
against this database; a cross-chain ``depends_on`` would be unenforceable
here. The platform baseline chain MUST be applied first as an operational
runbook step (08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=integrations upgrade integrations@head

Revision ID: 0001_integrations
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_integrations"
down_revision = None
branch_labels = ("integrations",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE integrations.connections (
          id             uuid PRIMARY KEY,
          workspace_id   uuid NOT NULL,
          connector_key  text NOT NULL,
          display_name   text,
          status         text NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','connected','revoked','error')),
          scopes         text[] NOT NULL DEFAULT '{}',
          token_ref      text,
          key_id         text,
          expires_at     timestamptz,
          last_error     text,
          created_by     uuid,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          version        integer NOT NULL DEFAULT 1,
          CONSTRAINT uq_conn_ws_connector UNIQUE (workspace_id, connector_key)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX ix_conn_ws ON integrations.connections(workspace_id)
            WHERE status = 'connected';
        """
    )

    op.execute(
        """
        CREATE TABLE integrations.mcp_servers (
          id             uuid PRIMARY KEY,
          workspace_id   uuid NOT NULL,
          name           text NOT NULL,
          endpoint_url   text NOT NULL,
          transport      text NOT NULL DEFAULT 'http'
                           CHECK (transport IN ('http','sse')),
          auth_ref       text,
          key_id         text,
          status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','error')),
          created_by     uuid,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          version        integer NOT NULL DEFAULT 1,
          CONSTRAINT uq_mcp_ws_name UNIQUE (workspace_id, name)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mcp_ws ON integrations.mcp_servers(workspace_id)
            WHERE status = 'active';
        """
    )

    # touch triggers (01 §0, shared platform.touch_updated_at()).
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON integrations.connections
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON integrations.mcp_servers
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3, verbatim pattern) on both tenant-scoped tables.
    for table in ("integrations.connections", "integrations.mcp_servers"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
              WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
            """
        )


def downgrade() -> None:
    for table in ("integrations.mcp_servers", "integrations.connections"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_touch ON {table}")
    op.execute("DROP INDEX IF EXISTS integrations.ix_mcp_ws")
    op.execute("DROP INDEX IF EXISTS integrations.ix_conn_ws")
    op.execute("DROP TABLE IF EXISTS integrations.mcp_servers")
    op.execute("DROP TABLE IF EXISTS integrations.connections")
