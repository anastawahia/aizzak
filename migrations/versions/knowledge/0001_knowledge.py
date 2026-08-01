"""knowledge module: documents + chunks (RAG ingestion/indexing state).

Creates ``knowledge.documents``/``knowledge.chunks`` (01-data-model §2.7),
their RLS tenant-isolation policies (01 §3), and the ``touch_updated_at``
trigger on ``documents`` (``chunks`` rows are append-only -- no ``updated_at``
column, so no trigger there).

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` even though it
needs the ``knowledge`` schema and ``platform.touch_updated_at()`` created by
the platform baseline chain (``platform/0001_baseline_platform.py``) --
Alembic records each module chain's applied revisions in its OWN
``version_table_schema`` (DAT-03, run via ``-x vts=knowledge``), so a single
``alembic upgrade`` invocation has no way to inspect whether another chain's
revision has already been applied against this database; a cross-chain
``depends_on`` would be unenforceable here (and Alembic's dependency
resolution only works within one shared version table). The platform
baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=knowledge upgrade knowledge@head

Revision ID: 0001_knowledge
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_knowledge"
down_revision = None
branch_labels = ("knowledge",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge.documents (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          file_id      uuid NOT NULL,
          status       text NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','indexing','indexed','failed')),
          chunk_count  integer NOT NULL DEFAULT 0,
          error        text,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          version      integer NOT NULL DEFAULT 1
        );
        """
    )
    op.execute("CREATE INDEX ix_doc_ws_status ON knowledge.documents(workspace_id, status);")

    op.execute(
        """
        CREATE TABLE knowledge.chunks (
          id           uuid PRIMARY KEY,
          document_id  uuid NOT NULL,
          workspace_id uuid NOT NULL,
          seq          integer NOT NULL,
          text         text NOT NULL,
          token_count  integer,
          collection   text,
          point_id     uuid,
          created_at   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT fk_chunk_doc FOREIGN KEY (document_id) REFERENCES knowledge.documents(id),
          CONSTRAINT uq_chunk_seq UNIQUE (document_id, seq)
        );
        """
    )

    # touch trigger -- documents only (01 §0); chunks has no updated_at.
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON knowledge.documents
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3, verbatim pattern) on both tenant-scoped tables.
    op.execute("ALTER TABLE knowledge.documents ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE knowledge.documents FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.documents
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )

    op.execute("ALTER TABLE knowledge.chunks ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE knowledge.chunks FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.chunks
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge.chunks")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge.documents")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON knowledge.documents")
    op.execute("DROP TABLE IF EXISTS knowledge.chunks")
    op.execute("DROP TABLE IF EXISTS knowledge.documents")
