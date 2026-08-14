"""Record platform provider-key changes in the admin ledger (BE-ADM-011).

``platform.admin_audit_log`` was built for events that happen TO a person:
``target_user_id``/``workspace_id`` are both ``NOT NULL`` (0004_admin_accounts)
because every action it carried so far named an account. Storing or revoking a
platform provider key names no one — it is the platform's own key, shared by
every workspace that has none of its own.

Two ways to record it were available and one is a lie: writing the acting
administrator into ``target_user_id`` would make the ledger say an action was
taken against the person who took it, which is exactly the sentence a reader
of an audit trail must be able to trust. So the columns become nullable, and
the invariant that used to be expressed by ``NOT NULL`` is expressed by a
CHECK instead — an event either names a person in a workspace or names no one,
never half of one. ``actor_user_id`` stays ``NOT NULL``: an administrative
action always has an actor, and a key change with no author is exactly the row
this ledger exists to prevent.

``previous_status``/``new_status`` need no new vocabulary to describe a key —
``absent → active`` for the first key on a provider, ``active → active`` for a
rotation, ``active → revoked`` for a removal — and ``details`` carries which
provider it was. A rotation is the one shape where the two statuses are equal,
which is why the account branch's ``previous_status <> new_status`` rule is
NOT copied into this branch: replacing a live key with another live key is a
real, audited event, not a no-op.

Revision ID: 0008_provider_key_audit
Revises: 0007_admin_delete
"""

from __future__ import annotations

from alembic import op

revision = "0008_provider_key_audit"
down_revision = "0007_admin_delete"
branch_labels = None
depends_on = None

_SHAPE_CHECK = """
  (action = 'account.status_changed'
     AND target_user_id IS NOT NULL
     AND previous_status IN ('active', 'disabled')
     AND new_status IN ('active', 'disabled')
     AND previous_status <> new_status
     AND details = '{}'::jsonb)
  OR
  (action = 'account.deleted'
     AND target_user_id IS NOT NULL
     AND previous_status IN ('active', 'disabled')
     AND new_status = 'deleted'
     AND details ? 'redacted'
     AND details ? 'roles_revoked')
  OR
  (action IN ('role.workspace.changed', 'role.platform_admin.changed')
     AND target_user_id IS NOT NULL
     AND previous_status IS NULL
     AND new_status IS NULL
     AND details ? 'scope'
     AND details ? 'role'
     AND details ? 'enabled')
  OR
  (action IN ('provider.key_stored', 'provider.key_revoked')
     AND target_user_id IS NULL
     AND previous_status IN ('absent', 'active')
     AND new_status IN ('active', 'revoked')
     AND details ? 'provider')
"""

_OLD_SHAPE_CHECK = """
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
"""


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.admin_audit_log "
        "ALTER COLUMN target_user_id DROP NOT NULL, "
        "ALTER COLUMN workspace_id DROP NOT NULL;"
    )
    # The general rule, kept independent of the per-action shape check below so
    # that an action added later cannot half-fill the pair by forgetting it.
    op.execute(
        """
        ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_subject_check
          CHECK ((target_user_id IS NULL) = (workspace_id IS NULL));
        """
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_action_check;")
    op.execute(
        """
        ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_action_check
          CHECK (action IN (
            'account.status_changed',
            'account.deleted',
            'role.workspace.changed',
            'role.platform_admin.changed',
            'provider.key_stored',
            'provider.key_revoked'
          ));
        """
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_shape_check;")
    op.execute(
        "ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_shape_check CHECK ("
        f"{_SHAPE_CHECK});"
    )


def downgrade() -> None:
    # The same refusal 0005 and 0007 wrote, for the same reason: the rows this
    # revision made expressible carry no target user, so narrowing the columns
    # back would mean deleting the record of an administrative action.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM platform.admin_audit_log
            WHERE action IN ('provider.key_stored', 'provider.key_revoked')
          ) THEN
            RAISE EXCEPTION 'cannot downgrade provider key audit while provider audit rows exist';
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_shape_check;")
    op.execute(
        "ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_shape_check CHECK ("
        f"{_OLD_SHAPE_CHECK});"
    )
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
    op.execute(
        "ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_subject_check;"
    )
    op.execute(
        "ALTER TABLE platform.admin_audit_log "
        "ALTER COLUMN target_user_id SET NOT NULL, "
        "ALTER COLUMN workspace_id SET NOT NULL;"
    )
