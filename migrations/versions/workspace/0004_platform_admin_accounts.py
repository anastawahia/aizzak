"""Add the audited platform-admin account status transition.

Revision ID: 0004_admin_accounts
Revises: 0003_workspace_presence
"""

from __future__ import annotations

from alembic import op

revision = "0004_admin_accounts"
down_revision = "0003_workspace_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This ledger is append-only for `app_rw` (the operational grant is in
    # app.ops.provision).  It intentionally has no API read path yet: BE-ADM-
    # 013 can add a bounded, redacted reader without widening the writer.
    op.execute(
        """
        CREATE TABLE platform.admin_audit_log (
          id              uuid PRIMARY KEY,
          actor_user_id   uuid NOT NULL REFERENCES workspace.users(id),
          target_user_id  uuid NOT NULL REFERENCES workspace.users(id),
          workspace_id    uuid NOT NULL REFERENCES workspace.workspaces(id),
          action          text NOT NULL CHECK (action = 'account.status_changed'),
          previous_status text NOT NULL CHECK (previous_status IN ('active','disabled')),
          new_status      text NOT NULL CHECK (new_status IN ('active','disabled')),
          reason          text NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 500),
          created_at      timestamptz NOT NULL DEFAULT now(),
          CHECK (previous_status <> new_status)
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_admin_audit_log_target_created "
        "ON platform.admin_audit_log(target_user_id, created_at DESC);"
    )
    # A write escape hatch distinct from both the JIT identity read and the
    # admin directory read.  No INSERT/DELETE policy accepts this sentinel.
    op.execute(
        """
        CREATE POLICY users_platform_admin_write ON workspace.users
          FOR UPDATE
          USING (current_setting('app.platform_admin_write', true) = 'on')
          WITH CHECK (current_setting('app.platform_admin_write', true) = 'on');
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_platform_admin_write ON workspace.users")
    op.execute("DROP TABLE IF EXISTS platform.admin_audit_log")
