"""RLS tenant-context machinery — Layer 1 of DD-04's two-layer isolation.

Layer 1 (here): every application/worker transaction sets the
``app.workspace_id`` GUC as its first statement, so PostgreSQL's Row-Level
Security policies (``01-data-model.md §3``) scope every subsequent statement
in that transaction to the caller's tenant. Layer 2 (``WHERE workspace_id =
:ws``) is applied by each module's SQL adapter itself (e.g.
``modules/media/adapters/sql_repository.py``) — this module knows nothing
about any module's tables and stays GUC-only.

``SET LOCAL app.workspace_id = '...'`` cannot be issued as a parameterized
statement (a GUC *name* is not a valid bind target for ``SET``/``SET
LOCAL``). ``set_config(name, value, is_local => true)`` is the parameterized
equivalent — transaction-scoped exactly like ``SET LOCAL``, but its
arguments bind safely like any other SQL function call.

``PlatformSessionFactory`` (R1, 01-data-model §2.1) is a SEPARATE, narrowly
scoped escape hatch: it sets the EXPLICIT sentinel GUC ``app.platform_read``
instead of any tenant id, and backs the ``workspace.users`` RLS policy
``users_platform_read`` (``migrations/versions/workspace/0001_workspace.py``).
It is deliberately not context-absence-keyed (an absent/empty
``app.workspace_id`` must keep failing safe to zero rows under
``tenant_isolation`` — see the empty-string GUC hardening below) and not
row-keyed (impossible before a user's own row is known). Its SOLE sanctioned
consumer is ``SqlUserRepository.get_by_firebase_uid``
(``modules/workspace/adapters/sql_repository.py``) — JIT login/provisioning
resolves a user by its external Firebase UID before any workspace/RLS scope
exists. Any new consumer is a security-review item.

**Request-scoped Unit of Work (5.1-أ).** ``TenantSessionFactory`` now plays
TWO roles behind the SAME injected instance. Called directly (``__call__``),
it is still the legacy one-session-per-call factory every
``modules/*/adapters/sql_repository.py`` and ``SqlEventOutbox`` were built
against — unchanged. Called as ``begin(ctx)`` (structurally satisfying
``framework/ports/unit_of_work.py``'s ``UnitOfWork``), it opens ONE session +
transaction for an entire producer-service call and publishes that session on
a module-level ``ContextVar``; every ``__call__`` issued while that unit of
work is active — by however many repositories, and by the Outbox adapter
itself — detects the active session and JOINS it instead of opening a second
one, so the domain effect and the Outbox row land in the SAME transaction
(04-event-catalog §3.1's first guarantee, «الأثر النطاقي + صف Outbox في نفس
المعاملة ⇒ لا فقد»). A caller that never opens a ``begin(ctx)`` block sees no
behavioural change at all. Closing the atomicity gap changed what
``tenant_session`` resolves to; it changed no adapter's code, because every
adapter already depended on ``tenant_session`` as an opaque callable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.framework.types import Uuid

_GUC = "app.workspace_id"
_PLATFORM_READ_GUC = "app.platform_read"

# Published only while a `TenantSessionFactory.begin(ctx)` block from THIS
# instance is active on the current asyncio task (ASGI request tasks and
# worker loop iterations never see each other's value). `__call__` reads
# these to decide whether to join the caller's active unit of work or open
# its own session -- see `TenantSessionFactory`'s docstring below. Paired:
# both are always set/reset together, never independently.
_active_session: ContextVar[AsyncSession | None] = ContextVar("_active_session", default=None)
_active_workspace_id: ContextVar[Uuid | None] = ContextVar("_active_workspace_id", default=None)


async def set_tenant(session: AsyncSession, workspace_id: Uuid) -> None:
    """Set the RLS GUC for the remainder of ``session``'s current transaction."""
    await session.execute(
        text("SELECT set_config(:guc, :ws, true)"), {"guc": _GUC, "ws": workspace_id}
    )


async def set_platform_read(session: AsyncSession) -> None:
    """Set the R1 sentinel GUC (``app.platform_read = 'on'``) for the remainder
    of ``session``'s current transaction. Never sets ``app.workspace_id`` —
    this is a platform-wide read escape hatch, not a tenant context."""
    await session.execute(text("SELECT set_config(:guc, 'on', true)"), {"guc": _PLATFORM_READ_GUC})


class TenantSessionFactory:
    """Builds a tenant-scoped, transactional session — either ONE per call
    (``__call__``, DD-04) or ONE per request-scoped unit of work (``begin``,
    5.1-أ) — over the same injected sessionmaker.

    ``__call__`` is the original shape: open a fresh session + transaction,
    set the RLS GUC as the FIRST statement in that transaction — so every
    statement a caller issues afterwards, inside the same ``async with``
    block, runs under the tenant's RLS context — then yield the session. The
    transaction commits on clean exit and rolls back on error (ordinary
    ``session.begin()`` semantics). Every ``modules/*/adapters/
    sql_repository.py`` and ``SqlEventOutbox`` were built against exactly
    this behaviour and still get it UNLESS a ``begin(ctx)`` block from this
    SAME instance is active on the current task, in which case ``__call__``
    joins that session instead of opening its own — see ``begin`` below.

    ``begin`` (structurally satisfies ``framework/ports/unit_of_work.py``'s
    ``UnitOfWork``) is the request-scoped unit of work this class's own
    docstring long predicted: a producer service wraps its whole body in
    ``async with uow.begin(ctx):``, so the aggregate write (through its
    repository's ``__call__``) and the Outbox append (``SqlEventOutbox``
    also calls ``__call__``) share ONE transaction — closing 04 §3.1's
    atomicity gap without either adapter's code changing at all.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        """Open ONE session + transaction for the remainder of ``ctx``'s
        request/job, set the RLS GUC once, and publish the session for any
        ``__call__`` issued inside this block to join. Commits on clean
        exit, rolls back on exception — the ``__call__`` semantics, applied
        once to the whole block instead of once per adapter call.

        The ``ContextVar``\\ s are ALWAYS reset in ``finally``, exception or
        not: a stale "active session" surviving past this block would make
        the NEXT unrelated ``__call__`` on this task silently join a session
        that is already committed/rolled back and about to be closed.
        """
        async with self._sessionmaker() as session, session.begin():
            await set_tenant(session, ctx.workspace_id)
            session_token = _active_session.set(session)
            workspace_token = _active_workspace_id.set(ctx.workspace_id)
            try:
                yield
            finally:
                _active_session.reset(session_token)
                _active_workspace_id.reset(workspace_token)

    @asynccontextmanager
    async def __call__(self, ctx: ExecutionContext) -> AsyncIterator[AsyncSession]:
        active_session = _active_session.get()
        if active_session is not None:
            # Join the enclosing `begin(ctx)` block's transaction rather than
            # opening a second one -- it owns the commit/rollback/close, so
            # this branch does none of that. A workspace that disagrees with
            # the active unit of work is never silently honoured: a
            # cross-tenant join inside one UoW is a bug in the CALLER (e.g. a
            # producer service handing a nested port the wrong `ctx`), not a
            # routing decision this factory should make quietly.
            if _active_workspace_id.get() != ctx.workspace_id:
                raise AppError(
                    "tenant session requested for a workspace that differs "
                    "from the active unit of work's workspace",
                    code="common.internal",
                )
            yield active_session
            return
        async with self._sessionmaker() as session, session.begin():
            await set_tenant(session, ctx.workspace_id)
            yield session


class PlatformSessionFactory:
    """Builds one platform-scoped, transactional session per call (R1).

    Each call opens a fresh session + transaction, sets the R1 sentinel GUC
    (``app.platform_read = 'on'``) as the FIRST statement in that transaction
    — never ``app.workspace_id`` — then yields the session. The transaction
    commits on clean exit and rolls back on error, exactly like
    ``TenantSessionFactory``.

    SOLE sanctioned consumer: ``SqlUserRepository.get_by_firebase_uid``
    (``modules/workspace/adapters/sql_repository.py``), which backs the JIT
    login/provisioning identity lookup — a read that must run before any
    workspace/tenant scope is known. Any new consumer of this factory is a
    security-review item, not a routine wiring change.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_platform_read(session)
            yield session
