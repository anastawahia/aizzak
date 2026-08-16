"""files: every file belongs to exactly one space (docs/spaces-backend-plan.md §3.2).

Adds ``files.files.space_id``, backfills it, and indexes it.

**The column is born NULLable, and the plan says ``NOT NULL``.** That is a
deliberate, recorded split, not a weakening of decision 1 ("لا حالة يتيمة").
Nothing writes ``space_id`` yet -- ``SqlFileRepository.add`` learns about it in
plan step 6, ``conversations`` in step 7, ``knowledge`` in step 8 -- so a
``NOT NULL`` column landing here would make every INSERT on this table fail
with ``23502`` from this migration until step 8 lands. Plan §0 binds EVERY
step to the five gates, so the tightening moves to its own step (§4 row 8-b),
which is a plain ``SET NOT NULL`` with no backfill of its own: a NULL found
there is a writer steps 6-8 forgot, and failing the migration is exactly the
right way to hear about it.

**The backfill never turns RLS off.** ``aizzak_owner`` is ``NOSUPERUSER`` and
not ``BYPASSRLS``, and this table is ``FORCE ROW LEVEL SECURITY`` -- so the
migrator is a SUBJECT of ``tenant_isolation`` like anyone else, and a bare
``UPDATE files.files SET space_id = ...`` here would silently touch ZERO rows.
The two ways out are turning the guard off for a moment
(``ALTER TABLE ... NO FORCE``) or doing what the application does: set
``app.workspace_id`` and work one workspace at a time. This takes the second.
A migration that switches RLS off is one edit away from never switching it
back on, and the loop costs one ``FOR``.

Enumerating the workspaces is possible only because ``workspace.workspaces``
carries NO RLS at all, and that too is by design rather than by luck
(``workspace/0001_workspace.py`` R2: the tenant root has no ``workspace_id``
to isolate on). Every other table this file touches is invisible until the
GUC names a workspace, which is why the loop drives off that one table.

**One space per workspace, shared by all three chains.** ``conversations/
0004_conversation_space.py`` and ``knowledge/0004_document_space.py`` run the
same block against their own tables and resolve the SAME space by name, so a
workspace's migrated files and its migrated conversations land TOGETHER.
Decision 1 says a space's conversations see all of that space's files; two
default spaces would migrate a workspace into a state where its conversations
can see none of its files.

That equivalence is also why ``knowledge.documents`` does not derive its space
from ``files.files.space_id`` via its ``file_id``, which at first looks more
precise: at migration time every file in a workspace is in that workspace's
one default space, so the derived answer and this one are the same value, and
this one does not make the ``knowledge`` chain read the ``files`` chain.

The space is named ``General`` and its id is a v4 UUID (SQL has no uuid7
generator; the application's ``new_uuid7`` is not reachable from here). Both
are cosmetic and both are the user's to change: the name is renameable --
``ux_spaces_ws_name`` is on ``lower(name)`` among live rows, so renaming frees
it -- and the id only decides where this one row sorts in a list ordered by
``id DESC``, which needs a stable total order, not a chronological one.

The ``trg_touch`` trigger is left ENABLED, so backfilled rows get a fresh
``updated_at``. Disabling it would be more faithful (a backfill is not a user
edit) but a ``DISABLE TRIGGER`` that loses its matching ``ENABLE`` in a later
edit costs a real invariant, while the cosmetic timestamp costs a sort nobody
performs -- ``ListFiles`` orders by ``id DESC``.

Operational ordering (DAT-03): this chain's ``depends_on`` stays ``None`` for
the reason ``0001_files.py`` gives -- each chain records its revisions in its
own ``version_table_schema``, so a cross-chain ``depends_on`` is
unenforceable. What IS new here is that this revision READS two other chains'
tables, so ``spaces@head`` and ``workspace@head`` must already be applied.
``app.ops.provision.MIGRATION_CHAINS`` encodes that order and
``tests/unit/test_ops_provision.py`` guards it.

Revision ID: 0002_file_space
Revises: 0001_files
"""

from __future__ import annotations

from alembic import op

revision = "0002_file_space"
down_revision = "0001_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE files.files ADD COLUMN space_id uuid NULL;")

    op.execute(
        """
        DO $$
        DECLARE
            ws uuid;
            sp uuid;
        BEGIN
            FOR ws IN SELECT id FROM workspace.workspaces LOOP
                PERFORM set_config('app.workspace_id', ws::text, true);

                IF NOT EXISTS (SELECT 1 FROM files.files WHERE space_id IS NULL) THEN
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

                UPDATE files.files SET space_id = sp WHERE space_id IS NULL;
            END LOOP;

            -- Leave no tenant behind: Alembic may run several revisions in one
            -- transaction, and a `set_config(..., is_local => true)` survives
            -- until COMMIT. An empty value is the safe one -- the NULLIF form
            -- of `tenant_isolation` reads it as "no workspace", i.e. no rows.
            PERFORM set_config('app.workspace_id', '', true);
        END
        $$;
        """
    )

    op.execute("CREATE INDEX ix_files_space ON files.files(space_id) WHERE deleted_at IS NULL;")


def downgrade() -> None:
    # The `General` spaces this migration may have created are deliberately
    # NOT removed: they are ordinary rows a user can already have opened,
    # renamed or filed things into, and a downgrade that deletes user-visible
    # content is worse than one that leaves a space behind.
    op.execute("DROP INDEX IF EXISTS files.ix_files_space")
    op.execute("ALTER TABLE files.files DROP COLUMN IF EXISTS space_id;")
