"""knowledge: manual re-index jobs (BE-RAG-007/008).

Adds ``knowledge.reindex_jobs`` + ``knowledge.reindex_job_items``
(01-data-model §2.7).

**The job row stores no progress and no status.** It carries an identity and
a ``cancelled_at``, and everything a client reads — running/completed, the
percentage, which file is being processed right now — is DERIVED by joining
the item rows onto ``knowledge.documents`` and reading the status that is
already there (INV-K5). A stored counter would need a writer somewhere in the
worker, would need that writer to be transactional with the document's own
status change, and would still be wrong the first time a worker died between
the two. A derived one cannot disagree with the corpus it describes.

**``document_id`` is the NEW document, not the one being replaced.**
Re-indexing never resets a document (INV-K3): each target is superseded by a
brand-new ``pending`` row over the same ``file_id``, and the old one is
DESTROYED first — its Qdrant points, then its ``chunks`` rows, then its own
row (INV-K4). That destruction is not cleanup, it is the point: a Qdrant point
id is derived from the document id (``chunk_point_id(document_id, seq)``), so
leaving both documents alive would make the file answer every search twice,
forever.

``source_document_id`` therefore has NO foreign key. It is the id of a row
this operation deleted on purpose — a historical record of what was replaced —
and an FK would forbid exactly the deletion the feature exists to perform.
``document_id`` DOES get one: its parent is created in the same transaction,
in this schema and this chain, and the FK is what stops an item from
outliving the document whose status it reports.

``job_id`` leads the composite PRIMARY KEY, which is also the only access
path this table has ("the items of one job"), so it needs no secondary index.
A document can appear under two different jobs — re-indexing the same file
twice is legitimate — but never twice under one, which is what makes the
request's own de-duplication enforceable rather than merely intended.

No ``updated_at``/touch trigger on the items (they are written once and
never edited, the ``chunks`` precedent); the JOB does get both, because
``cancel`` writes to it.

Operational ordering (DAT-03): same as ``0001_knowledge.py`` — this chain's
``depends_on`` stays ``None`` and the platform baseline must be applied first
as a runbook step.

Revision ID: 0002_reindex_jobs
Revises: 0001_knowledge
"""

from __future__ import annotations

from alembic import op

revision = "0002_reindex_jobs"
down_revision = "0001_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge.reindex_jobs (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          cancelled_at timestamptz,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          version      integer NOT NULL DEFAULT 1
        );
        """
    )
    # `id DESC` matches how a job is ever looked up beyond its own id: the
    # newest ones for a workspace, UUIDv7 being time-ordered.
    op.execute("CREATE INDEX ix_reindex_jobs_ws ON knowledge.reindex_jobs(workspace_id, id DESC);")

    op.execute(
        """
        CREATE TABLE knowledge.reindex_job_items (
          job_id             uuid NOT NULL,
          document_id        uuid NOT NULL,
          workspace_id       uuid NOT NULL,
          file_id            uuid NOT NULL,
          source_document_id uuid NOT NULL,
          created_at         timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (job_id, document_id),
          CONSTRAINT fk_reindex_item_job
            FOREIGN KEY (job_id) REFERENCES knowledge.reindex_jobs(id),
          CONSTRAINT fk_reindex_item_doc
            FOREIGN KEY (document_id) REFERENCES knowledge.documents(id)
        );
        """
    )

    # touch trigger -- jobs only (`cancel` writes there); items are write-once.
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON knowledge.reindex_jobs
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3, verbatim pattern) on both tenant-scoped tables -- born with
    # the NULLIF hardening, never retrofitted: on a pooled connection a GUC
    # that was ever SET LOCAL resets to '' rather than unset, and ''::uuid
    # raises 22P02 instead of failing closed.
    op.execute("ALTER TABLE knowledge.reindex_jobs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE knowledge.reindex_jobs FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.reindex_jobs
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )

    op.execute("ALTER TABLE knowledge.reindex_job_items ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE knowledge.reindex_job_items FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.reindex_job_items
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge.reindex_job_items")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge.reindex_jobs")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON knowledge.reindex_jobs")
    op.execute("DROP TABLE IF EXISTS knowledge.reindex_job_items")
    op.execute("DROP TABLE IF EXISTS knowledge.reindex_jobs")
