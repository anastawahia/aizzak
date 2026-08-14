"""Add the audited, irreversible platform-account deletion (BE-ADM-006).

**Why a tombstone and not a ``DELETE``.** ``platform.admin_audit_log`` holds
``actor_user_id``/``target_user_id`` as ``NOT NULL REFERENCES
workspace.users(id)`` with no ``ON DELETE`` action (0004_admin_accounts), so
removing the row would have to take the ledger with it -- including the very
row recording the removal. An erasure that erases its own audit is not
audited. The account therefore becomes a terminal ``deleted`` status: the id
survives so the ledger keeps meaning, and everything about the person is
erased from the row in the same transaction (the adapter blanks ``email`` and
``display_name``; see ``modules/admin/adapters/sql_accounts.py``).

That also keeps the write inside the existing sentinel. The admin-write
escape hatch on ``workspace.users`` is ``FOR UPDATE``/``FOR SELECT`` only
(0004/0006) -- a row removal would need a permanent DELETE policy on the
identity table for the rarest action in the surface, so this migration adds
no policy at all.

**``deleted_at`` is paired with the status by a CHECK**, not by convention:
the tombstone is the one row state a later retention sweep will key on, and a
``deleted`` row with no timestamp (or a timestamp on a live account) would be
a sweep target nobody can date.

Revision ID: 0007_admin_delete
Revises: 0006_admin_write_select
"""

from __future__ import annotations

from alembic import op

revision = "0007_admin_delete"
down_revision = "0006_admin_write_select"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `users_status_check` is PostgreSQL's own name for the inline column CHECK
    # in 0001_workspace (the 0005_admin_role_audit precedent for dropping an
    # auto-named constraint by that name).
    op.execute("ALTER TABLE workspace.users DROP CONSTRAINT users_status_check;")
    op.execute(
        "ALTER TABLE workspace.users ADD CONSTRAINT users_status_check "
        "CHECK (status IN ('active','disabled','deleted'));"
    )
    op.execute("ALTER TABLE workspace.users ADD COLUMN deleted_at timestamptz;")
    op.execute(
        "ALTER TABLE workspace.users ADD CONSTRAINT users_deleted_at_check "
        "CHECK ((status = 'deleted') = (deleted_at IS NOT NULL));"
    )
    # `uq_users_owner` (0001) is partial on `status='active'`, so a tombstone
    # neither holds nor releases its workspace's single active-user slot.

    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_action_check;")
    op.execute(
        """
        ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_action_check
          CHECK (action IN (
            'account.status_changed',
            'account.deleted',
            'role.workspace.changed',
            'role.platform_admin.changed'
          ));
        """
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_shape_check;")
    op.execute(
        """
        ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_shape_check CHECK (
          (action = 'account.status_changed'
             AND previous_status IN ('active', 'disabled')
             AND new_status IN ('active', 'disabled')
             AND previous_status <> new_status
             AND details = '{}'::jsonb)
          OR
          (action = 'account.deleted'
             AND previous_status IN ('active', 'disabled')
             AND new_status = 'deleted'
             AND details ? 'redacted'
             AND details ? 'roles_revoked')
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
    # Same refusal as 0005: the rows this revision made expressible cannot be
    # narrowed back into the old shape, and dropping them would destroy the
    # append-only record of an irreversible action.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM workspace.users WHERE status = 'deleted') THEN
            RAISE EXCEPTION 'cannot downgrade account deletion while deleted accounts exist';
          END IF;
          IF EXISTS (
            SELECT 1 FROM platform.admin_audit_log WHERE action = 'account.deleted'
          ) THEN
            RAISE EXCEPTION 'cannot downgrade account deletion while deletion audit rows exist';
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_shape_check;")
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
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_action_check;")
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
    op.execute("ALTER TABLE workspace.users DROP CONSTRAINT users_deleted_at_check;")
    op.execute("ALTER TABLE workspace.users DROP COLUMN deleted_at;")
    op.execute("ALTER TABLE workspace.users DROP CONSTRAINT users_status_check;")
    op.execute(
        "ALTER TABLE workspace.users ADD CONSTRAINT users_status_check "
        "CHECK (status IN ('active','disabled'));"
    )
