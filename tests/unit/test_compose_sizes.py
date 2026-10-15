"""Compose limits as variables, and the size files that set them (server-gpu 3.1-3.4).

Unit level only: nothing here starts a container. The `docker compose config`
checks run the CLI (which starts nothing) with a clean environment and an
explicit `--env-file`, so neither the host's variables nor the owner's `.env`
can change an answer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit.support_compose import (
    COMPOSE,
    GPU_OVERLAY,
    MODE_OVERLAYS,
    REPO_ROOT,
    SIZES,
    SIZES_DIR,
    UnsupportedInterpolation,
    compose_bytes,
    load_compose,
    read_size,
    redis_bytes,
    resolve,
    size_compose,
    var_name,
)

_RAW: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
_SERVICES: dict[str, Any] = _RAW["services"]
_VAR = re.compile(r"^\$\{([A-Z][A-Z0-9_]*):-([^}$]+)\}$")
_ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _limits(service: Mapping[str, Any]) -> Mapping[str, Any]:
    return ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}


def _expected_keys() -> dict[str, str]:
    """Every variable the compose file reads for sizing, name -> default."""
    found: dict[str, str] = {}
    for name, service in _SERVICES.items():
        deploy = service.get("deploy") or {}
        raw = {
            "CPUS": _limits(service).get("cpus"),
            "MEM": _limits(service).get("memory"),
            "REPLICAS": deploy.get("replicas"),
        }
        for suffix, value in raw.items():
            if value is None:
                continue
            match = _VAR.match(str(value))
            assert match, f"{name}: {suffix} is not a ${{VAR:-default}}: {value!r}"
            assert match.group(1) == var_name(name, suffix)
            found[match.group(1)] = match.group(2)
    for service, var in (("redis-stream", "STREAM"), ("redis-cache", "CACHE")):
        command = [str(t) for t in _SERVICES[service]["command"]]
        raw_max = command[command.index("--maxmemory") + 1]
        match = _VAR.match(raw_max)
        assert match and match.group(1) == f"REDIS_{var}_MAXMEMORY", raw_max
        found[match.group(1)] = match.group(2)
    found["POSTGRES_CONFIG_FILE"] = ""
    return found


# ------------------------------------------------------------------ AC-1.1 ---


def test_every_limit_and_replica_count_is_a_named_variable() -> None:
    counts = {"cpus": 0, "memory": 0, "replicas": 0}
    for name, service in _SERVICES.items():
        limits = _limits(service)
        assert "cpus" in limits and "memory" in limits, f"{name} lacks limits"
        for key, suffix in (("cpus", "CPUS"), ("memory", "MEM")):
            counts[key] += 1
            match = _VAR.match(str(limits[key]))
            assert match, f"{name}.{key} is a literal: {limits[key]!r}"
            assert match.group(1) == var_name(name, suffix)
        if "replicas" in (service.get("deploy") or {}):
            counts["replicas"] += 1
            match = _VAR.match(str(service["deploy"]["replicas"]))
            assert match and match.group(1) == var_name(name, "REPLICAS")
    assert counts == {"cpus": 39, "memory": 39, "replicas": 4}
    assert len(_expected_keys()) == 85


# ------------------------------------------------------------------ AC-1.2 ---


def test_resolve_absent_given_and_empty() -> None:
    raw = "${X_CPUS:-2.0}"
    assert resolve(raw) == "2.0"
    assert resolve(raw, {"X_CPUS": "4.0"}) == "4.0"
    assert resolve(raw, {"X_CPUS": ""}) == "2.0"  # `:-` means empty == unset
    assert resolve("plain") == "plain"
    assert resolve(3) == "3"


def test_resolve_ignores_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_CPUS", "99")
    assert resolve("${X_CPUS:-2.0}") == "2.0"
    assert load_compose()["services"]["app"]["deploy"]["replicas"] == "3"


@pytest.mark.parametrize(
    "raw", ["${X}", "${X?}", "${X:?msg}", "${X:-1}${Y:-2}", "pre-${X:-1}", "${X:-1}-post"]
)
def test_resolve_refuses_every_other_form(raw: str) -> None:
    with pytest.raises(UnsupportedInterpolation, match="unsupported interpolation"):
        resolve(raw)


# ------------------------------------------------------------ AC-3.1 .. 3.5 ---


def test_the_size_directory_holds_exactly_the_three_sizes() -> None:
    assert sorted(p.stem for p in SIZES_DIR.glob("*.env")) == sorted(SIZES)


@pytest.mark.parametrize("size", SIZES)
def test_a_size_file_has_a_header_and_exactly_the_derived_keys(size: str) -> None:
    parsed = read_size(size)
    assert parsed.name == size
    assert parsed.host_cpus > 0 and parsed.host_memory_gb > 0
    assert parsed.source.startswith(("measured", "derived"))
    assert set(parsed.values) == set(_expected_keys())
    assert len(parsed.values) == 85


@pytest.mark.parametrize("size", SIZES)
def test_every_value_has_its_grammar(size: str) -> None:
    for key, value in read_size(size).values.items():
        if key.endswith("_CPUS"):
            assert float(value) > 0, key
        elif key.endswith("_MAXMEMORY"):
            assert redis_bytes(value) > 0, key
        elif key.endswith("_MEM"):
            assert re.fullmatch(r"[1-9]\d*[mg]", value), f"{key}={value}"
        elif key.endswith("_REPLICAS"):
            assert int(value) >= 1, key


@pytest.mark.parametrize("size", SIZES)
def test_the_postgres_config_path_names_a_file_that_exists(size: str) -> None:
    path = read_size(size).values["POSTGRES_CONFIG_FILE"]
    prefix = "/etc/postgresql/sizes/"
    assert path == f"{prefix}{size}.conf"
    assert (REPO_ROOT / "deploy" / "postgres" / "sizes" / f"{size}.conf").is_file()


def test_large_is_the_default_except_what_gpu_mode_needs() -> None:
    values = read_size("large").values
    differing = {k for k, v in values.items() if _expected_keys()[k] != v}
    assert differing == {"POSTGRES_MEM", "POSTGRES_CONFIG_FILE"}
    assert values["POSTGRES_MEM"] == "15g"


def test_sizes_never_grow_downwards() -> None:
    sizes = [read_size(s).values for s in SIZES]  # small, medium, large
    for key in sizes[0]:
        if key.endswith("_MAXMEMORY"):
            parse = redis_bytes
        elif key.endswith("_MEM"):
            parse = compose_bytes
        elif key.endswith(("_CPUS", "_REPLICAS")):
            parse = float
        else:
            continue
        small, medium, large = (parse(v[key]) for v in sizes)
        assert small <= medium <= large, f"{key}: {small} {medium} {large}"


@pytest.mark.parametrize("size", SIZES)
def test_a_size_resolves_through_the_helper(size: str) -> None:
    parsed = read_size(size)
    compose = size_compose(parsed)
    app = compose["services"]["app"]["deploy"]
    assert app["replicas"] == parsed.values["APP_REPLICAS"]
    # the GPU overlay pins embedding, whatever the size file says
    assert compose["services"]["embedding"]["deploy"]["replicas"] == "1"
    assert MODE_OVERLAYS[parsed.mode] == (GPU_OVERLAY,)


# ----------------------------------------- AC-1.5 / 3.6 / US-1 edges (config) ---


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)
    return probe.returncode == 0


_needs_compose = pytest.mark.skipif(not _compose_available(), reason="docker compose missing")


def _config(
    *, size: str | None = None, overlay: bool = False, extra_env: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    env.update({k: v for k, v in os.environ.items() if k.startswith("DOCKER_")})
    cmd = ["docker", "compose", "--project-directory", str(REPO_ROOT), "-f", str(COMPOSE)]
    if overlay:
        cmd += ["-f", str(GPU_OVERLAY)]
    cmd += ["--env-file", str(_ENV_EXAMPLE)]
    if size:
        cmd += ["--env-file", str(SIZES_DIR / f"{size}.env")]
    if extra_env:
        cmd += ["--env-file", str(extra_env)]
    cmd += ["--profile", "*", "config", "--format", "json"]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)


def _deploy(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stderr[-500:]
    return json.loads(result.stdout)["services"]


@_needs_compose
@pytest.mark.parametrize("overlay", [False, True], ids=["cpu", "gpu"])
@pytest.mark.parametrize("size", [None, *SIZES], ids=["default", *SIZES])
def test_compose_resolves_limits_as_the_helper_does(size: str | None, overlay: bool) -> None:
    services = _deploy(_config(size=size, overlay=overlay))
    parsed = read_size(size) if size else None
    files = (COMPOSE, *((GPU_OVERLAY,) if overlay else ()))
    expected = load_compose(parsed.values if parsed else None, files=files)["services"]
    for name, service in expected.items():
        if "deploy" not in service or name not in services:
            continue
        got = services[name].get("deploy") or {}
        want = service["deploy"]
        limits = _limits(service)
        got_limits = (got.get("resources") or {}).get("limits") or {}
        if limits:
            assert float(got_limits["cpus"]) == pytest.approx(float(limits["cpus"])), name
            assert int(got_limits["memory"]) == compose_bytes(limits["memory"]), name
        if "replicas" in want:
            assert int(got.get("replicas", 1)) == int(want["replicas"]), name


@_needs_compose
def test_the_gpu_overlay_pins_embedding_at_any_size() -> None:
    for size in SIZES:
        embedding = _deploy(_config(size=size, overlay=True))["embedding"]["deploy"]
        assert embedding["replicas"] == 1
        assert int(embedding["resources"]["limits"]["memory"]) == 3221225472


@_needs_compose
def test_empty_falls_back_malformed_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty.env"
    empty.write_text("APP_REPLICAS=\n", encoding="utf-8")
    assert _deploy(_config(extra_env=empty))["app"]["deploy"]["replicas"] == 3
    for text in ("APP_REPLICAS=abc\n", "POSTGRES_MEM=18x\n"):
        bad = tmp_path / "bad.env"
        bad.write_text(text, encoding="utf-8")
        assert _config(extra_env=bad).returncode != 0, text
