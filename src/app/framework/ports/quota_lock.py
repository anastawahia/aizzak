"""QuotaLock driven port — the serialisation a per-workspace ceiling needs
before it may believe its own count (capacity-plan step 2.7).

**The bug this port exists to close, with the number that names it.** A
ceiling of the shape "count what the workspace already has, refuse if it is at
the limit, otherwise write one more" is a read followed by a write, and under
concurrency every request that starts before the first one commits reads the
same count. Measured on the live stack, on the workspace file cap
(``Limits.max_files_per_workspace``) with **one** slot left: 100 concurrent
registrations produced **30** files, not 1. The 30 was not a property of the
cap — the same run with ``pool_size=5`` produced 5 and with ``pool_size=30``
produced 29. **The ceiling was being enforced at the width of the connection
pool**, which means capacity-plan 2.2's pool increase (25 → 100) would have
made every ceiling in this repository four times leakier without touching a
line of quota code.

**What the concrete lock is, and why not a row.** ``spaces``' byte quota
(``framework/di/space_quota.py``) already solves the identical problem by
holding the SPACE's row still with ``SELECT ... FOR UPDATE``, and its
docstring states the principle this port generalises: "the serialisation IS
the reservation". A WORKSPACE-scoped ceiling has no equivalent row to hold —
``workspace.workspaces`` is owned by another module, is not reachable from a
tenant adapter, and locking a tenant's identity row to register a file would
put every unrelated write behind it. So the concrete adapter takes a
PostgreSQL transaction-level ADVISORY lock keyed by ``(workspace_id,
ceiling)``: two callers contending for the same ceiling in the same workspace
serialise, and nothing else in the database notices.

**``ceiling`` is a name, not a resource.** Two different ceilings in one
workspace take two different locks, so a file registration never waits behind
a summary build. The names are string constants declared beside the code that
owns each ceiling, and they are part of the lock's identity — renaming one
silently splits it into two locks, which is why every caller passes a
constant rather than a literal.

**It must be held INSIDE a unit of work, and the adapter refuses otherwise.**
A transaction-scoped lock is released at COMMIT; taken in a transaction of its
own it is released before the count that it was supposed to protect is even
issued. That failure is silent and total — the guard appears to be there and
serialises nothing — so the concrete adapter raises rather than allowing it.
That is why this port takes a ``ctx`` and yields nothing: the caller is
already inside ``UnitOfWork.begin``, and this is one more statement in that
transaction, exactly like ``EventOutbox.append``.

Application services depend on THIS port, never on ``app.infrastructure``
(import-linter contract 3) — the ``UnitOfWork`` precedent, and for the same
reason: the concrete lock is one ``SELECT pg_advisory_xact_lock(...)`` in
``infrastructure/persistence/quota_lock.py``, wired at the Composition Root,
and this module knows nothing about PostgreSQL or that a database is involved.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext


class QuotaLock(Protocol):
    """Serialise this workspace's contenders for ONE named ceiling until the
    caller's unit of work ends."""

    async def hold(self, ctx: ExecutionContext, ceiling: str) -> None:
        """Block until no other transaction holds ``ceiling`` for
        ``ctx.workspace_id``, then hold it for the rest of THIS transaction.

        Returns nothing and releases nothing: the lock's lifetime is the
        enclosing ``UnitOfWork.begin`` block's, so a rejection that leaves
        through an exception frees it by the same rollback that undoes the
        write. There is no path on which a caller can forget to release it,
        which is the whole reason the lock is transaction-scoped rather than
        session-scoped.
        """
        ...
