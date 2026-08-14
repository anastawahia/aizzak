"""knowledge: document summaries + their build jobs (BE-RAG-009/010/011).

Adds ``knowledge.summaries`` + ``knowledge.summary_jobs`` (01-data-model
§2.7).

**A summary hangs off the DOCUMENT, not the file.** The obvious key is the
file — a file is what the user uploaded and what they can name — but a
summary is a statement about TEXT, and the text a summary was written from
belongs to one document. Re-indexing destroys the document it supersedes
(INV-K4) precisely because the file's bytes may now parse differently, and a
summary keyed on the file would survive that destruction and go on describing
a corpus that no longer exists, with no constraint anywhere able to notice.
``fk_summary_doc`` is what makes staleness structurally impossible: the
summary of a document that was rebuilt is gone, and the next read says so.

**The FK carries no ``ON DELETE``, deliberately** — the ``fk_chunk_doc``
precedent. ``DocumentRepository.purge`` deletes a document's summaries, then
its chunks, then its row, and the missing cascade is what makes that order
mandatory rather than stylistic. A cascade would perform the same deletion
while hiding it from the one place (INV-K4) that documents what re-indexing
destroys and in what order.

**``summary_jobs.document_id`` carries NO foreign key, and ``summaries``
does.** The asymmetry is the ``reindex_job_items`` one, for the same reason.
A summary IS a claim about a document and must not outlive it, so the FK is
what enforces that. A JOB is the record of an operation, and re-indexing may
legitimately destroy the document out from under a build that is running — an
FK there would forbid exactly the deletion ``purge`` exists to perform, and
the operation would have to be forgotten to let the rebuild proceed. The
build that then tries to store its result is stopped by the FK on
``summaries`` instead, which is where the wrong outcome actually is; the
worker turns that conflict into a failed job with a reason (see
``build_knowledge_summary_handler``) rather than a redelivery loop.

**``summary_jobs`` stores its status and its progress. That is a documented
divergence from INV-K5, not an oversight.** A ``reindex_job`` can derive
every number it reports because the corpus already records the answer: its
items point at documents, and those documents carry their own statuses. A
summary job has no such second witness — until it finishes there is no
summary row, and "map step 7 of 42" is a fact that exists nowhere but in the
worker running it. Deriving it would mean deriving it from nothing. So the
row counts, and the cost is stated where the counter lives: a worker that
dies between two map steps leaves ``done_chunks`` stale until the redelivery
guard restarts the job, and the number a client sees in that window is the
last one that was true rather than the one that is.

``uq_summary_job_active`` is the ``uq_cred_active_platform`` lesson applied
here: without a partial unique index over the non-terminal statuses, two
concurrent POSTs for the same (document, kind, lang) both queue, both
map-reduce the whole document on the workspace's budget, and both race to
write one ``uq_summary_key`` row — so one of them pays in full and then
fails at the last statement. The index turns that into a 409 before the
first token is spent.

``kind``/``lang`` are CHECK-constrained rather than left free text: they are
a closed vocabulary the client picks from a select box, and a typo that
reaches the table would mint a summary nobody can ever read back — the read
looks up an exact key.

Operational ordering (DAT-03): same as the rest of this chain — ``depends_on``
stays ``None`` and the platform baseline must be applied first as a runbook
step.

Revision ID: 0003_summaries
Revises: 0002_reindex_jobs
"""

from __future__ import annotations

from alembic import op

revision = "0003_summaries"
down_revision = "0002_reindex_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge.summaries (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          document_id  uuid NOT NULL,
          kind         text NOT NULL CHECK (kind IN ('overview','full')),
          lang         text NOT NULL CHECK (lang IN ('auto','ar','en')),
          text         text NOT NULL,
          model        text NOT NULL,
          source_chunks integer NOT NULL,
          truncated    boolean NOT NULL DEFAULT false,
          built_at     timestamptz NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          version      integer NOT NULL DEFAULT 1,
          CONSTRAINT fk_summary_doc
            FOREIGN KEY (document_id) REFERENCES knowledge.documents(id),
          CONSTRAINT uq_summary_key UNIQUE (document_id, kind, lang)
        );
        """
    )
    # `uq_summary_key` is also the only access path a read has ("this
    # document's overview in Arabic"), so no secondary index is needed for it.
    # This one serves the OTHER access path: the document list showing which
    # files already have a summary, which asks by workspace.
    op.execute("CREATE INDEX ix_summaries_ws ON knowledge.summaries(workspace_id, document_id);")

    op.execute(
        """
        CREATE TABLE knowledge.summary_jobs (
          id            uuid PRIMARY KEY,
          workspace_id  uuid NOT NULL,
          document_id   uuid NOT NULL,
          kind          text NOT NULL CHECK (kind IN ('overview','full')),
          lang          text NOT NULL CHECK (lang IN ('auto','ar','en')),
          status        text NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
          total_chunks  integer NOT NULL DEFAULT 0,
          done_chunks   integer NOT NULL DEFAULT 0,
          error         text,
          cancelled_at  timestamptz,
          finished_at   timestamptz,
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),
          version       integer NOT NULL DEFAULT 1
        );
        """
    )
    # One build at a time per summary key. See the module docstring: without
    # this, two concurrent requests both pay for the whole document and then
    # collide on `uq_summary_key` at the very last write.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_summary_job_active
          ON knowledge.summary_jobs(document_id, kind, lang)
          WHERE status IN ('queued','running');
        """
    )
    # `id DESC` for the same reason `ix_reindex_jobs_ws` has it: UUIDv7 is
    # time-ordered, and "this workspace's newest jobs" is how a job is found
    # when its id is not already in hand.
    op.execute("CREATE INDEX ix_summary_jobs_ws ON knowledge.summary_jobs(workspace_id, id DESC);")

    # touch trigger on BOTH: a summary is overwritten in place when it is
    # rebuilt, and a job is written on every progress step.
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON knowledge.summaries
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON knowledge.summary_jobs
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3, verbatim pattern) on both tenant-scoped tables -- born with
    # the NULLIF hardening, never retrofitted: on a pooled connection a GUC
    # that was ever SET LOCAL resets to '' rather than unset, and ''::uuid
    # raises 22P02 instead of failing closed.
    op.execute("ALTER TABLE knowledge.summaries ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE knowledge.summaries FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.summaries
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )

    op.execute("ALTER TABLE knowledge.summary_jobs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE knowledge.summary_jobs FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.summary_jobs
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge.summary_jobs")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge.summaries")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON knowledge.summary_jobs")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON knowledge.summaries")
    op.execute("DROP TABLE IF EXISTS knowledge.summary_jobs")
    op.execute("DROP TABLE IF EXISTS knowledge.summaries")
