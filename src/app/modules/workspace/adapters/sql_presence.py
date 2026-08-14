"""PostgreSQL adapter for tenant-scoped user-presence heartbeats."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import cast

from sqlalchemy import Column, DateTime, MetaData, Table, Uuid
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid as UuidStr

_metadata = MetaData()
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

user_presence = Table(
    "user_presence",
    _metadata,
    Column("user_id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("last_seen_at", _timestamptz, nullable=False),
    schema="workspace",
)

TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlUserPresenceStore:
    """Upsert the authenticated caller's heartbeat under tenant RLS."""

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def record_heartbeat(
        self, ctx: ExecutionContext, *, user_id: UuidStr, seen_at: datetime
    ) -> datetime:
        # Both the router and this adapter derive the identity from ``ctx``.
        # The RLS policy's WITH CHECK remains the database-level backstop if a
        # future caller ever passes a mismatched workspace id.
        stmt = (
            insert(user_presence)
            .values(user_id=user_id, workspace_id=ctx.workspace_id, last_seen_at=seen_at)
            .on_conflict_do_update(
                index_elements=[user_presence.c.user_id],
                set_={"last_seen_at": seen_at},
                where=user_presence.c.workspace_id == ctx.workspace_id,
            )
            .returning(user_presence.c.last_seen_at)
        )
        async with self._tenant_session(ctx) as session:
            return cast(datetime, (await session.execute(stmt)).scalar_one())
