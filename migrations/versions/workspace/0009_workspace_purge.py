"""Add the workspace content-purge sweep's schema surface (BE-ADM-014).

**What this migration is for.** ``app.ops.purge`` (the periodic sweep that
empties a tombstoned-and-past-retention workspace's content across Qdrant,
MinIO and Postgres -- see that module's own docstring for the full design)
needs three things this schema does not yet have: a place to record that a
workspace was purged (``purged_at``), a fast way to find candidate
tombstones (``ix_users_deleted_at``), and cross-tenant read reach onto
``workspace.users`` for its own least-privilege role (``workspace_purger``,
``app.ops.provision.PURGE_ROLE``) -- the SAME "RLS wall" problem
``0003_retention_sweep.py``/``credentials/0002_transit_rotator.py`` already
solved, solved the identical way: this role's own migration-added policy,
never a widened ``app_rw``.

**``purged_at`` is paired with ``status`` by a CHECK**, the ``deleted_at``/
``status='deleted'`` precedent on ``workspace.users``
(``0007_platform_admin_delete.py``): a purged workspace is always
``status='archived'`` (``app.ops.purge.finalize`` sets both together, in the
SAME transaction as the row deletions' own final schema), and an archived
workspace with no ``purged_at`` is simply an ordinary suspension/archival
that never went through the purge sweep -- the CHECK only forbids the
inverse, a ``purged_at`` timestamp on a workspace that is not archived, which
would be a purge that forgot to also change ``status``.

**``ix_users_deleted_at`` is a plain partial index**, not a composite one:
``app.ops.purge.find_candidates`` scans ``workspace.users`` for the newest
tombstone per workspace and compares it to a cutoff, and DAT-03's per-module
version-table-schema convention means this table's own chain
(``migrations/versions/workspace/``) is the only place that index can be
added. ``WHERE deleted_at IS NOT NULL`` keeps it from indexing the (large,
ever-growing) majority of rows that were never deleted at all.

**``workspace_purger_select``** is SELECT-only, ``USING (true)`` -- the
``retention_sweeper_select``/``transit_rotator`` precedent applied to a
FOURTH role: unlike those two, this role's OWN grant (``app.ops.provision.
PURGE_GRANTS``) never touches identity columns (``email``/``display_name``/
``firebase_uid``), so the carve-out plus the column-scoped grant together
mean this role can see a tombstone's STATE across every tenant and nothing
about the person it belonged to. No comparable policy is added on any other
tenant table in this migration: every DELETE the purge sweep issues against
the OTHER nine schemas stays bound by each table's ordinary
``tenant_isolation`` policy, scoped per workspace via ``set_config(
'app.workspace_id', ...)`` exactly like ``app_rw`` itself (``app.ops.
purge.purge_rows``) -- deliberately NOT a blanket cross-tenant carve-out the
way ``retention_sweeper``'s is, because a purge sweep operates one workspace
at a time by design, never "every tenant's copy of this table in one
statement" the way an age-based ledger sweep does.

**The audit shape CHECK grows a fifth branch.** ``workspace.purged`` is an
event with a ``target_user_id`` (the tombstone whose deletion emptied the
workspace) and a ``workspace_id`` (the one purged) -- both required by the
EXISTING ``admin_audit_log_subject_check`` (0008) the moment ``target_user_id
IS NOT NULL``, so nothing new is needed there. ``previous_status='deleted'``/
``new_status='purged'`` extend the account-lifecycle vocabulary
(``active``/``disabled``/``deleted`` from 0007) with its own terminal state,
one step further than ``deleted`` -- a purged workspace can never become
``active``/``disabled`` again, the same one-way arrow ``deleted_at`` already
draws for the user row. ``details ? 'stores'`` is the ONE required key (the
per-store affected-row/collection/prefix counts ``app.ops.purge.finalize``
always writes); ``retention_days`` also always appears in the SAME payload
but is not separately required by this CHECK, the ``details ? 'redacted'``
AND ``details ? 'roles_revoked'`` precedent applied to the one key this
shape's own downgrade guard actually needs to detect a purge row by.

Revision ID: 0009_workspace_purge
Revises: 0008_provider_key_audit
"""

from __future__ import annotations

from alembic import op

revision = "0009_workspace_purge"
down_revision = "0008_provider_key_audit"
branch_labels = None
depends_on = None

# 0008's shape check, verbatim -- this migration's downgrade restores exactly
# this text, and it is kept here (rather than re-derived) so the two
# migrations can never silently drift on what "the previous shape" was.
_OLD_SHAPE_CHECK = """
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

_NEW_SHAPE_CHECK = (
    _OLD_SHAPE_CHECK
    + """
  OR
  (action = 'workspace.purged'
     AND target_user_id IS NOT NULL
     AND previous_status = 'deleted'
     AND new_status = 'purged'
     AND details ? 'stores')
"""
)


def upgrade() -> None:
    op.execute("ALTER TABLE workspace.workspaces ADD COLUMN purged_at timestamptz;")
    op.execute(
        "ALTER TABLE workspace.workspaces ADD CONSTRAINT workspaces_purged_at_check "
        "CHECK (purged_at IS NULL OR status = 'archived');"
    )
    op.execute(
        "CREATE INDEX ix_users_deleted_at ON workspace.users(deleted_at) "
        "WHERE deleted_at IS NOT NULL;"
    )
    # Cross-tenant SELECT-only carve-out for `workspace_purger` alone (module
    # docstring) -- combines via OR with the table's existing
    # `tenant_isolation`/`users_platform_read` policies, never replaces them.
    op.execute(
        """
        CREATE POLICY workspace_purger_select ON workspace.users
          FOR SELECT TO workspace_purger USING (true);
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
            'provider.key_revoked',
            'workspace.purged'
          ));
        """
    )
    op.execute("ALTER TABLE platform.admin_audit_log DROP CONSTRAINT admin_audit_log_shape_check;")
    op.execute(
        "ALTER TABLE platform.admin_audit_log ADD CONSTRAINT admin_audit_log_shape_check CHECK ("
        f"{_NEW_SHAPE_CHECK});"
    )


def downgrade() -> None:
    # Same refusal 0005/0007/0008 wrote, for the same reason: the rows this
    # revision made expressible (a purged workspace, a purge audit row)
    # cannot be narrowed back into the old shape without destroying the
    # append-only record of an irreversible action.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM workspace.workspaces WHERE purged_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'cannot downgrade workspace purge while purged workspaces exist';
          END IF;
          IF EXISTS (
            SELECT 1 FROM platform.admin_audit_log WHERE action = 'workspace.purged'
          ) THEN
            RAISE EXCEPTION 'cannot downgrade workspace purge while purge audit rows exist';
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
            'role.platform_admin.changed',
            'provider.key_stored',
            'provider.key_revoked'
          ));
        """
    )

    op.execute("DROP POLICY IF EXISTS workspace_purger_select ON workspace.users;")
    op.execute("DROP INDEX IF EXISTS workspace.ix_users_deleted_at;")
    op.execute("ALTER TABLE workspace.workspaces DROP CONSTRAINT workspaces_purged_at_check;")
    op.execute("ALTER TABLE workspace.workspaces DROP COLUMN purged_at;")
