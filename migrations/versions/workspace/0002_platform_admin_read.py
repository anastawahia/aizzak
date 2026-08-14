"""Permit the explicit platform-admin sentinel to read user-directory rows.

Revision ID: 0002_workspace_admin_read
Revises: 0001_workspace
"""

from __future__ import annotations

from alembic import op

revision = "0002_workspace_admin_read"
down_revision = "0001_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE POLICY users_platform_admin_read ON workspace.users
          FOR SELECT
          USING (current_setting('app.platform_admin_read', true) = 'on');
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_platform_admin_read ON workspace.users")
