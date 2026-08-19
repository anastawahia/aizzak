"""knowledge: mark whether a parent chunk holds every chunk under it.

Adds ``knowledge.parent_chunks.is_complete``.

**Why a stored bit and not a guess.** ``domain/tables.py``'s row-count
ladder (P-13, rag-indexing-plan.md §3.3) mints TWO shapes of parent from the
same code path: a table of at most ``TABLE_PARENT_MAX_ROWS`` rows becomes a
parent holding every one of its rows, while a larger table becomes a parent
holding the HEADER LINE ALONE -- column names, not one value under them.
P-42 (§4 step 18, §3.10) then lets a parent stand IN PLACE OF the chunks
under it when building summariser input, which is correct for the first
shape and silently destroys the second's content: a 30-row sheet entering
the summariser as ``"Name; Salary; Dept"``. The two steps are each right on
their own; only their interaction is wrong, and the missing fact -- which
rung minted this row -- is known exactly once, at explosion time. Storing it
is the only way the reader can know it; recovering it later from ``text``
alone (does it contain a newline? a colon?) would be a guess that a
single-row table or a colon in a heading breaks.

**Born ``NOT NULL DEFAULT true`` -- the ``0007_chunk_stats.py`` precedent,
with its reasoning inverted where the data demands.** ``true`` is the shape
plan §3.2 describes for a parent chunk in general (a whole
``SourceSegment``, complete by construction); the header-only parent is the
exception P-13 introduced. The default is therefore right for every writer
that does not know about this column -- and, deliberately, is NOT a claim
about pre-existing rows: an already-written header-only parent would keep
answering ``true``. That is acceptable here for one checkable reason only --
the environment holds no indexed content (every development document was
purged 2026-08-16) -- and it is why no backfill ``UPDATE`` ships with this
migration: none could be honest. Should real rows ever predate this column,
the fix is a re-index (INV-K4 mints brand-new rows), never a guess written
over old ones.

A column-only migration: ``app.ops.provision``'s ``_TENANT_TABLES``/
``PURGE_GRANTS``, ``app.ops.purge``'s ``_SCHEMA_ORDER`` and
``tests/integration/conftest.py``'s TRUNCATE list all already name
``knowledge.parent_chunks`` (``0005_parent_chunks.py``) and need no entry for
one more column on it. RLS is likewise untouched: the tenant policy is on
the table, not per column.

Operational ordering (DAT-03): ``depends_on`` stays ``None``, the
``0001_knowledge.py`` reason.

Revision ID: 0008_parent_chunk_complete
Revises: 0007_chunk_stats
"""

from __future__ import annotations

from alembic import op

revision = "0008_parent_chunk_complete"
down_revision = "0007_chunk_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge.parent_chunks ADD COLUMN is_complete boolean NOT NULL DEFAULT true;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge.parent_chunks DROP COLUMN IF EXISTS is_complete;")
