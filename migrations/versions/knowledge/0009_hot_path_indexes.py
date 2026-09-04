"""knowledge: the two predicates the synchronous RAG path issues and nothing indexed.

Capacity step 2.4 (``docs/capacity-plan.md`` §5, Wave 2 — index review by
``EXPLAIN (ANALYZE, BUFFERS)`` under a tenant's RLS context). Two indexes, both
found by running ``python -m app.ops.explain_hot_paths run`` against the Wave-0
seed (1,000,771 chunks · 100,004 documents · 200 workspaces) as ``app_rw``.

**``ix_chunks_ws_point`` — this is the one the acceptance criterion is written
about.** ``SqlDocumentRepository.parent_texts_for_chunk_ids`` (retrieval plan
§3.7, ``P-34``) is on the SYNCHRONOUS answer path: after Qdrant returns its
hits, this statement resolves each ``point_id`` to its parent chunk so the
answer can be widened. ``knowledge.chunks`` carried exactly two indexes —
``chunks_pkey(id)`` and ``uq_chunk_seq(document_id, seq)`` — and ``point_id``
was in neither. Measured on the seed, before::

    Gather  (actual time=97.533..100.298 rows=0)
      Workers Planned: 2  Workers Launched: 2
      Buffers: shared hit=229 read=125161
      ->  Parallel Seq Scan on chunks c  (rows=4 loops=3)
            Filter: ((workspace_id = …) AND (point_id = ANY (…)))
            Rows Removed by Filter: 333586
    Execution Time: 111.441 ms

A **parallel sequential scan of the whole table** — 1,000,758 rows and ~980 MB
of buffers — to return the fifty rows a retrieval asked for, and it recruits
two extra workers from the pool to do it. §0 targets 40 RAG queries per second
at peak; this one statement would ask the server for forty million rows a
second. After::

    Index Scan using ix_chunks_ws_point on chunks c  (rows=12)
      Buffers: shared hit=39 read=12
    Execution Time: 0.142 ms

785x on time, 2,460x on buffers, and the two stolen workers go back.

**``ix_doc_ws_file`` — the same defect one table over, without the Seq Scan to
announce it.** ``SqlDocumentRepository.ids_for_files`` resolves a caller's file
ids to document ids on two paths: file-scoped retrieval (before the vector
search) and the duplicate check on every document registration. The only index
whose leading column matched was ``ix_doc_ws_status(workspace_id, status)``, so
the planner scanned the tenant's ENTIRE document set and filtered::

    Bitmap Heap Scan on documents  (actual time=2.309..7.851 rows=20)
      Filter: (file_id = ANY (…))
      Rows Removed by Filter: 16992
      Buffers: shared hit=1 read=325
    Execution Time: 7.881 ms

17,012 rows read for 20 returned. That is an INDEX scan, and it is the reason
2.4's acceptance criterion ("no ``Seq Scan`` on a tenant table in the hot
path") is the weaker half of what ``explain_hot_paths`` checks: a guard looking
only for ``Seq Scan`` passes this plan without a word. After: ``Index Scan
using ix_doc_ws_file``, 20 rows read, 80 buffers, 0.080 ms — 99x.

**Why ``workspace_id`` leads both keys** — the same reason ``ix_doc_ws_status``
and ``ix_conv_ws_agent`` have it there. Every read on these tables carries the
tenant (DD-04 layer 2, enforced in every adapter), and the RLS policy filters
on it first, so a key that does not lead with it cannot be used as a search key
for the whole predicate. It is also what keeps one tenant's index pages
contiguous rather than interleaved with 199 others'.

**Neither index is UNIQUE, and ``point_id`` in particular is not.** It is a
Qdrant point id minted by the application, and a unique constraint here would
assert an invariant this schema does not hold (``INV-K3`` already refuses one
on ``(workspace_id, file_id)`` for documents — a file legitimately has more
than one document across re-indexing). An index is an access path; a constraint
is a promise, and only one of the two was measured.

**Lock cost, stated rather than assumed.** ``CREATE INDEX`` (not
``CONCURRENTLY``) takes a ``SHARE`` lock that blocks writes to the table for
its duration — on the seeded database, measured at **2.1 seconds for both
indexes together**. ``CONCURRENTLY`` cannot run inside a transaction and
Alembic runs each revision in one; the online path is capacity step **2.9**
(``expand/contract``, ``lock_timeout``, the advisory lock around
``app.ops.provision``), which has not landed. Until it does, this is a brief
write pause during a deploy, taken knowingly and recorded here rather than
discovered.

Revision ID: 0009_hot_path_indexes
Revises: 0008_parent_chunk_complete
"""

from __future__ import annotations

from alembic import op

revision = "0009_hot_path_indexes"
down_revision = "0008_parent_chunk_complete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_chunks_ws_point
          ON knowledge.chunks(workspace_id, point_id);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_doc_ws_file
          ON knowledge.documents(workspace_id, file_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS knowledge.ix_doc_ws_file")
    op.execute("DROP INDEX IF EXISTS knowledge.ix_chunks_ws_point")
