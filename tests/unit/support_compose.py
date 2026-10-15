"""Shared Compose-limit helpers for the unit suite (no Docker, no os.environ).

Every `cpus` / `memory` / `replicas` in `docker-compose.yml` is a
`${NAME:-default}`; `deploy/sizes/<size>.env` supplies other values. This module
resolves them the same way `deploy/resource-budget.sh` does (that script carries
its own copy; `test_resource_budget` keeps the two in agreement).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import yaml

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
COMPOSE: Final[Path] = REPO_ROOT / "docker-compose.yml"
GPU_OVERLAY: Final[Path] = REPO_ROOT / "docker-compose.gpu.yml"
SIZES_DIR: Final[Path] = REPO_ROOT / "deploy" / "sizes"

SIZES: Final[tuple[str, ...]] = ("small", "medium", "large")  # ascending (AC-3.5)
MODE_OVERLAYS: Final[Mapping[str, tuple[Path, ...]]] = {"cpu": (), "gpu": (GPU_OVERLAY,)}
MODE_PROFILES: Final[Mapping[str, frozenset[str]]] = {
    "cpu": frozenset(),
    "gpu": frozenset({"local-llm"}),
}
SUFFIXES: Final = ("CPUS", "MEM", "REPLICAS", "MAXMEMORY")

_INTERP = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}$]*)\}")
_SUFFIX = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


class UnsupportedInterpolation(ValueError):
    """A `$` form other than a whole-value `${NAME:-default}`."""


def var_name(service: str, suffix: str) -> str:
    return f"{service.upper().replace('-', '_')}_{suffix}"


def resolve(raw: object, values: Mapping[str, str] | None = None) -> str:
    text = str(raw)
    if "$" not in text:
        return text
    match = _INTERP.fullmatch(text)
    if match is None:
        raise UnsupportedInterpolation(f"unsupported interpolation: {text!r}")
    return (values or {}).get(match.group(1)) or match.group(2)


def _merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def load_compose(
    values: Mapping[str, str] | None = None, *, files: Sequence[Path] = (COMPOSE,)
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in files:
        _merge(merged, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    for service in (merged.get("services") or {}).values():
        deploy = (service or {}).get("deploy")
        if not isinstance(deploy, dict):
            continue
        if "replicas" in deploy:
            deploy["replicas"] = resolve(deploy["replicas"], values)
        limits = (deploy.get("resources") or {}).get("limits")
        if isinstance(limits, dict):
            for key in ("cpus", "memory"):
                if key in limits:
                    limits[key] = resolve(limits[key], values)
    return merged


@dataclass(frozen=True)
class SizeFile:
    name: str
    path: Path
    host_cpus: float
    host_memory_gb: float
    mode: Literal["cpu", "gpu"]
    source: str
    values: Mapping[str, str]


_HOST = re.compile(r"^# host: cpus=([0-9.]+) memory_gb=([0-9.]+) mode=(cpu|gpu)$")
_SOURCE = re.compile(r"^# source: (.+)$")
_KV = re.compile(r"^([A-Z][A-Z0-9_]*)=(\S*)$")


def read_size(name_or_path: str | Path) -> SizeFile:
    path = Path(name_or_path)
    if not path.suffix:
        path = SIZES_DIR / f"{name_or_path}.env"
    host = source = None
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if (m := _HOST.match(line)) and host is None:
            host = m
        elif (m := _SOURCE.match(line)) and source is None:
            source = m.group(1)
        elif line.startswith("#") or not line.strip():
            continue
        else:
            kv = _KV.match(line)
            assert kv, f"{path}: not KEY=VALUE: {line!r}"
            assert kv.group(1) not in values, f"{path}: duplicate key {kv.group(1)}"
            values[kv.group(1)] = kv.group(2)
    assert host is not None, f"{path}: missing '# host: cpus=N memory_gb=G mode=cpu|gpu' line"
    assert source is not None, f"{path}: missing '# source:' line"
    mode: Literal["cpu", "gpu"] = "gpu" if host.group(3) == "gpu" else "cpu"
    return SizeFile(
        path.stem, path, float(host.group(1)), float(host.group(2)), mode, source, values
    )


def size_compose(size: SizeFile) -> dict[str, Any]:
    return load_compose(size.values, files=(COMPOSE, *MODE_OVERLAYS[size.mode]))


def compose_bytes(raw: str) -> int:
    """Compose's own size grammar -- a bare integer is bytes, otherwise one of
    b/k/m/g with an optional trailing `b`."""
    text = str(raw).strip().lower().removesuffix("b") or "0"
    if text[-1] in _SUFFIX:
        return int(float(text[:-1]) * _SUFFIX[text[-1]])
    return int(float(text))


def redis_bytes(raw: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(mb|gb)", raw)
    if match is None:
        raise ValueError(f"not a Redis size (mb/gb only): {raw!r}")
    return int(match.group(1)) * (1024**2 if match.group(2) == "mb" else 1024**3)
