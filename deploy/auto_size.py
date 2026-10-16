#!/usr/bin/env python3
"""auto-size: write Compose limits computed from THIS host's CPUs and memory.

    deploy/auto_size.py [--role dev|server] [--dry-run] [--host-cpus N] [--host-memory-gb G]
                        [--mode cpu|gpu] [--profiles a,b] [--env-file PATH]

It writes two things and nothing else:

  * a marked block in `.env` holding the 85 keys of `deploy/sizes/large.env`, and
  * `deploy/postgres/sizes/auto.conf` (not in git), which the block points Postgres at.

Every number comes from the named size files (`small`, `large`) and the Compose
files as they are; the formula interpolates between the two with integer
arithmetic, so the output is byte-for-byte reproducible. Delete the block (both
marker lines included) to return to the repository defaults.

Exit codes: 0 written (or shown by --dry-run) · 1 refused: the host is too small for
`small` (role server) or `resource-budget.sh` rejected the result · 2 usage error ·
3 `.env` cannot be touched safely (missing, malformed block, conflicting key).

Standard library plus PyYAML only, Python >= 3.10: a server may be Ubuntu 22.04.
Design: docs/delivery/compose-auto-size/design.md. Nothing here prints a value read from
`.env`: messages carry key names, line numbers, paths and GB figures.
"""

from __future__ import annotations

import argparse
import copy
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

import yaml

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
BLOCK_START: Final = "# >>> aizzak auto-size >>>"
BLOCK_END: Final = "# <<< aizzak auto-size <<<"
CONTAINER_CONF: Final = "/etc/postgresql/sizes/auto.conf"
CONF_RELATIVE: Final = Path("deploy/postgres/sizes/auto.conf")  # under the .env's directory
STEPS: Final = 1000
MEMORY_SHARE: Final = 0.9
CPUS_PER_REPLICA: Final = 8
DEV_PEAK_MARGIN: Final = Fraction(5, 4)
DEV_ROUND_MIB: Final = 256
REDIS_CACHE_OVERHEAD_MIB: Final = 768
ECS_SHARE: Final = Fraction(3, 8)

# The CPU ladder runs from `small` (8 vCPU) to `large` (32 vCPU), the two `# host:`
# headers of deploy/sizes/{small,large}.env, in hundredths of a vCPU.
_CC_FLOOR: Final = 800
_CC_SPAN: Final = 2400
_GIB_MIB: Final = 1024
_GB: Final = 1024**3
_PLAIN_CONF_MODE: Final = 0o644

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_ENV = 0, 1, 2, 3

Role = Literal["server", "dev"]
Mode = Literal["cpu", "gpu"]

# Peak `container_memory_working_set_bytes` per service over 15 days on the development
# host, in MiB, kept as text so Fraction() reads them exactly. Source:
# docs/delivery/monitoring-host-postgres/design.md:360-380. The `dev` role never sets a
# limit below peak x 1.25 (rounded up to 256 MiB), nor above `large`. Services with no
# measured peak follow the formula.
DEV_PEAKS_MIB: Final[Mapping[str, str]] = {
    "QDRANT_MEM": "7519.2",
    "POSTGRES_MEM": "2877.2",
    "APP_MEM": "1033.5",
    "LOKI_MEM": "1020.8",
    "MINIO_MEM": "797.7",
    "GRAFANA_MEM": "511.7",
    "PROMETHEUS_MEM": "214.8",
    "OPS_SCHEDULER_MEM": "183.2",
    "REDIS_CACHE_MEM": "21.0",
    "ALERT_SINK_MEM": "31.1",
    "OLLAMA_BRIDGE_MEM": "5.9",
}

_INTERP = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}$]*)\}")
_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_NUMBER = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_SUFFIX = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
_MEM = re.compile(r"^([0-9]+)([mg])$")
_CONF_LINE = re.compile(r"^([a-z_]+)\s*=\s*([0-9]+)(MB|GB)$")
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class UsageError(Exception):
    """A bad argument (exit 2)."""


class Refused(Exception):
    """The host cannot run this stack (exit 1). GB figures only."""


class EnvRefused(Exception):
    """`.env` cannot be touched safely (exit 3). Key names, line numbers and paths only."""


@dataclass(frozen=True)
class Host:
    cpus: str  # exactly what is printed and passed to resource-budget.sh
    memory_gb: str


@dataclass(frozen=True)
class Plan:
    role: Role
    mode: Mode
    profiles: tuple[str, ...]
    host: Host
    k: int
    values: dict[str, str]  # the 85 keys of large.env, in its order
    conf: str  # auto.conf text
    total_cpus: float
    total_memory_gb: float
    allowed_gb: float


# --------------------------------------------------------------------------- files --


def _read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, _, value = line.partition("=")
        values[key] = value
    return values


@lru_cache(maxsize=1)
def _ladder() -> tuple[dict[str, str], dict[str, str]]:
    sizes = REPO_ROOT / "deploy" / "sizes"
    return _read_values(sizes / "small.env"), _read_values(sizes / "large.env")


def _conf_mib(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _CONF_LINE.match(line.strip())
        if match:
            amount = int(match.group(2))
            out[match.group(1)] = amount * (_GIB_MIB if match.group(3) == "GB" else 1)
    return out


@lru_cache(maxsize=1)
def _confs() -> tuple[dict[str, int], dict[str, int]]:
    sizes = REPO_ROOT / "deploy" / "postgres" / "sizes"
    return _conf_mib(sizes / "small.conf"), _conf_mib(sizes / "large.conf")


@lru_cache(maxsize=2)
def _compose(mode: Mode) -> dict[str, Any]:
    def load(name: str) -> dict[str, Any]:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        loaded: Any = yaml.load(text, Loader=_LOADER)
        return dict(loaded or {})

    def merge(base: dict[str, Any], extra: Mapping[str, Any]) -> None:
        for key, value in extra.items():
            if isinstance(value, Mapping) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)

    merged = load("docker-compose.yml")
    if mode == "gpu":
        merge(merged, load("docker-compose.gpu.yml"))
    return merged


@lru_cache(maxsize=1)
def _known_profiles() -> frozenset[str]:
    names: set[str] = set()
    for mode in ("cpu", "gpu"):
        for service in _compose(mode).get("services", {}).values():
            names.update((service or {}).get("profiles") or ())
    return frozenset(names)


@dataclass(frozen=True)
class _Row:
    name: str
    profiles: frozenset[str]
    oneshot: bool
    replicas: str
    cpus: str
    memory: str


@lru_cache(maxsize=2)
def _rows(mode: Mode) -> tuple[_Row, ...]:
    rows = []
    for name, service in sorted(_compose(mode)["services"].items()):
        deploy = service.get("deploy") or {}
        limits = (deploy.get("resources") or {}).get("limits") or {}
        if "cpus" not in limits or "memory" not in limits:
            continue
        rows.append(
            _Row(
                name,
                frozenset(service.get("profiles") or ()),
                service.get("restart") == "no",
                str(deploy.get("replicas", 1)),
                str(limits["cpus"]),
                str(limits["memory"]),
            )
        )
    return tuple(rows)


def _resolve(raw: str, values: Mapping[str, str]) -> str:
    if "$" not in raw:
        return raw
    match = _INTERP.fullmatch(raw)
    if match is None:
        raise ValueError(f"unsupported interpolation: {raw!r}")
    return values.get(match.group(1)) or match.group(2)


def _to_bytes(raw: str) -> int:
    text = raw.strip().lower().removesuffix("b") or "0"
    if text[-1] in _SUFFIX:
        return int(float(text[:-1]) * _SUFFIX[text[-1]])
    return int(float(text))


def _standing_totals(
    mode: Mode, values: Mapping[str, str], enabled: frozenset[str]
) -> tuple[float, int]:
    """(vCPU, bytes) of the services a default `docker compose up -d` keeps running."""
    cpus = 0.0
    memory = 0
    for row in _rows(mode):
        if row.profiles - enabled or row.oneshot:
            continue
        replicas = int(_resolve(row.replicas, values))
        cpus += float(_resolve(row.cpus, values)) * replicas
        memory += _to_bytes(_resolve(row.memory, values)) * replicas
    return cpus, memory


# --------------------------------------------------------------------------- host --


def read_host(cpus: str | None, memory_gb: str | None) -> Host:
    for flag, raw in (("--host-cpus", cpus), ("--host-memory-gb", memory_gb)):
        if raw is not None and (not _NUMBER.match(raw) or float(raw) <= 0):
            raise UsageError(f"{flag} needs a positive number, got {raw!r}")
    if cpus is None:
        cpus = str(len(os.sched_getaffinity(0)))
    if memory_gb is None:
        kib = 0
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                kib = int(line.split()[1])
        memory_gb = f"{kib / 1048576:.2f}"
    return Host(cpus, memory_gb)


# --------------------------------------------------------------------------- formula --


def _mib(raw: str) -> int:
    match = _MEM.match(raw)
    if match is None:
        raise ValueError(f"not a memory size in m/g: {raw!r}")
    return int(match.group(1)) * (_GIB_MIB if match.group(2) == "g" else 1)


def _fmt_mib(mib: int) -> str:
    return f"{mib // _GIB_MIB}g" if mib % _GIB_MIB == 0 else f"{mib}m"


def _fmt_redis(mib: int) -> str:
    return f"{mib // _GIB_MIB}gb" if mib % _GIB_MIB == 0 else f"{mib}mb"


def _fmt_cpus(hundredths: int) -> str:
    whole, frac = divmod(hundredths, 100)
    return f"{whole}.{frac:02d}".rstrip("0") if frac else f"{whole}.0"


def _cc(raw: str) -> int:
    return round(Fraction(raw) * 100)


def dev_floor_mib(key: str) -> int | None:
    """The lowest limit the `dev` role gives `key` (MiB), or None without a measured peak."""
    peak = DEV_PEAKS_MIB.get(key)
    if peak is None:
        return None
    rounded = math.ceil(Fraction(peak) * DEV_PEAK_MARGIN / DEV_ROUND_MIB) * DEV_ROUND_MIB
    return min(rounded, _mib(_ladder()[1][key]))


def _maxmemory_mib(key: str, mem_mib: int) -> int:
    if key == "REDIS_STREAM_MAXMEMORY":
        return (mem_mib * 2) // 3  # mem >= 1.5 x maxmemory
    if key == "REDIS_CACHE_MAXMEMORY":
        return mem_mib - REDIS_CACHE_OVERHEAD_MIB
    raise ValueError(f"no rule for {key}")


def _mem_mib(key: str, small: int, large: int, k: int, role: Role) -> int:
    value = small + (k * (large - small)) // STEPS
    floor = dev_floor_mib(key) if role == "dev" else None
    if floor is not None:
        value = min(large, max(value, floor))
    return value


def _values(k: int, host: Host, role: Role, *, memory_bound_replicas: bool) -> dict[str, str]:
    """The 85 keys for step `k`. With `memory_bound_replicas` the replica counts are the
    ones memory alone would allow, which is what the search for `k` has to budget for."""
    small, large = _ladder()
    host_cc = _cc(host.cpus)
    cpu_replicas = math.floor(Fraction(host.cpus) / CPUS_PER_REPLICA)
    out: dict[str, str] = {}
    mem: dict[str, int] = {}
    for key, big in large.items():
        low = small[key]
        if key == "POSTGRES_CONFIG_FILE":
            out[key] = CONTAINER_CONF
        elif key.endswith("_CPUS"):
            span = min(max(host_cc - _CC_FLOOR, 0), _CC_SPAN)
            lo, hi = _cc(low), _cc(big)
            out[key] = _fmt_cpus(lo + (span * (hi - lo)) // _CC_SPAN)
        elif key.endswith("_MEM"):
            mem[key] = _mem_mib(key, _mib(low), _mib(big), k, role)
            out[key] = _fmt_mib(mem[key])
        elif key.endswith("_REPLICAS"):
            lo, hi = int(low), int(big)
            by_memory = lo + (2 * k * (hi - lo) + 1000) // 2000
            by_cpu = min(max(cpu_replicas, lo), hi)
            out[key] = str(by_memory if memory_bound_replicas else min(by_memory, by_cpu))
        elif key.endswith("_MAXMEMORY"):
            out[key] = ""  # derived below, once the MEM it follows is known
        else:
            raise ValueError(f"no rule for {key}")
    for key in out:
        if key.endswith("_MAXMEMORY"):
            out[key] = _fmt_redis(_maxmemory_mib(key, mem[key.replace("_MAXMEMORY", "_MEM")]))
    return out


def _fits(mode: Mode, values: Mapping[str, str], enabled: frozenset[str], host: Host) -> bool:
    _, memory = _standing_totals(mode, values, enabled)
    return not memory / _GB > float(host.memory_gb) * MEMORY_SHARE


def compute(host: Host, role: Role, mode: Mode, profiles: frozenset[str]) -> Plan:
    enabled = frozenset(profiles | ({"local-llm"} if mode == "gpu" else frozenset()))
    allowed = float(host.memory_gb) * MEMORY_SHARE

    def budgeted(k: int) -> dict[str, str]:
        return _values(k, host, role, memory_bound_replicas=True)

    if _fits(mode, budgeted(0), enabled, host):
        low, high = 0, STEPS
        while low < high:  # S(k) never decreases, so the largest fitting k is found by bisection
            mid = (low + high + 1) // 2
            if _fits(mode, budgeted(mid), enabled, host):
                low = mid
            else:
                high = mid - 1
        k = low
    elif role == "server":
        _, memory = _standing_totals(mode, budgeted(0), enabled)
        need = memory / _GB
        raise Refused(
            f"role server, mode {mode}: the smallest limits need {need:.2f} GB, {allowed:.2f} GB"
            f" allowed on a {float(host.memory_gb):.2f} GB host (10% margin):"
            f" {need - allowed:.2f} GB short. Nothing was written."
            " On a development machine use --role dev."
        )
    else:
        k = 0  # dev: over-commit is a warning, reported by resource-budget.sh
    values = _values(k, host, role, memory_bound_replicas=False)
    cpus, memory = _standing_totals(mode, values, enabled)
    plan = Plan(
        role,
        mode,
        tuple(sorted(profiles)),
        host,
        k,
        values,
        "",
        cpus,
        memory / _GB,
        allowed,
    )
    return replace(plan, conf=render_conf(plan))


# --------------------------------------------------------------------------- output --


def _g(raw: str) -> str:
    return f"{float(raw):g}"


def _conf_size(mib: int) -> str:
    return f"{mib // _GIB_MIB}GB" if mib % _GIB_MIB == 0 else f"{mib}MB"


def render_conf(plan: Plan) -> str:
    small, large = _confs()
    pick = {
        key: small[key] + (plan.k * (large[key] - small[key])) // STEPS
        for key in ("shared_buffers", "maintenance_work_mem")
    }
    host_mib = math.floor(Fraction(plan.host.memory_gb) * _GIB_MIB)
    cache = math.floor(host_mib * ECS_SHARE)
    return (
        f"# Generated by deploy/auto_size.py (role {plan.role}, host {_g(plan.host.cpus)} vCPU"
        f" / {_g(plan.host.memory_gb)} GB, mode {plan.mode}). Not in git; re-run to change.\n"
        "include '/etc/postgresql/postgresql.conf'\n"
        f"shared_buffers = {_conf_size(pick['shared_buffers'])}\n"
        f"effective_cache_size = {_conf_size(cache)}\n"
        f"maintenance_work_mem = {_conf_size(pick['maintenance_work_mem'])}\n"
        f"work_mem = {_conf_size(large['work_mem'])}\n"
    )


def render_block(plan: Plan) -> str:
    lines = [
        BLOCK_START,
        "# Generated by deploy/auto_size.py. Do not edit: re-run it. Delete this block, both"
        " marker",
        "# lines included, to return to the repository defaults (docs/design/08-local-runbook.md"
        " 2-z).",
        f"# role: {plan.role}",
        f"# host: cpus={_g(plan.host.cpus)} memory_gb={_g(plan.host.memory_gb)} mode={plan.mode}",
        f"# profiles: {','.join(plan.profiles)}",
        f"# source: auto (deploy/auto_size.py, t={plan.k / STEPS:.3f})",
        *(f"{key}={value}" for key, value in plan.values.items()),
        BLOCK_END,
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- .env --


def _lines(text: str) -> list[str]:
    """Lines with their endings kept; splitlines() would also cut on \\x0b, \\x0c, U+2028."""
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def _bare(line: str) -> str:
    return line.rstrip("\r\n")


def find_block(text: str) -> tuple[int, int] | None:
    """Indexes of the marker lines, or None. EnvRefused if the block is malformed."""
    lines = [_bare(line) for line in _lines(text)]
    starts = [i for i, line in enumerate(lines) if line == BLOCK_START]
    ends = [i for i, line in enumerate(lines) if line == BLOCK_END]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1:
        raise EnvRefused(
            f"the auto-size block is malformed: {len(starts)} start and {len(ends)} end marker"
            " lines (expected one of each). Fix or delete it by hand. Nothing was written."
        )
    if ends[0] < starts[0]:
        raise EnvRefused(
            f"the auto-size block is malformed: its end marker (line {ends[0] + 1}) comes before"
            f" its start marker (line {starts[0] + 1}). Fix or delete it by hand."
            " Nothing was written."
        )
    return starts[0], ends[0]


def block_role(text: str) -> Role | None:
    span = find_block(text)
    if span is None:
        return None
    for line in _lines(text)[span[0] : span[1] + 1]:
        match = re.fullmatch(r"# role: (server|dev)", _bare(line))
        if match:
            return "dev" if match.group(1) == "dev" else "server"
    return None


def _unquote(raw: str) -> str:
    value = raw.strip()
    quoted = re.fullmatch(r"""(['"])(.*)\1""", value)
    return quoted.group(2) if quoted else value


def _outside(text: str) -> list[tuple[int, str]]:
    """(1-based line number, line) for every line outside the block."""
    span = find_block(text)
    return [
        (i + 1, line)
        for i, line in enumerate(_lines(text))
        if span is None or not span[0] <= i <= span[1]
    ]


def splice(text: str, block: str, template: Mapping[str, str]) -> str:
    """`text` with `block` in place of the old block, or appended. EnvRefused on a conflict."""
    span = find_block(text)
    lines = _lines(text)
    keys = {
        line.partition("=")[0] for line in block.splitlines() if line and not line.startswith("#")
    }
    for number, line in _outside(text):
        match = _ASSIGN.match(_bare(line))
        if match is None or match.group(1) not in keys:
            continue
        key = match.group(1)
        before_block = span is None or number - 1 < span[0]
        if before_block and key in template and _unquote(match.group(2)) == template[key]:
            continue  # the untouched .env.example default; the block comes later and wins
        raise EnvRefused(
            f"{key} is also defined outside the auto-size block (.env line {number}); Compose"
            " keeps the last definition, so one would override the other silently. Remove that"
            " line and re-run. Nothing was written."
        )
    eol = "\r\n" if "\r\n" in text else "\n"
    new = block.replace("\n", eol)
    if span is None:
        glue = "" if not text or text.endswith("\n") else eol
        return text + glue + new
    if not lines[span[1]].endswith("\n"):
        new = new.removesuffix(eol)
    return "".join(lines[: span[0]]) + new + "".join(lines[span[1] + 1 :])


def _dotenv_last(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for _, line in _outside(text):
        match = _ASSIGN.match(_bare(line))
        if match:
            found[match.group(1)] = _unquote(match.group(2))
    return found


def _lookup(
    name: str, text_values: Mapping[str, str], environ: Mapping[str, str]
) -> tuple[str | None, str]:
    if name in environ:
        return environ[name], f"{name} in the environment"
    if name in text_values:
        return text_values[name], f"{name} in .env"
    return None, "default"


def detect(
    text: str, environ: Mapping[str, str], mode: Mode | None, profiles: str | None
) -> tuple[Mode, frozenset[str], str]:
    """Mode and profiles as Compose itself would pick them. The str says where from."""
    dotenv = _dotenv_last(text)
    if mode is not None:
        found: Mode = mode
        mode_from = "--mode"
    else:
        raw, mode_from = _lookup("COMPOSE_FILE", dotenv, environ)
        sep = _lookup("COMPOSE_PATH_SEPARATOR", dotenv, environ)[0] or ":"
        names = [Path(part).name for part in (raw or "").split(sep)]
        found = "gpu" if "docker-compose.gpu.yml" in names else "cpu"
    known = _known_profiles()
    raw_profiles: str | None
    if profiles is not None:
        raw_profiles, profiles_from = profiles, "--profiles"
    else:
        raw_profiles, profiles_from = _lookup("COMPOSE_PROFILES", dotenv, environ)
    wanted = [part.strip() for part in (raw_profiles or "").split(",") if part.strip()]
    if wanted == ["*"]:
        chosen = known
    else:
        if profiles is not None and set(wanted) - known:
            raise UsageError(f"unknown profile(s) {','.join(sorted(set(wanted) - known))}")
        chosen = frozenset(wanted) & known
    label = (
        f"mode {found} ({mode_from}), profiles {','.join(sorted(chosen)) or 'none'}"
        f" ({profiles_from})"
    )
    return found, chosen, label


@lru_cache(maxsize=1)
def _template() -> dict[str, str]:
    path = REPO_ROOT / ".env.example"
    return _read_values(path) if path.is_file() else {}


def write_atomic(path: Path, text: str, mode: int) -> None:
    """Temp file next to `path`, fsync, chmod, then os.replace. Never a half-written file."""
    prefix = ".env.bak.auto-size-" if path.name.startswith(".env") else f"{path.name}."
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=prefix)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        if os.geteuid() == 0 and path.exists():
            owner = path.stat()
            os.chown(tmp, owner.st_uid, owner.st_gid)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- budget --


def budget_check(plan: Plan) -> tuple[int, str]:
    """Run `resource-budget.sh --block` on exactly what would be written (FR-14)."""
    with tempfile.TemporaryDirectory(prefix="auto-size-") as tmp:
        root = Path(tmp)
        root.chmod(0o700)
        conf_dir = root / CONF_RELATIVE.parent
        conf_dir.mkdir(parents=True)
        (conf_dir / CONF_RELATIVE.name).write_text(plan.conf, encoding="utf-8")
        (root / ".env").write_text(render_block(plan), encoding="utf-8")
        done = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "deploy" / "resource-budget.sh"),
                "--block",
                str(root / ".env"),
                "--host-cpus",
                plan.host.cpus,
                "--host-memory-gb",
                plan.host.memory_gb,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={
                **os.environ,
                "COMPOSE_FILE": str(REPO_ROOT / "docker-compose.yml"),
                "GPU_COMPOSE_FILE": str(REPO_ROOT / "docker-compose.gpu.yml"),
            },
            timeout=120,
            check=False,
        )
    return done.returncode, done.stdout + done.stderr


# --------------------------------------------------------------------------- main --


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto_size.py",
        description="Write Compose limits computed from this host into a marked block in .env.",
    )
    parser.add_argument("--role", choices=("dev", "server"), default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host-cpus")
    parser.add_argument("--host-memory-gb")
    parser.add_argument("--mode", choices=("cpu", "gpu"), default=None)
    parser.add_argument("--profiles", default=None)
    parser.add_argument("--env-file", default=None)
    return parser


def _pick_role(flag: str | None, text: str) -> tuple[Role, str]:
    if flag is not None:
        return ("dev" if flag == "dev" else "server"), "--role"
    remembered = block_role(text)
    if remembered is not None:
        return remembered, "kept from the existing block"
    return "server", "default"


def _summary(plan: Plan, role_from: str, label: str) -> str:
    replicas = " ".join(f"{k}={v}" for k, v in plan.values.items() if k.endswith("_REPLICAS"))
    return (
        f"auto-size: role {plan.role} ({role_from}), {label}\n"
        f"host: {_g(plan.host.cpus)} vCPU / {_g(plan.host.memory_gb)} GB\n"
        f"t = {plan.k / STEPS:.3f}  {replicas}\n"
        f"  TOTAL  {plan.total_cpus:6.2f} vCPU  {plan.total_memory_gb:7.2f} GB"
        f"  (allowed {plan.allowed_gb:.2f} GB)\n"
    )


def _run(args: argparse.Namespace, env_path: Path, environ: Mapping[str, str]) -> int:
    host = read_host(args.host_cpus, args.host_memory_gb)
    if not env_path.is_file():
        raise EnvRefused(
            f"{env_path.name} does not exist; create it first: cp .env.example .env."
            " Nothing was created."
        )
    conf_dir = env_path.parent / CONF_RELATIVE.parent
    if not conf_dir.is_dir():
        raise EnvRefused(
            f"{conf_dir} is missing: auto-size must run next to the Compose project."
            " Nothing was written."
        )
    with open(env_path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    role, role_from = _pick_role(args.role, text)
    mode, profiles, label = detect(text, environ, args.mode, args.profiles)
    plan = compute(host, role, mode, profiles)
    new_text = splice(text, render_block(plan), _template())
    exported = [key for key in plan.values if key in environ]
    if exported:
        print(
            f"auto-size: warning: {', '.join(exported)} exported in the shell; Compose gives the"
            " environment priority over .env, so the block does not take effect there.",
            file=sys.stderr,
        )
    code, report = budget_check(plan)
    if code != 0:
        print(report, file=sys.stderr, end="")
        print(
            f"auto-size: resource-budget.sh --block rejected the result (exit {code})."
            " Nothing was written.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    print(_summary(plan, role_from, label) + report, end="")
    if args.dry_run:
        print(f"\n--dry-run: nothing written. The block would be:\n{render_block(plan)}")
        print(f"and {conf_dir / CONF_RELATIVE.name}:\n{plan.conf}")
        return EXIT_OK
    write_atomic(conf_dir / CONF_RELATIVE.name, plan.conf, _PLAIN_CONF_MODE)
    write_atomic(env_path, new_text, stat.S_IMODE(env_path.stat().st_mode))
    print(
        f"\nwrote {env_path} (auto-size block) and {conf_dir / CONF_RELATIVE.name}\n"
        "next: docker compose up -d\n"
        "      docker compose restart postgres   (it reads auto.conf at boot)"
    )
    return EXIT_OK


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    env_path = Path(args.env_file) if args.env_file else REPO_ROOT / ".env"
    try:
        return _run(args, env_path.resolve(), os.environ if environ is None else environ)
    except UsageError as exc:
        parser.error(str(exc))
    except Refused as exc:
        print(f"auto-size: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except EnvRefused as exc:
        print(f"auto-size: {exc}", file=sys.stderr)
        return EXIT_ENV


if __name__ == "__main__":
    sys.exit(main())
