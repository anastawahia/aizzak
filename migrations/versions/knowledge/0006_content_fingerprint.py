"""knowledge: the content fingerprint pair for the P-03 skip/rebuild rule
(rag-indexing-plan.md §3.6, decision س-14, plan step 15).

Adds ``knowledge.documents.content_hash``/``knowledge.documents.pipeline_version``.

**Both are born NULLable and stay that way** — not "until a backfill", as
some earlier columns on this table were, but permanently: a document that is
``pending``/``indexing``/``failed`` has genuinely never produced output to
fingerprint, and ``NULL`` is the honest answer for it, not a placeholder
waiting to be filled in. ``domain/pipeline.py::content_pipeline_unchanged``
already treats ``NULL`` (``None`` in Python) as "never equal to a real
value" on both columns, so no backfill loop is needed here the way
``0004_document_space.py``'s was — the empty column is itself the correct
answer for every row that predates this migration.

Written together, ONLY on the ``pending|indexing -> indexed`` transition
(``SqlDocumentRepository.set_status``, alongside the ``chunk_count`` refresh
that transition already does) — never on ``failed``, which produced nothing
to fingerprint, and never independently of that transition (mirrors
``Document.complete_indexing``'s own docstring in the domain layer).

A column-only migration: no new table, so none of ``app.ops.provision``'s
``_TENANT_TABLES``/``PURGE_GRANTS``, ``app.ops.purge``'s ``_SCHEMA_ORDER``, or
``tests/integration/conftest.py``'s TRUNCATE list need a new entry — all four
already name ``knowledge.documents`` from ``0001_knowledge.py`` onward, and
adding two columns to a table those already cover changes nothing they
enumerate (verified by reading each of the four, not assumed).

Operational ordering (DAT-03): ``depends_on`` stays ``None`` for
``0001_knowledge.py``'s reason — this revision touches only
``knowledge.documents``, which the platform baseline chain already created
the schema for.

Revision ID: 0006_content_fingerprint
Revises: 0005_parent_chunks
"""

from __future__ import annotations

from alembic import op

revision = "0006_content_fingerprint"
down_revision = "0005_parent_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge.documents ADD COLUMN content_hash text NULL;")
    op.execute("ALTER TABLE knowledge.documents ADD COLUMN pipeline_version integer NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS pipeline_version;")
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS content_hash;")
