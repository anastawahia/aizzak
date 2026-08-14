"""Every long-running Compose service must declare a healthcheck, and every
edge that can wait for readiness must (ت-3, ``docs/operational-findings.md``
§3).

**The measured state this replaces.** A live reading on 2026-08-13 found six
of thirteen running services with no healthcheck at all -- ``qdrant``,
``pgbouncer``, ``ollama-bridge``, ``outbox-relay`` and two workers (three,
once ``worker-media`` joined the default stack in §3.134). Two consequences
were concrete rather than theoretical: ``depends_on`` could express nothing
stronger than ``service_started`` for the pooler and the vector store, and a
worker whose loop had stopped was indistinguishable from a healthy one.

**Why a test and not a checklist.** The gap did not arrive in one commit; it
grew one service at a time, each addition individually reasonable. This module
is the thing that makes the NEXT service declare its liveness at birth -- the
same reasoning as ``test_deploy_worker_default.py`` and
``test_edge_hardening.py``: two files claiming to agree, with nothing that
checked.

Textual/structural only -- it reads ``docker-compose.yml`` and asserts about
its shape. That the commands actually PASS against the running images is a
live measurement, recorded in ``docs/log/3.136.md``, not something a unit test
can claim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.framework.observability.heartbeat import HEARTBEAT_PROCESS_NAMES

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Services that run to completion and exit. Docker only ever reports a
# healthcheck on a RUNNING container, so a check on a one-shot service is
# unobservable by construction -- their contract is the exit code, which
# `depends_on: {condition: service_completed_successfully}` already gates on.
_ONE_SHOT = frozenset({"migrate", "vault-bootstrap", "minio-bootstrap", "nginx-certs"})

# The four processes whose liveness is a heartbeat file rather than a port:
# Compose service name -> the process name passed to `app.ops.healthcheck`.
_HEARTBEAT_SERVICES = {
    "worker-memory": "memory",
    "worker-knowledge": "knowledge",
    "worker-media": "media",
    "outbox-relay": "outbox-relay",
}


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    """PyYAML resolves the ``<<`` merge keys the file uses for
    ``x-worker-service``/``x-heartbeat-healthcheck``, so what these tests see
    is the EFFECTIVE service definition -- the same thing Compose sees, not
    the literal text of each block."""
    loaded = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    services: dict[str, Any] = loaded["services"]
    return services


def test_every_long_running_service_declares_a_healthcheck(compose: dict[str, Any]) -> None:
    """The ت-3 invariant itself. A new service either declares how it reports
    liveness, or declares itself one-shot in `_ONE_SHOT` above."""
    missing = sorted(
        name
        for name, spec in compose.items()
        if name not in _ONE_SHOT and "healthcheck" not in spec
    )

    assert missing == [], (
        f"services with no healthcheck: {missing}. Add one, or -- if the service "
        f"runs to completion and exits -- add it to _ONE_SHOT in this module."
    )


def test_one_shot_services_are_actually_one_shot(compose: dict[str, Any]) -> None:
    """Guards the exemption list from becoming a place to hide a real service:
    every name in it must actually declare `restart: "no"`."""
    for name in _ONE_SHOT:
        assert compose[name].get("restart") == "no", (
            f"{name} is exempted from the healthcheck rule as a one-shot service, "
            f'but does not declare `restart: "no"`'
        )


def test_heartbeat_checks_name_a_process_the_workers_actually_beat_as(
    compose: dict[str, Any],
) -> None:
    """The two halves of the file-based check -- the name the process stamps
    under, and the name the healthcheck reads -- live in different files. A
    mismatch would be a check that fails forever against a file nobody
    writes."""
    for service, process in _HEARTBEAT_SERVICES.items():
        test = compose[service]["healthcheck"]["test"]
        assert test[:3] == ["CMD", "python", "-m"], f"{service}: unexpected healthcheck shape"
        assert test[3] == "app.ops.healthcheck", f"{service}: not the heartbeat checker"
        assert test[4] == process, f"{service}: checks {test[4]!r}, should check {process!r}"
        assert process in HEARTBEAT_PROCESS_NAMES


def test_worker_services_check_the_same_process_they_run(compose: dict[str, Any]) -> None:
    """`WORKER=media` with a healthcheck reading `memory`'s file would report
    a dead media worker as healthy for as long as the memory worker lives."""
    for service in ("worker-memory", "worker-knowledge", "worker-media"):
        assert compose[service]["environment"]["WORKER"] == _HEARTBEAT_SERVICES[service]


@pytest.mark.parametrize("dependency", ["pgbouncer", "qdrant"])
def test_readiness_gated_dependencies_are_never_merely_started(
    compose: dict[str, Any], dependency: str
) -> None:
    """These two gained a healthcheck precisely so their edges could stop
    saying `service_started`, which §3.111 recorded in writing as a known
    looseness ("started != accepting connections"). Any NEW edge to them must
    not reintroduce it."""
    for name, spec in compose.items():
        condition = spec.get("depends_on", {}).get(dependency)
        if condition is None:
            continue
        assert condition["condition"] == "service_healthy", (
            f"{name} -> {dependency} is `{condition['condition']}`; both services now "
            f"have a healthcheck, so the edge can and should require readiness"
        )


def test_app_declares_the_qdrant_edge_it_was_missing(compose: dict[str, Any]) -> None:
    """§3.133's open debt, restated in `x-worker-service`'s own comment ("`app`
    still carries the `qdrant` half of that same gap"). `QDRANT_URL` names
    `qdrant:6333`, so `docker compose up -d app` alone must bring it."""
    assert "qdrant" in compose["app"]["depends_on"]


def test_the_bridge_is_probed_end_to_end_but_gates_nothing(compose: dict[str, Any]) -> None:
    """Deliberate asymmetry, and the one exception to the rule above: the
    bridge's healthcheck crosses all the way into the NATIVE Ollama, so an
    unhealthy bridge means "LLM calls will fail". Nothing may gate on it,
    because a host service outside Compose's lifecycle must not be able to
    stop the stack from booting."""
    assert "11435" in " ".join(compose["ollama-bridge"]["healthcheck"]["test"])

    for name, spec in compose.items():
        condition = spec.get("depends_on", {}).get("ollama-bridge")
        if condition is None:
            continue
        assert condition["condition"] == "service_started", (
            f"{name} -> ollama-bridge must stay `service_started`: gating on a "
            f"native, non-Compose Ollama would make the whole stack refuse to boot"
        )
