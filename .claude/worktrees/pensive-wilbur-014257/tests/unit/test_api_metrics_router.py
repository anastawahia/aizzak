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
    OUTBOX_AGE_METRIC,
    VAULT_AUTH_METRIC,
    metrics_router,
)


class _FakeMetricsSource:
    """A ``MetricsSource`` whose return values a test can change BETWEEN
    calls — see the module docstring's "what this proves" paragraph."""

    def __init__(self, *, outbox_age: float, dlq_depths: dict[str, int]) -> None:
        self.outbox_age = outbox_age
        self.dlq_depths_value = dlq_depths

    async def outbox_oldest_unpublished_age_seconds(self) -> float:
        return self.outbox_age

    async def dlq_depths(self) -> dict[str, int]:
        return dict(self.dlq_depths_value)


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
