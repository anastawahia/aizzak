"""PostgreSQL adapter for the read-only platform user directory.

This adapter is intentionally separate from tenant repositories.  Its injected
session factory sets the explicit ``app.platform_admin_read`` GUC and the two
tables it reads each have a SELECT-only policy for that sentinel.  No write
method exists here, so the administration surface cannot mutate tenant data.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import (
    Column,
    ColumnElement,
    DateTime,
    MetaData,
    Table,
    Text,
    Uuid,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.ports.directory import PlatformUser, PlatformUserPage

_metadata = MetaData()
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

users = Table(
    "users",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("email", Text, nullable=False),
    Column("display_name", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema="workspace",
)
role_assignments = Table(
    "role_assignments",
    _metadata,
    Column("workspace_id", _uuid_col, nullable=False),
    Column("user_id", _uuid_col, nullable=False),
    Column("role", Text, nullable=False),
    schema="access",
)
user_presence = Table(
    "user_presence",
    _metadata,
    Column("user_id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("last_seen_at", _timestamptz, nullable=False),
    schema="workspace",
)

# A heartbeat is emitted once per minute by the active browser.  The two-minute
# server-side window tolerates one missed client tick without calling an idle
# tab online indefinitely.
_ONLINE_WINDOW = text("INTERVAL '2 minutes'")

PlatformAdminSessionProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_LIKE_ESCAPE = "\\"
_LIVE = users.c.status != "deleted"


def _contains(term: str) -> ColumnElement[bool]:
    """Match a case-insensitive substring of the email or the display name.

    The operator's own ``%`` and ``_`` are escaped: a search for ``a_b`` looks
    for that literal text, and a stray ``%`` must not silently return everyone.
    """
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    pattern = f"%{escaped}%"
    return or_(
        users.c.email.ilike(pattern, escape=_LIKE_ESCAPE),
        func.coalesce(users.c.display_name, "").ilike(pattern, escape=_LIKE_ESCAPE),
    )


class SqlPlatformUserDirectory:
    """Read platform users and their assigned roles through the admin GUC."""

    def __init__(self, platform_admin_session: PlatformAdminSessionProvider) -> None:
        self._platform_admin_session = platform_admin_session

    async def list_users(
        self, *, limit: int, offset: int, search: str | None = None
    ) -> PlatformUserPage:
        online_at = (users.c.status == "active") & (
            user_presence.c.last_seen_at >= func.now() - _ONLINE_WINDOW
        )
        is_online = func.coalesce(online_at, False).label("online")
        roles = func.coalesce(
            func.array_agg(role_assignments.c.role).filter(role_assignments.c.role.is_not(None)),
            array([], type_=Text),
        ).label("roles")
        page_stmt = (
            select(
                users.c.id,
                users.c.workspace_id,
                users.c.email,
                users.c.display_name,
                users.c.status,
                user_presence.c.last_seen_at,
                is_online,
                users.c.created_at,
                roles,
            )
            .select_from(
                users.outerjoin(
                    role_assignments,
                    (role_assignments.c.workspace_id == users.c.workspace_id)
                    & (role_assignments.c.user_id == users.c.id),
                ).outerjoin(user_presence, user_presence.c.user_id == users.c.id)
            )
            .group_by(
                users.c.id,
                users.c.workspace_id,
                users.c.email,
                users.c.display_name,
                users.c.status,
                user_presence.c.last_seen_at,
                users.c.created_at,
            )
            .order_by(users.c.created_at.desc(), users.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
        totals_stmt = select(
            func.count(users.c.id).label("total"),
            func.count(users.c.id).filter(users.c.status == "active").label("active"),
            func.count(users.c.id).filter(users.c.status == "disabled").label("disabled"),
            func.count(users.c.id).filter(online_at).label("online"),
        ).select_from(users.outerjoin(user_presence, user_presence.c.user_id == users.c.id))
        # A deleted account is a tombstone the audit ledger points at, not a
        # directory entry: its identity fields were erased at deletion, so a
        # row for it would list nobody while still inflating the total the
        # operator pages through.
        page_stmt = page_stmt.where(_LIVE)
        totals_stmt = totals_stmt.where(_LIVE)
        if search is not None:
            # Both statements take the same predicate: the counts describe the
            # set being paged through, so ``total`` stays a truthful bound on
            # the offsets the caller may still ask for.
            match = _contains(search)
            page_stmt = page_stmt.where(match)
            totals_stmt = totals_stmt.where(match)
        async with self._platform_admin_session() as session:
            rows = (await session.execute(page_stmt)).mappings().all()
            totals = (await session.execute(totals_stmt)).mappings().one()
        return PlatformUserPage(
            items=tuple(
                PlatformUser(
                    id=row["id"],
                    workspace_id=row["workspace_id"],
                    email=row["email"],
                    display_name=row["display_name"] or "",
                    status=row["status"],
                    roles=tuple(sorted(row["roles"] or ())),
                    last_seen_at=row["last_seen_at"],
                    online=row["online"],
                    created_at=row["created_at"],
                )
                for row in rows
            ),
            total=totals["total"],
            active=totals["active"],
            disabled=totals["disabled"],
            online=totals["online"],
        )
