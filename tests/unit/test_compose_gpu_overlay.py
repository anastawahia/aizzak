"""``docker-compose.gpu.yml``: everything that needs an NVIDIA GPU, in one file (plan step 2.5).

gpu-compose design.md §3.8, §5.3 and §5.4. The overlay is read as YAML and as
text (AC-5.4 and AC-5.5 search the whole file, comments included), and resolved
with ``docker compose config`` -- which contacts no service and needs no GPU
toolkit -- with an explicit ``--env-file .env.example`` and both ``COMPOSE_FILE``
and ``COMPOSE_PROFILES`` removed from the environment. ``config`` tests skip when
Docker is not installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_BASE = _ROOT / "docker-compose.yml"
_GPU = _ROOT / "docker-compose.gpu.yml"
_BUILD = _ROOT / "docker-compose.build.yml"
_ENV_EXAMPLE = _ROOT / ".env.example"

_CPU_IMAGE = "aizzak/embedding:dev"
_CUDA_IMAGE = "aizzak/embedding-cuda:dev"
_STRIPPED_FROM_ENV = frozenset({"COMPOSE_FILE", "COMPOSE_PROFILES"})
_EXPORTER_IMAGE = (
    "utkuozdemir/nvidia_gpu_exporter:1.15.1"
    "@sha256:7aee2d42836ad29d4adb2722b7cfdc806ec9cc8d45fd0b8971ebbad2d9f3d087"
)
_SCRAPE_MOUNT = "./deploy/prometheus/scrape-gpu.yml:/etc/prometheus/scrape.d/gpu.yml:ro"


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


def _overlay() -> dict[str, Any]:
    data = yaml.safe_load(_GPU.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _config(*files: Path, profiles: Sequence[str] = ()) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_FROM_ENV}
    cmd = ["docker", "compose", "--env-file", str(_ENV_EXAMPLE)]
    for path in files:
        cmd += ["-f", str(path)]
    for profile in profiles:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    done = subprocess.run(
        cmd, cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    resolved = json.loads(done.stdout)
    assert isinstance(resolved, dict)
    return resolved


def _keys_anywhere(node: object) -> set[str]:
    if isinstance(node, dict):
        found = set(node)
        for value in node.values():
            found |= _keys_anywhere(value)
        return found
    if isinstance(node, list):
        found = set()
        for value in node:
            found |= _keys_anywhere(value)
        return found
    return set()


# ── AC-5.1 ──────────────────────────────────────────────────────────────────


def test_the_overlay_has_only_services_and_exactly_five() -> None:
    """ollama, embedding, app (2.5), then prometheus (one mount) and the exporter (4.2)."""
    overlay = _overlay()
    assert set(overlay) == {"services", "networks"}
    assert set(overlay["services"]) == {
        "ollama",
        "embedding",
        "app",
        "prometheus",
        "nvidia-gpu-exporter",
    }


# ── AC-5.2 ──────────────────────────────────────────────────────────────────


def test_ollama_gets_exactly_one_gpu_reservation() -> None:
    assert _overlay()["services"]["ollama"] == {
        "environment": {"LLAMA_ARG_CACHE_RAM": "256"},  # B-5: the only non-GPU key
        "deploy": {
            "resources": {
                "reservations": {"devices": [{"driver": "nvidia", "capabilities": ["gpu"]}]}
            }
        },
    }


# ── AC-5.3 ──────────────────────────────────────────────────────────────────


def test_embedding_is_one_cuda_replica_with_a_written_memory_limit() -> None:
    embedding = _overlay()["services"]["embedding"]
    assert set(embedding) <= {"image", "deploy", "environment"}
    image = embedding["image"]
    assert image == (
        "${EMBEDDING_CUDA_IMAGE_REPO:-aizzak/embedding-cuda}:${EMBEDDING_CUDA_IMAGE_TAG:-dev}"
    )
    defaults = re.findall(r"\$\{[A-Z_]+:-([^}]*)\}", image)
    assert ":".join(defaults) == _CUDA_IMAGE
    assert ":".join(defaults) != _CPU_IMAGE, "never the CPU image's name"
    assert defaults[-1] != "latest"
    deploy = embedding["deploy"]
    assert deploy["replicas"] == 1
    assert deploy["resources"]["limits"] == {"memory": "3g"}
    assert deploy["resources"]["reservations"]["devices"] == [
        {"driver": "nvidia", "capabilities": ["gpu"]}
    ]
    assert embedding["environment"] == {"EMB_REQUIRE_CUDA": "1"}


# ── AC-5.4 ──────────────────────────────────────────────────────────────────


def test_app_gets_utility_only_and_no_wsl_path() -> None:
    app = _overlay()["services"]["app"]
    assert app == {
        "deploy": {
            "resources": {
                "reservations": {"devices": [{"driver": "nvidia", "capabilities": ["utility"]}]}
            }
        }
    }
    text = _GPU.read_text(encoding="utf-8")
    for forbidden in ("/dev/dxg", "/usr/lib/wsl", "LD_LIBRARY_PATH"):
        assert forbidden not in text, forbidden


# ── monitoring-profile AC-4.1 and AC-4.2 (the overlay's 4.2 entries) ──────────────


def test_prometheus_gets_one_mount_and_nothing_else() -> None:
    assert _overlay()["services"]["prometheus"] == {
        "volumes": [_SCRAPE_MOUNT],
        # `default` listed on purpose: a list replaces the implicit network (SR-1).
        "networks": ["default", "gpu-metrics"],
    }


def test_the_gpu_exporter_is_pinned_internal_unprivileged_and_bounded() -> None:
    exporter = _overlay()["services"]["nvidia-gpu-exporter"]
    assert exporter == {
        "profiles": ["monitoring"],
        "image": _EXPORTER_IMAGE,
        "user": "65534:65534",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "command": [
            "--web.listen-address=:9835",
            "--collect.interval=15s",
            "--web.max-requests=2",
        ],
        "networks": ["gpu-metrics"],
        "deploy": {
            "resources": {
                "limits": {"cpus": "0.25", "memory": "64m"},
                "reservations": {"devices": [{"driver": "nvidia", "capabilities": ["utility"]}]},
            }
        },
        "expose": ["9835"],
        "healthcheck": {
            "test": ["CMD", "nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
            "interval": "30s",
            "timeout": "10s",
            "retries": 3,
            "start_period": "20s",
        },
        "restart": "unless-stopped",
        "logging": exporter["logging"],
    }
    assert not set(exporter) & {
        "ports",
        "privileged",
        "network_mode",
        "pid",
        "cap_add",
        "devices",
        "volumes",
    }
    assert "count" not in exporter["deploy"]["resources"]["reservations"]["devices"][0]


def test_the_exporter_joins_no_network_but_an_internal_gpu_metrics() -> None:
    """SR-1: a third-party image must not share `default` with the unauthenticated Redis,
    Qdrant, Loki and Alertmanager; its one peer is Prometheus."""
    overlay = _overlay()
    assert overlay["services"]["nvidia-gpu-exporter"]["networks"] == ["gpu-metrics"]
    assert overlay["networks"] == {"gpu-metrics": {"internal": True}}
    others = [
        name
        for name, spec in overlay["services"].items()
        if "gpu-metrics" in (spec.get("networks") or ())
    ]
    assert sorted(others) == ["nvidia-gpu-exporter", "prometheus"]


def test_the_gpu_exporter_logs_like_every_other_service() -> None:
    """Alloy drops lines without `attrs`: anchors do not cross files, so the overlay restates
    `x-app-logging` and this keeps the two equal."""
    anchor = yaml.safe_load(_BASE.read_text(encoding="utf-8"))["x-app-logging"]["logging"]
    assert _overlay()["services"]["nvidia-gpu-exporter"]["logging"] == anchor


@_needs_compose
@pytest.mark.parametrize(
    "profiles",
    [(), ("local-llm",), ("monitoring",), ("local-llm", "monitoring")],
)
def test_the_exporter_exists_only_with_the_monitoring_profile(profiles: tuple[str, ...]) -> None:
    services = _config(_BASE, _GPU, profiles=profiles)["services"]
    assert ("nvidia-gpu-exporter" in services) == ("monitoring" in profiles)


# ── AC-5.5 ──────────────────────────────────────────────────────────────────


def test_the_overlay_builds_publishes_and_names_nothing() -> None:
    found = _keys_anywhere(_overlay())
    assert found, "the key scan matched nothing"
    assert not found & {"build", "ports", "container_name", "network_mode"}, found
    assert ":latest" not in _GPU.read_text(encoding="utf-8")


# ── AC-5.6 ──────────────────────────────────────────────────────────────────


@_needs_compose
@pytest.mark.parametrize("profile", [None, "local-llm"])
def test_the_overlay_resolves_one_cuda_replica_and_leaves_the_balancer_alone(
    profile: str | None,
) -> None:
    profiles = (profile,) if profile else ()
    base = _config(_BASE, profiles=profiles)
    with_gpu = _config(_BASE, _GPU, profiles=profiles)
    embedding = with_gpu["services"]["embedding"]
    assert embedding["image"] == _CUDA_IMAGE
    assert embedding["deploy"]["replicas"] == 1
    assert embedding["deploy"]["resources"]["reservations"]["devices"][0]["driver"] == "nvidia"
    assert "build" not in embedding
    assert with_gpu["services"]["embedding-lb"] == base["services"]["embedding-lb"]
    if profile is None:
        assert "ollama" not in with_gpu["services"], "its entry has no effect without local-llm"
    else:
        reserved = with_gpu["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]
        assert reserved[0]["capabilities"] == ["gpu"]


@_needs_compose
@pytest.mark.parametrize("profiles", [("local-llm",), ("local-llm", "monitoring")])
def test_the_overlay_changes_only_the_services_it_names(profiles: tuple[str, ...]) -> None:
    """AC-4.2: three services changed as since 2.5; with `monitoring` also `prometheus` (the
    one mount) and one service added, the exporter. Without it neither exists."""
    base = _config(_BASE, profiles=profiles)["services"]
    with_gpu = _config(_BASE, _GPU, profiles=profiles)["services"]
    monitoring = "monitoring" in profiles
    assert set(with_gpu) - set(base) == ({"nvidia-gpu-exporter"} if monitoring else set())
    assert set(base) <= set(with_gpu)
    changed = {name for name in base if base[name] != with_gpu[name]}
    assert changed == {"ollama", "embedding", "app"} | ({"prometheus"} if monitoring else set())
    if monitoring:
        before, after = base["prometheus"], with_gpu["prometheus"]
        assert set(after["networks"]) == set(before["networks"]) | {"gpu-metrics"}
        assert "default" in after["networks"]
        assert set(with_gpu["nvidia-gpu-exporter"]["networks"]) == {"gpu-metrics"}
        added = [v for v in after["volumes"] if v not in before["volumes"]]
        assert len(added) == 1 and len(after["volumes"]) == len(before["volumes"]) + 1
        assert added[0]["target"] == "/etc/prometheus/scrape.d/gpu.yml"
        assert added[0]["read_only"] is True
        assert {k: v for k, v in after.items() if k not in ("volumes", "networks")} == {
            k: v for k, v in before.items() if k not in ("volumes", "networks")
        }


# ── AC-5.7, in the wording of design.md §5.4 ────────────────────────────────


@_needs_compose
def test_only_the_release_script_builds_cuda_and_a_cpu_build_under_its_name_refuses() -> None:
    """D-1: Compose can only ever build the CPU image, so the CUDA name is a promise the
    release script keeps -- and EMB_REQUIRE_CUDA holds anyone who breaks it."""
    # (a) the development overlay alone: the CPU image, no CUDA argument -- as today.
    dev = _config(_BASE, _BUILD)["services"]["embedding"]
    assert dev["image"] == _CPU_IMAGE
    assert "EMBEDDING_REQUIREMENTS" not in (dev["build"].get("args") or {})
    # (b) the server shape: the CUDA image, never built here.
    server = _config(_BASE, _GPU)["services"]["embedding"]
    assert server["image"] == _CUDA_IMAGE
    assert "build" not in server
    # (c) an UNSUPPORTED combination: the CUDA name over a CPU build. What makes it safe is
    # that the resolved service refuses to start without CUDA.
    combined = _config(_BASE, _GPU, _BUILD)["services"]["embedding"]
    assert combined["image"] == _CUDA_IMAGE
    assert "EMBEDDING_REQUIREMENTS" not in (combined["build"].get("args") or {})
    assert combined["environment"]["EMB_REQUIRE_CUDA"] == "1"
    # (d) the base file alone carries no such guard: the CPU image runs anywhere.
    assert "EMB_REQUIRE_CUDA" not in _config(_BASE)["services"]["embedding"]["environment"]


def test_the_overlay_header_says_never_with_the_build_overlay() -> None:
    text = _GPU.read_text(encoding="utf-8")
    assert "NEVER together with docker-compose.build.yml" in text
    assert "deploy/release/build-push.sh" in text


# ── AC-5.8 ──────────────────────────────────────────────────────────────────


def test_the_base_file_reserves_no_device() -> None:
    base = yaml.safe_load(_BASE.read_text(encoding="utf-8"))
    found = _keys_anywhere(base)
    assert "services" in found, "the key scan matched nothing"
    assert not found & {"devices", "gpus"}, "a GPU key in the base file breaks every CPU host"


# ── AC-6.2 (the overlay's half) ─────────────────────────────────────────────


def test_the_ollama_knobs_live_in_the_base_file_only() -> None:
    """They hold on the CPU too, so they are not the GPU overlay's to set."""
    assert set(_overlay()["services"]["ollama"]["environment"]) == {"LLAMA_ARG_CACHE_RAM"}
    text = _GPU.read_text(encoding="utf-8")
    for name in ("OLLAMA_MAX_LOADED_MODELS", "OLLAMA_NUM_PARALLEL"):
        assert name not in text, name


# ── AC-8.1 (the server lines) ───────────────────────────────────────────────


def test_env_example_offers_the_server_lines_commented_only() -> None:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^#COMPOSE_FILE=docker-compose\.yml:docker-compose\.gpu\.yml$", text, re.M)
    assert re.search(r"^#EMBEDDING_CUDA_IMAGE_REPO=anastawahia/aizzak$", text, re.M)
    assert re.search(r"^#EMBEDDING_CUDA_IMAGE_TAG=embedding-cuda-\d{4}\.\d+\.\d+$", text, re.M)
    assert not re.search(r"^COMPOSE_[A-Z_]+=", text, re.M), "CI copies this file as its environment"
    assert not re.search(r"^EMBEDDING_CUDA_IMAGE_(REPO|TAG)=", text, re.M)
