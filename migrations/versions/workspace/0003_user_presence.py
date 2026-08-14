"""Add tenant-scoped, server-observed user presence.

Revision ID: 0003_workspace_presence
Revises: 0002_workspace_admin_read
"""

from __future__ import annotations

from alembic import op

revision = "0003_workspace_presence"
down_revision = "0002_workspace_admin_read"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.user_presence (
          user_id      uuid PRIMARY KEY REFERENCES workspace.users(id) ON DELETE CASCADE,
          workspace_id uuid NOT NULL REFERENCES workspace.workspaces(id) ON DELETE CASCADE,
          last_seen_at timestamptz NOT NULL
        );
        """
    )
    op.execute("ALTER TABLE workspace.user_presence ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE workspace.user_presence FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON workspace.user_presence
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )
    # The platform directory may read the timestamp, but never create or
    # mutate it.  The heartbeat remains a tenant-scoped self-write only.
    op.execute(
        """
        CREATE POLICY user_presence_platform_admin_read ON workspace.user_presence
          FOR SELECT
          USING (current_setting('app.platform_admin_read', true) = 'on');
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_presence_platform_admin_read ON workspace.user_presence")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON workspace.user_presence")
    op.execute("DROP TABLE IF EXISTS workspace.user_presence")
