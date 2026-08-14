"""Let the platform-admin write sentinel manage platform-scope keys (BE-ADM-011).

``0001_credentials`` shipped ``platform_credentials_read`` — a SELECT-only
escape hatch — and its own module docstring recorded the consequence in R3:
"no RLS-subject path inserts platform credentials in v1". They were seeded out
of band, which is why ``POST /api/v1/credentials`` answers ``scope='platform'``
with a 403 rather than a 422. This revision opens exactly one path, no wider
than it has to be.

**Three policies, not one ``FOR ALL``.** The sentinel needs SELECT (find and
lock the current key), INSERT (store the replacement) and UPDATE (revoke).
It deliberately gets no DELETE: revocation in this module is a status flip
that leaves the row — that is what keeps ``GET /credentials`` able to explain
why a provider went quiet — and a DELETE policy would let the surface erase
the record of a key that was once live. The same narrowness
``0004_admin_accounts``/``0006_admin_write_select`` chose on
``workspace.users`` (``FOR UPDATE`` + ``FOR SELECT``, never ``FOR ALL``).

**Every policy is also pinned to platform rows.** ``workspace_id IS NULL AND
scope = 'platform'`` sits in both ``USING`` and ``WITH CHECK``, so the
administrative sentinel cannot read, forge or revoke a TENANT's own provider
key even by mistake: a platform administrator manages the platform's keys, and
a workspace's keys stay the workspace's. Without that clause the sentinel would
have quietly become a cross-tenant credential surface.

**``uq_cred_active_platform``** is the platform-scope twin of
``0001_credentials``'s ``uq_cred_active_user``, and it closes the same TOCTOU:
"replace the key" reads the active row, revokes it and inserts a new one, so
two administrators rotating the same provider at once could otherwise leave
two active platform rows behind. That is not merely untidy — ``find_active``
resolves platform keys with ``LIMIT 1`` and no ``ORDER BY``, so which of the
two every request in the fleet then uses is undefined. The index turns the
race into a ``23505`` the adapter already translates to 409.

Operational note: a database that ALREADY holds two active platform rows for
one provider (only reachable by out-of-band seeding, since nothing in the API
could create them) will fail this index build. That is the correct outcome —
it is the ambiguity above, made visible at migration time instead of at
request time.

Revision ID: 0003_platform_keys
Revises: 0002_rotator_credentials
"""

from __future__ import annotations

from alembic import op

revision = "0003_platform_keys"
down_revision = "0002_rotator_credentials"
branch_labels = None
depends_on = None

_PLATFORM_ROW = "workspace_id IS NULL AND scope = 'platform'"
_SENTINEL = "current_setting('app.platform_admin_write', true) = 'on'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE POLICY credentials_platform_admin_select ON credentials.credentials
          FOR SELECT
          USING ({_PLATFORM_ROW} AND {_SENTINEL});
        """
    )
    op.execute(
        f"""
        CREATE POLICY credentials_platform_admin_insert ON credentials.credentials
          FOR INSERT
          WITH CHECK ({_PLATFORM_ROW} AND {_SENTINEL});
        """
    )
    op.execute(
        f"""
        CREATE POLICY credentials_platform_admin_update ON credentials.credentials
          FOR UPDATE
          USING ({_PLATFORM_ROW} AND {_SENTINEL})
          WITH CHECK ({_PLATFORM_ROW} AND {_SENTINEL});
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_cred_active_platform ON credentials.credentials (provider)
            WHERE status = 'active' AND scope = 'platform';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS credentials.uq_cred_active_platform")
    op.execute("DROP POLICY IF EXISTS credentials_platform_admin_update ON credentials.credentials")
    op.execute("DROP POLICY IF EXISTS credentials_platform_admin_insert ON credentials.credentials")
    op.execute("DROP POLICY IF EXISTS credentials_platform_admin_select ON credentials.credentials")
