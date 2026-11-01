"""files: the three classification tags of a file.

PLT-26 (docs/delivery/electrical-drawings/docs/lld-inc-1.md §4.1). A file may
carry up to three opaque reference tags -- ``system``, ``equipment`` and
``department`` -- chosen at registration and changed by ``File.retag``. They
are the second half of the same-name replacement rule (س-29 rule 1): two files
in a space replace one another only if the name AND all three tags match, an
absent tag matching only an absent one. Unlike ``upload_id``/``part_bytes``
(INV-F5, fixed at registration) the tags are mutable (INV-F6), so
``SqlFileRepository.save`` writes them next to ``name``.

**Expand-only.** Three nullable columns with no default are a catalogue-only
change on PostgreSQL 16 (no table rewrite), and the code that predates this
revision keeps working on the new schema: its ``Table`` does not name the
columns, and its INSERT leaves them NULL, which the CHECK below accepts. Every
existing row is therefore "untagged" -- exactly the rule as it was.

**The CHECK is ``NOT VALID`` and then validated**, the pattern of
``0005_file_multipart.py``. Caveat, stated plainly as it is there: both
statements run inside Alembic's one transaction, so the ``ACCESS EXCLUSIVE``
taken by the first is held until commit and the validating scan runs under it.
It is accepted because every new column is NULL on every existing row and the
table is bounded by ``max_files_per_workspace`` per tenant; the
``lock_timeout`` that ``app.ops.provision`` sets bounds the wait for the lock.

**The shape (INV-F6):** a tag is NULL (absent) or 1..128 characters. NULL is
the ONLY spelling of "absent" -- never ``''`` -- so the database refuses an
empty string from a writer that bypasses the aggregate (``FileTags`` already
refuses it). A CHECK passes on NULL, so each branch needs no explicit
``IS NOT NULL``. 128 is ``MAX_TAG_CHARS`` in
``modules/files/domain/value_objects.py``; ``char_length`` counts characters,
as ``len`` counts code points.

**No index (INC1-D-4).** The tag condition of ``live_namesakes`` is applied as
a filter after the lookup through ``ix_files_space_name (workspace_id,
space_id, name_key)`` (``0004_space_quota_covering.py``), which already narrows
to one or two rows. A fourth index sharing that prefix could steal the planner's
choice for the name lookup, the failure ``0004`` documents.
``test_the_name_key_is_an_index_condition_and_not_a_filter`` guards it, with
tagged files present. RLS and grants are unchanged -- ``tenant_isolation``
covers the whole row and ``app_rw`` holds table-level privileges.

**Downgrade loses data, explicitly:** dropping the columns discards every file's
tags. After it every file is "untagged" again and the same-name replacement
falls back to the name alone, so a later upload may replace a file that a tag
used to protect. Prefer leaving the columns in place on a rollback; they are
inert for the older code.
"""

from alembic import op

revision = "0006_file_tags"
down_revision = "0005_file_multipart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE files.files
          ADD COLUMN tag_system     text NULL,
          ADD COLUMN tag_equipment  text NULL,
          ADD COLUMN tag_department text NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE files.files ADD CONSTRAINT ck_files_tags_shape CHECK (
              (tag_system     IS NULL OR char_length(tag_system)     BETWEEN 1 AND 128)
          AND (tag_equipment  IS NULL OR char_length(tag_equipment)  BETWEEN 1 AND 128)
          AND (tag_department IS NULL OR char_length(tag_department) BETWEEN 1 AND 128)
        ) NOT VALID;
        """
    )
    op.execute("ALTER TABLE files.files VALIDATE CONSTRAINT ck_files_tags_shape;")


def downgrade() -> None:
    # DATA LOSS: every file's tags are discarded (module docstring).
    op.execute("ALTER TABLE files.files DROP CONSTRAINT IF EXISTS ck_files_tags_shape;")
    op.execute(
        "ALTER TABLE files.files DROP COLUMN IF EXISTS tag_department, "
        "DROP COLUMN IF EXISTS tag_equipment, DROP COLUMN IF EXISTS tag_system;"
    )
