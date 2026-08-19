"""knowledge: explicit per-kind chunk statistics for the P-05 file-stats
rule (rag-indexing-plan.md §4 step 16, decision س-15 = أ).

Adds ``knowledge.documents.text_chunks``/``.table_chunks``/``.image_chunks``.

**Born ``NOT NULL DEFAULT 0`` — the ``chunk_count`` precedent on this exact
table (``0001_knowledge.py``), not the NULLable ``content_hash``/
``pipeline_version`` precedent of ``0006_content_fingerprint.py``.** The
difference is deliberate: a document that has not (yet) produced a text/
table/image chunk genuinely has ZERO of each, which is a countable fact
``0`` states correctly — unlike the fingerprint pair, where ``NULL`` means
"never indexed" and ``0``/``""`` would be a lie about a real value. No
backfill loop needed for the same reason ``0006`` needed none: every
pre-existing row's true breakdown IS ``0`` until it is (re)indexed under the
code that writes these three columns.

Written together, ONLY on the ``pending|indexing -> indexed`` transition
(``SqlDocumentRepository.set_status``, alongside ``chunk_count`` and the
``0006`` fingerprint pair) — the three numbers always sum to that same
``chunk_count`` (``application/indexing.py::IndexOutcome``'s own docstring).

A column-only migration: the same verification ``0006_content_fingerprint.py``
already recorded applies unchanged — ``app.ops.provision``'s
``_TENANT_TABLES``/``PURGE_GRANTS``, ``app.ops.purge``'s ``_SCHEMA_ORDER``, and
``tests/integration/conftest.py``'s TRUNCATE list all already name
``knowledge.documents`` and need no new entry for two more columns on it.

Operational ordering (DAT-03): ``depends_on`` stays ``None``, the
``0001_knowledge.py`` reason.

Revision ID: 0007_chunk_stats
Revises: 0006_content_fingerprint
"""

from __future__ import annotations

from alembic import op

revision = "0007_chunk_stats"
down_revision = "0006_content_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge.documents ADD COLUMN text_chunks integer NOT NULL DEFAULT 0;")
    op.execute(
        "ALTER TABLE knowledge.documents ADD COLUMN table_chunks integer NOT NULL DEFAULT 0;"
    )
    op.execute(
        "ALTER TABLE knowledge.documents ADD COLUMN image_chunks integer NOT NULL DEFAULT 0;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS image_chunks;")
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS table_chunks;")
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS text_chunks;")
