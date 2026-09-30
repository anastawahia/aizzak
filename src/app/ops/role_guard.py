"""A cross-tenant sweep refuses to run as any role but its own (capacity 5.7).

**The failure this closes was measured, not imagined.** On 2026-09-30, on the
live stack, ``python -m app.ops.retention sweep --dry-run`` run with the table
OWNER's DSN printed::

    {"table": "idempotency_keys", ..., "affected": 0, "dry_run": true}

and exited 0 -- while a superuser count of the same table found **6,943 rows**
past the two-day window. Nothing was wrong with the tool. ``idempotency_keys``
and every ``usage`` table are under ``FORCE ROW LEVEL SECURITY``, which binds
the owner too, and an RLS policy does not answer "you may not" -- it answers
"there is nothing here". ``retention_sweeper`` reaches every tenant only
because two migrations gave THAT role a carve-out; any other role sees an
empty table and sweeps it successfully.

That is harmless in a terminal, where a person can wonder why the number is
zero. It is precisely the failure 5.7 is written against once the same
command runs on a timer: «مهمّةٌ مجدولةٌ تفشل صامتةً أسوأُ من غياب المهمّة». A
scheduler wired with the wrong DSN would record a success every night,
forever, and the table would grow exactly as if nothing were scheduled.

The same holds for the other two cross-tenant tools, each through its own
carve-out: ``purge`` finds candidates through a SELECT policy scoped to
``workspace_purger`` (and a wrong role finds no deleted users, i.e. nothing to
purge), and ``rotate_transit`` reaches every tenant's ciphertext through
policies scoped to ``transit_rotator`` (and a wrong role finds none to
rewrap). So all three check ``current_user`` before they read anything, and a
mismatch is an exit code, not a zero.

``backup`` is the precedent: its ``preflight`` refuses ``pg_dump`` without
``BYPASSRLS`` for the same reason -- a dump under the wrong role exits 0 with
empty tenant tables.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# The exit code a refused tool returns: `2`, the configuration-fault code
# `app.ops.healthcheck` and `app.ops.backup` already use -- distinct from a
# run that started and failed.
ROLE_MISMATCH_EXIT = 2


class RoleMismatchError(RuntimeError):
    """The connected role is not the one this tool's RLS carve-outs name."""

    def __init__(self, *, tool: str, expected: str, actual: str) -> None:
        super().__init__(
            f"{tool} refused: connected as {actual!r}, but only {expected!r} holds the "
            "cross-tenant policies this sweep reads through. Under any other role RLS "
            "returns zero rows rather than an error, so the run would 'succeed' having "
            f"done nothing. Point DATABASE_URL at the {expected} role's own DSN."
        )
        self.expected = expected
        self.actual = actual


async def require_role(engine: AsyncEngine, *, tool: str, expected: str) -> None:
    """Raise ``RoleMismatchError`` unless ``current_user`` is ``expected``.

    One round trip on its own connection, before the tool opens any
    transaction. ``current_user`` rather than ``session_user``: the policies
    are evaluated against the former, and it is also what PgBouncer's
    per-user pools hand through unchanged."""
    async with engine.connect() as conn:
        actual = str((await conn.execute(text("SELECT current_user"))).scalar_one())
    if actual != expected:
        raise RoleMismatchError(tool=tool, expected=expected, actual=actual)
