"""The CUDA variant of the embedding image (server-gpu-plan.md 2.4).

gpu-compose design.md §3.5 and AC-4.1, AC-4.2, AC-4.5. One Dockerfile, two pinned
dependency sets chosen by a build argument; Compose never builds the CUDA one
(D-1). Reading text, plus one ``docker compose config`` (no container) that skips
when Docker is not installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_EMBEDDING = _ROOT / "services" / "embedding"
_CPU = _EMBEDDING / "requirements.txt"
_CUDA = _EMBEDDING / "requirements-cuda.txt"
_DOCKERFILE = _EMBEDDING / "Dockerfile"
_RELEASE = _ROOT / "deploy" / "release" / "build-push.sh"
_PREFLIGHT = _ROOT / "deploy" / "gpu-preflight.sh"
_ENV_EXAMPLE = _ROOT / ".env.example"

_PINS = {
    "torch==2.5.1",
    "sentence-transformers==3.3.1",
    "fastapi==0.115.6",
    "uvicorn[standard]==0.32.1",
    "pydantic==2.10.4",
}
_CPU_INDEX = "--extra-index-url https://download.pytorch.org/whl/cpu"
_CUDA_INDEX = re.compile(r"--extra-index-url https://download\.pytorch\.org/whl/(cu1\d\d)")


def _lines(path: Path) -> list[str]:
    """Every line that is neither blank nor a comment."""
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, check=False
    )
    return probe.returncode == 0


# ── AC-4.1 ──────────────────────────────────────────────────────────────────


def test_cuda_requirements_pin_the_same_versions_and_differ_only_in_the_index() -> None:
    cpu, cuda = _lines(_CPU), _lines(_CUDA)
    assert [ln for ln in cpu if not ln.startswith("--")] != [], "the parser matched nothing"
    cpu_pins = {ln for ln in cpu if not ln.startswith("--")}
    cuda_pins = {ln for ln in cuda if not ln.startswith("--")}
    assert cpu_pins == cuda_pins == _PINS
    assert [ln for ln in cpu if ln.startswith("--")] == [_CPU_INDEX]
    cuda_options = [ln for ln in cuda if ln.startswith("--")]
    assert len(cuda_options) == 1 and _CUDA_INDEX.fullmatch(cuda_options[0]), cuda_options
    assert not [ln for ln in cuda if "cpu" in ln.lower()], "no CPU wheel index in the CUDA set"


def test_the_cuda_index_matches_the_driver_the_preflight_smoke_checks() -> None:
    """cu124 wheels need a driver that supports CUDA 12.4, and the live check in
    deploy/gpu-preflight.sh runs a 12.4 image: the two must name the same minor."""
    index = next(m for ln in _lines(_CUDA) if (m := _CUDA_INDEX.fullmatch(ln)))
    digits = index.group(1).removeprefix("cu")
    wheel_cuda = f"{digits[:-1]}.{digits[-1]}"
    smoke = re.search(r'readonly SMOKE_IMAGE="nvidia/cuda:(\d+\.\d+)\.', _PREFLIGHT.read_text())
    assert smoke is not None
    assert smoke.group(1) == wheel_cuda


# ── AC-4.2 ──────────────────────────────────────────────────────────────────


def test_one_arg_selects_the_requirements_and_defaults_to_cpu() -> None:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines()]
    args = [ln for ln in lines if ln.startswith("ARG ")]
    assert args == ["ARG EMBEDDING_REQUIREMENTS=requirements.txt"]
    copy = next(ln for ln in lines if ln.startswith("COPY services/embedding/${"))
    assert copy == ("COPY services/embedding/${EMBEDDING_REQUIREMENTS} ./${EMBEDDING_REQUIREMENTS}")
    install = next(ln for ln in lines if ln.startswith("RUN pip install"))
    assert "${EMBEDDING_REQUIREMENTS}" in install
    assert "requirements.txt" not in install, "no hard-coded CPU file left"
    froms = [ln for ln in lines if ln.startswith("FROM ")]
    assert froms, "the parser matched nothing"
    for line in froms:
        tag = line.split()[1].partition(":")[2]
        assert tag and tag != "latest", line


def test_the_arg_sits_after_the_apt_layer_so_that_layer_stays_shared() -> None:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert text.index("apt-get install") < text.index("ARG EMBEDDING_REQUIREMENTS")
    assert text.index("ARG EMBEDDING_REQUIREMENTS") > text.index("FROM ")


# ── the release script and the Dockerfile agree ─────────────────────────────


def test_the_release_script_and_the_dockerfile_agree_on_the_cuda_argument() -> None:
    done = subprocess.run(
        ["bash", str(_RELEASE), "--list-images"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    cuda = [ln for ln in done.stdout.splitlines() if ln.startswith("embedding-cuda ")]
    assert len(cuda) == 1, done.stdout
    _, dockerfile, build_arg = cuda[0].split()
    assert dockerfile == "services/embedding/Dockerfile"
    name, _, value = build_arg.partition("=")
    assert f"ARG {name}=" in _DOCKERFILE.read_text(encoding="utf-8")
    assert (_EMBEDDING / value).is_file(), value
    assert value == _CUDA.name


# ── AC-4.5 ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not _docker_available(), reason="docker compose CLI not installed")
def test_the_build_overlay_builds_embedding_without_the_cuda_arg() -> None:
    """D-1/D-16: Compose never builds the CUDA image, so `docker compose build embedding`
    on the owner's machine is the CPU image, as it was."""
    env = {k: v for k, v in os.environ.items() if k not in {"COMPOSE_FILE", "COMPOSE_PROFILES"}}
    done = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_ENV_EXAMPLE),
            "-f",
            str(_ROOT / "docker-compose.yml"),
            "-f",
            str(_ROOT / "docker-compose.build.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    build = json.loads(done.stdout)["services"]["embedding"]["build"]
    assert build["dockerfile"] == "services/embedding/Dockerfile"
    assert "EMBEDDING_REQUIREMENTS" not in (build.get("args") or {})
