"""Hermetic tests for ``GET /metrics`` (P1-3, ``docs/p1-hardening-plan.md``
§3 step 10, plus ن-10's Vault-authentication gauge) — the rendering logic in
isolation, over fake ``MetricsSource``/``VaultHealth`` collaborators rather
than a live Postgres/Redis/Vault (the live ``MetricsSource`` wiring is proven
separately in ``tests/integration/test_metrics_source_live.py``; the probe's
own decision logic in ``tests/unit/test_vault_health_probe.py``).

Mounted directly on a bare ``FastAPI()`` rather than through the full
``create_app``: the router reads only ``request.app.state.metrics_source``
(``api/metrics.py``), so pulling in the whole ``ApiServices``/orchestrator
stack every other router test needs would test nothing this module does not
already cover on its own.

**What this specifically proves that a single scrape cannot.** Two
back-to-back requests against the SAME fake, configured to return two
DIFFERENT values, must render two DIFFERENT numbers — never the value from
the first call frozen into the second. That is the router-level half of the
design brief's central pitfall: a ``Gauge`` registered once at import time
and merely ``.set()`` on each request would still be correct for THIS
single-process test, but the fresh-``CollectorRegistry``-per-request shape
this test pins is exactly what stays correct once the same code runs inside
several sibling gunicorn workers (``api/metrics.py``'s own module docstring)
— a fake swapped mid-test is the cheapest way to prove "nothing survives
between requests" without standing up a second OS process.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client.parser import text_string_to_metric_families

from app.api.metrics import (
    DLQ_DEPTH_METRIC,
    OPS_TASK_ARMED_METRIC,
    OPS_TASK_EXPECTED_METRIC,
    OPS_TASK_LAST_FAILURE_METRIC,
    OPS_TASK_LAST_SUCCESS_METRIC,
    OPS_TASK_MAX_SUCCESS_AGE_METRIC,
    OUTBOX_AGE_METRIC,
    STREAM_LAG_METRIC,
    STREAM_LENGTH_METRIC,
    STREAM_MAXLEN_METRIC,
    STREAM_QUEUE_WAIT_METRIC,
    STREAM_UNCONSUMED_AGE_METRIC,
    STREAM_UNREAD_TRIMMED_METRIC,
    VAULT_AUTH_METRIC,
    metrics_router,
)
from app.framework.ports.metrics_source import StreamRetention
from app.framework.ports.task_ledger import TaskRecord

_NO_RETENTION = StreamRetention(
    lengths={}, backstop=None, oldest_unconsumed_age_s={}, unread_trimmed={}
)


class _FakeMetricsSource:
    """A ``MetricsSource`` whose return values a test can change BETWEEN
    calls — see the module docstring's "what this proves" paragraph."""

    def __init__(
        self,
        *,
        outbox_age: float,
        dlq_depths: dict[str, int],
        stream_lag: dict[tuple[str, str], float] | None = None,
        stream_queue_wait: dict[tuple[str, str], float] | None = None,
        stream_retention: StreamRetention = _NO_RETENTION,
        scheduled_tasks: dict[str, TaskRecord] | None = None,
    ) -> None:
        self.outbox_age = outbox_age
        self.dlq_depths_value = dlq_depths
        # Wave 0 step 0.2's fourth live gauge. Defaulted to empty so every
        # pre-existing construction of this fake keeps working unchanged, and
        # because "no stream has been published to yet" is a real state the
        # adapter reports the same way (see its own docstring).
        self.stream_lag_value = stream_lag or {}
        # Capacity 5.3 -- empty by default, the line above's reason.
        self.stream_queue_wait_value = stream_queue_wait or {}
        # Capacity 5.5 -- defaulted to "nothing published, no backstop" for the
        # same reason.
        self.stream_retention_value = stream_retention
        # Capacity 5.7 -- defaulted to "no task in the catalog" for the same
        # reason; the adapter itself always returns one record per task.
        self.scheduled_tasks_value = scheduled_tasks or {}

    async def outbox_oldest_unpublished_age_seconds(self) -> float:
        return self.outbox_age

    async def dlq_depths(self) -> dict[str, int]:
        return dict(self.dlq_depths_value)

    async def stream_lag_seconds(self) -> dict[tuple[str, str], float]:
        return dict(self.stream_lag_value)

    async def stream_queue_wait_seconds(self) -> dict[tuple[str, str], float]:
        return dict(self.stream_queue_wait_value)

    async def stream_retention(self) -> StreamRetention:
        return self.stream_retention_value

    async def scheduled_tasks(self) -> dict[str, TaskRecord]:
        return dict(self.scheduled_tasks_value)


class _FakeVaultHealth:
    """A ``VaultHealth`` whose answer a test controls — the port is
    contractually TOTAL (it never raises), so a fake that only ever returns a
    bool is a faithful double, not a convenient one."""

    def __init__(self, *, healthy: bool) -> None:
        self.healthy = healthy

    async def authenticated(self) -> bool:
        return self.healthy


def _build_app(source: object | None, vault_health: object | None = None) -> FastAPI:
    app = FastAPI()
    app.state.metrics_source = source
    app.state.vault_health = vault_health
    app.include_router(metrics_router)
    return app


def _metric_names(body: str) -> set[str]:
    return {family.name for family in text_string_to_metric_families(body)}


def _metric_value(body: str, name: str, *, labels: dict[str, str] | None = None) -> float:
    for family in text_string_to_metric_families(body):
        if family.name != name:
            continue
        for sample in family.samples:
            if labels is None or all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    raise AssertionError(f"metric {name!r} (labels={labels!r}) not found in:\n{body}")


def test_metrics_renders_both_gauges_from_the_source() -> None:
    source = _FakeMetricsSource(
        outbox_age=1.5,
        dlq_depths={"stream.knowledge": 3, "stream.media": 0},
    )
    client = TestClient(_build_app(source))

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    body = response.text
    assert _metric_value(body, OUTBOX_AGE_METRIC) == 1.5
    assert _metric_value(body, DLQ_DEPTH_METRIC, labels={"stream": "stream.knowledge"}) == 3
    assert _metric_value(body, DLQ_DEPTH_METRIC, labels={"stream": "stream.media"}) == 0


def test_metrics_reflects_a_changed_source_on_the_very_next_scrape() -> None:
    """The router-level proof that nothing is cached between requests --
    module docstring's central point."""
    source = _FakeMetricsSource(outbox_age=0.1, dlq_depths={"stream.knowledge": 0})
    client = TestClient(_build_app(source))

    first = client.get("/metrics")
    assert _metric_value(first.text, OUTBOX_AGE_METRIC) == 0.1
    assert _metric_value(first.text, DLQ_DEPTH_METRIC, labels={"stream": "stream.knowledge"}) == 0

    # The "reality underneath" changes -- an outbox row aged, a DLQ entry
    # arrived -- exactly the exit criterion's own wording ("صفٌّ غير منشورٍ
    # يشيخ ⇐ الزمن يرتفع؛ مدخلةٌ في DLQ ⇐ العمق يرتفع").
    source.outbox_age = 4.2
    source.dlq_depths_value = {"stream.knowledge": 1}

    second = client.get("/metrics")
    assert _metric_value(second.text, OUTBOX_AGE_METRIC) == 4.2
    assert _metric_value(second.text, DLQ_DEPTH_METRIC, labels={"stream": "stream.knowledge"}) == 1


def test_vault_gauge_renders_one_when_the_probe_says_authenticated() -> None:
    source = _FakeMetricsSource(outbox_age=0.0, dlq_depths={})
    client = TestClient(_build_app(source, _FakeVaultHealth(healthy=True)))

    body = client.get("/metrics").text

    assert _metric_value(body, VAULT_AUTH_METRIC) == 1.0


def test_vault_gauge_renders_zero_when_the_probe_says_unauthenticated() -> None:
    """The value that arms `AizzakVaultAuthFailing` (`expr:
    aizzak_vault_authenticated < 1`). It must be a rendered `0`, not an absent
    metric — Prometheus cannot alert on a sample that was never scraped."""
    source = _FakeMetricsSource(outbox_age=0.0, dlq_depths={})
    client = TestClient(_build_app(source, _FakeVaultHealth(healthy=False)))

    body = client.get("/metrics").text

    assert _metric_value(body, VAULT_AUTH_METRIC) == 0.0


def test_vault_gauge_is_omitted_entirely_when_no_probe_is_wired() -> None:
    """`create_app`'s default `vault_health=None` (every pre-existing 6.1
    router test's call site) must render the two P1-3 gauges exactly as before
    and simply not mention the third.

    **Omitted, never `0`.** Rendering `0` for an unwired probe would make "we
    never measured this" indistinguishable from "the Vault credential is
    dead", i.e. it would arm a `critical` alert on a fact nobody checked."""
    source = _FakeMetricsSource(outbox_age=2.5, dlq_depths={"stream.media": 4})
    client = TestClient(_build_app(source, None))

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.text
    assert VAULT_AUTH_METRIC not in _metric_names(body)
    assert VAULT_AUTH_METRIC not in body  # not even as a stray HELP/TYPE line
    # The other two are untouched by the third one's absence.
    assert _metric_value(body, OUTBOX_AGE_METRIC) == 2.5
    assert _metric_value(body, DLQ_DEPTH_METRIC, labels={"stream": "stream.media"}) == 4


def test_metrics_answers_503_when_no_source_is_wired() -> None:
    """`create_app`'s default `metrics_source=None` (every pre-existing 6.1
    router test's call site) must not crash the process -- a plain, honest
    503, never an unhandled exception."""
    client = TestClient(_build_app(None))

    response = client.get("/metrics")

    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# Wave 0 step 0.2 (docs/capacity-plan.md) — the stream-lag gauge              #
# --------------------------------------------------------------------------- #
def test_stream_lag_renders_one_series_per_stream_and_group() -> None:
    source = _FakeMetricsSource(
        outbox_age=0.0,
        dlq_depths={},
        stream_lag={("stream.knowledge", "cg.knowledge"): 12.5, ("stream.media", "cg.media"): 0.0},
    )
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    assert STREAM_LAG_METRIC in _metric_names(body)
    assert 'stream="stream.knowledge"' in body
    assert 'group="cg.knowledge"' in body
    assert "12.5" in body


def test_a_caught_up_group_renders_zero_and_an_unpublished_stream_renders_nothing() -> None:
    """The distinction the adapter pays for: `0.0` means measured-and-caught-up,
    and ABSENCE means no such stream exists yet. Collapsing them would assert a
    healthy consumer for a stream nothing has ever published to."""
    source = _FakeMetricsSource(
        outbox_age=0.0, dlq_depths={}, stream_lag={("stream.memory", "cg.memory"): 0.0}
    )
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    assert 'stream="stream.memory"' in body
    assert 'stream="stream.media"' not in body


def test_the_queue_wait_the_admission_gate_reads_is_on_the_scrape() -> None:
    """Capacity 5.3: a 429 from the indexing queue must be readable against
    the value that caused it -- the gate and this gauge share one port
    method."""
    source = _FakeMetricsSource(
        outbox_age=0.0,
        dlq_depths={},
        stream_lag={("stream.knowledge", "cg.knowledge"): 600.0},
        stream_queue_wait={("stream.knowledge", "cg.knowledge"): 3.25},
    )
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    assert STREAM_QUEUE_WAIT_METRIC in _metric_names(body)
    assert (
        f'{STREAM_QUEUE_WAIT_METRIC}{{group="cg.knowledge",stream="stream.knowledge"}} 3.25' in body
    )


# --------------------------------------------------------------------------- #
# Capacity 5.5 (docs/capacity-plan.md, ح-17) -- the four stream-trim gauges   #
# --------------------------------------------------------------------------- #
def test_stream_retention_renders_length_backstop_age_and_loss() -> None:
    retention = StreamRetention(
        lengths={"stream.knowledge": 1234, "stream.files": 7},
        backstop=100_000,
        oldest_unconsumed_age_s={
            ("stream.knowledge", "cg.knowledge"): 1200.0,
            ("stream.knowledge", "cg.notify"): 0.0,
        },
        unread_trimmed={("stream.knowledge", "cg.knowledge"): 0},
    )
    source = _FakeMetricsSource(outbox_age=0.0, dlq_depths={}, stream_retention=retention)
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    assert _metric_value(body, STREAM_LENGTH_METRIC, labels={"stream": "stream.knowledge"}) == 1234
    assert _metric_value(body, STREAM_LENGTH_METRIC, labels={"stream": "stream.files"}) == 7
    assert _metric_value(body, STREAM_MAXLEN_METRIC) == 100_000
    assert (
        _metric_value(
            body,
            STREAM_UNCONSUMED_AGE_METRIC,
            labels={"stream": "stream.knowledge", "group": "cg.knowledge"},
        )
        == 1200.0
    )
    assert (
        _metric_value(
            body,
            STREAM_UNCONSUMED_AGE_METRIC,
            labels={"stream": "stream.knowledge", "group": "cg.notify"},
        )
        == 0.0
    )
    assert (
        _metric_value(
            body,
            STREAM_UNREAD_TRIMMED_METRIC,
            labels={"stream": "stream.knowledge", "group": "cg.knowledge"},
        )
        == 0
    )


def test_a_switched_off_backstop_is_absent_not_zero() -> None:
    """``STREAM_MAXLEN=0`` means no cap. Rendered as ``0``, the backstop
    rule's ratio would read every stream as infinitely full; absent, the rule
    has nothing to divide by and stays quiet -- which is the truth."""
    retention = StreamRetention(
        lengths={"stream.knowledge": 10},
        backstop=None,
        oldest_unconsumed_age_s={},
        unread_trimmed={},
    )
    source = _FakeMetricsSource(outbox_age=0.0, dlq_depths={}, stream_retention=retention)
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    names = _metric_names(body)
    assert STREAM_LENGTH_METRIC in names
    assert STREAM_MAXLEN_METRIC not in names


# --------------------------------------------------------------------------
# Capacity 5.7 -- the scheduled-task ledger.
# --------------------------------------------------------------------------


def _task_series(body: str, name: str) -> dict[str, float]:
    return {
        sample.labels["task"]: sample.value
        for family in text_string_to_metric_families(body)
        if family.name == name
        for sample in family.samples
    }


def test_every_task_is_expected_and_each_timestamp_appears_only_once_it_happened() -> None:
    """Three tasks in three states: one never armed, one armed and never run,
    one that succeeded and then failed. ``expected`` names all three -- that
    is what lets the never-armed rule see the first -- and every other gauge
    names only the tasks for which its moment actually happened: a ``0``
    would read as 1970, i.e. as the most overdue task there could be."""
    records = {
        "retention": TaskRecord(task="retention"),
        "purge": TaskRecord(task="purge", interval_s=86_400.0, armed_at=1_000.0),
        "backup": TaskRecord(
            task="backup",
            interval_s=86_400.0,
            max_runtime_s=7_200.0,
            armed_at=1_000.0,
            last_success_at=2_000.0,
            last_failure_at=3_000.0,
        ),
    }
    source = _FakeMetricsSource(outbox_age=0.0, dlq_depths={}, scheduled_tasks=records)
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    assert _task_series(body, OPS_TASK_EXPECTED_METRIC) == {
        "retention": 1.0,
        "purge": 1.0,
        "backup": 1.0,
    }
    assert _task_series(body, OPS_TASK_ARMED_METRIC) == {"purge": 1_000.0, "backup": 1_000.0}
    assert _task_series(body, OPS_TASK_LAST_SUCCESS_METRIC) == {"backup": 2_000.0}
    assert _task_series(body, OPS_TASK_LAST_FAILURE_METRIC) == {"backup": 3_000.0}
    # Two cycles plus one run: 2 x 86,400 + 7,200.
    assert _task_series(body, OPS_TASK_MAX_SUCCESS_AGE_METRIC) == {
        "purge": 172_800.0,
        "backup": 180_000.0,
    }


def test_a_switched_off_task_has_no_deadline() -> None:
    """A cycle of 0 is the kill switch: armed, expected, and not late for
    anything -- no deadline series, so the overdue rule cannot match it."""
    records = {"purge": TaskRecord(task="purge", interval_s=0.0, armed_at=1_000.0)}
    source = _FakeMetricsSource(outbox_age=0.0, dlq_depths={}, scheduled_tasks=records)
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    assert _task_series(body, OPS_TASK_ARMED_METRIC) == {"purge": 1_000.0}
    assert _task_series(body, OPS_TASK_MAX_SUCCESS_AGE_METRIC) == {}


def test_the_task_label_is_never_job() -> None:
    """``job`` is the label Prometheus stamps with the SCRAPE job's name; an
    exposed ``job`` would be renamed ``exported_job`` on ingestion and every
    ``max by (job)`` in the rules would group by ``aizzak-app`` instead."""
    records = {"retention": TaskRecord(task="retention", interval_s=60.0, armed_at=1.0)}
    source = _FakeMetricsSource(outbox_age=0.0, dlq_depths={}, scheduled_tasks=records)
    with TestClient(_build_app(source)) as client:
        body = client.get("/metrics").text

    for family in text_string_to_metric_families(body):
        if family.name.startswith("aizzak_ops_task_"):
            for sample in family.samples:
                assert set(sample.labels) == {"task"}, (family.name, sample.labels)
