"""knowledge: parent chunks for the P-14 retrieval-widening join
(rag-indexing-plan.md §3.2, step 6).

Adds ``knowledge.parent_chunks`` and ``knowledge.chunks.parent_id``.

**Why a new table and not a column on ``chunks``.** ``SourceSegment`` is the
parent chunk naturally — ``chunk_segments`` builds its word-windows INSIDE
one segment and a window never crosses a segment boundary (plan §3.2's
"البنيوي" note) — so the whole feature needs is one row per segment holding
its text, plus a pointer from every ``chunks`` row that was cut from it. A
column added to ``chunks`` itself cannot hold that: ``chunks`` is one row per
INDEXED WINDOW (h-8, ``0001_knowledge.py``), and a segment ordinarily yields
several windows, so the segment's text would either repeat once per window
(the exact "N-times blow-up" alpha's own note warns against, plan §3.2 clause
1) or force a self-referential parent/child split of the same table that the
plan explicitly rejected (plan §3.2 clause 2: "الجدول جديد ولا يُخلَط
بـ``chunks``" — ``chunks`` stays append-only, one row per Qdrant point, so
``chunk_count`` and ``UNIQUE(document_id, seq)`` keep meaning exactly what
they say today).

**The three inviolable constraints from the plan, carried into the DDL
verbatim:**

1. Qdrant's payload carries NEITHER a parent chunk's text NOR its id (an
   indexing-time decision this migration does not enforce, but the schema
   below is what makes carrying neither sufficient). Injecting the parent
   TEXT into every point is the N-way storage blow-up alpha's own comment
   warns against; the same reasoning drops even the bare id along with it,
   because duplicating it into every point buys nothing that
   ``chunk_id -> chunks.parent_id -> parent_chunks`` — one SQL join from a
   key the payload already holds — does not already reach. Parent expansion
   at retrieval time is therefore a REPOSITORY lookup (plan §3.2 constraint
   1, retrieval plan §3.7 `P-34`), never a payload read. See
   ``domain/entities.py::Chunk.parent_id``, which states the same rule at
   the other end of the same decision.
2. ``parent_chunks`` is a genuinely separate table from ``chunks`` — see
   above.
3. ``fk_parent_chunk_doc`` carries ``ON DELETE CASCADE`` on ``document_id``
   for exactly one reason: so ``SqlDocumentRepository.purge``'s existing
   three-statement sequence (summaries, then chunks, then the document row —
   ``fk_chunk_doc``/``fk_summary_doc`` have NO cascade, and that absence is
   what makes the ORDER load-bearing, see ``0001_knowledge.py`` /
   ``0003_summaries.py``) stays correct **without gaining a fourth
   statement**. ``chunks`` is always deleted explicitly, before the
   document row, so by the time Postgres reaches the document's own delete
   no ``chunks`` row still points at a ``parent_chunks`` row — the CASCADE
   on ``document_id`` then removes the now-unreferenced parent rows as part
   of that same final delete, and ``purge``/``purge_space`` need no new line
   to stay correct.

``chunks.parent_id`` carries NO ``ON DELETE`` of its own (the ``fk_chunk_doc``
precedent, same file): a parent row is never deleted on its own — only ever
as part of its whole document going, via the cascade above — so there is no
independent parent deletion for a child row to react to.

RLS is born with the NULLIF hardening (the ``0002``/``0003`` precedent),
never retrofitted: on a pooled connection a GUC that was ever ``SET LOCAL``
resets to ``''`` rather than unset, and ``''::uuid`` raises ``22P02`` instead
of failing closed.

Operational ordering (DAT-03): same as the rest of this chain —
``depends_on`` stays ``None`` and the platform baseline must already be
applied, as a runbook step.

Revision ID: 0005_parent_chunks
Revises: 0004_document_space
"""

from __future__ import annotations

from alembic import op

revision = "0005_parent_chunks"
down_revision = "0004_document_space"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge.parent_chunks (
          id           uuid PRIMARY KEY,
          document_id  uuid NOT NULL,
          workspace_id uuid NOT NULL,
          seq          integer NOT NULL,
          text         text NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT fk_parent_chunk_doc
            FOREIGN KEY (document_id) REFERENCES knowledge.documents(id) ON DELETE CASCADE,
          CONSTRAINT uq_parent_seq UNIQUE (document_id, seq)
        );
        """
    )

    op.execute(
        """
        ALTER TABLE knowledge.chunks
          ADD COLUMN parent_id uuid NULL
            REFERENCES knowledge.parent_chunks(id);
        """
    )

    # RLS (01 §3, verbatim pattern) -- born with the NULLIF hardening (see
    # module docstring).
    op.execute("ALTER TABLE knowledge.parent_chunks ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE knowledge.parent_chunks FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.parent_chunks
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    # `chunks.parent_id` first -- it references `parent_chunks(id)`, so the
    # table cannot go while the column still points at it.
    op.execute("ALTER TABLE knowledge.chunks DROP COLUMN IF EXISTS parent_id;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge.parent_chunks")
    op.execute("DROP TABLE IF EXISTS knowledge.parent_chunks")
