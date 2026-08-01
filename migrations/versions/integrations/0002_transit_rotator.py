"""integrations: RLS carve-out for the Transit key-rotation sweep (P1-9,
docs/p1-hardening-plan.md §3 step 12, ``app.ops.rotate_transit``).

Same reasoning as ``migrations/versions/credentials/0002_transit_rotator.py``
(read that module's docstring for the full argument -- it is not repeated
here) applied to the other two Transit-ciphertext-bearing columns: both
``integrations.connections.token_ref`` and
``integrations.mcp_servers.auth_ref`` sit behind a bare ``tenant_isolation``
policy (``0001_integrations.py``) with no cross-tenant escape hatch at all
(unlike ``credentials.credentials``, neither table even HAS a second,
platform-scoped policy) -- so a rotation sweep needs the identical
role-scoped carve-out on both tables, not just one.

Revision ID: 0002_rotator_integrations
Revises: 0001_integrations
"""

from __future__ import annotations

from alembic import op

# See the credentials sibling migration's docstring for why this cannot be
# the bare "0002_transit_rotator", and why it must fit `varchar(32)` too.
revision = "0002_rotator_integrations"
down_revision = "0001_integrations"
branch_labels = None
depends_on = None

# Literal copy of `app.ops.provision.TRANSIT_ROTATOR_ROLE` -- see the
# credentials sibling migration's docstring for why this is a literal, not
# an import.
TRANSIT_ROTATOR_ROLE = "transit_rotator"

_TABLES: tuple[str, ...] = ("integrations.connections", "integrations.mcp_servers")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"""
            CREATE POLICY transit_rotator_select ON {table}
              FOR SELECT
              TO {TRANSIT_ROTATOR_ROLE}
              USING (true);
            """
        )
        op.execute(
            f"""
            CREATE POLICY transit_rotator_update ON {table}
              FOR UPDATE
              TO {TRANSIT_ROTATOR_ROLE}
              USING (true)
              WITH CHECK (true);
            """
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS transit_rotator_update ON {table}")
        op.execute(f"DROP POLICY IF EXISTS transit_rotator_select ON {table}")
