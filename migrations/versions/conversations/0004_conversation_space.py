"""conversations: every conversation belongs to exactly one space (plan §3.2).

Adds ``conversations.conversations.space_id``, backfills it, and indexes it.

The full reasoning lives in ``files/0002_file_space.py``'s docstring -- why
the column is born NULLable and where the ``SET NOT NULL`` went (plan §4 row
8-b), why the backfill loops per workspace instead of switching RLS off, and
why all three chains resolve the SAME ``General`` space per workspace. The
last one matters most here: decision 1 says a space's conversations see all
of that space's files, so a conversation that migrated into a different space
than its workspace's files would come out of the migration able to see
nothing.

``conversation_files`` (the pin table) gets no column. A pin is a narrowing
INSIDE a space, and plan §3.5 makes "pin a file from another space" an
explicit rejection in the application, in step 7 -- not a second copy of the
same id in the database.

Operational ordering (DAT-03): ``depends_on`` stays ``None`` for
``0001_conversations.py``'s reason, but this revision READS ``spaces.spaces``
and ``workspace.workspaces``, so both chains must already be applied --
``app.ops.provision.MIGRATION_CHAINS`` encodes that order and
``tests/unit/test_ops_provision.py`` guards it.

Revision ID: 0004_conversation_space
Revises: 0003_conversation_files
"""

from __future__ import annotations

from alembic import op

revision = "0004_conversation_space"
down_revision = "0003_conversation_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations.conversations ADD COLUMN space_id uuid NULL;")

    op.execute(
        """
        DO $$
        DECLARE
            ws uuid;
            sp uuid;
        BEGIN
            FOR ws IN SELECT id FROM workspace.workspaces LOOP
                PERFORM set_config('app.workspace_id', ws::text, true);

                IF NOT EXISTS (
                    SELECT 1 FROM conversations.conversations WHERE space_id IS NULL
                ) THEN
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

                UPDATE conversations.conversations SET space_id = sp WHERE space_id IS NULL;
            END LOOP;

            PERFORM set_config('app.workspace_id', '', true);
        END
        $$;
        """
    )

    op.execute(
        "CREATE INDEX ix_conv_space ON conversations.conversations(space_id) "
        "WHERE deleted_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS conversations.ix_conv_space")
    op.execute("ALTER TABLE conversations.conversations DROP COLUMN IF EXISTS space_id;")
