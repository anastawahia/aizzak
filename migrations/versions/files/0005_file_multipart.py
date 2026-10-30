"""files: the storage-side session of a multipart (resumable) upload.

PLT-01 (docs/delivery/electrical-drawings/docs/lld-inc-0.md §4.1). A file above
the single-PUT ceiling is uploaded in parts straight to the object store, and
the server must remember two things about it between requests: the storage's
own ``upload_id`` and the ``part_bytes`` the parts were cut to. Both are fixed
at registration and never change afterwards (INV-F5), exactly like
``content_type`` and ``size_bytes`` -- so ``SqlFileRepository.save`` does not
write them.

**Expand-only.** Two nullable columns with no default are a catalogue-only
change on PostgreSQL 16 (no table rewrite), and the code that predates this
revision keeps working on the new schema: its ``Table`` does not name the
columns, and its INSERT leaves them NULL, which the CHECK below accepts.

**The CHECK is ``NOT VALID`` and then validated.** ``ADD CONSTRAINT ... CHECK``
takes ``ACCESS EXCLUSIVE`` and scans the whole table to prove it. Added
``NOT VALID`` it takes the lock only for the catalogue change; ``VALIDATE
CONSTRAINT`` then scans under ``SHARE UPDATE EXCLUSIVE``, which does not block
reads or writes. **Caveat, stated plainly:** both statements run inside
Alembic's one transaction, so the ``ACCESS EXCLUSIVE`` taken by the first is
held until commit and the validating scan runs under it -- the split buys
nothing here unless the two are committed separately. It is accepted because
the scan is over a table whose new columns are all NULL and which is bounded
by ``max_files_per_workspace`` per tenant; the ``lock_timeout`` that
``app.ops.provision`` sets bounds the wait for the lock itself.

The shape: both columns NULL (a single-PUT file), or both set with a
non-empty ``upload_id`` of at most 1024 characters and a ``part_bytes`` of at
least 5 MiB, the S3 minimum for every part but the last (``MIN_PART_BYTES`` in
``modules/files/domain/value_objects.py``). The explicit ``part_bytes IS NOT
NULL`` matters: a CHECK passes on NULL, so ``part_bytes >= 5242880`` alone
would accept an id with no part size (found by the E3 test, not by the LLD's
draft). The domain object enforces the same thing; this is the database's own
guarantee for a writer that bypasses the aggregate.

No index: nothing looks a file up by ``upload_id``; reads are by primary key.
RLS and grants are unchanged -- ``tenant_isolation`` covers the whole row and
``app_rw`` holds table-level privileges.

**Downgrade loses data.** Dropping the columns discards every in-flight
multipart upload's ``upload_id``; those uploads can then no longer be
completed (their rows stay ``uploaded`` until the owner deletes them, and the
object store reaps the orphaned parts). Redeploy the previous code first.

Revision ID: 0005_file_multipart
Revises: 0004_space_quota_covering
"""

from __future__ import annotations

from alembic import op

revision = "0005_file_multipart"
down_revision = "0004_space_quota_covering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE files.files
          ADD COLUMN upload_id  text    NULL,
          ADD COLUMN part_bytes integer NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE files.files ADD CONSTRAINT ck_files_multipart_shape CHECK (
          (upload_id IS NULL AND part_bytes IS NULL)
          OR (upload_id IS NOT NULL
              AND char_length(upload_id) BETWEEN 1 AND 1024
              AND part_bytes IS NOT NULL
              AND part_bytes >= 5242880)
        ) NOT VALID;
        """
    )
    op.execute("ALTER TABLE files.files VALIDATE CONSTRAINT ck_files_multipart_shape;")


def downgrade() -> None:
    # Loses every in-flight multipart upload's `upload_id` (module docstring).
    op.execute("ALTER TABLE files.files DROP CONSTRAINT IF EXISTS ck_files_multipart_shape;")
    op.execute(
        "ALTER TABLE files.files DROP COLUMN IF EXISTS part_bytes, DROP COLUMN IF EXISTS upload_id;"
    )
