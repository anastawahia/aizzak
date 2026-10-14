"""Ollama as a Compose service, and where the LLM comes from (server-gpu-plan.md 2.2, 2.3, 2.6).

gpu-compose design.md §3.2-§3.3 and §5.3. ``docker-compose.yml`` is read with
``yaml.safe_load`` (it resolves the ``<<`` merges and the anchors, so what these
tests see is the EFFECTIVE service, the same thing Compose sees). The tests that
need Compose itself call ``docker compose config`` only -- it contacts no
service -- with an explicit ``--env-file .env.example`` and both ``COMPOSE_FILE``
and ``COMPOSE_PROFILES`` removed from the environment, so neither the operator's
real environment file nor a profile left in their shell is ever read.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_BASE = _ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _ROOT / ".env.example"

_ROUTING_PREFIX = "${PROVIDER_ROUTING:-"
_OLLAMA_EDGES = ("app", "worker-memory", "worker-knowledge", "worker-media")
_STRIPPED_FROM_ENV = frozenset({"COMPOSE_FILE", "COMPOSE_PROFILES"})


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, check=False
    )
    return probe.returncode == 0


_needs_compose = pytest.mark.skipif(
    not _compose_available(), reason="docker compose CLI not installed"
)


def _load() -> dict[str, Any]:
    data = yaml.safe_load(_BASE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _services() -> dict[str, dict[str, Any]]:
    services = _load()["services"]
    assert isinstance(services, dict)
    return services


def _app_env() -> dict[str, str]:
    env = _load()["x-app-env"]
    assert isinstance(env, dict)
    return env


def _compose(
    *args: str, profile: str | None = None, env_file: Path = _ENV_EXAMPLE
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_FROM_ENV}
    cmd = ["docker", "compose", "--env-file", str(env_file), "-f", str(_BASE)]
    if profile:
        cmd += ["--profile", profile]
    cmd += list(args)
    return subprocess.run(
        cmd, cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False
    )


def _service_names(profile: str | None = None) -> set[str]:
    done = _compose("config", "--services", profile=profile)
    assert done.returncode == 0, done.stderr
    return set(done.stdout.split())


def _routing_default() -> dict[str, Any]:
    """The JSON default of PROVIDER_ROUTING in x-app-env, outside ``${VAR:-...}``."""
    raw = _app_env()["PROVIDER_ROUTING"]
    assert raw.startswith(_ROUTING_PREFIX), raw
    assert raw.endswith("}"), raw
    parsed = json.loads(raw.removeprefix(_ROUTING_PREFIX).removesuffix("}"))
    assert isinstance(parsed, dict)
    return parsed


def _env_example_routing() -> dict[str, Any]:
    match = re.search(r"^PROVIDER_ROUTING=(.+)$", _ENV_EXAMPLE.read_text("utf-8"), re.MULTILINE)
    assert match is not None
    parsed = json.loads(match.group(1))
    assert isinstance(parsed, dict)
    return parsed


def _ollama_models(table: dict[str, Any]) -> set[str]:
    return {
        route["model"]
        for routes in table.values()
        for route in routes.values()
        if route["provider"] == "ollama"
    }


def _edges(spec: dict[str, Any]) -> dict[str, Any]:
    depends = spec.get("depends_on") or {}
    if isinstance(depends, list):
        return {name: {} for name in depends}
    assert isinstance(depends, dict)
    return depends


# ── AC-2.1 ──────────────────────────────────────────────────────────────────


def test_the_ollama_service_shape() -> None:
    compose = _load()
    svc = compose["services"]["ollama"]
    assert re.fullmatch(r"ollama/ollama:\d+\.\d+\.\d+", svc["image"]), svc["image"]
    assert svc["profiles"] == ["local-llm"]
    assert "ports" not in svc, "Ollama has no authentication: expose, never publish"
    assert "container_name" not in svc
    assert svc["expose"] == ["11434"]
    assert svc["volumes"] == ["ollama-models:/root/.ollama"]
    assert "ollama-models" in compose["volumes"]
    probe = svc["healthcheck"]["test"]
    assert probe[:3] == ["CMD", "bash", "-c"], "bash's /dev/tcp: the image has no curl or wget"
    assert "/dev/tcp/127.0.0.1/11434" in probe[3]
    assert "GET /api/version" in probe[3]
    limits = svc["deploy"]["resources"]["limits"]
    assert limits["cpus"] and limits["memory"]
    assert svc["restart"] == "unless-stopped"
    assert svc["logging"]["driver"] == "json-file"


def test_the_ollama_image_is_the_pinned_native_version() -> None:
    assert _services()["ollama"]["image"] == "ollama/ollama:0.13.5"


# ── AC-2.2 ──────────────────────────────────────────────────────────────────


def test_the_ollama_pull_service_shape() -> None:
    svc = _services()["ollama-pull"]
    assert svc["profiles"] == ["local-llm"]
    assert svc["restart"] == "no"
    assert svc["depends_on"] == {"ollama": {"condition": "service_healthy", "required": False}}
    limits = svc["deploy"]["resources"]["limits"]
    assert limits["cpus"] and limits["memory"]
    assert "ports" not in svc
    assert svc["image"] == "python:3.12-slim"
    assert svc["user"] == "65534:65534"
    assert svc["read_only"] is True
    assert svc["volumes"] == ["./deploy/ollama/pull_models.py:/pull/pull_models.py:ro"]
    assert svc["command"] == ["python", "-u", "/pull/pull_models.py"]
    env = svc["environment"]
    assert env["OLLAMA_BASE_URL"] == "http://ollama:11434", "never the shared URL"
    assert env["PROVIDER_ROUTING"] == _app_env()["PROVIDER_ROUTING"], "the value `app` resolves"
    assert set(env) == {"OLLAMA_BASE_URL", "PROVIDER_ROUTING"}, "no credential, no database URL"
    assert (_ROOT / "deploy" / "ollama" / "pull_models.py").is_file()


# ── AC-2.4 ──────────────────────────────────────────────────────────────────


def test_ollama_base_url_defaults_to_the_in_stack_service() -> None:
    assert _app_env()["OLLAMA_BASE_URL"] == "${OLLAMA_BASE_URL:-http://ollama:11434}"


def test_env_example_sets_no_other_ollama_base_url() -> None:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert not re.search(r"^OLLAMA_BASE_URL=", text, re.MULTILINE), "no active line (D-5)"
    assert re.search(r"^#OLLAMA_BASE_URL=http://host\.docker\.internal:11435$", text, re.MULTILINE)


# ── AC-2.5 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _OLLAMA_EDGES)
def test_app_and_the_three_workers_wait_for_ollama_optionally(name: str) -> None:
    edge = _edges(_services()[name])["ollama"]
    assert edge == {"condition": "service_healthy", "required": False}


def test_no_edge_requires_ollama_or_ollama_pull() -> None:
    offenders = []
    for name, spec in _services().items():
        for target, edge in _edges(spec).items():
            if target in {"ollama", "ollama-pull"} and edge.get("required") is not False:
                offenders.append(f"{name} -> {target}: {edge}")
    assert not offenders, offenders


# ── AC-2.6 ──────────────────────────────────────────────────────────────────


@_needs_compose
def test_local_llm_adds_exactly_ollama_and_ollama_pull() -> None:
    assert _compose("config", "-q").returncode == 0
    assert _compose("config", "-q", profile="local-llm").returncode == 0
    without = _service_names()
    with_profile = _service_names("local-llm")
    assert not {"ollama", "ollama-pull"} & without
    assert with_profile - without == {"ollama", "ollama-pull"}
    assert without <= with_profile
    done = _compose("config", "--format", "json", profile="local-llm")
    assert done.returncode == 0, done.stderr
    resolved = json.loads(done.stdout)["services"]
    assert resolved["app"]["depends_on"]["ollama"]["required"] is False


def _resolved_routings(env_file: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    done = _compose("config", "--format", "json", profile="local-llm", env_file=env_file)
    assert done.returncode == 0, done.stderr
    resolved = json.loads(done.stdout)["services"]
    from_app = json.loads(resolved["app"]["environment"]["PROVIDER_ROUTING"])
    from_pull = json.loads(resolved["ollama-pull"]["environment"]["PROVIDER_ROUTING"])
    return from_app, from_pull


@_needs_compose
def test_the_routing_anchor_gives_ollama_pull_the_value_app_resolves(tmp_path: Path) -> None:
    # With the template's own value, and with the compose default (a copy of the
    # template without its PROVIDER_ROUTING line, so the nested braces of the
    # `${VAR:-{...}}` default are what Compose parses).
    from_app, from_pull = _resolved_routings(_ENV_EXAMPLE)
    assert from_app == from_pull == _env_example_routing()
    kept = [
        line
        for line in _ENV_EXAMPLE.read_text("utf-8").splitlines()
        if not line.startswith("PROVIDER_ROUTING=")
    ]
    without = tmp_path / "env-without-routing"
    without.write_text("\n".join(kept) + "\n", encoding="utf-8")
    from_app, from_pull = _resolved_routings(without)
    assert from_app == from_pull == _routing_default()


# ── AC-6.2 ──────────────────────────────────────────────────────────────────


def test_ollama_concurrency_knobs_and_their_relation() -> None:
    """D-7: NUM_PARALLEL equals OLLAMA_MAX_CONCURRENCY by default, and a test holds them."""
    env = _services()["ollama"]["environment"]
    assert env["OLLAMA_MAX_LOADED_MODELS"] == "${OLLAMA_MAX_LOADED_MODELS:-1}"
    parallel = re.fullmatch(r"\$\{OLLAMA_NUM_PARALLEL:-(\d+)\}", env["OLLAMA_NUM_PARALLEL"])
    guard = re.fullmatch(
        r"\$\{OLLAMA_MAX_CONCURRENCY:-(\d+)\}", _app_env()["OLLAMA_MAX_CONCURRENCY"]
    )
    assert parallel is not None and guard is not None
    assert parallel.group(1) == guard.group(1), "move OLLAMA_NUM_PARALLEL and the guard together"
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    values = {
        name: re.findall(rf"^#?{name}=(\d+)$", text, re.MULTILINE)
        for name in ("OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_CONCURRENCY", "OLLAMA_MAX_LOADED_MODELS")
    }
    assert values["OLLAMA_NUM_PARALLEL"] == values["OLLAMA_MAX_CONCURRENCY"] == [parallel.group(1)]
    assert values["OLLAMA_MAX_LOADED_MODELS"] == ["1"]


# ── AC-6.3 ──────────────────────────────────────────────────────────────────


def test_every_ollama_route_is_gemma3_1b() -> None:
    """Decision 3: the model stays gemma3:1b, in the compose default and in the template."""
    assert _ollama_models(_routing_default()) == {"gemma3:1b"}
    assert _ollama_models(_env_example_routing()) == {"gemma3:1b"}


# ── AC-3.1 ──────────────────────────────────────────────────────────────────

# `ollama-bridge` as it was on master 1814998, minus the profile this step adds. A pinned
# dictionary, not a pattern: the claim is "nothing else about the bridge changed".
_BRIDGE_ON_MASTER: dict[str, Any] = {
    "deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "256m"}}},
    "image": "alpine/socat:1.8.0.3",
    "network_mode": "host",
    "command": "TCP-LISTEN:11435,bind=172.17.0.1,fork,reuseaddr TCP:127.0.0.1:11434",
    "healthcheck": {
        "test": [
            "CMD",
            "wget",
            "-q",
            "-T",
            "4",
            "-O",
            "/dev/null",
            "http://172.17.0.1:11435/api/version",
        ],
        "interval": "30s",
        "timeout": "10s",
        "retries": 3,
        "start_period": "10s",
    },
    "restart": "unless-stopped",
}


def test_the_bridge_is_profiled_and_otherwise_unchanged() -> None:
    bridge = dict(_services()["ollama-bridge"])
    assert bridge.pop("profiles") == ["wsl-ollama"]
    assert bridge.pop("logging")["driver"] == "json-file"
    assert bridge == _BRIDGE_ON_MASTER


# ── AC-3.2 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _OLLAMA_EDGES)
def test_the_four_bridge_edges_are_optional(name: str) -> None:
    edge = _edges(_services()[name])["ollama-bridge"]
    assert edge == {"condition": "service_started", "required": False}


def test_no_edge_requires_the_bridge() -> None:
    offenders = [
        f"{name}: {edge}"
        for name, spec in _services().items()
        for target, edge in _edges(spec).items()
        if target == "ollama-bridge" and edge.get("required") is not False
    ]
    assert not offenders, offenders


# ── AC-3.4 ──────────────────────────────────────────────────────────────────


@_needs_compose
def test_wsl_ollama_adds_exactly_the_bridge() -> None:
    plain = _compose("config", "-q")
    assert plain.returncode == 0, plain.stderr
    assert "depends on undefined service" not in plain.stderr
    with_bridge = _compose("config", "-q", profile="wsl-ollama")
    assert with_bridge.returncode == 0, with_bridge.stderr
    without = _service_names()
    assert "ollama-bridge" not in without
    assert _service_names("wsl-ollama") - without == {"ollama-bridge"}


# ── AC-7.3 ──────────────────────────────────────────────────────────────────


@_needs_compose
def test_the_default_stack_is_the_28_standing_and_5_one_shots() -> None:
    """The default `up -d` set is exactly the services with no `profiles:`, and the bridge
    is not among them. (Master's own set is compared once by QA with `git show`.)"""
    unprofiled = {name for name, spec in _services().items() if not spec.get("profiles")}
    assert _service_names() == unprofiled
    assert len(unprofiled) == 33, "28 standing + 5 one-shots"
    assert "ollama-bridge" not in unprofiled


# ── AC-3.5 and AC-8.1 (the profile lines) ───────────────────────────────────


def test_env_example_offers_both_profiles_commented_only() -> None:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^#COMPOSE_PROFILES=wsl-ollama$", text, re.MULTILINE)
    assert re.search(r"^#COMPOSE_PROFILES=local-llm$", text, re.MULTILINE)
    assert not re.search(r"^COMPOSE_PROFILES=", text, re.MULTILINE), (
        "CI copies this file and `_compose_config` does not clear COMPOSE_PROFILES"
    )
    assert not re.search(r"^OLLAMA_(MAX_LOADED_MODELS|NUM_PARALLEL)=", text, re.MULTILINE)
