"""files module: files (shared, workspace-scoped file descriptors).

Creates ``files.files`` (01-data-model §2.6), its ``touch_updated_at``
trigger, and the ``tenant_isolation`` RLS policy (01 §3).

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same
reason as ``knowledge/0001_knowledge.py``/``media/0001_media.py`` -- each
module chain's applied revisions are recorded in its OWN
``version_table_schema`` (DAT-03, run via ``-x vts=files``), so a single
``alembic upgrade`` invocation has no way to inspect whether another
chain's revision has already been applied against this database; a
cross-chain ``depends_on`` would be unenforceable here. The platform
baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=files upgrade files@head

Revision ID: 0001_files
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_files"
down_revision = None
branch_labels = ("files",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE files.files (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
          content_type text NOT NULL,
          size_bytes   bigint NOT NULL CHECK (size_bytes >= 0),
          storage_key  text NOT NULL UNIQUE,
          checksum     text,
          status       text NOT NULL DEFAULT 'uploaded'
                         CHECK (status IN ('uploaded','scanning','ready','quarantined')),
          uploaded_by  uuid,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          deleted_at   timestamptz NULL,
          version      integer NOT NULL DEFAULT 1
        );
        """
    )
    op.execute("CREATE INDEX ix_files_ws ON files.files(workspace_id) WHERE deleted_at IS NULL;")

    # touch trigger (01 §0, shared platform.touch_updated_at()).
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON files.files
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3 pattern, hardened NULLIF form -- see media/0001_media.py /
    # rls-empty-string-hardening).
    op.execute("ALTER TABLE files.files ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE files.files FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON files.files
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON files.files")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON files.files")
    op.execute("DROP INDEX IF EXISTS files.ix_files_ws")
    op.execute("DROP TABLE IF EXISTS files.files")
