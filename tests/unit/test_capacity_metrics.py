"""Wave 0 step 0.2 of ``docs/capacity-plan.md`` — the RED middleware, the
saturation sampler and the stream-lag reading, in isolation.

``ح-12`` is "لا قياس": nothing in this platform could say what a request cost
until this step, so nothing in that plan could be proved or disproved. These
tests pin the three properties that decide whether the new numbers are worth
trusting:

* the route label is a TEMPLATE and an unrouted request is one fixed string,
  because the failure mode of getting that wrong is monitoring that falls over
  before the thing it monitors does (``0.2``'s own cardinality warning);
* a request that RAISES is still counted, and counted as a 500 — the one class
  of request an error budget most needs and the one a naive middleware most
  easily drops;
* event-loop lag actually reflects a BLOCKED loop, since diagnosing ``ح-5``
  (a synchronous ``model.encode`` inside an async handler) is the entire
  reason that gauge exists.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from redis.exceptions import ResponseError

from app.api.middleware.metrics import RedMetricsMiddleware
from app.framework.observability.metrics import (
    DB_POOL_AVAILABLE_METRIC,
    DB_POOL_IN_USE_METRIC,
    DB_POOL_OVERFLOW_METRIC,
    EVENT_LOOP_LAG_METRIC,
    HTTP_DURATION_METRIC,
    HTTP_REQUESTS_METRIC,
    UNMATCHED_ROUTE,
    PoolStats,
    pool_stats_of,
    sample_process_metrics,
)
from app.infrastructure.monitoring.metrics_source import SqlRedisMetricsSource


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _requests(route: str, method: str, status: str) -> float:
    """The counter's current value, or 0.0 before its first observation.

    Read as a DELTA by every test below: these metrics are module-level
    globals on the default registry (they have to be — that is what makes the
    multiprocess mmap files work), so an absolute assertion would couple this
    module to whatever else the suite happened to run first.
    """
    # The constant already ends in `_total`, which is also the sample suffix
    # prometheus_client renders for a Counter -- it strips the suffix off the
    # metric name and puts it back on the sample, so the two agree and no
    # second `_total` belongs here.
    value = REGISTRY.get_sample_value(
        HTTP_REQUESTS_METRIC, {"route": route, "method": method, "status": status}
    )
    return 0.0 if value is None else value


def _duration_count(route: str, method: str) -> float:
    value = REGISTRY.get_sample_value(
        HTTP_DURATION_METRIC + "_count", {"route": route, "method": method}
    )
    return 0.0 if value is None else value


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RedMetricsMiddleware, routed=app)

    @app.get("/things/{thing_id}")
    async def _get_thing(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    @app.get("/boom")
    async def _boom() -> dict[str, str]:
        raise RuntimeError("deliberate")

    return app


# --------------------------------------------------------------------------- #
# The route label                                                             #
# --------------------------------------------------------------------------- #
def test_ten_distinct_ids_produce_one_time_series_not_ten() -> None:
    """The whole cardinality argument, as an assertion rather than a comment."""
    app = _build_app()
    before = _requests("/things/{thing_id}", "GET", "200")

    with TestClient(app) as client:
        for i in range(10):
            assert client.get(f"/things/id-{i}").status_code == 200

    assert _requests("/things/{thing_id}", "GET", "200") - before == 10
    # And the raw paths minted nothing of their own.
    assert _requests("/things/id-0", "GET", "200") == 0.0


def test_an_unrouted_path_is_labelled_once_however_many_urls_are_guessed() -> None:
    app = _build_app()
    before = _requests(UNMATCHED_ROUTE, "GET", "404")

    with TestClient(app) as client:
        for i in range(5):
            assert client.get(f"/no/such/path/{i}").status_code == 404

    assert _requests(UNMATCHED_ROUTE, "GET", "404") - before == 5


# --------------------------------------------------------------------------- #
# Failures are the point of an error budget                                   #
# --------------------------------------------------------------------------- #
def test_a_handler_that_raises_is_counted_as_a_500() -> None:
    app = _build_app()
    before = _requests("/boom", "GET", "500")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 500

    assert _requests("/boom", "GET", "500") - before == 1


def test_every_counted_request_is_also_timed() -> None:
    """The two families must not drift: a count without a duration would make
    every percentile a percentile of the subset that happened to be timed."""
    app = _build_app()
    before = _duration_count("/things/{thing_id}", "GET")

    with TestClient(app) as client:
        client.get("/things/x")
        client.get("/things/y")

    assert _duration_count("/things/{thing_id}", "GET") - before == 2


# --------------------------------------------------------------------------- #
# The pool reading                                                            #
# --------------------------------------------------------------------------- #
class _FakePool:
    def __init__(self, *, out: int, in_: int, overflow: int) -> None:
        self._out, self._in, self._overflow = out, in_, overflow

    def checkedout(self) -> int:
        return self._out

    def checkedin(self) -> int:
        return self._in

    def overflow(self) -> int:
        return self._overflow


def test_pool_stats_are_read_structurally() -> None:
    assert pool_stats_of(_FakePool(out=7, in_=3, overflow=2)) == PoolStats(
        in_use=7, available=3, overflow=2
    )


def test_a_pool_that_has_never_been_full_reports_zero_overflow_not_a_negative() -> None:
    """SQLAlchemy's `overflow()` starts at `-pool_size`. Published unclamped it
    would SUBTRACT from the `livesum` across gunicorn siblings, and §3's
    connection budget would be compared against a total lower than the real
    one — the one direction of error that matters here."""
    assert pool_stats_of(_FakePool(out=0, in_=5, overflow=-10)).overflow == 0


def test_a_pool_that_cannot_report_is_none_rather_than_an_exception() -> None:
    """NullPool and StaticPool are real configurations in this repo's tests.
    A gauge must never be the reason an app fails to start."""
    assert pool_stats_of(object()) is None


# --------------------------------------------------------------------------- #
# The saturation sampler                                                      #
# --------------------------------------------------------------------------- #
async def test_the_sampler_publishes_pool_gauges_on_its_first_window() -> None:
    task = asyncio.create_task(
        sample_process_metrics(
            _FakePool(out=4, in_=6, overflow=-1),
            report_interval_s=0.01,
            probe_interval_s=0.001,
        )
    )
    try:
        await _until(lambda: REGISTRY.get_sample_value(DB_POOL_IN_USE_METRIC) == 4.0)
    finally:
        task.cancel()

    assert REGISTRY.get_sample_value(DB_POOL_IN_USE_METRIC) == 4.0
    assert REGISTRY.get_sample_value(DB_POOL_AVAILABLE_METRIC) == 6.0
    assert REGISTRY.get_sample_value(DB_POOL_OVERFLOW_METRIC) == 0.0


async def test_event_loop_lag_sees_a_loop_blocked_by_a_synchronous_call() -> None:
    """`ح-5`'s exact shape, reproduced: a blocking call inside the loop. The
    gauge exists to make that diagnosable, so a test that only proved it
    publishes SOME number would prove nothing worth having."""
    blocked_for_s = 0.2
    task = asyncio.create_task(
        sample_process_metrics(report_interval_s=0.01, probe_interval_s=0.001)
    )
    try:
        await asyncio.sleep(0.02)
        time.sleep(blocked_for_s)
        await _until(
            lambda: (REGISTRY.get_sample_value(EVENT_LOOP_LAG_METRIC) or 0.0) >= blocked_for_s / 2
        )
    finally:
        task.cancel()

    observed = REGISTRY.get_sample_value(EVENT_LOOP_LAG_METRIC) or 0.0
    assert observed >= blocked_for_s / 2


async def _until(predicate: object, *, timeout_s: float = 5.0) -> None:
    """Poll rather than sleep-a-fixed-amount: a fixed sleep is either flaky on
    a loaded CI box or slow on every run, and this suite is neither."""
    assert callable(predicate)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    pytest.fail("condition never held within the timeout")


# --------------------------------------------------------------------------- #
# The stream-lag reading                                                      #
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Replies shaped like the real client's: `decode_responses=False`, so
    every key and value is `bytes` (`infrastructure/cache/redis_cache.py`).
    That detail is the whole reason this test exists — the id arithmetic is
    trivial and the byte handling is where it would silently return nothing."""

    def __init__(self, streams: dict[str, tuple[str, dict[str, str]]]) -> None:
        self._streams = streams

    async def xinfo_stream(self, name: str) -> dict[bytes, bytes]:
        if name not in self._streams:
            raise ResponseError("no such key")
        head, _ = self._streams[name]
        return {b"last-generated-id": head.encode()}

    async def xinfo_groups(self, name: str) -> list[dict[bytes, bytes]]:
        if name not in self._streams:
            raise ResponseError("no such key")
        _, groups = self._streams[name]
        return [
            {b"name": g.encode(), b"last-delivered-id": delivered.encode()}
            for g, delivered in groups.items()
        ]


def _source(streams: dict[str, tuple[str, dict[str, str]]]) -> SqlRedisMetricsSource:
    # The engine is never touched by `stream_lag_seconds`; passing `None` for
    # it keeps this a unit test of the Redis half rather than a reason to
    # stand up Postgres.
    return SqlRedisMetricsSource(None, _FakeRedis(streams))  # type: ignore[arg-type]


async def test_lag_is_the_gap_between_the_head_and_the_group_in_seconds() -> None:
    source = _source({"stream.knowledge": ("1700000012500-0", {"cg.knowledge": "1700000000000-0"})})
    assert await source.stream_lag_seconds() == {("stream.knowledge", "cg.knowledge"): 12.5}


async def test_a_caught_up_group_is_zero_however_old_the_last_entry_is() -> None:
    """The property an "age of the newest entry" metric would not have: on a
    quiet stream that one would climb forever while every consumer was idle
    and healthy, and an operator would learn to ignore it."""
    ancient = "1000000000000-0"
    source = _source({"stream.memory": (ancient, {"cg.memory": ancient})})
    assert await source.stream_lag_seconds() == {("stream.memory", "cg.memory"): 0.0}


async def test_a_stream_nothing_has_published_to_is_absent_not_zero() -> None:
    """`0.0` would be indistinguishable from a healthy caught-up group, i.e.
    it would assert something nobody measured."""
    assert await _source({}).stream_lag_seconds() == {}


async def test_a_group_ahead_of_the_head_never_reports_a_negative_lag() -> None:
    """Only reachable through clock skew inside Redis or a trimmed head, and
    a negative "seconds behind" is never a real value."""
    source = _source({"stream.media": ("1700000000000-0", {"cg.media": "1700000005000-0"})})
    assert await source.stream_lag_seconds() == {("stream.media", "cg.media"): 0.0}
