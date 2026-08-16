"""knowledge: every document belongs to exactly one space (plan §3.2).

Adds ``knowledge.documents.space_id``, backfills it, and indexes it.

The full reasoning lives in ``files/0002_file_space.py``'s docstring -- why
the column is born NULLable and where the ``SET NOT NULL`` went (plan §4 row
8-b), why the backfill loops per workspace instead of switching RLS off, why
all three chains resolve the SAME ``General`` space per workspace, and why
this table does NOT derive its space from ``files.files`` through its own
``file_id`` even though that reads as the more precise answer (at migration
time it is the same value, and deriving it would make the ``knowledge`` chain
read the ``files`` chain).

**``ix_kndoc_space`` is NOT partial, and the plan's §3.2 table says it is.**
``knowledge.documents`` has no ``deleted_at`` column -- it is the one of the
three tables without soft delete (``0001_knowledge.py``) -- so
``WHERE deleted_at IS NULL`` would not compile. The plan carries a 📌
correction; this note is the other half of it, so the next reader of the
migration does not "fix" the index back into a syntax error.

Plan §5-a is a separate obligation this migration does NOT discharge: points
already in Qdrant carry no ``space`` payload key and no filter change here
makes them visible again. Re-indexing is the remedy, and it is step 9's and
§5-a's, not this file's.

Operational ordering (DAT-03): ``depends_on`` stays ``None`` for
``0001_knowledge.py``'s reason, but this revision READS ``spaces.spaces`` and
``workspace.workspaces``, so both chains must already be applied --
``app.ops.provision.MIGRATION_CHAINS`` encodes that order and
``tests/unit/test_ops_provision.py`` guards it.

Revision ID: 0004_document_space
Revises: 0003_summaries
"""

from __future__ import annotations

from alembic import op

revision = "0004_document_space"
down_revision = "0003_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge.documents ADD COLUMN space_id uuid NULL;")

    op.execute(
        """
        DO $$
        DECLARE
            ws uuid;
            sp uuid;
        BEGIN
            FOR ws IN SELECT id FROM workspace.workspaces LOOP
                PERFORM set_config('app.workspace_id', ws::text, true);

                IF NOT EXISTS (SELECT 1 FROM knowledge.documents WHERE space_id IS NULL) THEN
                    CONTINUE;
                END IF;

                SELECT id INTO sp FROM spaces.spaces
                 WHERE workspace_id = ws
                   AND lower(name) = 'general'
                   AND deleted_at IS NULL;

                IF sp IS NULL THEN
                    sp := gen_random_uuid();
                    INSERT INTO spaces.spaces (id, workspace_id, name)
                        VALUES (sp, ws, 'General');
                END IF;

                UPDATE knowledge.documents SET space_id = sp WHERE space_id IS NULL;
            END LOOP;

            PERFORM set_config('app.workspace_id', '', true);
        END
        $$;
        """
    )

    op.execute("CREATE INDEX ix_kndoc_space ON knowledge.documents(space_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS knowledge.ix_kndoc_space")
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS space_id;")
