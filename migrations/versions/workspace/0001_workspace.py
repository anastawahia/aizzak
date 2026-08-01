"""workspace module: workspaces (tenant root) + users (identity mirror).

Creates ``workspace.workspaces`` and ``workspace.users`` (01-data-model
§2.1), both tables' ``touch_updated_at`` triggers, and (``users`` only) the
``tenant_isolation`` RLS policy (01 §3) plus the R1 ``users_platform_read``
sentinel policy.

``workspace.workspaces`` gets NO RLS (01 §2.1's own note, R2): it is the
tenant ROOT itself -- it has no ``workspace_id`` column to isolate on, and
is instead access-restricted purely by AuthZ
(``SqlWorkspaceRepository``'s ``id = :ctx_ws`` predicate,
``modules/workspace/adapters/sql_repository.py``).

R1 (security-approved, NOT context-absence-keyed, NOT row-keyed):
``users_platform_read`` is an EXPLICIT sentinel-GUC policy
(``current_setting('app.platform_read', true) = 'on'``), SELECT-only, OR'd
on top of ``tenant_isolation`` (RLS policies combine with OR). Its sentinel
is set ONLY by ``infrastructure/persistence/rls.py::PlatformSessionFactory``,
whose sole sanctioned consumer is
``SqlUserRepository.get_by_firebase_uid`` -- resolving a user by its
external Firebase UID before any workspace/RLS context exists (JIT login).

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same
reason as ``knowledge/0001_knowledge.py``/``media/0001_media.py`` -- each
module chain's applied revisions are recorded in its OWN
``version_table_schema`` (DAT-03, run via ``-x vts=workspace``), so a single
``alembic upgrade`` invocation has no way to inspect whether another
chain's revision has already been applied against this database; a
cross-chain ``depends_on`` would be unenforceable here. The platform
baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=workspace upgrade workspace@head

Revision ID: 0001_workspace
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_workspace"
down_revision = None
branch_labels = ("workspace",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace.workspaces (
          id            uuid PRIMARY KEY,
          owner_user_id uuid NOT NULL,
          name          text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
          status        text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','suspended','archived')),
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),
          version       integer NOT NULL DEFAULT 1
        );
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON workspace.workspaces
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )
    # NOTE: no RLS here by design -- see the module docstring above (R2).

    op.execute(
        """
        CREATE TABLE workspace.users (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          firebase_uid text NOT NULL UNIQUE,
          email        text NOT NULL,
          display_name text,
          status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          version      integer NOT NULL DEFAULT 1,
          CONSTRAINT fk_user_ws FOREIGN KEY (workspace_id) REFERENCES workspace.workspaces(id)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_users_owner ON workspace.users(workspace_id) WHERE status='active';"
    )
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON workspace.users
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3 pattern, hardened NULLIF form -- see media/0001_media.py /
    # rls-empty-string-hardening: `NULLIF(..., '')` turns a pooled
    # connection's reset-to-`''` GUC into SQL NULL before the `::uuid` cast,
    # so the comparison fails safe to zero rows instead of raising 22P02).
    op.execute("ALTER TABLE workspace.users ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE workspace.users FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON workspace.users
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )
    # R1: explicit sentinel-GUC escape hatch for the platform-only
    # `get_by_firebase_uid` read path -- see the module docstring above.
    # SELECT-only ⇒ no write ever bypasses `tenant_isolation` through here.
    op.execute(
        """
        CREATE POLICY users_platform_read ON workspace.users
          FOR SELECT
          USING (current_setting('app.platform_read', true) = 'on');
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_platform_read ON workspace.users")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON workspace.users")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON workspace.users")
    op.execute("DROP INDEX IF EXISTS workspace.uq_users_owner")
    # users BEFORE workspaces: fk_user_ws references workspace.workspaces(id).
    op.execute("DROP TABLE IF EXISTS workspace.users")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON workspace.workspaces")
    op.execute("DROP TABLE IF EXISTS workspace.workspaces")
