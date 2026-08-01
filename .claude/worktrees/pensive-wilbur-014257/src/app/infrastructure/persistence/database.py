"""Async SQLAlchemy engine/sessionmaker factories (OPS-02).

The engine/sessionmaker themselves are infrastructure (this module lives
under ``app.infrastructure`` and is wired exclusively by the Composition
Root, per import-linter contract 6); RLS machinery (``rls.py``) and module
SQL adapters receive an already-built ``async_sessionmaker`` rather than
importing this module directly.

``statement_cache_size``/``prepared_statement_cache_size`` are pinned to
``0`` on *every* engine (OPS-02): the app talks to Postgres through PgBouncer
in transaction-pooling mode, which is incompatible with asyncpg's client-side
statement caching / named prepared statements.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, Pool

from app.framework.settings.settings import DatabaseSettings

_CONNECT_ARGS = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}


def create_engine(db: DatabaseSettings, *, poolclass: type[Pool] | None = None) -> AsyncEngine:
    """Build the async engine for ``db`` (OPS-02 connect args on every engine).

    ``poolclass=None`` (the default, used by the app at runtime) pools
    connections via ``AsyncAdaptedQueuePool``, sized from
    ``db.pool_size``/``db.max_overflow`` with health-checked checkouts
    (``pool_pre_ping``). Tests instead inject ``poolclass=NullPool`` (no
    pooling across short-lived test connections/transactions) -- ``NullPool``
    *rejects* ``pool_size``/``max_overflow`` keyword arguments, so those are
    only passed in the default-pool branch.
    """
    if poolclass is None:
        return create_async_engine(
            db.url,
            connect_args=_CONNECT_ARGS,
            poolclass=AsyncAdaptedQueuePool,
            pool_pre_ping=True,
            pool_size=db.pool_size,
            max_overflow=db.max_overflow,
        )
    return create_async_engine(
        db.url,
        connect_args=_CONNECT_ARGS,
        poolclass=poolclass,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``.

    ``expire_on_commit=False``: an aggregate hydrated from a query stays
    usable after its enclosing transaction commits -- ``TenantSessionFactory``
    (``rls.py``) commits on clean scope exit, and callers keep reading
    already-loaded attributes off the returned domain objects afterwards.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
