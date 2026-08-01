"""memory module: memory_items (per-agent semantic/episodic memory).

Creates ``memory.memory_items`` (01-data-model §2.5) and its
``tenant_isolation`` RLS policy (01 §3). NO touch trigger here -- the table
carries no ``updated_at`` column (access precedent, 01 §2.5): rows are
append + soft-delete only, so there is nothing for
``platform.touch_updated_at()`` to touch.

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same
reason as ``knowledge/0001_knowledge.py``/``media/0001_media.py`` -- each
module chain's applied revisions are recorded in its OWN
``version_table_schema`` (DAT-03, run via ``-x vts=memory``), so a single
``alembic upgrade`` invocation has no way to inspect whether another
chain's revision has already been applied against this database; a
cross-chain ``depends_on`` would be unenforceable here. The platform
baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=memory upgrade memory@head

Revision ID: 0001_memory
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_memory"
down_revision = None
branch_labels = ("memory",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory.memory_items (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          agent_key    text NOT NULL,
          kind         text NOT NULL CHECK (kind IN ('semantic','episodic')),
          content      text NOT NULL,
          collection   text,
          point_id     uuid,
          salience     real NOT NULL DEFAULT 0,
          created_at   timestamptz NOT NULL DEFAULT now(),
          deleted_at   timestamptz NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_mem_ws_agent ON memory.memory_items(workspace_id, agent_key) "
        "WHERE deleted_at IS NULL;"
    )

    # RLS (01 §3 pattern, hardened NULLIF form -- see media/0001_media.py /
    # rls-empty-string-hardening).
    op.execute("ALTER TABLE memory.memory_items ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE memory.memory_items FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON memory.memory_items
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON memory.memory_items")
    op.execute("DROP INDEX IF EXISTS memory.ix_mem_ws_agent")
    op.execute("DROP TABLE IF EXISTS memory.memory_items")
