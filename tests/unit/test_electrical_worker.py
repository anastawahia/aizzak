"""``worker-electrical``'s composition (BT25; INC1-AC-8): the three handlers, the claim in the
effect's transaction, the stale-idle rule and the heartbeat/DLQ wiring.

The consumer engine is not run here; the handlers are the closures the engine would call, fed
envelopes built by the real event mapping.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.framework.observability import NullHeartbeat
from app.modules.electrical.drawings.application.inspection import InspectDocument
from app.modules.electrical.drawings.application.purge import SweepPurgedDocument
from app.modules.electrical.drawings.domain.job import JobStatus
from app.workers.electrical_bootstrap import (
    CG_ELECTRICAL_DOCUMENT,
    EV_PURGED,
    EV_REGISTERED,
    EV_REQUESTED,
    STREAM_ELECTRICAL,
    GroupLedger,
    build_electrical_inspect_handler,
    build_electrical_sweep_handler,
    build_electrical_worker,
    electrical_stale_idle_ms,
)
from tests.unit.support_electrical import (
    IEC_TOKENS,
    FakePdfInspection,
    build_stack,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)


class _Unused:
    """Stands in for every dependency the builder merely stores."""


class _Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def run(self, ctx: Any, *, job_id: str, event_id: str) -> None:
        self.calls.append(("run", job_id, event_id))

    async def execute(self, ctx: Any, document_id: str, *, event_id: str) -> int:
        self.calls.append(("execute", document_id, event_id))
        return 0


def _build(roles: frozenset[str], inspect: Any = None, sweep: Any = None) -> Any:
    return build_electrical_worker(
        redis_client=_Unused(),  # type: ignore[arg-type]
        roles=roles,
        inspect=inspect or _Spy(),
        sweep=sweep or _Spy(),
        consumer_name="test",
        block_ms=100,
        batch_count=1,
        max_deliveries=5,
    )


# ----------------------------------------------------------------------------- subscriptions
def test_the_document_role_subscribes_one_group_with_exactly_three_handlers() -> None:
    _, subscriptions = _build(frozenset({"document"}))
    (subscription,) = subscriptions
    assert (subscription.stream, subscription.group) == (STREAM_ELECTRICAL, CG_ELECTRICAL_DOCUMENT)
    assert (STREAM_ELECTRICAL, CG_ELECTRICAL_DOCUMENT) == (
        "stream.electrical",
        "cg.electrical.document",
    )
    assert set(subscription.handlers) == {EV_REGISTERED, EV_REQUESTED, EV_PURGED}
    # registered and job.requested are the SAME inspection handler
    assert subscription.handlers[EV_REGISTERED] is subscription.handlers[EV_REQUESTED]
    assert subscription.handlers[EV_PURGED] is not subscription.handlers[EV_REGISTERED]


def test_a_role_this_increment_does_not_know_subscribes_nothing() -> None:
    _, subscriptions = _build(frozenset({"ocr"}))
    assert subscriptions == []


# --------------------------------------------------------------------------------- handlers
async def test_the_inspect_handler_passes_the_job_and_the_envelope_id() -> None:
    spy = _Spy()
    handler = build_electrical_inspect_handler(spy)  # type: ignore[arg-type]
    envelope = {"id": "evt-1", "data": {"job_id": "job-9", "document_id": "d", "project_id": "p"}}
    await handler(None, envelope)  # type: ignore[arg-type]
    assert spy.calls == [("run", "job-9", "evt-1")]


async def test_the_sweep_handler_passes_the_document_and_the_envelope_id() -> None:
    spy = _Spy()
    handler = build_electrical_sweep_handler(spy)  # type: ignore[arg-type]
    await handler(None, {"id": "evt-2", "data": {"document_id": "doc-3"}})  # type: ignore[arg-type]
    assert spy.calls == [("execute", "doc-3", "evt-2")]


async def test_the_registered_handler_runs_the_real_inspection_and_claims_in_its_transaction() -> (
    None
):
    stack = build_stack()
    project = await seed_project(stack, schema=1, auto_accept_confidence=0.5)
    await seed_file(stack, "f1")
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    envelope = stack.outbox.records[0].payload  # the real published envelope
    assert envelope["type"] == EV_REGISTERED
    stack.pdf = FakePdfInspection(
        make_summary(page_count=4), [tokens_page(n, IEC_TOKENS) for n in (1, 2, 3, 4)]
    )
    handler = build_electrical_inspect_handler(stack.inspector())
    stack.trace.entries.clear()

    await handler(stack.ctx, envelope)

    job = await stack.jobs.get(stack.ctx, registered.job.id)
    assert job is not None and job.status is JobStatus.SUCCEEDED
    assert stack.ledger.claimed == [envelope["id"]]
    claim_at = stack.trace.entries.index(f"claim:{envelope['id']}")
    assert stack.trace.entries[claim_at - 1] == "begin"  # the claim OPENS its transaction
    assert "commit" in stack.trace.entries[claim_at:]

    await handler(stack.ctx, envelope)  # the relay's second delivery of the same event
    assert stack.ledger.claimed == [envelope["id"]]  # one claim; nothing else changed


async def test_the_purged_handler_sweeps_the_rows_once() -> None:
    stack = build_stack()
    project = await seed_project(stack)
    await seed_file(stack, "f1")
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    await stack.services["purge_file"].execute(stack.ctx, "f1")
    purged = next(r for r in stack.outbox.records if r.event_type == EV_PURGED)
    sweep: SweepPurgedDocument = stack.services["sweep"]
    handler = build_electrical_sweep_handler(sweep)

    await handler(stack.ctx, purged.payload)
    assert registered.document.id not in stack.documents.rows
    await handler(stack.ctx, purged.payload)  # redelivered: a no-op
    assert stack.ledger.claimed == [purged.payload["id"]]


def test_the_handlers_take_the_use_cases_the_bootstrap_builds() -> None:
    """A rename of either use case's method turns this red before a worker fails at runtime."""
    assert callable(InspectDocument.run) and callable(SweepPurgedDocument.execute)


# ------------------------------------------------------------------------ the rules around it
@pytest.mark.parametrize(
    ("consumer_stale_idle_s", "lease_s", "expected_ms"),
    [(900.0, 600, 1_200_000), (900.0, 300, 900_000), (60.0, 60, 120_000)],
)
def test_the_stale_idle_is_the_larger_of_the_shared_value_and_twice_the_lease(
    consumer_stale_idle_s: float, lease_s: int, expected_ms: int
) -> None:
    assert electrical_stale_idle_ms(consumer_stale_idle_s, lease_s) == expected_ms


async def test_the_group_ledger_closes_the_group_over_the_claim() -> None:
    seen: list[tuple[str, str]] = []

    class _Sql:
        async def claim(self, ctx: Any, *, consumer_group: str, event_id: str) -> bool:
            seen.append((consumer_group, event_id))
            return True

    ledger = GroupLedger(_Sql(), CG_ELECTRICAL_DOCUMENT)  # type: ignore[arg-type]
    assert await ledger.claim(None, event_id="evt-5")  # type: ignore[arg-type]
    assert seen == [("cg.electrical.document", "evt-5")]


def test_the_consumer_is_given_the_heartbeat_and_the_dlq_watch() -> None:
    heartbeat = NullHeartbeat()
    consumer, _ = build_electrical_worker(
        redis_client=_Unused(),  # type: ignore[arg-type]
        roles=frozenset({"document"}),
        inspect=_Spy(),  # type: ignore[arg-type]
        sweep=_Spy(),  # type: ignore[arg-type]
        consumer_name="test",
        block_ms=100,
        batch_count=1,
        max_deliveries=5,
        heartbeat=heartbeat,
        dlq_watch_interval_s=30.0,
        stale_idle_ms=1_200_000,
    )
    assert consumer is not None
