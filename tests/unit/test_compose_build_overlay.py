"""``docker-compose.yml`` names the platform images; ``docker-compose.build.yml`` builds them.

prebuilt-images design.md §3.2 / §5 (server-gpu-plan.md 1.1). A server pulls a
released image and has no source tree, so the base file must carry no ``build:``
anywhere, and the development overlay must carry ``build`` and nothing else
(the owner declined a ``pull_policy``, D-3). The tests that need Compose itself
call ``docker compose config`` only -- it contacts no service -- with an
explicit ``--env-file .env.example`` and ``COMPOSE_FILE`` removed, so the
operator's real environment file is never read.
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
_OVERLAY = _ROOT / "docker-compose.build.yml"
_ENV_EXAMPLE = _ROOT / ".env.example"

_APP_IMAGE = "${APP_IMAGE_REPO:-aizzak/app}:"
_EMBEDDING_IMAGE = "${EMBEDDING_IMAGE_REPO:-aizzak/embedding}:"


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


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _contains_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_contains_key(v, key) for v in node)
    return False


def _services(path: Path) -> dict[str, dict[str, Any]]:
    services = _load(path)["services"]
    assert isinstance(services, dict)
    return services


def _platform_services() -> dict[str, str]:
    """service -> expected Dockerfile, for every service on a platform image."""
    expected: dict[str, str] = {}
    for name, svc in _services(_BASE).items():
        image = svc.get("image", "")
        if image.startswith(_APP_IMAGE):
            expected[name] = "Dockerfile"
        elif image.startswith(_EMBEDDING_IMAGE):
            expected[name] = "services/embedding/Dockerfile"
    return expected


def _compose_config(*files: str, profile: str | None = None) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_FILE"}
    cmd = ["docker", "compose", "--env-file", str(_ENV_EXAMPLE)]
    for f in files:
        cmd += ["-f", f]
    if profile:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    done = subprocess.run(
        cmd, cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert isinstance(result, dict)
    return result


# ── AC-1.1 ──────────────────────────────────────────────────────────────────


def test_base_file_has_no_build_key_in_any_service_or_extension() -> None:
    assert not _contains_key(_load(_BASE), "build"), (
        "docker-compose.yml must only NAME images; `build:` lives in docker-compose.build.yml"
    )


# ── AC-1.2 ──────────────────────────────────────────────────────────────────


def test_every_platform_image_service_has_a_build_entry() -> None:
    expected = _platform_services()
    assert len(expected) == 10, sorted(expected)
    assert "backup" in expected
    overlay = _services(_OVERLAY)
    assert set(overlay) == set(expected)
    for name, dockerfile in expected.items():
        build = overlay[name]["build"]
        assert build == {"context": ".", "dockerfile": dockerfile}, name


# ── AC-1.3 ──────────────────────────────────────────────────────────────────


def test_the_overlay_only_carries_build() -> None:
    overlay = _load(_OVERLAY)
    assert set(overlay) == {"services"}
    for name, svc in overlay["services"].items():
        assert set(svc) == {"build"}, f"{name}: the overlay carries only `build` (no pull_policy)"


# ── AC-1.4 ──────────────────────────────────────────────────────────────────


def test_platform_image_defaults_resolve_to_todays_local_names() -> None:
    raw = _BASE.read_text(encoding="utf-8")
    for var in ("APP_IMAGE_REPO", "APP_IMAGE_TAG", "EMBEDDING_IMAGE_REPO", "EMBEDDING_IMAGE_TAG"):
        assert var in raw

    def resolve(ref: str) -> str:
        return re.sub(r"\$\{[A-Z_]+:-([^}]*)\}", r"\1", ref)

    images = {
        svc["image"] for name, svc in _services(_BASE).items() if name in _platform_services()
    }
    assert {resolve(i) for i in images} == {"aizzak/app:dev", "aizzak/embedding:dev"}


# ── AC-1.5 (automatic proxy) · AC-1.6 ───────────────────────────────────────


@_needs_compose
def test_overlay_renders_todays_build_and_image_names() -> None:
    cfg = _compose_config("docker-compose.yml", "docker-compose.build.yml", profile="backup")
    built = {n: s for n, s in cfg["services"].items() if "build" in s}
    expected = _platform_services()
    assert set(built) == set(expected)
    for name, svc in built.items():
        assert svc["build"]["context"] == str(_ROOT), name
        assert svc["build"]["dockerfile"] == expected[name], name
        want = "aizzak/embedding:dev" if name == "embedding" else "aizzak/app:dev"
        assert svc["image"] == want, name
    assert not any("pull_policy" in s for s in cfg["services"].values())


@_needs_compose
def test_base_file_alone_renders_and_builds_nothing(tmp_path: Path) -> None:
    shutil.copy(_BASE, tmp_path / "docker-compose.yml")
    shutil.copy(_ENV_EXAMPLE, tmp_path / ".env")
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_FILE"}
    # `.env.example` references repo paths; mirror the tree Compose resolves.
    for rel in ("deploy",):
        (tmp_path / rel).symlink_to(_ROOT / rel)
    quiet = subprocess.run(
        ["docker", "compose", "config", "-q"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert quiet.returncode == 0, quiet.stderr
    rendered = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    assert [n for n, s in services.items() if "build" in s] == []


# ── AC-5.1 ──────────────────────────────────────────────────────────────────


def test_env_example_offers_the_overlay_line_commented_only() -> None:
    lines = _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    assert "#COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml" in lines
    assert not [ln for ln in lines if ln.startswith("COMPOSE_FILE=")]
    assert not [ln for ln in lines if re.match(r"^(APP|EMBEDDING)_IMAGE_(REPO|TAG)=", ln)]
