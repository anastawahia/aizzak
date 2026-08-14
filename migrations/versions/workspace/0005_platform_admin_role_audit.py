"""Extend platform audit rows for administrative role changes.

Revision ID: 0005_admin_role_audit
Revises: 0004_admin_accounts
"""

from __future__ import annotations

from alembic import op

revision = "0005_admin_role_audit"
down_revision = "0004_admin_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original status-transition fields are nullable only for the new
    # role actions.  Existing account rows remain complete and the paired
    # checks below keep either shape coherent.
    op.execute(
        "ALTER TABLE platform.admin_audit_log "
        "ALTER COLUMN previous_status DROP NOT NULL, "
        "ALTER COLUMN new_status DROP NOT NULL, "
        "ADD COLUMN details jsonb NOT NULL DEFAULT '{}'::jsonb;"
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_action_check;")
    op.execute(
        "ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_previous_status_check;"
    )
    op.execute(
        "ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_new_status_check;"
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_check;")
    op.execute(
        """
        ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_action_check
          CHECK (action IN (
            'account.status_changed',
            'role.workspace.changed',
            'role.platform_admin.changed'
          ));
        """
    )
    op.execute(
        """
        ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_shape_check CHECK (
          (action = 'account.status_changed'
             AND previous_status IN ('active', 'disabled')
             AND new_status IN ('active', 'disabled')
             AND previous_status <> new_status
             AND details = '{}'::jsonb)
          OR
          (action IN ('role.workspace.changed', 'role.platform_admin.changed')
             AND previous_status IS NULL
             AND new_status IS NULL
             AND details ? 'scope'
             AND details ? 'role'
             AND details ? 'enabled')
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM platform.admin_audit_log
            WHERE action <> 'account.status_changed'
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade admin role audit while role audit rows exist';
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_shape_check;")
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_action_check;")
    op.execute(
        "ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_action_check "
        "CHECK (action = 'account.status_changed');"
    )
    op.execute(
        "ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_previous_status_check "
        "CHECK (previous_status IN ('active', 'disabled'));"
    )
    op.execute(
        "ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_new_status_check "
        "CHECK (new_status IN ('active', 'disabled'));"
    )
    op.execute(
        "ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_check "
        "CHECK (previous_status <> new_status);"
    )
    op.execute(
        "ALTER TABLE platform.admin_audit_log "
        "ALTER COLUMN previous_status SET NOT NULL, "
        "ALTER COLUMN new_status SET NOT NULL, "
        "DROP COLUMN details;"
    )
