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


def _config(*files: Path, profile: str | None = None) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_FROM_ENV}
    cmd = ["docker", "compose", "--env-file", str(_ENV_EXAMPLE)]
    for path in files:
        cmd += ["-f", str(path)]
    if profile:
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


def test_the_overlay_has_only_services_and_exactly_three() -> None:
    overlay = _overlay()
    assert set(overlay) == {"services"}
    assert set(overlay["services"]) == {"ollama", "embedding", "app"}


# ── AC-5.2 ──────────────────────────────────────────────────────────────────


def test_ollama_gets_exactly_one_gpu_reservation() -> None:
    assert _overlay()["services"]["ollama"] == {
        "deploy": {
            "resources": {
                "reservations": {"devices": [{"driver": "nvidia", "capabilities": ["gpu"]}]}
            }
        }
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
    base = _config(_BASE, profile=profile)
    with_gpu = _config(_BASE, _GPU, profile=profile)
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
def test_the_overlay_changes_only_the_three_services_it_names() -> None:
    base = _config(_BASE, profile="local-llm")["services"]
    with_gpu = _config(_BASE, _GPU, profile="local-llm")["services"]
    assert set(base) == set(with_gpu)
    changed = {name for name in base if base[name] != with_gpu[name]}
    assert changed == {"ollama", "embedding", "app"}


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
    assert "environment" not in _overlay()["services"]["ollama"]
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
