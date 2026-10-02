"""The indexing queue's declared backpressure — capacity-plan step 5.3.

Three kinds of claim, separated the way ``test_heavy_job_limit.py`` separates
its own, because each can be false while the others are true:

* **the POLICY** — refuse past the ceiling with the contract's 429 and a
  ``Retry-After``, admit at or under it, admit when there is no reading, fail
  OPEN when the reading fails, and read the source at most once per refresh;
* **the PLACEMENT** — the gate is on exactly the operations that put work on
  ``stream.knowledge``, after the permission guard and BEFORE the job budget;
* **the BEHAVIOUR through a real route** — a refusal lands before the handler
  and spends nothing of the user's 30-job minute.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.middleware.heavy_jobs import heavy_job, knowledge_queue_open
from app.api.middleware.queue_backpressure import (
    KNOWLEDGE_GROUP,
    KNOWLEDGE_STREAM,
    QueueBackpressure,
)
from app.api.middleware.rate_limit import HEAVY_SCOPE, HeavyJobRateLimiter
from app.api.middleware.rbac import PermissionGuard
from app.framework.errors import RateLimitedError, ValidationError
from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.framework.ports.metrics_source import StreamRetention
from app.framework.ports.task_ledger import TaskRecord
from app.framework.settings import Settings
from tests.unit.support_rate_limit import InMemoryRateLimiter
from tests.unit.test_heavy_job_limit import _U1, _auth, _make_app, _operations

pytestmark = pytest.mark.anyio

_KEY = (KNOWLEDGE_STREAM, KNOWLEDGE_GROUP)


class _LagSource:
    """A ``MetricsSource`` that answers only the one question the gate asks,
    and counts how often it was asked."""

    def __init__(self, lag: float | None = 0.0) -> None:
        self.lag = lag
        self.failure: Exception | None = None
        self.reads = 0

    async def stream_queue_wait_seconds(self) -> dict[tuple[str, str], float]:
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return {} if self.lag is None else {_KEY: self.lag, ("stream.media", "cg.media"): 9e9}

    async def stream_lag_seconds(self) -> dict[tuple[str, str], float]:
        # The gate must NOT read this one -- `queue_backpressure.py` says why.
        raise AssertionError("the gate reads the queue wait, never the head gap")

    async def outbox_oldest_unpublished_age_seconds(self) -> float:
        raise AssertionError("not exercised")

    async def dlq_depths(self) -> dict[str, int]:
        raise AssertionError("not exercised")

    async def stream_retention(self) -> StreamRetention:
        raise AssertionError("not exercised")

    async def scheduled_tasks(self) -> dict[str, TaskRecord]:
        raise AssertionError("not exercised")


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _gate(
    source: _LagSource, *, ceiling: float = 120, clock: _Clock | None = None
) -> QueueBackpressure:
    return QueueBackpressure(
        source,
        stream=KNOWLEDGE_STREAM,
        group=KNOWLEDGE_GROUP,
        lag_ceiling_s=ceiling,
        retry_after_s=30,
        clock=clock or _Clock(),
    )


# --------------------------------------------------------------------------- #
# The policy                                                                   #
# --------------------------------------------------------------------------- #
async def test_a_queue_past_its_ceiling_refuses_with_a_retry_after() -> None:
    with pytest.raises(RateLimitedError) as caught:
        await _gate(_LagSource(lag=120.001)).check()

    assert caught.value.status == 429
    assert caught.value.retry_after_s == 30


@pytest.mark.parametrize("lag", [0.0, 2.4, 120.0])
async def test_a_queue_at_or_under_its_ceiling_admits(lag: float) -> None:
    await _gate(_LagSource(lag=lag)).check()


async def test_no_reading_admits() -> None:
    """Before the first publish there is no stream and no lag -- nothing to
    be behind. The port reports that by ABSENCE, and the gate must not read
    absence as saturation."""
    await _gate(_LagSource(lag=None)).check()


async def test_another_streams_lag_does_not_close_this_door() -> None:
    """The fake reports an enormous media lag beside a healthy knowledge one."""
    await _gate(_LagSource(lag=1.0)).check()


async def test_a_redis_outage_admits_rather_than_refusing() -> None:
    source = _LagSource(lag=9_999)
    source.failure = ConnectionError("redis is unreachable")

    await _gate(source).check()


async def test_the_source_is_read_at_most_once_per_refresh() -> None:
    clock = _Clock()
    source = _LagSource(lag=1.0)
    gate = _gate(source, clock=clock)

    for _ in range(50):
        await gate.check()
    assert source.reads == 1

    clock.now += 5.0
    source.lag = 500.0
    with pytest.raises(RateLimitedError):
        await gate.check()
    assert source.reads == 2


async def test_a_failed_read_is_not_cached() -> None:
    """An outage admits; the NEXT request must try again rather than ride a
    stale 'unknown' for the whole refresh window."""
    source = _LagSource(lag=500.0)
    source.failure = ConnectionError("down")
    gate = _gate(source)

    await gate.check()
    source.failure = None
    with pytest.raises(RateLimitedError):
        await gate.check()


@pytest.mark.parametrize(("ceiling", "retry"), [(0, 30), (-1, 30), (120, 0)])
def test_non_positive_numbers_are_refused_at_construction(ceiling: float, retry: int) -> None:
    with pytest.raises(ValidationError):
        QueueBackpressure(
            _LagSource(),
            stream=KNOWLEDGE_STREAM,
            group=KNOWLEDGE_GROUP,
            lag_ceiling_s=ceiling,
            retry_after_s=retry,
        )


def test_the_watched_pair_is_a_row_of_the_binding_table() -> None:
    """A renamed group would leave the gate reading a key that never exists
    -- which admits everything, silently."""
    assert any((b.stream, b.group) == _KEY for b in STATIC_CONSUMER_TOPOLOGY)


def test_the_defaults_are_the_declared_two_minutes() -> None:
    limits = Settings().rate_limit
    assert limits.queue_lag_ceiling_s == 120
    assert limits.queue_retry_after_s > 0


# --------------------------------------------------------------------------- #
# Placement                                                                    #
# --------------------------------------------------------------------------- #
def _gated(route: APIRoute) -> bool:
    return any(d.call is knowledge_queue_open for d in route.dependant.dependencies)


def _knowledge_202s(app: FastAPI) -> Iterator[tuple[str, str, APIRoute]]:
    for method, path, route in _operations(app):
        if route.status_code == 202 and path.startswith("/api/v1/knowledge/"):
            yield method, path, route


def test_the_gate_is_on_exactly_the_knowledge_queue_entrances() -> None:
    app, _stack = _make_app()

    gated = {(m, p) for m, p, r in _operations(app) if _gated(r)}
    entrances = {(m, p) for m, p, _r in _knowledge_202s(app)}

    assert gated == entrances
    assert len(entrances) == 3


def test_the_gate_sits_after_the_permission_and_before_the_job_budget() -> None:
    app, _stack = _make_app()

    for _method, path, route in _knowledge_202s(app):
        calls = [d.call for d in route.dependant.dependencies]
        permission = next(i for i, c in enumerate(calls) if isinstance(c, PermissionGuard))
        assert permission < calls.index(knowledge_queue_open) < calls.index(heavy_job), path


# --------------------------------------------------------------------------- #
# Behaviour through a real route                                               #
# --------------------------------------------------------------------------- #
def _client(lag: float | None) -> tuple[TestClient, InMemoryRateLimiter]:
    budget = InMemoryRateLimiter()
    app, _stack = _make_app(heavy_job_limiter=HeavyJobRateLimiter(budget, jobs_per_min=30))
    app.state.services = replace(app.state.services, queue_backpressure=_gate(_LagSource(lag)))
    return TestClient(app), budget


def test_a_saturated_queue_answers_the_wire_contracts_429_and_spends_no_budget() -> None:
    client, budget = _client(lag=600.0)

    response = client.post(
        "/api/v1/knowledge/documents", json={"file_id": "absent"}, headers=_auth()
    )

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["Retry-After"] == "30"
    assert response.json()["code"] == "common.rate_limited"
    assert budget.count(f"{HEAVY_SCOPE}:{_U1}") == 0


def test_a_healthy_queue_reaches_the_handler() -> None:
    """The handler runs and answers for the absent file -- anything but 429
    proves the door was open, and the job budget was charged."""
    client, budget = _client(lag=3.0)

    response = client.post(
        "/api/v1/knowledge/documents", json={"file_id": "absent"}, headers=_auth()
    )

    assert response.status_code != 429
    assert budget.count(f"{HEAVY_SCOPE}:{_U1}") == 1


def test_a_saturated_knowledge_queue_does_not_close_the_media_door() -> None:
    client, _budget = _client(lag=600.0)

    response = client.post(
        "/api/v1/media/jobs",
        json={
            "kind": "image",
            "prompt": "a cat",
            "agent_key": "image-agent",
            "params": {"width": 512, "height": 512},
        },
        headers=_auth(),
    )

    assert response.status_code == 202
