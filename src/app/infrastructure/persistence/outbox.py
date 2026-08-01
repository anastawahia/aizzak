"""SQL adapter for ``EventOutbox`` — writes ``platform.outbox`` (D-18 ·
01-data-model §4.1 · 04-event-catalog §3.1).

Structural ``Protocol`` match, like every adapter here. It lives under
``infrastructure/persistence/`` rather than in any module's ``adapters/``
because ``platform.outbox`` is platform infrastructure shared by all four
producing modules — a module-local copy would be four copies of one table.

**Tenant session, no tenant RLS.** ``platform.outbox`` carries no RLS policy:
01 §4.1 marks its ``workspace_id`` as nullable and "للتتبّع" (for tracing),
since platform-scope events have no tenant and the relay must read every row
regardless. The write still goes through the ordinary ``tenant_session``
provider anyway — not for isolation, but because that is the session a
producing use-case's aggregate write already runs under, and sharing it is
the whole mechanism by which the two writes become atomic (see the 5.1-أ
paragraph below).

✅ **Atomicity achieved as of 5.1-أ** (recorded honestly, history included):
from 4.7-d-1 through the end of Phase 4, ``TenantSessionFactory`` opened one
transaction PER CALL (see its docstring and ``SqlMediaJobRepository``'s), so
an aggregate write and this append were two transactions. The failure mode
was an aggregate row with no event — exactly the behaviour *without* an
outbox at all, since every use-case's returned events were discarded — so it
was strictly an improvement even then, never a regression; but 04 §3.1's "لا
فقد" guarantee did not yet hold for real. 5.1-أ closed that gap by making
``TenantSessionFactory`` itself unit-of-work-aware (its ``begin(ctx)``,
structurally the ``framework/ports/unit_of_work.py`` port): a producer
service now opens ONE session/transaction for its whole call, and every
``__call__`` issued inside it — by however many repositories, and by THIS
adapter's own ``append`` — joins that SAME session instead of opening its
own. Closing that gap changed what ``tenant_session`` resolves to, not this
class: nothing below this paragraph changed at all.

**Columns this adapter deliberately does not write:** ``created_at`` (database
``DEFAULT now()`` — the relay pulls unpublished rows *in ``created_at``
order*, so server time, not a producer's clock, must decide that order or
clock skew across app instances silently reorders the stream);
``published_at`` (relay-owned, and a producer that could set it would be able
to make an event vanish unpublished); ``attempts`` (relay-owned, DB default).

**5.1-ب adds the relay's own read/write side, ``SqlOutboxRelayStore``, below
``SqlEventOutbox`` in this same file (unchanged by this addition).** The two
share the one ``outbox`` ``Table`` object above — there is exactly one table,
never two schemas of it. Unlike ``SqlEventOutbox``, the relay store takes a
PLAIN ``async_sessionmaker`` rather than a ``tenant_session`` provider:
``platform.outbox`` carries no RLS policy at all (this docstring's opening
paragraph), so there is no tenant GUC to set, and the relay's whole reason to
exist is reading EVERY workspace's unpublished rows in one pass — a
tenant-scoped session would be a contradiction in terms here. It also runs
under its OWN Postgres role, ``outbox_relay`` (SELECT/UPDATE only), never
``app_rw`` (INSERT only) — the least-privilege split this file's ``_translate``
already anticipated: a producer that could ``UPDATE published_at`` could make
an event vanish unpublished, and a relay that could ``INSERT`` could forge
events no producer ever wrote.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.types import Json
from app.framework.types import Uuid as UuidStr

_metadata = MetaData()

_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

outbox = Table(
    "outbox",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=True),
    Column("aggregate_type", Text, nullable=False),
    Column("aggregate_id", _uuid_col, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("stream", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("correlation_id", _uuid_col, nullable=True),
    Column("causation_id", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("published_at", _timestamptz, nullable=True),
    Column("attempts", Integer, nullable=False),
    schema="platform",
)

TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlEventOutbox:
    """``EventOutbox`` over ``platform.outbox``."""

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        if not records:
            # No statement at all, not an empty INSERT: an empty VALUES list is
            # a SQL error, and opening a transaction to do nothing is a wasted
            # round trip on every event-free use-case path.
            return
        stmt = insert(outbox).values(
            [
                {
                    "id": record.event_id,
                    "workspace_id": ctx.workspace_id,
                    "aggregate_type": record.aggregate_type,
                    "aggregate_id": record.aggregate_id,
                    "event_type": record.event_type,
                    "stream": record.stream,
                    "payload": record.payload,
                    "correlation_id": ctx.correlation_id,
                }
                for record in records
            ]
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver failure onto the shared error hierarchy (R6) — no
    ``sqlalchemy``/``asyncpg`` type escapes this adapter.

    ``23505`` (duplicate ``id``): the same event was appended twice. Surfaced
    as a conflict rather than swallowed — a UUIDv7 collision is not credible,
    so in practice this means a caller replayed a use-case with an event id it
    had already minted, and hiding that would hide the bug. (Duplicate
    *delivery* is a different problem, already solved downstream by
    ``platform.processed_events``, DD-09.)

    ``42501``: the ``app_rw`` role lacks INSERT on ``platform.outbox`` — a
    deployment/grant gap (01 §6 makes grants a runbook step, not a migration),
    hence a 500-class internal error, not a caller-facing 4xx.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("outbox append lost a uniqueness race (duplicate event id)")
    if sqlstate == "42501":
        return AppError(
            "outbox append rejected: app role lacks INSERT on platform.outbox",
            code="common.internal",
        )
    return AppError(
        "unexpected database error while appending to the outbox", code="common.internal"
    )


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """One unpublished row, shaped exactly as the relay needs it — a
    narrower READ than ``OutboxRecord`` (the producer's WRITE shape, above):
    the relay never looks at ``aggregate_type``/``aggregate_id``/
    ``correlation_id``/``workspace_id``, it only republishes ``payload`` onto
    ``stream`` and tracks ``event_id`` for ``mark_published``/``bump_attempts``.
    """

    event_id: UuidStr
    stream: str
    payload: Json


# The three columns `fetch_unpublished` reads — named once so the SELECT and
# the `OutboxEntry` construction below can never drift out of step.
_UNPUBLISHED_COLUMNS = (outbox.c.id, outbox.c.stream, outbox.c.payload)


class SqlOutboxRelayStore:
    """The relay's read/write side of ``platform.outbox`` (01 §4.1, D-18/26).

    A PLAIN, non-tenant sessionmaker (see the module docstring's 5.1-ب
    paragraph) — every method here opens its OWN short session + transaction
    (``PlatformSessionFactory``'s one-call-one-transaction shape, ``rls.py``),
    since the relay's fetch → publish → mark-published cycle already spans an
    external I/O call (``EventPublisher.publish``, a Redis round trip)
    between the read and the write half. Holding one long-lived transaction
    across that hop would pin a DB connection idle for the network call, and
    R2 (5.1-أ's hand-off to this sub-step) already rules out a DB transaction
    wrapping external I/O — the same reason ``OutboxRelay.run_once`` itself
    never opens a transaction spanning its own loop.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def fetch_unpublished(self, limit: int) -> list[OutboxEntry]:
        """Unpublished rows, oldest first (01 §4.1's partial index —
        ``ix_outbox_unpublished`` — exists precisely for this predicate).

        ``created_at, id`` is a DETERMINISTIC tie-break: two rows minted in
        the same server-clock tick still sort by their UUIDv7's own embedded
        timestamp (time-ordered by construction, ``identifiers.py``), so a
        page is never ambiguous even under heavy concurrent producer writes.
        """
        stmt = (
            select(*_UNPUBLISHED_COLUMNS)
            .where(outbox.c.published_at.is_(None))
            .order_by(outbox.c.created_at, outbox.c.id)
            .limit(limit)
        )
        try:
            async with self._sessionmaker() as session, session.begin():
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate_relay(exc) from exc
        return [
            OutboxEntry(event_id=row["id"], stream=row["stream"], payload=row["payload"])
            for row in rows
        ]

    async def mark_published(self, event_ids: Sequence[UuidStr]) -> None:
        """Stamp ``published_at`` for exactly these rows — never the
        caller's whole fetched batch. ``OutboxRelay.run_once`` passes only
        the PREFIX that actually reached Redis, so a row this call does not
        name stays unpublished and is picked up again by the next poll.
        """
        if not event_ids:
            # No statement at all -- an empty poll or an all-failed batch
            # must not spend a round trip on a no-op UPDATE (the
            # `SqlEventOutbox.append` empty-sequence precedent, above).
            return
        stmt = update(outbox).where(outbox.c.id.in_(event_ids)).values(published_at=func.now())
        try:
            async with self._sessionmaker() as session, session.begin():
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate_relay(exc) from exc

    async def bump_attempts(self, event_ids: Sequence[UuidStr]) -> None:
        """Record that a publish was ATTEMPTED on these rows — whether or
        not the batch as a whole eventually succeeds. This counter is the
        only durable signal today that a row was ever picked up at all, and
        the threshold Phase 5.2's DLQ will eventually check it against.
        """
        if not event_ids:
            return
        stmt = (
            update(outbox).where(outbox.c.id.in_(event_ids)).values(attempts=outbox.c.attempts + 1)
        )
        try:
            async with self._sessionmaker() as session, session.begin():
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate_relay(exc) from exc


def _translate_relay(exc: DBAPIError) -> AppError:
    """Map a driver failure from the relay-side store onto the shared error
    hierarchy (R6) — mirrors ``_translate`` above but names ``outbox_relay``
    (not ``app_rw``) in the grant-gap message, since this store runs under a
    DIFFERENT least-privilege role (the module docstring's 5.1-ب paragraph).
    ``23505`` is not special-cased here: neither ``SELECT`` nor ``UPDATE``
    can raise a uniqueness violation, so it would only ever appear as a
    genuinely unexpected database error, exactly like every other sqlstate
    the fallthrough already covers.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return AppError(
            "outbox relay operation rejected: outbox_relay role lacks "
            "SELECT/UPDATE on platform.outbox",
            code="common.internal",
        )
    return AppError("unexpected database error in the outbox relay store", code="common.internal")
