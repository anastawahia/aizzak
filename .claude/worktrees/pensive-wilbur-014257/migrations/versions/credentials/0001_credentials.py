"""credentials module: credentials (provider API keys, platform or user-scoped).

Creates ``credentials.credentials`` (01-data-model §2.3), its
``touch_updated_at`` trigger, the ``tenant_isolation`` RLS policy (01 §3),
and the ``platform_credentials_read`` SELECT-only policy for
``scope='platform'`` rows (01 §3.1).

Deviation from 01 §2.3, documented here (R4 -- flagged as a doc-sync
recommendation for 01-data-model.md §2.3): ADDS ``uq_cred_active_user``, a
unique index on ``(workspace_id, provider) WHERE status = 'active' AND
scope = 'user'``, alongside the doc's own (kept verbatim, non-unique)
``ix_cred_ws_provider``. Without it, two concurrent ``AddUserCredential``
calls for the same provider can both pass the application-level
``find_active`` check before either commits (TOCTOU), and both insert a
second active row. The unique index turns that race into a clean ``23505``
-> ``ConflictError`` (409) at the database, which ``AddUserCredential``
already treats as its own invariant ("an active credential for this
provider already exists") -- it just moves the enforcement from "usually
true" to "always true". Platform rows (``scope = 'platform'``) fall outside
this index's partial predicate and stay unconstrained by it.

Operational ordering (DAT-03, 12-module-authoring-guide §4 binding point 3):
this chain's ``depends_on`` is deliberately left ``None`` for the same
reason as ``knowledge/0001_knowledge.py``/``media/0001_media.py`` -- each
module chain's applied revisions are recorded in its OWN
``version_table_schema`` (DAT-03, run via ``-x vts=credentials``), so a
single ``alembic upgrade`` invocation has no way to inspect whether another
chain's revision has already been applied against this database; a
cross-chain ``depends_on`` would be unenforceable here. The platform
baseline chain MUST be applied first as an operational runbook step
(08-local-runbook), not something Alembic verifies for us:

    alembic upgrade platform@head
    alembic -x vts=credentials upgrade credentials@head

Revision ID: 0001_credentials
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_credentials"
down_revision = None
branch_labels = ("credentials",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE credentials.credentials (
          id             uuid PRIMARY KEY,
          workspace_id   uuid NULL,
          provider       text NOT NULL,
          scope          text NOT NULL CHECK (scope IN ('platform','user')),
          label          text,
          ciphertext_ref text NOT NULL,
          key_id         text NOT NULL,
          status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
          created_by     uuid,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          version        integer NOT NULL DEFAULT 1,
          CONSTRAINT ck_scope_ws CHECK ((scope='user' AND workspace_id IS NOT NULL)
                                     OR (scope='platform' AND workspace_id IS NULL))
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_cred_ws_provider ON credentials.credentials(workspace_id, provider) "
        "WHERE status='active';"
    )
    # R4 hardening (deviation, doc-sync recommended -- see module docstring).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_cred_active_user ON credentials.credentials (workspace_id, provider)
            WHERE status = 'active' AND scope = 'user';
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_touch BEFORE UPDATE ON credentials.credentials
            FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
        """
    )

    # RLS (01 §3 pattern, hardened NULLIF form -- see media/0001_media.py /
    # rls-empty-string-hardening).
    op.execute("ALTER TABLE credentials.credentials ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE credentials.credentials FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON credentials.credentials
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
        """
    )
    # 01 §3.1: SELECT-only escape hatch for shared platform-level keys, OR'd
    # on top of `tenant_isolation` (RLS policies combine with OR) -- never a
    # write path. `workspace_id IS NULL` keeps this from ever leaking another
    # tenant's own rows.
    op.execute(
        """
        CREATE POLICY platform_credentials_read ON credentials.credentials
          FOR SELECT
          USING (workspace_id IS NULL AND scope = 'platform');
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS platform_credentials_read ON credentials.credentials")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON credentials.credentials")
    op.execute("DROP TRIGGER IF EXISTS trg_touch ON credentials.credentials")
    op.execute("DROP INDEX IF EXISTS credentials.uq_cred_active_user")
    op.execute("DROP INDEX IF EXISTS credentials.ix_cred_ws_provider")
    op.execute("DROP TABLE IF EXISTS credentials.credentials")
