"""media module: media_jobs (image/video generation job state).

Creates ``media.media_jobs`` (01-data-model §2.8), its RLS tenant-isolation
policy (01 §3), and the ``touch_updated_at`` trigger.

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same reason
as ``knowledge/0001_knowledge.py`` -- each module chain's applied revisions
are recorded in its OWN ``version_table_schema`` (DAT-03, run via
``-x vts=media``), so a single ``alembic upgrade`` invocation has no way to
inspect whether another chain's revision has already been applied against
this database; a cross-chain ``depends_on`` would be unenforceable here. The
platform baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=media upgrade media@head

Revision ID: 0001_media
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_media"
down_revision = None
branch_labels = ("media",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE media.media_jobs (
          id             uuid PRIMARY KEY,
          workspace_id   uuid NOT NULL,
          agent_key      text NOT NULL,
          kind           text NOT NULL CHECK (kind IN ('image','video')),
          prompt         text NOT NULL,
          params         jsonb NOT NULL DEFAULT '{}',
          status         text NOT NULL DEFAULT 'queued'
                           CHECK (status IN ('queued','running','succeeded','failed')),
          result_file_id uuid,
          error          text,
          created_by     uuid,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          version        integer NOT NULL DEFAULT 1
        );
        """
    )
    op.execute("CREATE INDEX ix_media_ws_status ON media.media_jobs(workspace_id, status);")

    # touch trigger (01 §0, shared platform.touch_updated_at()).
    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON media.media_jobs
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3 pattern, hardened here per the empty-string GUC discriminator
    # fix -- see rls-empty-string-hardening: a pooled connection that has
    # never had `app.workspace_id` SET LOCAL for it reads back '' (empty
    # string), not NULL, from `current_setting(..., true)`. The raw
    # `current_setting(...)::uuid` cast then raises `22P02 invalid input
    # syntax for type uuid` instead of failing safe to zero rows.
    # `NULLIF(..., '')` turns that empty string into SQL NULL first, so the
    # cast yields NULL and the `workspace_id = NULL` comparison is simply
    # false (zero rows, no exception) -- matching 01-data-model §3's stated
    # invariant ("`current_setting(..., true)` يعيد NULL عند غياب السياق ⇒
    # صفر صفوف") for both the missing-GUC AND empty-string-GUC cases.
    op.execute("ALTER TABLE media.media_jobs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE media.media_jobs FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON media.media_jobs
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON media.media_jobs")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON media.media_jobs")
    op.execute("DROP INDEX IF EXISTS media.ix_media_ws_status")
    op.execute("DROP TABLE IF EXISTS media.media_jobs")
