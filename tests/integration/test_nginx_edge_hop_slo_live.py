"""P1-6 (``docs/p1-hardening-plan.md`` §3 step 13 · ``docs/log/3.92.md``) —
the ONE gap ``test_conversation_get_slo_live.py``'s in-process measurement
does not cover: the ``nginx → uvicorn/gunicorn`` edge hop.

That file drives the ASGI app in-process (``httpx.ASGITransport``) from
inside a throwaway container on the ``aizzak_default`` network — faithful to
every collaborator on the request path EXCEPT the reverse proxy itself, which
sits in front of the real ``app`` replica and which no in-process harness can
measure. This file measures ONLY that hop, against the real ``nginx``
container's published port, hitting the health path it already serves
unauthenticated (``08-local-runbook.md §3``) — no seeding, no
``CompositionRoot`` needed.

**Reported as an ADDITIVE component with its own distribution — explicitly
NOT folded into the P1-6 number, and explicitly NOT claimed to be zero.**
The mandate is small, honest, and separate: this is a real, measured
millisecond count for a hop the SLO row's own chain includes but the primary
measurement structurally cannot reach.

Same warm-up/sample discipline as ``test_conversation_get_slo_live.py`` (fewer
samples — this is a much cheaper hop with no database round trip, and the
budget it is checked against is a broad sanity ceiling, not `07-nfr-slo.md`'s
own row, which this file is not measuring).

Runs directly against the published host port — no docker network needed
(unlike the sibling file), so this one CAN run from a bare dev venv the
moment the live stack is up. Gated on the SAME ``RUN_P1_6_LOAD_TEST`` opt-in
as its sibling so both stay off under the bare gate-5 ``pytest`` run.
"""

from __future__ import annotations

import os
import socket
import ssl
import time
from dataclasses import dataclass

import httpx
import pytest

_ENABLE_VAR = "RUN_P1_6_LOAD_TEST"
_HOST_VAR = "LOAD_TEST_NGINX_HOST"
_PORT_VAR = "LOAD_TEST_NGINX_PORT"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 443
_PROBE_TIMEOUT_S = 1.5

_WARMUP_REQUESTS = 20
_MEASURED_REQUESTS = 200

# A broad sanity ceiling for a same-host TLS round trip to a static health
# endpoint — NOT `07-nfr-slo.md`'s own budget (this file measures a hop that
# document's table does not name a number for). Generous on purpose: this
# test exists to REPORT the distribution honestly, not to gate a number
# nobody has approved.
_SANITY_CEILING_S = 1.0


def _tcp_reachable(host: str, port: int, timeout_s: float = _PROBE_TIMEOUT_S) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _skip_reason() -> str | None:
    if not os.environ.get(_ENABLE_VAR):
        return f"set {_ENABLE_VAR}=1 to measure the live nginx edge hop (docs/log/3.92.md)"
    host = os.environ.get(_HOST_VAR, _DEFAULT_HOST)
    port = int(os.environ.get(_PORT_VAR, str(_DEFAULT_PORT)))
    if not _tcp_reachable(host, port):
        return f"nginx not reachable at {host}:{port} — bring the live stack up first"
    return None


_SKIP_REASON = _skip_reason()

pytestmark = [pytest.mark.live_stack_slo]


@pytest.fixture(autouse=True)
def _require_live_stack() -> None:
    """Fixture-based skip (the ``live_db``/``live_redis`` precedent), not a
    static ``skipif`` — see the sibling module's identical note."""
    if _SKIP_REASON is not None:
        pytest.skip(_SKIP_REASON)


@dataclass(frozen=True, slots=True)
class EdgeHopReport:
    samples: int
    warmup: int
    wall_s: float
    p50_s: float
    p95_s: float
    p99_s: float
    min_s: float
    max_s: float

    def render(self) -> str:
        return (
            "nginx → app edge hop — ADDITIVE, NOT folded into the P1-6 number, "
            "NOT claimed to be zero (this file's own docstring)\n"
            f"  samples={self.samples} warmup={self.warmup} wall={self.wall_s:.3f}s\n"
            f"  p50={self.p50_s * 1000:.2f}ms p95={self.p95_s * 1000:.2f}ms "
            f"p99={self.p99_s * 1000:.2f}ms "
            f"min={self.min_s * 1000:.2f}ms max={self.max_s * 1000:.2f}ms"
        )


def _percentile(sorted_samples: list[float], p: float) -> float:
    n = len(sorted_samples)
    index = min(int(n * p), n - 1)
    return sorted_samples[index]


async def measure_nginx_edge_hop() -> EdgeHopReport:
    host = os.environ.get(_HOST_VAR, _DEFAULT_HOST)
    port = int(os.environ.get(_PORT_VAR, str(_DEFAULT_PORT)))
    url = f"https://{host}:{port}/health"

    # Self-signed dev cert (08-local-runbook.md §3.2), `server_name _;` on
    # both nginx confs — no real hostname to verify against here, so
    # verification is off exactly the way any local `curl -k` probe of this
    # same endpoint already is (never the production posture — the point is
    # the TLS handshake + proxy hop's latency, not certificate trust).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    async with httpx.AsyncClient(verify=ctx, timeout=10.0) as client:
        for _ in range(_WARMUP_REQUESTS):
            response = await client.get(url)
            response.raise_for_status()

        latencies: list[float] = []
        start = time.perf_counter()
        for _ in range(_MEASURED_REQUESTS):
            request_start = time.perf_counter()
            response = await client.get(url)
            latencies.append(time.perf_counter() - request_start)
            response.raise_for_status()
        wall_s = time.perf_counter() - start

    latencies.sort()
    return EdgeHopReport(
        samples=len(latencies),
        warmup=_WARMUP_REQUESTS,
        wall_s=wall_s,
        p50_s=_percentile(latencies, 0.50),
        p95_s=_percentile(latencies, 0.95),
        p99_s=_percentile(latencies, 0.99),
        min_s=latencies[0],
        max_s=latencies[-1],
    )


def _assert_sanity_ceiling(report: EdgeHopReport) -> None:
    """One exit criterion shared by pytest and the documented ``__main__``."""
    ceiling_ms = _SANITY_CEILING_S * 1000
    assert report.p99_s <= _SANITY_CEILING_S, (
        f"p99 {report.p99_s * 1000:.2f}ms exceeds the {ceiling_ms:.0f}ms sanity ceiling"
    )


@pytest.mark.anyio
async def test_nginx_edge_hop_latency_is_measured_separately_and_not_folded_in() -> None:
    report = await measure_nginx_edge_hop()
    # `-s` is how an operator re-running this test SEES this evidence.
    print(report.render())
    # A sanity ceiling only — see the module docstring for why this is not
    # checked against `07-nfr-slo.md`'s own budget.
    _assert_sanity_ceiling(report)


if __name__ == "__main__":
    import asyncio

    reason = _skip_reason()
    if reason is not None:
        raise SystemExit(f"refusing to run: {reason}")
    edge_report = asyncio.run(measure_nginx_edge_hop())
    print(edge_report.render())
    _assert_sanity_ceiling(edge_report)
