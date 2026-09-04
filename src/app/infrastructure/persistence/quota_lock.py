"""The concrete ``QuotaLock`` — a PostgreSQL transaction-level advisory lock
keyed by ``(workspace_id, ceiling)`` (capacity-plan step 2.7).

See ``framework/ports/quota_lock.py`` for WHY a per-workspace ceiling needs
one at all, and for the measurement (a cap of 1 admitting 30) that says the
gap is real. This module is only about how the lock is taken.

**Why advisory and not a row.** There is no row to hold. The ceilings this
guards are counts over a whole tenant (files in a workspace, summary builds in
a workspace), and the only row that stands for the tenant itself lives in
another module's schema. ``pg_advisory_xact_lock`` needs no row, no table and
no cleanup path: it is held by the transaction and released by its COMMIT or
its ROLLBACK, so a rejection that raises out of the unit of work frees it on
the way past.

**One bigint, hashed in SQL.** The two-argument form of the function takes two
``int4``\\ s, which is not enough room for a UUID plus a name, so the key is a
single ``int8`` computed by ``hashtextextended`` over
``'<workspace_id>:<ceiling>'``. Hashing in SQL rather than in Python is
deliberate: Python's ``hash()`` is randomised per process (``PYTHONHASHSEED``),
so two app workers would compute two different keys for the same workspace and
serialise against nobody at all -- a guard that silently does nothing, which
is the exact failure mode this whole step exists to remove. ``hashtextextended``
is immutable, is the same on every backend, and is what the string is passed
to as a BIND PARAMETER, so the ceiling name cannot reach the statement text.

A hash collision between two different ``(workspace, ceiling)`` pairs is
harmless in the only direction that matters: it can make two unrelated
ceilings serialise against each other (a wasted wait), never let two
contenders for the SAME ceiling through.

**No timeout here, deliberately.** The wait is bounded by the caller's own
transaction budget (capacity-plan 2.6: ``statement_timeout`` on the request
path, 5 s), which is the number an operator already knows and tunes. A second
timeout here would be a second answer to the same question, and the losing
side of it would be a 500 where the platform already has a truthful 429.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.infrastructure.persistence.rls import in_unit_of_work

TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]

_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


class AdvisoryQuotaLock:
    """``QuotaLock`` over the same tenant-session seam every SQL adapter uses
    (structural Protocol match — no inheritance, this codebase's convention).

    It takes the session through ``tenant_session(ctx)`` rather than being
    handed one, so it joins whatever unit of work the caller opened exactly as
    the caller's repositories do (``rls.py``'s ``__call__`` docstring). That is
    also what makes the check below sufficient: if a unit of work is active on
    this task, this statement runs inside it.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def hold(self, ctx: ExecutionContext, ceiling: str) -> None:
        if not in_unit_of_work():
            # Fail loudly rather than take a lock that is released one round
            # trip later. A ceiling guarded by such a lock reads exactly like a
            # guarded one in the source and is not guarded at all, and the only
            # symptom is an over-quota tenant nobody can explain.
            raise AppError(
                f"quota lock for {ceiling!r} was taken outside a unit of work, "
                "where it would be released before the count it protects",
                code="common.internal",
            )
        async with self._tenant_session(ctx) as session:
            await session.execute(_LOCK_SQL, {"key": f"{ctx.workspace_id}:{ceiling}"})
