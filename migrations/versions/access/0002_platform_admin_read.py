"""Permit the explicit platform-admin sentinel to read role assignments.

Revision ID: 0002_access_admin_read
Revises: 0001_access
"""

from __future__ import annotations

from alembic import op

revision = "0002_access_admin_read"
down_revision = "0001_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE POLICY role_assignments_platform_admin_read ON access.role_assignments
          FOR SELECT
          USING (current_setting('app.platform_admin_read', true) = 'on');
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS role_assignments_platform_admin_read ON access.role_assignments"
    )
