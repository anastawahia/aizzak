"""access module: role_assignments (RBAC bindings).

Creates ``access.role_assignments`` (01-data-model §2.2) and its
``tenant_isolation`` RLS policy (01 §3). NO touch trigger here -- the table
carries no ``updated_at`` column: assignments are create-or-delete only
(06-domain-models §2), never mutated in place, so there is nothing for
``platform.touch_updated_at()`` to touch.

The static Permission catalog (RBAC matrix) lives in code, not a table --
see ``app.modules.access.domain.value_objects.RoleCatalog``
(05-rbac-config-secrets.md §1).

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same
reason as ``knowledge/0001_knowledge.py``/``media/0001_media.py`` -- each
module chain's applied revisions are recorded in its OWN
``version_table_schema`` (DAT-03, run via ``-x vts=access``), so a single
``alembic upgrade`` invocation has no way to inspect whether another
chain's revision has already been applied against this database; a
cross-chain ``depends_on`` would be unenforceable here. The platform
baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=access upgrade access@head

Revision ID: 0001_access
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_access"
down_revision = None
branch_labels = ("access",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE access.role_assignments (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          user_id      uuid NOT NULL,
          role         text NOT NULL
                         CHECK (role IN ('owner','admin','member','viewer','platform_admin')),
          granted_by   uuid,
          created_at   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_assignment UNIQUE (workspace_id, user_id, role)
        );
        """
    )
    op.execute("CREATE INDEX ix_ra_ws_user ON access.role_assignments(workspace_id, user_id);")

    # RLS (01 §3 pattern, hardened NULLIF form -- see media/0001_media.py /
    # rls-empty-string-hardening).
    op.execute("ALTER TABLE access.role_assignments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE access.role_assignments FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON access.role_assignments
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON access.role_assignments")
    op.execute("DROP INDEX IF EXISTS access.ix_ra_ws_user")
    op.execute("DROP TABLE IF EXISTS access.role_assignments")
