"""Permit the explicit platform-admin write sentinel to manage assignments.

Revision ID: 0003_access_admin_write
Revises: 0002_access_admin_read
"""

from __future__ import annotations

from alembic import op

revision = "0003_access_admin_write"
down_revision = "0002_access_admin_read"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE POLICY role_assignments_platform_admin_write ON access.role_assignments
          FOR ALL
          USING (current_setting('app.platform_admin_write', true) = 'on')
          WITH CHECK (current_setting('app.platform_admin_write', true) = 'on');
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS role_assignments_platform_admin_write ON access.role_assignments"
    )
