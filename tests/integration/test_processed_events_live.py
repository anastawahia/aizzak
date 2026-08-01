"""Live proof of the DD-09 idempotency ledger (5.2-أ):
``SqlProcessedEventLedger`` over the real ``platform.processed_events`` on
real Postgres, as the real ``app_rw`` role.

Three claims are proven here, none of which a hermetic fake can prove:

1. **Claim semantics on the real PK** — first ``claim`` per
   ``(consumer_group, event_id)`` is ``True``, a repeat is ``False``, and a
   DIFFERENT group's claim on the same event is ``True`` (each group owns an
   independent idempotency history, 04 §2's consumer-group topology).
2. **The claim rolls back WITH the unit of work** — DD-09's whole point
   («داخل معاملة الأثر»): a crash after claiming but before the effect
   commits must leave NO claim behind, or redelivery would skip a
   never-performed effect forever.
3. **Least privilege, both directions** — ``app_rw`` CAN insert (tests 1/2
   run as it) but CANNOT read the ledger back or delete from it: a
   compromised producer/worker role can neither enumerate what the platform
   processed nor un-process an event to force a replay
   (``SqlProcessedEventLedger``'s own docstring; the ``outbox_relay``
   bidirectional-grant proof precedent, ``docs/log/3.45.md``).

The full pipeline proof (double-published event ⇒ one document, through the
real engine + register handler) lives in ``test_e2e_outbox_to_worker.py``,
next to the rest of the AC-08 chain.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from tests.integration.conftest import LiveDbDsns

pytestmark = pytest.mark.live_db


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=None,
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


@pytest.mark.anyio
async def test_first_claim_wins_repeat_loses_and_groups_are_independent(
    tenant_session: TenantSessionFactory,
) -> None:
    ledger = SqlProcessedEventLedger(tenant_session)
    ctx = _ctx(new_uuid7())
    event_id = new_uuid7()

    assert await ledger.claim(ctx, consumer_group="cg.knowledge", event_id=event_id) is True
    assert await ledger.claim(ctx, consumer_group="cg.knowledge", event_id=event_id) is False
    # A different group processing the SAME event is a first delivery to IT.
    assert await ledger.claim(ctx, consumer_group="cg.notify", event_id=event_id) is True


@pytest.mark.anyio
async def test_a_claim_rolls_back_with_the_unit_of_work(
    tenant_session: TenantSessionFactory,
) -> None:
    """Claim inside ``uow.begin``, then blow the block up: the rollback must
    take the claim with it, so the retried delivery claims ``True`` again --
    the crash-mid-handler redelivery path, live."""
    ledger = SqlProcessedEventLedger(tenant_session)
    ctx = _ctx(new_uuid7())
    event_id = new_uuid7()

    with pytest.raises(RuntimeError, match="handler died mid-effect"):
        async with tenant_session.begin(ctx):
            assert await ledger.claim(ctx, consumer_group="cg.knowledge", event_id=event_id) is True
            raise RuntimeError("handler died mid-effect")

    # Rolled back with the transaction -- the redelivery starts clean.
    assert await ledger.claim(ctx, consumer_group="cg.knowledge", event_id=event_id) is True


@pytest.mark.anyio
async def test_a_duplicate_claim_does_not_poison_the_enclosing_transaction(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
) -> None:
    """The savepoint's OWN contract (``SqlProcessedEventLedger.claim``'s
    docstring), tested on the primitive rather than through any handler:
    work already written in the enclosing unit of work must survive a
    ``False`` claim and COMMIT.

    The handlers all happen to claim FIRST (a policy of theirs), which makes
    this difference invisible through them -- the 5.2-أ mutation battery's
    savepoint-removal mutant survived every handler-shaped test for exactly
    that reason. Without the savepoint, the duplicate-key error aborts the
    WHOLE transaction (Postgres abort-on-error), and the outbox row written
    below would be silently rolled back on exit instead of committed.
    """
    ledger = SqlProcessedEventLedger(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    ctx = _ctx(new_uuid7())
    event_id = new_uuid7()

    # Commit the claim once, standalone -- the next claim is a duplicate.
    assert await ledger.claim(ctx, consumer_group="cg.knowledge", event_id=event_id) is True

    outbox_event_id = new_uuid7()
    record = OutboxRecord(
        event_id=outbox_event_id,
        aggregate_type="document",
        aggregate_id=new_uuid7(),
        event_type="knowledge.document.registered.v1",
        stream="stream.test.savepoint",
        payload=build_envelope(
            event_id=outbox_event_id,
            source="knowledge",
            event_type="knowledge.document.registered.v1",
            subject="doc-1",
            occurred_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
            workspace_id=ctx.workspace_id,
            data={"document_id": "doc-1", "file_id": "file-1"},
        ),
    )

    async with tenant_session.begin(ctx):
        await outbox.append(ctx, [record])  # a REAL write, before the claim
        assert await ledger.claim(ctx, consumer_group="cg.knowledge", event_id=event_id) is False

    # The block exited cleanly, so the write must have COMMITTED -- a
    # poisoned transaction would have rolled it back silently.
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM platform.outbox WHERE id = :id"),
                    {"id": outbox_event_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert count == 1


@pytest.mark.anyio
async def test_app_rw_can_neither_read_nor_delete_the_ledger(
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """The INSERT-only grant, proven in the denying direction (the
    ``outbox_relay`` role-split precedent): SELECT and DELETE as ``app_rw``
    must both die with ``42501`` (insufficient_privilege)."""
    for statement in (
        "SELECT count(*) FROM platform.processed_events",
        "DELETE FROM platform.processed_events",
    ):
        with pytest.raises(DBAPIError) as exc_info:
            async with sessionmaker_app() as session, session.begin():
                await session.execute(text(statement))
        assert getattr(exc_info.value.orig, "sqlstate", None) == "42501", statement
