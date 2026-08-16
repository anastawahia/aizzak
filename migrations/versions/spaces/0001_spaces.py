"""spaces module: spaces (workspace-scoped units owning files + conversations).

Creates ``spaces.spaces`` (docs/spaces-backend-plan.md §3.2), its partial
unique index, its ``touch_updated_at`` trigger, and the ``tenant_isolation``
RLS policy (01-data-model §3) -- the ``files/0001_files.py`` template applied
literally, with the hardened ``NULLIF`` policy form.

The schema itself is NOT created here but by
``platform/0004_spaces_schema.py``; that file's docstring records why (this
chain's ``spaces.alembic_version`` is created by Alembic before any of the SQL
below runs).

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same reason
as ``files/0001_files.py``'s -- each chain's applied revisions live in its own
``version_table_schema``, so a cross-chain ``depends_on`` would be
unenforceable. The platform baseline chain MUST be applied first as an
operational runbook step:

    alembic upgrade platform@head
    alembic -x vts=spaces upgrade spaces@head

**RLS stays on ``workspace_id`` alone, and that is a decision, not an
omission** (plan §3.2): the workspace is derived from the authenticated
IDENTITY, while the space is chosen by the REQUEST. Putting a
request-chosen value into a security GUC adds no security -- the application
sets it either way -- and converts an ordinary filtering bug into a silent
hole. Filtering by space belongs in the repository's ``WHERE``.

Revision ID: 0001_spaces
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_spaces"
down_revision = None
branch_labels = ("spaces",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE spaces.spaces (
          id           uuid PRIMARY KEY,
          workspace_id uuid NOT NULL,
          name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
          created_by   uuid,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          deleted_at   timestamptz NULL,
          version      integer NOT NULL DEFAULT 1
        );
        """
    )

    # Uniqueness is CASE-INSENSITIVE and applies to LIVE rows only: two spaces
    # named "Research" and "research" in one workspace are the same name to a
    # human, and a soft-deleted space must not hold its name hostage forever.
    # This index is also the module's whole duplicate-name defence -- the
    # application deliberately does not pre-read (a read-then-insert pair is
    # wrong exactly when two requests race), so `23505` from here is what
    # becomes `spaces.duplicate_name` (§3.139).
    op.execute(
        """
        CREATE UNIQUE INDEX ux_spaces_ws_name
          ON spaces.spaces(workspace_id, lower(name)) WHERE deleted_at IS NULL;
        """
    )

    # touch trigger (01 §0, shared platform.touch_updated_at()).
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON spaces.spaces
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3 pattern, hardened NULLIF form -- see files/0001_files.py /
    # rls-empty-string-hardening: without NULLIF, an EMPTY `app.workspace_id`
    # raises `invalid input syntax for type uuid` instead of matching nothing).
    op.execute("ALTER TABLE spaces.spaces ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE spaces.spaces FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON spaces.spaces
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON spaces.spaces")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON spaces.spaces")
    op.execute("DROP INDEX IF EXISTS spaces.ux_spaces_ws_name")
    op.execute("DROP TABLE IF EXISTS spaces.spaces")
