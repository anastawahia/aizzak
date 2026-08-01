"""SQL adapter for the consumer idempotency ledger — writes
``platform.processed_events`` (DD-09 · 01-data-model §4.2 · 04-event-catalog
§3, landed in 5.2-أ).

The table has existed since the Phase-1 baseline migration with no writer;
this adapter is that writer. One method, ``claim``: a plain ``INSERT`` on
the ``(consumer_group, event_id)`` primary key under a SAVEPOINT, with the
PK conflict (``23505``) consumed as the ``False`` outcome — returning
whether THIS call inserted the row. A worker handler calls it FIRST, inside
its own effect transaction (``uow.begin`` in ``workers/bootstrap.py``'s
handler closures):

* ``True``  — first delivery: the claim row and the handler's domain effect
  commit together, or roll back together. A crash mid-handler leaves NO claim
  behind, so the redelivered event finds the ledger empty and re-runs — which
  is exactly what at-least-once redelivery is for.
* ``False`` — the ``(consumer_group, event_id)`` pair is already committed:
  a duplicate delivery (the relay's at-least-once double publish, or a crash
  after commit but before ``XACK``). The handler returns without touching
  anything, the engine ``XACK``\\ s, and at-least-once becomes
  effectively-once (04 §3: «تكرار = تجاهُل + XACK»).

**Tenant session, no tenant RLS — the ``SqlEventOutbox`` precedent.**
``platform.processed_events`` carries no RLS policy (a platform bookkeeping
table keyed by consumer group, not by tenant). The write still goes through
the ordinary ``tenant_session`` provider anyway, because joining the
handler's active unit of work is the WHOLE point: DD-09's letter is that the
claim lands «داخل معاملة الأثر», and sharing the session published by
``TenantSessionFactory.begin`` is the one mechanism this codebase has for
that.

**Least privilege: ``app_rw`` gets INSERT only — which is why the claim is
a plain INSERT under a SAVEPOINT, not ``ON CONFLICT DO NOTHING``.** Verified
live against PG16 (5.2-أ): a plain ``INSERT`` succeeds under the bare
``INSERT`` privilege, but the same statement with ``ON CONFLICT
(consumer_group, event_id) DO NOTHING`` dies with ``42501`` — arbiter
inference READS the conflicting row, so ``ON CONFLICT`` (like ``RETURNING``)
quietly demands SELECT. Granting SELECT to buy the nicer statement would
hand every producer/worker role the ability to enumerate what the platform
has processed; keeping INSERT-only means a compromised role can neither
enumerate the ledger nor (no DELETE/UPDATE) un-process an event to force a
replay. The SAVEPOINT shape is also the one 04 §3.1's sequence diagram
literally draws: «INSERT يفشل» على تعارض PK ⇒ تجاهُل — the insert FAILS and
the failure is contained (the savepoint protects the handler's enclosing
effect transaction from Postgres's abort-on-error), never re-raised.

**Unbounded growth is a known, deferred concern:** 01 §4.2 defines no
retention for this ledger. Rows are tiny (two keys + a timestamp) and
``processed_at`` is exactly the column an ops-side sweep would prune on;
nothing in v1 reads the ledger back, so retention is an operational knob,
not a code seam.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, Table, Text, Uuid, insert
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.framework.types import Uuid as UuidStr
from app.infrastructure.persistence.outbox import TenantSessionProvider

_metadata = MetaData()

processed_events = Table(
    "processed_events",
    _metadata,
    Column("consumer_group", Text, primary_key=True),
    Column("event_id", Uuid(as_uuid=False), primary_key=True),
    # DB-defaulted (like `outbox.created_at`): server time, never a worker's
    # clock, decides when a claim happened.
    Column("processed_at", DateTime(timezone=True), nullable=False),
    schema="platform",
)


class SqlProcessedEventLedger:
    """The DD-09 idempotency ledger over ``platform.processed_events``."""

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def claim(self, ctx: ExecutionContext, *, consumer_group: str, event_id: UuidStr) -> bool:
        """Insert ``(consumer_group, event_id)``; ``True`` iff this call
        inserted it (first delivery), ``False`` on the PK conflict (duplicate
        delivery). Call it inside the handler's ``uow.begin`` block so the
        claim shares the effect's transaction — outside one it would open its
        own (one-call-one-transaction ``TenantSessionFactory.__call__``
        semantics) and the atomicity DD-09 exists for would silently not
        hold.

        The SAVEPOINT (``begin_nested``) is load-bearing, not decorative:
        Postgres aborts the WHOLE enclosing transaction on any statement
        error, so a bare duplicate-key failure would poison the handler's
        unit of work (every later statement, and the final COMMIT, would
        die with ``25P02 in_failed_sql_transaction``). Rolling back to the
        savepoint contains the expected ``23505`` and leaves the enclosing
        effect transaction healthy. (Why not ``ON CONFLICT DO NOTHING``:
        the module docstring's least-privilege paragraph — it demands
        SELECT, verified live.)
        """
        stmt = insert(processed_events).values(consumer_group=consumer_group, event_id=event_id)
        try:
            async with self._tenant_session(ctx) as session:
                try:
                    async with session.begin_nested():
                        await session.execute(stmt)
                except IntegrityError as exc:
                    if getattr(exc.orig, "sqlstate", None) == "23505":
                        return False  # duplicate delivery — DD-09's «تجاهُل»
                    raise
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return True


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver failure onto the shared error hierarchy (R6) — the
    ``SqlEventOutbox._translate`` precedent. No ``23505`` branch HERE: the
    duplicate key is ``claim``'s own normal ``False`` outcome, consumed at
    the savepoint before this translator ever sees it."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return AppError(
            "processed-events claim rejected: app role lacks INSERT on platform.processed_events",
            code="common.internal",
        )
    return AppError(
        "unexpected database error while claiming a processed event", code="common.internal"
    )
