"""Allow the admin-write sentinel to lock its target user row.

Revision ID: 0006_admin_write_select
Revises: 0005_admin_role_audit
"""

from __future__ import annotations

from alembic import op

revision = "0006_admin_write_select"
down_revision = "0005_admin_role_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL applies SELECT policies to `SELECT ... FOR UPDATE` as well as
    # UPDATE policies.  The role manager must lock the target user before it
    # changes an assignment, so this is deliberately paired with — and no
    # wider than — the existing admin-write sentinel.
    op.execute(
        """
        CREATE POLICY users_platform_admin_write_select ON workspace.users
          FOR SELECT
          USING (current_setting('app.platform_admin_write', true) = 'on');
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_platform_admin_write_select ON workspace.users")
