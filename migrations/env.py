"""Alembic async environment (06 §6, OPS-02).

Migrations run against the async engine (asyncpg). Because the app connects via
PgBouncer in transaction-pooling mode, ``statement_cache_size=0`` is mandatory
(OPS-02) — set on the connect args here too so migrations behave identically.

v1 uses raw-SQL migrations that mirror the DDL in 01-data-model.md; there is no
ORM metadata autogeneration, so ``target_metadata`` is ``None``.

Per-module migration chains each record their applied revisions in their OWN
``version_table_schema`` (DAT-03, 01-data-model §6) rather than sharing one
``alembic_version`` table. The caller selects which schema via the ``-x vts=``
CLI flag, e.g. ``alembic -x vts=knowledge upgrade knowledge@head``; with no
``-x vts=`` (the platform baseline chain's own commands), ``version_table_schema``
stays ``None`` — Alembic's own default — so existing behavior is unchanged.

Every migration connection carries a short ``lock_timeout`` (capacity step
2.9, ``ح-18``). It is set through asyncpg's ``server_settings`` — i.e. in the
connection's own startup packet — rather than with a ``SET`` statement, and
that is not a style choice: Alembic runs each revision inside a transaction it
opens itself, so a ``SET`` issued here would either have to fight that
transaction for the connection or land inside it and be rolled back with the
first revision that fails. A startup-packet GUC is in force before Alembic's
first statement and survives every rollback.

What the number means, and why it is NOT "how long a migration may wait", is
in ``MigrationSettings``'s docstring, with the measurement: a DDL statement
waiting for a lock parks every later reader behind it, so this is the freeze
the deploy is allowed to impose on the request path — 6.57 s of blocked
``SELECT`` without it, 2.46 s with it, on the same 8-second contender.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.config import load_settings

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False is load-bearing: fileConfig's default
    # (True) silently sets .disabled on EVERY logger already created, so any
    # process running migrations in-process (the live-DB test session, a
    # future entrypoint) would mute every module-level logger imported before
    # this line — found in 4.1 when the plugin_loader's isolation warning
    # vanished under caplog whenever the live_db fixture had run first.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def _database_url() -> str:
    """Resolve the migration DB URL from Settings (the app's DATABASE_URL)."""
    return load_settings().database.url


def _lock_timeout_ms() -> int:
    """The migration session's ``lock_timeout``, in milliseconds."""
    return load_settings().migrations.lock_timeout_ms


def _version_table_schema() -> str | None:
    """The per-module ``version_table_schema`` requested via ``-x vts=<schema>``,
    or ``None`` (Alembic's own default) when the flag is absent -- the
    platform baseline chain's commands never pass it, so their behavior is
    unchanged."""
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("vts") or None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=_version_table_schema(),
    )
    with context.begin_transaction():
        # Offline mode emits a script an operator pipes into psql, so the
        # guard has to be IN the script -- a generated file that freezes a hot
        # table is the same defect as a connection that does.
        context.execute(f"SET lock_timeout = {_lock_timeout_ms()}")
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=_version_table_schema(),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async connection."""
    connect_args: dict[str, Any] = {
        "statement_cache_size": 0,
        # In the startup packet, not a `SET` -- see the module docstring.
        "server_settings": {"lock_timeout": str(_lock_timeout_ms())},
    }
    engine = create_async_engine(
        _database_url(),
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
