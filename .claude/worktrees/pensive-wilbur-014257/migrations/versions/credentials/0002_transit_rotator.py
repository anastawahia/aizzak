"""credentials: RLS carve-out for the Transit key-rotation sweep (P1-9,
docs/p1-hardening-plan.md §3 step 12, ``app.ops.rotate_transit``).

**The same "RLS wall" ``retention_sweeper`` hit
(``migrations/versions/platform/0003_retention_sweep.py``), resolved the
identical way.** A rotation sweep re-encrypts every stored ciphertext across
EVERY tenant in one pass, but ``credentials.credentials``' own
``tenant_isolation`` policy (``0001_credentials.py``) has no ``TO`` clause --
it confines EVERY role, ``transit_rotator`` included, to whatever
``app.workspace_id`` its own session happens to have set (nothing, for a
role that never sets one). ``platform_credentials_read`` does not help
either: it is SELECT-only and scoped to ``scope='platform'`` rows alone, so a
user-scoped credential in any OTHER workspace is invisible to it, and it
grants no UPDATE path at all.

This migration adds two permissive policies scoped ``TO transit_rotator``
alone (``USING (true)``, OR-combined with ``tenant_isolation`` -- 01 §3.1's
own ``platform_credentials_read`` mechanism, here role-scoped instead of
PUBLIC) so this ONE role reaches every workspace's credential row, while
``app_rw``'s own tenant confinement is completely untouched.
``transit_rotator``'s ACTUAL reach beyond that stays bounded by its own
GRANT (``app.ops.provision.TRANSIT_ROTATOR_GRANTS``: ``SELECT`` plus
column-scoped ``UPDATE (ciphertext_ref)`` -- it cannot touch ``status``,
``label``, or any other column, so a forged/garbage ciphertext can never
misrepresent a row's identity or state, only break decryption of that ONE
secret).

**Role must pre-exist.** ``CREATE POLICY ... TO transit_rotator`` validates
the role at DDL time (the ``0003_retention_sweep.py`` precedent) --
``app.ops.provision.provision()``'s ``_require_roles`` check already fails
fast on a missing role BEFORE running any migration, and this revision adds
``TRANSIT_ROTATOR_ROLE`` to that check.

Revision ID: 0002_rotator_credentials
Revises: 0001_credentials
"""

from __future__ import annotations

from alembic import op

# Alembic revision IDs are unique across the WHOLE script directory (every
# `version_locations` entry shares one revision map, regardless of branch) --
# and `alembic_version.version_num` is `varchar(32)`, so the bare
# "0002_transit_rotator" is not even an option twice over (the integrations
# sibling migration needs its own distinct, equally short id).
revision = "0002_rotator_credentials"
down_revision = "0001_credentials"
branch_labels = None
depends_on = None

# Literal copy of `app.ops.provision.TRANSIT_ROTATOR_ROLE` -- `migrations/`
# is not importable application code (the `0003_retention_sweep.py`
# precedent for schema literals), so this only needs to name the role a
# `CREATE POLICY ... TO` clause validates, not share behaviour with it.
TRANSIT_ROTATOR_ROLE = "transit_rotator"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE POLICY transit_rotator_select ON credentials.credentials
          FOR SELECT
          TO {TRANSIT_ROTATOR_ROLE}
          USING (true);
        """
    )
    op.execute(
        f"""
        CREATE POLICY transit_rotator_update ON credentials.credentials
          FOR UPDATE
          TO {TRANSIT_ROTATOR_ROLE}
          USING (true)
          WITH CHECK (true);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS transit_rotator_update ON credentials.credentials")
    op.execute("DROP POLICY IF EXISTS transit_rotator_select ON credentials.credentials")
