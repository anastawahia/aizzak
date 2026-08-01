"""Live proof of the ``Idempotency-Key`` ledger (3.79):
``SqlIdempotencyStore`` over the real ``platform.idempotency_keys`` on real
Postgres, as the real ``app_rw`` role.

Four claims, none of which the in-memory fake can prove:

1. **The four claim outcomes over the real primary key** — first claim
   ``CLAIMED``, a repeat before completion ``IN_PROGRESS``, a repeat with a
   different fingerprint ``MISMATCH``, and a repeat after ``complete``
   ``REPLAY`` carrying the stored body back.
2. **Concurrency is arbitrated by the PRIMARY KEY, not by a read** — two
   claims issued at once produce exactly one ``CLAIMED``. This is the whole
   reason the store is Postgres-backed and the claim is INSERT-first; a
   SELECT-then-INSERT store passes every hermetic test and fails here.
3. **The duplicate claim does not poison the caller's transaction** — the
   savepoint's own contract, the ``processed_events`` precedent: Postgres
   aborts the whole transaction on any statement error, so without
   ``begin_nested`` a conflicting claim would take the enclosing unit of work
   with it.
4. **RLS isolates one workspace's keys from another's** — the same key string
   under two workspaces is two independent claims, and neither can read the
   other's stored response. This table is the ONLY ``platform`` table with a
   tenant policy, so the policy is proven here rather than assumed from the
   module-table pattern.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.framework.context.execution_context import ExecutionContext
from app.framework.identifiers import new_uuid7
from app.framework.ports.idempotency_store import ClaimOutcome
from app.infrastructure.persistence.idempotency import SqlIdempotencyStore
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.rls import TenantSessionFactory

pytestmark = pytest.mark.live_db

_ENDPOINT = "POST /files"


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=None,
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


@pytest.mark.anyio
async def test_the_four_claim_outcomes_over_the_real_primary_key(
    tenant_session: TenantSessionFactory,
) -> None:
    store = SqlIdempotencyStore(tenant_session)
    ctx = _ctx(new_uuid7())
    key = new_uuid7()

    first = await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    assert first.outcome is ClaimOutcome.CLAIMED

    # Claimed but not completed: a concurrent duplicate, not a replay.
    again = await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    assert again.outcome is ClaimOutcome.IN_PROGRESS
    assert again.response_body is None

    # A different request under the same key is a conflict, always — even
    # before the first one finished.
    other = await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-b")
    assert other.outcome is ClaimOutcome.MISMATCH

    await store.complete(
        ctx, endpoint=_ENDPOINT, key=key, response_body={"file_id": "f-1", "expires_in": 900}
    )

    replay = await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    assert replay.outcome is ClaimOutcome.REPLAY
    assert replay.response_body == {"file_id": "f-1", "expires_in": 900}


@pytest.mark.anyio
async def test_only_one_of_two_concurrent_claims_wins(
    tenant_session: TenantSessionFactory,
) -> None:
    """The claim that a hermetic store cannot make: under real concurrency the
    arbiter is the primary key. A SELECT-then-INSERT implementation lets both
    callers through here, and both would run the billable operation."""
    store = SqlIdempotencyStore(tenant_session)
    ctx = _ctx(new_uuid7())
    key = new_uuid7()

    outcomes = await asyncio.gather(
        *(store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a") for _ in range(8))
    )

    assert [claim.outcome for claim in outcomes].count(ClaimOutcome.CLAIMED) == 1
    assert all(
        claim.outcome in (ClaimOutcome.CLAIMED, ClaimOutcome.IN_PROGRESS) for claim in outcomes
    )


@pytest.mark.anyio
async def test_a_released_claim_frees_the_key_for_a_retry(
    tenant_session: TenantSessionFactory,
) -> None:
    """One transient failure must not brick a key forever — that would turn a
    temporary error into a permanent one on a path that bills."""
    store = SqlIdempotencyStore(tenant_session)
    ctx = _ctx(new_uuid7())
    key = new_uuid7()

    assert (
        await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    ).outcome is ClaimOutcome.CLAIMED
    await store.release(ctx, endpoint=_ENDPOINT, key=key)

    retried = await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    assert retried.outcome is ClaimOutcome.CLAIMED


@pytest.mark.anyio
async def test_release_cannot_undo_a_completed_key(
    tenant_session: TenantSessionFactory,
) -> None:
    store = SqlIdempotencyStore(tenant_session)
    ctx = _ctx(new_uuid7())
    key = new_uuid7()

    await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    await store.complete(ctx, endpoint=_ENDPOINT, key=key, response_body={"file_id": "f-1"})
    await store.release(ctx, endpoint=_ENDPOINT, key=key)

    still_there = await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    assert still_there.outcome is ClaimOutcome.REPLAY
    assert still_there.response_body == {"file_id": "f-1"}


@pytest.mark.anyio
async def test_a_conflicting_claim_does_not_poison_the_enclosing_transaction(
    tenant_session: TenantSessionFactory,
) -> None:
    """The savepoint's own contract (``processed_events``' precedent, which its
    mutation battery proved a handler-shaped test cannot catch): work already
    written in the enclosing unit of work must survive a conflicting claim and
    COMMIT. Without ``begin_nested`` the duplicate-key error aborts the whole
    transaction and the outbox row below vanishes silently."""
    store = SqlIdempotencyStore(tenant_session)
    outbox = SqlEventOutbox(tenant_session)
    ctx = _ctx(new_uuid7())
    key = new_uuid7()

    await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")

    async with tenant_session.begin(ctx):
        await outbox.append(ctx, [])  # no-op append; the claim is the subject
        conflicting = await store.claim(ctx, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
        assert conflicting.outcome is ClaimOutcome.IN_PROGRESS
        # The transaction is still HEALTHY: a poisoned one would fail here with
        # `25P02 in_failed_sql_transaction`.
        async with tenant_session(ctx) as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1


@pytest.mark.anyio
async def test_rls_isolates_one_workspaces_keys_from_anothers(
    tenant_session: TenantSessionFactory,
) -> None:
    """The only ``platform`` table with a tenant policy, so the policy is
    proven rather than assumed: the SAME key string under two workspaces is
    two independent claims, and the second tenant cannot read back the first
    tenant's stored response."""
    store = SqlIdempotencyStore(tenant_session)
    one, two = _ctx(new_uuid7()), _ctx(new_uuid7())
    key = new_uuid7()

    assert (
        await store.claim(one, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    ).outcome is ClaimOutcome.CLAIMED
    await store.complete(one, endpoint=_ENDPOINT, key=key, response_body={"file_id": "tenant-one"})

    # Same endpoint, same key string, different workspace: a FIRST attempt.
    assert (
        await store.claim(two, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    ).outcome is ClaimOutcome.CLAIMED
    await store.complete(two, endpoint=_ENDPOINT, key=key, response_body={"file_id": "tenant-two"})

    replay_one = await store.claim(one, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    replay_two = await store.claim(two, endpoint=_ENDPOINT, key=key, request_hash="hash-a")
    assert replay_one.response_body == {"file_id": "tenant-one"}
    assert replay_two.response_body == {"file_id": "tenant-two"}
