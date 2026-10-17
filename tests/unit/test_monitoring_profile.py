"""The monitoring services behind profile ``monitoring`` -- server-gpu-plan.md 4.1 and 4.2.

``docs/delivery/monitoring-profile/`` (requirements.md FR-1 ... FR-13, design.md §3 and §5).

Eleven services of ``docker-compose.yml`` -- Prometheus, Grafana, Alertmanager, alert-sink,
the five exporters, Loki and Alloy -- carry ``profiles: ["monitoring"]`` and nothing else
changed in them. Decision 7 keeps the profile ON by default (``COMPOSE_PROFILES`` on the
owner's machine and on a server names it), so what these tests hold is the other half:

* the profile key is the ONLY change in the eleven, so no container is recreated (AC-1.1,
  AC-1.5);
* nothing outside the eleven depends on one of them or names its host, so switching the
  profile off cannot break the platform itself (AC-1.2, AC-1.3);
* with the profile off the model has exactly the services that carry no profile, and with
  it on exactly the eleven more (AC-1.4).

The ``config`` tests pass an explicit ``--env-file .env.example`` and remove
``COMPOSE_FILE`` and ``COMPOSE_PROFILES`` from the environment: CI's ``integration`` job
exports a three-file ``COMPOSE_FILE`` (``.github/workflows/ci.yml``), which broke
compose-auto-size on PR #11. They skip when Docker is not installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from tests.unit.support_compose import COMPOSE, GPU_OVERLAY, REPO_ROOT

ENV_EXAMPLE: Final[Path] = REPO_ROOT / ".env.example"

MONITORING: Final[frozenset[str]] = frozenset(
    {
        "prometheus",
        "grafana",
        "alertmanager",
        "alert-sink",
        "pgbouncer-exporter",
        "redis-stream-exporter",
        "redis-cache-exporter",
        "node-exporter",
        "postgres-exporter",
        "loki",
        "alloy",
    }
)
GPU_MONITORING: Final[frozenset[str]] = frozenset({"nvidia-gpu-exporter"})

# Edges between two of the eleven, and the five that leave them. All of them point from the
# monitoring side to what it reads, never the other way round (AC-1.2).
INTERNAL_EDGES: Final[frozenset[tuple[str, str]]] = frozenset(
    {("grafana", "prometheus"), ("alertmanager", "alert-sink"), ("alloy", "loki")}
)
OUTBOUND_EDGES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("prometheus", "app"),
        ("pgbouncer-exporter", "pgbouncer"),
        ("redis-stream-exporter", "redis-stream"),
        ("redis-cache-exporter", "redis-cache"),
        ("postgres-exporter", "postgres"),
    }
)

# AC-1.1: the sha256 of each service's raw YAML mapping on master 47225ff, minus its
# `profiles` key. A later edit to one of these services updates its digest in the SAME
# commit -- the way `_BRIDGE_ON_MASTER` in test_compose_local_llm.py is kept.
_SPEC_ON_MASTER_SHA256: Final[dict[str, str]] = {
    "prometheus": "5d8e624a46134d5b404406af9df1ab2cb2201674090b3f13dfc7117ae5910ffa",
    "grafana": "db448ba6b1be7232de501060ac3c8bd35f2588c47454266662e37ccee0d1b0d7",
    "alertmanager": "601887332e1edbfba1a9fab3cd306b4e99500add73f9c6cd92ffb6b40b863eb5",
    "alert-sink": "7ac34e2075124fed611f6fcf6464fa53d8ae5630c286c29485dc4a4c593d44f8",
    "pgbouncer-exporter": "130db28021dd78f3613029c4c3bfe1fe93ac85046591002dbd3c82b1adf24201",
    "redis-stream-exporter": "ac153e76b101a1c4c75101aa96b7c5d86a5d03c902a2a7d6d0ea8355d075b271",
    "redis-cache-exporter": "14c103a58f45485ff51135a9f5b41ac1f28a8a5b6190bef0d2787beef44bc97a",
    "node-exporter": "2371996456b840307ed43ddf3542323a911fc10adb6db43987ff4493088f1321",
    "postgres-exporter": "f36c986fac631ac63b556a5625e0835c0e8529baef6d3a78f32954e1fff86316",
    "loki": "120b387f19d4ebbc2ba32936b2b530341874b3774fd442692fd131acdb40b1dd",
    "alloy": "cc447e3de99857b188ec1ec94102f7bc6f232a8ab040a090d39a1cdc41d0f603",
}

_ALL_MONITORING: Final[frozenset[str]] = MONITORING | GPU_MONITORING
# A monitoring host followed by a port: `prometheus:9090`, `loki:3100`. Case-sensitive on
# purpose, so PROMETHEUS_MULTIPROC_DIR does not match. Longest names first, so that
# `redis-stream-exporter` is not read as a shorter name's prefix.
_HOST_REF: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.-])("
    + "|".join(re.escape(n) for n in sorted(_ALL_MONITORING, key=len, reverse=True))
    + r")(?=:)"
)

_PROFILE_LINE: Final = '    profiles: ["monitoring"]\n'


def _spec_digest(spec: Mapping[str, Any]) -> str:
    text = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _raw(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    services = data["services"]
    assert isinstance(services, dict)
    return services


def _services() -> dict[str, Any]:
    """Every service of the base file and of the GPU file, as written."""
    merged = dict(_raw(COMPOSE))
    for name, spec in _raw(GPU_OVERLAY).items():
        merged.setdefault(name, spec)
    return merged


def _edges(services: Mapping[str, Any]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for name, spec in services.items():
        depends = (spec or {}).get("depends_on") or {}
        targets = depends if isinstance(depends, list) else list(depends)
        found |= {(name, target) for target in targets}
    return found


def _profiles(spec: Mapping[str, Any]) -> set[str]:
    return set(spec.get("profiles") or ())


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, check=False
    )
    return probe.returncode == 0


_needs_compose = pytest.mark.skipif(
    not _docker_available(), reason="docker compose CLI not installed"
)


def _compose(
    *args: str,
    files: Sequence[Path] = (COMPOSE,),
    profiles: Sequence[str] = (),
    env_file: Path = ENV_EXAMPLE,
    project_directory: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("COMPOSE_FILE", "COMPOSE_PROFILES")}
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    if project_directory is not None:
        cmd += ["--project-directory", str(project_directory)]
    for path in files:
        cmd += ["-f", str(path)]
    for profile in profiles:
        cmd += ["--profile", profile]
    cmd += ["config", *args]
    return subprocess.run(
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False
    )


# ── AC-1.1 ──────────────────────────────────────────────────────────────────


def test_exactly_the_eleven_carry_the_monitoring_profile() -> None:
    base = {n for n, s in _raw(COMPOSE).items() if "monitoring" in _profiles(s)}
    assert base == MONITORING, (
        f"the monitoring profile is on {sorted(base)}; it belongs on exactly the eleven "
        f"{sorted(MONITORING)}"
    )
    for name in sorted(MONITORING):
        assert _raw(COMPOSE)[name]["profiles"] == ["monitoring"], name
    overlay = {n for n, s in _raw(GPU_OVERLAY).items() if "monitoring" in _profiles(s)}
    assert overlay <= GPU_MONITORING, f"the GPU file puts {sorted(overlay)} behind monitoring"


@pytest.mark.parametrize("name", sorted(MONITORING))
def test_each_monitoring_service_is_unchanged_but_for_its_profile(name: str) -> None:
    """Same spec, same `config --hash`, so `up -d` recreates nothing (NFR-1)."""
    spec = {k: v for k, v in _raw(COMPOSE)[name].items() if k != "profiles"}
    assert _spec_digest(spec) == _SPEC_ON_MASTER_SHA256[name], (
        f"`{name}` differs from master 47225ff in more than its `profiles:` key. If this is a "
        "deliberate later change, update its digest in the same commit."
    )


def test_cadvisor_stays_behind_container_metrics_alone() -> None:
    assert _raw(COMPOSE)["cadvisor"]["profiles"] == ["container-metrics"]


# ── AC-1.2 ──────────────────────────────────────────────────────────────────


def test_no_service_outside_monitoring_depends_on_it() -> None:
    for source, target in sorted(_edges(_services())):
        if source in _ALL_MONITORING:
            continue
        assert target not in _ALL_MONITORING, (
            f"`{source}` depends_on `{target}`, a service behind profile `monitoring`. With "
            "the profile off the target is absent and Compose refuses the whole model."
        )


def test_the_monitoring_edges_are_the_known_three_inside_and_five_out() -> None:
    inside, outside = set(), set()
    for source, target in _edges(_services()):
        if source not in _ALL_MONITORING:
            continue
        (inside if target in _ALL_MONITORING else outside).add((source, target))
    assert inside == INTERNAL_EDGES, sorted(inside ^ INTERNAL_EDGES)
    assert outside == OUTBOUND_EDGES, sorted(outside ^ OUTBOUND_EDGES)


# ── AC-1.3 ──────────────────────────────────────────────────────────────────


def test_no_service_outside_monitoring_names_a_monitoring_host() -> None:
    for name, spec in sorted(_services().items()):
        if name in _ALL_MONITORING:
            continue
        for key in ("environment", "command", "entrypoint", "healthcheck"):
            text = json.dumps(spec.get(key), sort_keys=True)
            found = _HOST_REF.search(text)
            assert found is None, (
                f"`{name}` names the monitoring host `{found.group(1) if found else ''}` in "
                f"`{key}`. Without profile `monitoring` that host does not exist."
            )


def test_the_host_pattern_matches_a_host_and_not_an_environment_variable_name() -> None:
    assert _HOST_REF.search("http://prometheus:9090/-/ready")
    assert _HOST_REF.search("redis-stream-exporter:9121")
    assert not _HOST_REF.search("PROMETHEUS_MULTIPROC_DIR=/tmp/prom")
    assert not _HOST_REF.search("my-loki:3100")


def test_the_log_anchor_is_still_json_file() -> None:
    """Alloy reads the json-file logs of every service; the profile must not touch that."""
    anchor = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["x-app-logging"]["logging"]
    assert anchor["driver"] == "json-file"


# ── AC-1.4 ──────────────────────────────────────────────────────────────────


def _service_names(profiles: Sequence[str] = ()) -> set[str]:
    done = _compose("--services", profiles=profiles)
    assert done.returncode == 0, done.stderr
    return set(done.stdout.split())


@_needs_compose
def test_without_the_profile_none_of_the_eleven_is_in_the_model() -> None:
    names = _service_names()
    assert not names & MONITORING
    assert names == {n for n, s in _raw(COMPOSE).items() if not _profiles(s)}
    assert len(names) == 22, "17 standing + 5 one-shots"
    assert _compose("-q").returncode == 0


@_needs_compose
def test_the_profile_adds_exactly_the_eleven() -> None:
    assert _service_names(["monitoring"]) - _service_names() == MONITORING
    assert _compose("-q", profiles=["monitoring"]).returncode == 0


# ── AC-1.5 ──────────────────────────────────────────────────────────────────


@_needs_compose
def test_the_profile_key_does_not_change_any_config_hash(tmp_path: Path) -> None:
    """Compose's own fingerprint of each service, with and without the `profiles:` line.
    The copy is rendered with `--project-directory <repo>` so that every relative bind path
    resolves to the same place."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count(_PROFILE_LINE) == len(MONITORING), "one line per service, no other"
    copy = tmp_path / "docker-compose.yml"
    copy.write_text(text.replace(_PROFILE_LINE, ""), encoding="utf-8")

    names = ",".join(sorted(MONITORING))
    original = _compose("--hash", names, profiles=["monitoring"])
    stripped = _compose("--hash", names, files=[copy], project_directory=REPO_ROOT)
    assert original.returncode == 0, original.stderr
    assert stripped.returncode == 0, stripped.stderr
    assert len(original.stdout.split("\n")) > len(MONITORING)
    assert sorted(original.stdout.splitlines()) == sorted(stripped.stdout.splitlines())


# ── the written word: AC-2.6, AC-3.1, AC-3.5 ────────────────────────────────

_DOCS: Final[Path] = REPO_ROOT / "docs"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head : text.index(end, head + len(start))]


def test_the_ledger_explains_monitoring_on_by_default() -> None:
    """AC-2.6: the budget prose says monitoring is on by default and what it costs."""
    runbook = _text(_DOCS / "design" / "08-local-runbook.md")
    section = _between(runbook, "### 2\u2011ز", "### 2\u2011ح")
    for needle in (
        "قرار 7",
        "--without-monitoring",
        "3.60",
        "4.66",
        "2.35",
        "2.41",
        "1.40",
        "1.66",
    ):
        assert needle in section, f"08 section 2-z no longer says {needle!r}"
    not_counted = section[section.index("وما لا يُحسَب في المجموع") :]
    assert "`monitoring`" in not_counted.split("\n", 1)[0], "'what is not counted' omits monitoring"


@pytest.mark.parametrize("guide", ["stack-commands.md", "quickstart.md"])
def test_both_guides_carry_the_monitoring_lines(guide: str) -> None:
    """AC-3.1."""
    text = _text(_DOCS / guide)
    assert "COMPOSE_PROFILES=wsl-ollama,monitoring" in text
    assert "COMPOSE_PROFILES=local-llm,monitoring" in text
    assert "--without-monitoring" in text and "لا تنبيهات" in text
    assert re.search(r"--profile X`[^\n]*يحلّ محلّ(\*\*)? `COMPOSE_PROFILES`", text)
    assert "deploy/auto_size.py --role dev" in text


def test_no_monitoring_means_no_alerts_is_said_where_it_is_read() -> None:
    """AC-3.5: the runbook's §0 and the server guide's monitoring section."""
    runbook = _text(_DOCS / "runbooks" / "alerts.md")
    zero = _between(runbook, "## 0)", "## 1)")
    for needle in (
        "profile `monitoring`",
        "AizzakWatchdog",
        "docker compose ps prometheus alertmanager alert-sink",
    ):
        assert needle in zero, f"alerts.md §0 no longer says {needle!r}"
    server = _text(_DOCS / "deploy-server.md")
    section = _between(server, "## 2) المراقبة والتنبيهات", "---")
    for needle in ("/health", "من خارج الخادم", "لا تنبيهات"):
        assert needle in section, f"deploy-server.md §2 no longer says {needle!r}"
