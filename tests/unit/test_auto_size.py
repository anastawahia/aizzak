"""deploy/auto_size.py -- limits computed from the host (compose-auto-size).

The formula is checked in-process through `compute()`; the command line through
`main([...], environ={})` on a copy of `.env.example` under `tmp_path`; one real
subprocess proves the file runs on its own. Nothing here reads or writes the
repository's own `.env` or `deploy/postgres/sizes/auto.conf` -- an autouse guard
compares their `os.stat` (never their content) before and after every test.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.unit.support_compose import (
    COMPOSE,
    GPU_OVERLAY,
    MODE_OVERLAYS,
    MODE_PROFILES,
    REPO_ROOT,
    compose_bytes,
    load_compose,
    read_size,
    redis_bytes,
)

_SCRIPT = REPO_ROOT / "deploy" / "auto_size.py"
_BUDGET = REPO_ROOT / "deploy" / "resource-budget.sh"
_ENV_EXAMPLE = REPO_ROOT / ".env.example"
_RUNBOOK = REPO_ROOT / "docs" / "design" / "08-local-runbook.md"
_REAL_FILES = (REPO_ROOT / ".env", REPO_ROOT / "deploy" / "postgres" / "sizes" / "auto.conf")
_SENTINEL = "SENTINEL_SECRET_VALUE_4711"
_TEMPLATE_CONF = "POSTGRES_CONFIG_FILE=/etc/postgresql/postgresql.conf"
_GB = 1024**3


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("auto_size", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["auto_size"] = module
    spec.loader.exec_module(module)
    return module


az = _load()


# ------------------------------------------------------------------ fixtures --


def _stat_of(path: Path) -> tuple[bool, int, int, int]:
    try:
        info = path.stat()
    except FileNotFoundError:
        return (False, 0, 0, 0)
    return (True, info.st_mtime_ns, info.st_size, info.st_ino)


@pytest.fixture(autouse=True)
def _never_touch_the_real_files() -> Iterator[None]:
    before = [_stat_of(p) for p in _REAL_FILES]
    yield
    assert [_stat_of(p) for p in _REAL_FILES] == before, "a test touched the real .env/auto.conf"


@dataclass(frozen=True)
class Tree:
    root: Path

    @property
    def env(self) -> Path:
        return self.root / ".env"

    @property
    def conf(self) -> Path:
        return self.root / "deploy" / "postgres" / "sizes" / "auto.conf"

    def text(self) -> str:
        with open(self.env, encoding="utf-8", newline="") as handle:
            return handle.read()

    def sha(self) -> str:
        return hashlib.sha256(self.env.read_bytes()).hexdigest()


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    shutil.copy(_ENV_EXAMPLE, tmp_path / ".env")
    (tmp_path / "deploy" / "postgres" / "sizes").mkdir(parents=True)
    return Tree(tmp_path)


Run = Callable[..., tuple[int, str, str]]


@pytest.fixture
def run(tree: Tree, capsys: pytest.CaptureFixture[str]) -> Run:
    def _run(*args: str, environ: dict[str, str] | None = None) -> tuple[int, str, str]:
        code = az.main([*args, "--env-file", str(tree.env)], environ=environ or {})
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _without_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.rstrip("\r\n") == az.BLOCK_START)
    end = next(i for i, ln in enumerate(lines) if ln.rstrip("\r\n") == az.BLOCK_END)
    return "".join(lines[:start] + lines[end + 1 :])


def _block_values(text: str) -> dict[str, str]:
    inside = text.split(az.BLOCK_START + "\n", 1)[1].split(az.BLOCK_END, 1)[0]
    return dict(line.partition("=")[::2] for line in inside.splitlines() if line and line[0] != "#")


def _plan(
    cpus: float, gb: float, role: str = "server", mode: str = "cpu", profiles: str = "monitoring"
) -> Any:
    # decision 7: a server runs monitoring, so the plan counts it unless a test says otherwise
    wanted = frozenset(p for p in profiles.split(",") if p)
    return az.compute(az.Host(f"{cpus:g}", f"{gb:g}"), role, mode, wanted)


def _number(key: str, value: str) -> float:
    if key.endswith("_CPUS"):
        return float(value)
    if key.endswith("_MAXMEMORY"):
        return float(redis_bytes(value))
    if key.endswith("_MEM"):
        return float(compose_bytes(value))
    if key.endswith("_REPLICAS"):
        return float(value)
    return 0.0  # POSTGRES_CONFIG_FILE


def _independent_totals(plan: Any) -> tuple[float, float]:
    """vCPU and GB of the standing services, summed from Compose itself."""
    values, mode = plan.values, plan.mode
    enabled = MODE_PROFILES[mode] | frozenset(plan.profiles)
    compose = load_compose(values, files=(COMPOSE, *MODE_OVERLAYS[mode]))
    cpus, memory = 0.0, 0
    for service in compose["services"].values():
        limits = ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        if set(service.get("profiles") or ()) - enabled or service.get("restart") == "no":
            continue
        if "cpus" not in limits:
            continue
        replicas = int((service.get("deploy") or {}).get("replicas", 1))
        cpus += float(limits["cpus"]) * replicas
        memory += compose_bytes(limits["memory"]) * replicas
    return cpus, memory / _GB


_HOSTS = [(8, 16), (12, 24), (16, 32), (24, 48), (32, 64), (64, 128)]
_MODES = ["cpu", "gpu"]


# ---------------------------------------------------------------- US-1: formula --


def test_the_block_carries_exactly_the_keys_of_large(tmp_path: Path) -> None:
    plan = _plan(32, 64, mode="gpu")
    large = read_size("large").values
    assert list(plan.values) == list(large)
    assert len(plan.values) == 85
    block = tmp_path / "block.env"
    block.write_text(az.render_block(plan), encoding="utf-8")
    parsed = read_size(block)  # what sits between the markers is a valid size file
    assert list(parsed.values) == list(large)
    assert (parsed.host_cpus, parsed.host_memory_gb, parsed.mode) == (32, 64, "gpu")


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(("cpus", "gb"), _HOSTS)
def test_every_host_fits_and_stays_between_small_and_large(cpus: int, gb: int, mode: str) -> None:
    plan = _plan(cpus, gb, mode=mode)
    small, large = read_size("small").values, read_size("large").values
    for key, value in plan.values.items():
        if key == "POSTGRES_CONFIG_FILE":
            continue
        low, high = _number(key, small[key]), _number(key, large[key])
        assert low <= _number(key, value) <= high, (
            f"{key}={value} outside [{small[key]}, {large[key]}]"
        )
    vcpu, memory = _independent_totals(plan)
    assert memory <= gb * 0.9
    assert (vcpu, memory) == pytest.approx((plan.total_cpus, plan.total_memory_gb))


@pytest.mark.parametrize("mode", _MODES)
def test_the_reference_host_reproduces_large_exactly(mode: str) -> None:
    plan = _plan(32, 64, mode=mode)
    large = read_size("large").values
    differing = {k for k, v in plan.values.items() if v != large[k]}
    assert differing == {"POSTGRES_CONFIG_FILE"}
    assert plan.values["POSTGRES_CONFIG_FILE"] == az.CONTAINER_CONF


def _all_numbers(plan: Any) -> dict[str, float]:
    numbers = {k: _number(k, v) for k, v in plan.values.items()}
    numbers["TOTAL_CPUS"] = plan.total_cpus
    numbers["TOTAL_GB"] = plan.total_memory_gb
    return numbers


@pytest.mark.parametrize("mode", _MODES)
def test_no_value_shrinks_as_the_host_grows(mode: str) -> None:
    sweeps = [(32.0, float(gb)) for gb in range(16, 129, 2)]  # memory grows, CPUs fixed
    sweeps += [(float(c), 24.0) for c in range(8, 65)]  # CPUs grow, memory is the limit
    for axis in (sweeps[:57], sweeps[57:]):
        previous: dict[str, float] | None = None
        for cpus, gb in axis:
            current = _all_numbers(_plan(cpus, gb, mode=mode))
            if previous is not None:
                shrunk = {k: (previous[k], v) for k, v in current.items() if v < previous[k]}
                assert not shrunk, f"{cpus} vCPU / {gb} GB: {shrunk}"
            previous = current


@pytest.mark.parametrize(
    ("cpus", "replicas"), [(8, "1"), (16, "2"), (24, "3"), (32, "3"), (64, "3")]
)
def test_replicas_follow_the_cpus_up_to_three(cpus: int, replicas: str) -> None:
    plan = _plan(cpus, 128)
    assert plan.values["APP_REPLICAS"] == plan.values["EMBEDDING_REPLICAS"] == replicas


def test_the_gpu_embedding_row_is_one_replica_whatever_the_key_says() -> None:
    plan = _plan(32, 128, mode="gpu")
    compose = load_compose(plan.values, files=(COMPOSE, GPU_OVERLAY))
    assert compose["services"]["embedding"]["deploy"]["replicas"] == "1"


def test_a_many_core_small_memory_host_still_fits() -> None:
    for mode in _MODES:
        plan = _plan(64, 24, mode=mode)
        assert plan.total_memory_gb <= 24 * 0.9
        assert _independent_totals(plan)[1] <= 24 * 0.9


def test_a_huge_host_gets_large() -> None:
    for mode in _MODES:
        plan = _plan(256, 1024, mode=mode)
        large = read_size("large").values
        assert {k for k, v in plan.values.items() if v != large[k]} == {"POSTGRES_CONFIG_FILE"}


_REFERENCE = [  # design.md 3-h, from the architect's model: (cpus, GB, mode, k*, vCPU, GB)
    (8, 16, "gpu", 3, 11.75, 14.38),
    (8, 16, "cpu", 85, 10.50, 14.38),
    (12, 24, "gpu", 196, 15.15, 21.58),
    (12, 24, "cpu", 249, 13.40, 20.00),
    (16, 32, "gpu", 353, 20.07, 28.79),
    (16, 32, "cpu", 427, 18.82, 28.79),
    (24, 48, "gpu", 685, 28.26, 43.20),
    (24, 48, "cpu", 749, 26.01, 41.90),
    (32, 64, "gpu", 1000, 37.85, 57.59),
    (32, 64, "cpu", 1000, 35.60, 54.28),
    (64, 24, "gpu", 196, 32.85, 21.58),
    (64, 24, "cpu", 249, 28.60, 20.00),
    (8, 15, "cpu", 59, 10.50, 13.48),
]


@pytest.mark.parametrize(("cpus", "gb", "mode", "k", "vcpu", "memory"), _REFERENCE)
def test_the_formula_matches_the_reference_table(
    cpus: int, gb: int, mode: str, k: int, vcpu: float, memory: float
) -> None:
    plan = _plan(cpus, gb, mode=mode)
    assert plan.k == k
    assert (round(plan.total_cpus, 2), round(plan.total_memory_gb, 2)) == (vcpu, memory)


def test_a_host_below_small_is_refused_naming_the_shortfall(tree: Tree, run: Run) -> None:
    before = tree.sha()
    code, _, err = run(
        "--host-cpus", "8", "--host-memory-gb", "15", "--mode", "gpu", "--profiles", "monitoring"
    )
    assert code == 1
    assert all(token in err for token in ("gpu", "14.28", "13.50", "0.78"))
    assert tree.sha() == before
    assert not tree.conf.exists()


def test_cpu_oversubscription_is_reported_not_refused(run: Run) -> None:
    code, out, _ = run("--host-cpus", "4", "--host-memory-gb", "32")
    assert code == 0
    assert "OVERSUBSCRIBED" in out


def test_nothing_runs_the_generator_on_its_own() -> None:
    watched = [*REPO_ROOT.glob("docker-compose*.yml"), REPO_ROOT / "deploy" / "rolling-deploy.sh"]
    watched += REPO_ROOT.glob("**/Dockerfile*")
    watched = [p for p in watched if p.is_file() and ".venv" not in p.parts]
    assert len(watched) > 5
    for path in watched:
        assert "auto_size" not in path.read_text(encoding="utf-8", errors="ignore"), path


@pytest.mark.parametrize(
    "args",
    [
        ["--host-memory-gb", "abc"],
        ["--host-memory-gb", "0"],
        ["--host-memory-gb", "-1"],
        ["--host-cpus", "0"],
        ["--role", "admin"],
    ],
)
def test_bad_arguments_are_usage_errors(tree: Tree, run: Run, args: list[str]) -> None:
    before = tree.sha()
    with pytest.raises(SystemExit) as raised:
        run(*args)
    assert raised.value.code == 2
    assert tree.sha() == before


def test_gpu_mode_comes_from_compose_file(tree: Tree, run: Run) -> None:
    tree.env.write_text(
        tree.text() + "COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml\nCOMPOSE_PROFILES=\n",
        encoding="utf-8",
    )
    code, out, _ = run("--host-cpus", "32", "--host-memory-gb", "64")
    assert code == 0
    assert "mode gpu (COMPOSE_FILE in .env)" in out
    assert "memory_gb=64 mode=gpu" in tree.text()


@pytest.mark.parametrize("sep", [": ", ":", " : "])
def test_detect_reads_the_colon_spelling(sep: str) -> None:
    dotenv = (
        f"COMPOSE_FILE{sep}docker-compose.yml:docker-compose.gpu.yml\nCOMPOSE_PROFILES{sep}backup\n"
    )
    mode, profiles, _ = az.detect(dotenv, {}, None, None)
    assert mode == "gpu"
    assert profiles == frozenset({"backup"}) & az._known_profiles()


def test_detection_order_is_flag_then_environment_then_dotenv() -> None:
    dotenv = "COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml\nCOMPOSE_PROFILES=backup\n"
    assert az.detect(dotenv, {}, None, None)[:2] == ("gpu", frozenset({"backup"}))
    assert az.detect(dotenv, {"COMPOSE_FILE": "docker-compose.yml"}, None, "")[:2] == (
        "cpu",
        frozenset(),
    )
    assert az.detect(dotenv, {}, "cpu", "load")[:2] == ("cpu", frozenset({"load"}))
    sep = {"COMPOSE_FILE": "a.yml;docker-compose.gpu.yml", "COMPOSE_PATH_SEPARATOR": ";"}
    assert az.detect("", sep, None, None)[0] == "gpu"
    assert az.detect("COMPOSE_PROFILES=*\n", {}, None, None)[1] == az._known_profiles()
    assert az.detect("COMPOSE_PROFILES=nonsense\n", {}, None, None)[1] == frozenset()
    assert az.detect("", {}, None, None)[:2] == ("cpu", frozenset())


# ------------------------------------------------------------ US-2: postgres/redis --


_PG = {"MB": 1, "GB": 1024}


def _conf_settings(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([a-z_]+) = ([0-9]+)(MB|GB)", line)
        if match:
            out[match.group(1)] = int(match.group(2)) * _PG[match.group(3)]
    return out


def _conf_problem(plan: Any, text: str) -> str | None:
    settings = _conf_settings(text)
    limit = compose_bytes(plan.values["POSTGRES_MEM"]) // 1024**2
    floor = settings["shared_buffers"] + 4 * settings["maintenance_work_mem"]
    if floor >= limit:
        return f"auto: {floor} MiB asked, POSTGRES_MEM {limit} MiB"
    return None


_CONF_CASES = [(*h, m, "server") for h in _HOSTS for m in _MODES]
_CONF_CASES.append((10, 12.67, "cpu", "dev"))


@pytest.mark.parametrize(("cpus", "gb", "mode", "role"), _CONF_CASES)
def test_auto_conf_has_the_size_shape_and_fits(
    cpus: float, gb: float, mode: str, role: str
) -> None:
    plan = _plan(cpus, gb, role=role, mode=mode)
    lines = [ln for ln in plan.conf.splitlines() if ln and not ln.startswith("#")]
    assert lines[0] == "include '/etc/postgresql/postgresql.conf'"
    settings = _conf_settings(plan.conf)
    assert list(settings) == [
        "shared_buffers",
        "effective_cache_size",
        "maintenance_work_mem",
        "work_mem",
    ]
    assert len(lines) == 5
    assert settings["effective_cache_size"] == math.floor(gb * 1024) * 3 // 8
    assert _conf_problem(plan, plan.conf) is None
    assert settings["maintenance_work_mem"] <= 1024
    assert settings["effective_cache_size"] < gb * 1024
    assert settings["shared_buffers"] >= 256
    inflated = re.sub(r"shared_buffers = \S+", "shared_buffers = 64GB", plan.conf)
    problem = _conf_problem(plan, inflated)
    assert problem is not None and "auto" in problem


def test_the_reference_host_writes_large_conf_values() -> None:
    plan = _plan(32, 64, mode="gpu")
    text = (REPO_ROOT / "deploy" / "postgres" / "sizes" / "large.conf").read_text("utf-8")
    keep = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert [ln for ln in plan.conf.splitlines() if ln and not ln.startswith("#")] == keep


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(("cpus", "gb"), [*_HOSTS, (10, 12.67)])
def test_redis_keeps_its_container_headroom(cpus: float, gb: float, mode: str) -> None:
    for role in ("server", "dev"):
        try:
            plan = _plan(cpus, gb, role=role, mode=mode)
        except az.Refused:
            continue
        v = plan.values
        stream = redis_bytes(v["REDIS_STREAM_MAXMEMORY"])
        assert compose_bytes(v["REDIS_STREAM_MEM"]) >= 1.5 * stream
        cache = redis_bytes(v["REDIS_CACHE_MAXMEMORY"])
        assert compose_bytes(v["REDIS_CACHE_MEM"]) >= cache + 768 * 1024**2


def test_a_run_points_postgres_at_the_written_auto_conf(tree: Tree, run: Run) -> None:
    code, _, _ = run("--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 0
    assert _block_values(tree.text())["POSTGRES_CONFIG_FILE"] == az.CONTAINER_CONF
    assert tree.conf.is_file()
    assert stat.S_IMODE(tree.conf.stat().st_mode) == 0o644
    assert tree.conf.read_text(encoding="utf-8").startswith("# Generated by deploy/auto_size.py")


# ---------------------------------------------------------------- US-3: the .env --


def test_only_the_block_changes_mode_survives_and_nothing_leaks(tmp_path: Path) -> None:
    (tmp_path / "deploy" / "postgres" / "sizes").mkdir(parents=True)
    env = tmp_path / ".env"
    original = _ENV_EXAMPLE.read_text(encoding="utf-8") + f"GUARDED_PASSWORD={_SENTINEL}\n"
    env.write_text(original, encoding="utf-8")
    env.chmod(0o600)
    assert os.access(_SCRIPT, os.X_OK)
    assert _SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")
    done = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            *("--host-cpus", "16", "--host-memory-gb", "32", "--env-file", str(env)),
        ],
        capture_output=True,
        text=True,
        env={k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ},
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    after = env.read_text(encoding="utf-8")
    assert after.count(az.BLOCK_START) == after.count(az.BLOCK_END) == 1
    assert _without_block(after) == original
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert _SENTINEL not in done.stdout + done.stderr
    assert not list(tmp_path.glob(".env.bak*")), "a temp file was left behind"


def test_a_second_run_changes_nothing(tree: Tree, run: Run) -> None:
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    first = tree.sha()
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    assert tree.sha() == first


def test_a_rerun_on_a_bigger_host_replaces_only_the_block(tree: Tree, run: Run) -> None:
    original = tree.text()
    assert run("--host-cpus", "32", "--host-memory-gb", "64")[0] == 0
    small_block = tree.text()
    assert run("--host-cpus", "64", "--host-memory-gb", "128")[0] == 0
    big_block = tree.text()
    assert small_block != big_block
    assert _without_block(small_block) == _without_block(big_block) == original


def test_dry_run_writes_nothing(tree: Tree, run: Run) -> None:
    before = tree.sha()
    code, out, _ = run("--dry-run", "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 0
    assert tree.sha() == before
    assert not tree.conf.exists()
    assert az.BLOCK_START in out and "shared_buffers" in out and "TOTAL" in out


@pytest.mark.parametrize("case", ["pgdata-conf", "qdrant-mem", "template-after-block"])
def test_a_conflicting_key_outside_the_block_is_refused_by_name(
    tree: Tree, run: Run, case: str
) -> None:
    secret = "9876m"
    if case == "pgdata-conf":
        key, value = "POSTGRES_CONFIG_FILE", "/var/lib/postgresql/data/postgresql.conf"
        tree.env.write_text(tree.text().replace(_TEMPLATE_CONF, f"{key}={value}"), "utf-8")
    elif case == "qdrant-mem":
        key, value = "QDRANT_MEM", secret
        tree.env.write_text(tree.text() + f"{key}={value}\n", encoding="utf-8")
    else:
        key, value = "POSTGRES_CONFIG_FILE", "/etc/postgresql/postgresql.conf"
        assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
        tree.env.write_text(tree.text() + f"{key}={value}\n", encoding="utf-8")
    before = tree.sha()
    code, _, err = run("--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3
    assert key in err
    assert value not in err
    assert tree.sha() == before


@pytest.mark.parametrize(
    "line",
    [
        "QDRANT_MEM=6543m",
        "QDRANT_MEM: 6543m",
        "QDRANT_MEM:6543m",
        "QDRANT_MEM : 6543m",
        "export QDRANT_MEM: 6543m",
    ],
)
def test_every_spelling_of_a_conflicting_key_is_refused(tree: Tree, run: Run, line: str) -> None:
    # Compose reads `KEY: value` (also without a space, or after blanks) like `KEY=value`.
    tree.env.write_text(tree.text() + line + "\n", encoding="utf-8")
    before = tree.sha()
    code, _, err = run("--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3
    assert "QDRANT_MEM" in err and "6543m" not in err
    assert tree.sha() == before


def test_the_template_postgres_config_file_is_overridden_by_the_block(tree: Tree, run: Run) -> None:
    assert _TEMPLATE_CONF in tree.text()
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    last = [ln for ln in tree.text().splitlines() if ln.startswith("POSTGRES_CONFIG_FILE=")][-1]
    assert last == f"POSTGRES_CONFIG_FILE={az.CONTAINER_CONF}"


def test_missing_env_is_refused_and_nothing_is_created(tree: Tree, run: Run) -> None:
    tree.env.unlink()
    code, _, err = run("--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3
    assert "cp .env.example .env" in err
    assert not tree.env.exists() and not tree.conf.exists()


def test_a_missing_sizes_directory_is_refused(tree: Tree, run: Run) -> None:
    shutil.rmtree(tree.root / "deploy")
    before = tree.sha()
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 3
    assert tree.sha() == before


@pytest.mark.parametrize("case", ["start-only", "two", "reversed"])
def test_a_malformed_block_is_refused(tree: Tree, run: Run, case: str) -> None:
    pieces = {
        "start-only": f"{az.BLOCK_START}\nX=1\n",
        "two": f"{az.BLOCK_START}\n{az.BLOCK_END}\n{az.BLOCK_START}\n{az.BLOCK_END}\n",
        "reversed": f"{az.BLOCK_END}\n{az.BLOCK_START}\n",
    }
    tree.env.write_text(tree.text() + pieces[case], encoding="utf-8")
    before = tree.sha()
    code, _, err = run("--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3 and "malformed" in err
    assert tree.sha() == before
    assert not tree.conf.exists()


def test_a_file_without_final_newline_gets_the_block_on_its_own_line(tree: Tree, run: Run) -> None:
    tree.env.write_text("A=1\nB=2", encoding="utf-8")
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    assert tree.text().startswith(f"A=1\nB=2\n{az.BLOCK_START}\n")
    assert _without_block(tree.text()) == "A=1\nB=2\n"


def test_crlf_endings_are_kept(tree: Tree, run: Run) -> None:
    tree.env.write_bytes(b"A=1\r\nB=2\r\n")
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    data = tree.env.read_bytes()
    assert data.startswith(b"A=1\r\nB=2\r\n") and b"\r\n" in data
    assert re.search(rb"[^\r]\n", data) is None


def test_a_limit_exported_in_the_shell_is_named_as_a_warning(run: Run) -> None:
    code, _, err = run(
        "--host-cpus", "16", "--host-memory-gb", "32", environ={"QDRANT_MEM": "7777m"}
    )
    assert code == 0
    assert "QDRANT_MEM" in err and "7777m" not in err


def test_a_failed_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("A=1\n", encoding="utf-8")

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(az.os, "replace", boom)
    with pytest.raises(az.EnvRefused, match="Nothing changed"):
        az.write_pair((target, "B=2\n", 0o600), (tmp_path / "other", "C=3\n", 0o600))
    assert target.read_text(encoding="utf-8") == "A=1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def _tmp_leftovers(tree: Tree) -> list[Path]:
    return list(tree.root.rglob(f"{az.TEMP_PREFIX}*"))


def _fail_second_replace(monkeypatch: pytest.MonkeyPatch, *, also_restore: bool = False) -> None:
    real = az.os.replace
    calls: list[int] = []

    def flaky(src: Any, dst: Any) -> None:
        calls.append(1)
        if len(calls) == 2 or (also_restore and len(calls) == 3):
            raise OSError(13, "Permission denied", str(src))
        real(src, dst)

    monkeypatch.setattr(az.os, "replace", flaky)


@pytest.mark.parametrize("had_conf", [False, True])
def test_a_failed_second_replace_restores_the_pair(
    tree: Tree, run: Run, monkeypatch: pytest.MonkeyPatch, had_conf: bool
) -> None:
    if had_conf:
        assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    before_env = tree.sha()
    before_conf = tree.conf.read_bytes() if had_conf else None
    _fail_second_replace(monkeypatch)
    code, out, err = run("--host-cpus", "64", "--host-memory-gb", "128")
    assert code == 3
    assert "Traceback" not in err
    assert az.TEMP_PREFIX not in err and az.TEMP_PREFIX not in out
    assert str(tree.env) in err or ".env" in err
    assert "nothing changed" in err.lower()
    assert tree.sha() == before_env
    if had_conf:
        assert tree.conf.read_bytes() == before_conf
    else:
        assert not tree.conf.exists()
    assert _tmp_leftovers(tree) == []


def test_a_failed_restore_says_the_pair_no_longer_matches(
    tree: Tree, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    _fail_second_replace(monkeypatch, also_restore=True)
    code, _, err = run("--host-cpus", "64", "--host-memory-gb", "128")
    assert code == 3
    assert "no longer match" in err and "do not restart postgres" in err
    assert az.TEMP_PREFIX not in err and "Traceback" not in err
    assert _tmp_leftovers(tree) == []


def test_a_rewritten_file_gets_a_fresh_mtime(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("A=1\n", encoding="utf-8")
    os.utime(target, (1_000_000_000, 1_000_000_000))
    old = target.stat()
    az.write_pair((target, "A=2\n", 0o600), (tmp_path / "other", "B=1\n", 0o600))
    new = target.stat()
    assert new.st_mtime_ns > old.st_mtime_ns
    assert new.st_ino != old.st_ino


def test_an_unrelated_oserror_is_not_blamed_on_env(
    tree: Tree, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_bash(*_a: object, **_k: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "bash")

    monkeypatch.setattr(az.subprocess, "run", no_bash)
    code, _, err = run("--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3
    assert "cannot read" not in err
    assert "bash" in err
    assert "Nothing was written" in err


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)
    return probe.returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="docker compose missing")
def test_compose_resolves_the_block(tree: Tree, run: Run) -> None:
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    env.update({k: v for k, v in os.environ.items() if k.startswith("DOCKER_")})
    done = subprocess.run(
        [
            *("docker", "compose", "--project-directory", str(REPO_ROOT), "-f", str(COMPOSE)),
            *("--env-file", str(tree.env), "--profile", "*", "config", "--format", "json"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-500:]
    services = json.loads(done.stdout)["services"]
    expected = load_compose(_block_values(tree.text()), files=(COMPOSE,))["services"]
    assert expected["app"]["deploy"]["replicas"] == "2"
    for name in ("postgres", "qdrant", "app", "redis-stream"):
        got = services[name]["deploy"]
        want = expected[name]["deploy"]
        assert int(got.get("replicas", 1)) == int(want.get("replicas", 1)), name
        assert float(got["resources"]["limits"]["cpus"]) == pytest.approx(
            float(want["resources"]["limits"]["cpus"])
        )
        assert int(got["resources"]["limits"]["memory"]) == compose_bytes(
            want["resources"]["limits"]["memory"]
        )
    assert f"config_file={az.CONTAINER_CONF}" in services["postgres"]["command"]


# ---------------------------------------------------- US-4: resource-budget.sh --


def _budget(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_BUDGET), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "COMPOSE_FILE": str(COMPOSE)},
        check=False,
    )


@pytest.mark.parametrize(("cpus", "gb", "mode"), [(16, 32, "gpu"), (24, 48, "cpu"), (8, 16, "gpu")])
def test_the_budget_script_agrees_with_the_generator(
    tree: Tree, run: Run, cpus: int, gb: int, mode: str
) -> None:
    tree.env.write_text(tree.text() + f"GUARDED_PASSWORD={_SENTINEL}\n", encoding="utf-8")
    args = ("--host-cpus", str(cpus), "--host-memory-gb", str(gb), "--mode", mode)
    assert run(*args, "--profiles", "monitoring")[0] == 0
    plan = _plan(cpus, gb, mode=mode)
    done = _budget("--block", str(tree.env), "--host-cpus", str(cpus), "--host-memory-gb", str(gb))
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{plan.total_cpus:6.2f} vCPU  {plan.total_memory_gb:7.2f} GB" in done.stdout
    assert f"role server, mode {mode}" in done.stdout
    assert _SENTINEL not in done.stdout + done.stderr


def test_the_budget_script_refuses_an_edited_block(tree: Tree, run: Run) -> None:
    assert run("--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    tree.env.write_text(
        re.sub(r"^QDRANT_MEM=.*$", "QDRANT_MEM=30g", tree.text(), flags=re.M), encoding="utf-8"
    )
    done = _budget("--block", str(tree.env), "--host-cpus", "16", "--host-memory-gb", "32")
    assert done.returncode == 1
    assert "OVER" in done.stdout and "block (role server)" in done.stdout


def test_the_budget_script_needs_one_well_formed_block(tree: Tree, run: Run) -> None:
    args = ("--host-cpus", "16", "--host-memory-gb", "32")
    assert _budget("--block", str(tree.env), *args).returncode == 2  # no block yet
    assert run(*args)[0] == 0
    good = tree.text()
    no_role = re.sub(r"^# role: .*\n", "", good, flags=re.M)
    no_host = re.sub(r"^# host: .*\n", "", good, flags=re.M)
    two = good + good[good.index(az.BLOCK_START) :]
    for broken in (no_role, no_host, two):
        tree.env.write_text(broken, encoding="utf-8")
        assert _budget("--block", str(tree.env), *args).returncode == 2
    assert _budget("--block").returncode == 2
    assert _budget("--block", str(tree.env), "--size", "large").returncode == 2


def test_the_budget_script_warns_on_a_dev_block(tree: Tree, run: Run) -> None:
    tree.env.write_text(tree.text() + "COMPOSE_PROFILES=wsl-ollama\n", encoding="utf-8")
    assert run("--role", "dev", "--host-cpus", "10", "--host-memory-gb", "12.67")[0] == 0
    done = _budget("--block", str(tree.env), "--host-cpus", "10", "--host-memory-gb", "12.67")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "role dev" in done.stdout and "Warning only" in done.stdout


def test_the_budget_script_names_a_missing_auto_conf(tree: Tree, run: Run) -> None:
    args = ("--host-cpus", "16", "--host-memory-gb", "32")
    assert run(*args)[0] == 0
    tree.conf.unlink()
    done = _budget("--block", str(tree.env), *args)
    assert done.returncode == 1
    assert "Postgres would not start; re-run deploy/auto_size.py" in done.stdout


def test_the_budget_block_follows_the_blocks_profiles(tree: Tree, run: Run) -> None:
    """AC-2.3: `--block` says what Compose will run -- the block's `# profiles:` line."""
    args = ("--host-cpus", "32", "--host-memory-gb", "64")
    totals: dict[str, tuple[float, float]] = {}
    for profiles, line in (
        ("monitoring,wsl-ollama", "monitoring: counted (in the block's profiles)"),
        ("wsl-ollama", "monitoring: NOT counted (not in the block's profiles)"),
    ):
        shutil.copy(_ENV_EXAMPLE, tree.env)
        assert run(*args, "--profiles", profiles)[0] == 0
        assert f"# profiles: {profiles}\n" in tree.text()
        done = _budget("--block", str(tree.env), *args)
        assert done.returncode == 0, done.stdout + done.stderr
        assert line in done.stdout, done.stdout
        match = re.search(r"TOTAL\s+([0-9.]+) vCPU\s+([0-9.]+) GB", done.stdout)
        assert match, done.stdout
        totals[profiles] = (float(match.group(1)), float(match.group(2)))
    on, off = totals["monitoring,wsl-ollama"], totals["wsl-ollama"]
    assert on[0] - off[0] == pytest.approx(3.60, abs=0.011)
    assert on[1] - off[1] == pytest.approx(4.66, abs=0.011)  # 4768m, printed to 2 places


def test_monitoring_is_a_known_profile_and_counts_only_when_named() -> None:
    """AC-2.4: auto_size.py itself is unchanged -- it reads the profiles from Compose and
    counts what COMPOSE_PROFILES names, which is what Compose runs."""
    assert "monitoring" in az._known_profiles()
    without, with_ = _plan(32, 64, mode="cpu", profiles=""), _plan(32, 64, mode="cpu")
    assert (round(without.total_cpus, 2), round(without.total_memory_gb, 2)) == (32.00, 49.62)
    assert (round(with_.total_cpus, 2), round(with_.total_memory_gb, 2)) == (35.60, 54.28)


def test_without_a_block_the_budget_is_the_reference_ledger() -> None:
    done = _budget("--host-cpus", "32", "--host-memory-gb", "64")
    assert "35.60 vCPU" in done.stdout and "57.28 GB" in done.stdout


# ------------------------------------------------------- US-5: the dev machine --

_DEV_FLOORS_MIB = {  # design.md 3-f: peak x 1.25 up to 256 MiB, never past large
    "QDRANT_MEM": 8192,
    "POSTGRES_MEM": 3840,
    "APP_MEM": 1536,
    "LOKI_MEM": 1024,
    "MINIO_MEM": 1024,
    "GRAFANA_MEM": 512,
    "PROMETHEUS_MEM": 512,
}


def _dev_plan() -> Any:
    return _plan(10, 12.67, role="dev", mode="cpu", profiles="wsl-ollama,monitoring")


def test_dev_role_on_the_development_host_writes_a_dev_block(tree: Tree, run: Run) -> None:
    tree.env.write_text(tree.text() + "COMPOSE_PROFILES=wsl-ollama\n", encoding="utf-8")
    code, out, _ = run("--role", "dev", "--host-cpus", "10", "--host-memory-gb", "12.67")
    assert code == 0
    assert "# role: dev\n" in tree.text()
    assert "# profiles: wsl-ollama\n" in tree.text()
    assert "role dev (--role)" in out


def test_server_role_refuses_the_development_host(tree: Tree, run: Run) -> None:
    tree.env.write_text(tree.text() + "COMPOSE_PROFILES=wsl-ollama,monitoring\n", encoding="utf-8")
    before = tree.sha()
    code, _, err = run("--role", "server", "--host-cpus", "10", "--host-memory-gb", "12.67")
    assert code == 1
    assert all(token in err for token in ("11.72", "11.40", "0.32"))
    assert tree.sha() == before


def test_dev_floors_hold_and_never_pass_large() -> None:
    plan = _dev_plan()
    large = read_size("large").values
    for key, floor in _DEV_FLOORS_MIB.items():
        assert az.dev_floor_mib(key) == floor, key
        assert compose_bytes(plan.values[key]) >= floor * 1024**2, key
    for key, value in plan.values.items():
        assert _number(key, value) <= _number(key, large[key]), key
    assert az.dev_floor_mib("VAULT_MEM") is None
    assert "monitoring-host-postgres/design.md" in _SCRIPT.read_text(encoding="utf-8")
    assert set(az.DEV_PEAKS_MIB) <= set(large)


def test_dev_overcommit_is_a_warning_with_the_ratio(tree: Tree, run: Run) -> None:
    code, out, _ = run(
        "--role",
        "dev",
        "--host-cpus",
        "10",
        "--host-memory-gb",
        "12.67",
        "--profiles",
        "wsl-ollama,monitoring",
    )
    assert code == 0
    assert all(token in out for token in ("23.22", "12.67", "1.83x", "Warning only"))


def test_dev_host_runs_one_replica_of_everything() -> None:
    values = _dev_plan().values
    keys = ("APP", "EMBEDDING", "WORKER_KNOWLEDGE", "OUTBOX_RELAY")
    assert [values[f"{k}_REPLICAS"] for k in keys] == ["1"] * 4


def test_the_role_is_remembered_from_the_block(tree: Tree, run: Run) -> None:
    args = ("--host-cpus", "32", "--host-memory-gb", "64")
    assert run(*args)[0] == 0
    assert "# role: server\n" in tree.text()
    assert run("--role", "dev", *args)[0] == 0
    assert run(*args)[0] == 0  # no --role: the block says dev
    assert "# role: dev\n" in tree.text()
    assert az.block_role(tree.text()) == "dev"
    assert az.block_role("A=1\n") is None


def test_dev_role_follows_the_gpu_overlay() -> None:
    plan = _plan(12, 24, role="dev", mode="gpu")
    compose = load_compose(plan.values, files=(COMPOSE, GPU_OVERLAY))
    assert compose["services"]["embedding"]["deploy"]["replicas"] == "1"
    assert plan.mode == "gpu"
    assert plan.k == 0  # the floors alone pass 90% of 24 GB: over-commit, reported not refused


def test_dev_role_on_a_large_host_is_large_without_warning(tree: Tree, run: Run) -> None:
    code, out, _ = run("--role", "dev", "--host-cpus", "32", "--host-memory-gb", "64")
    assert code == 0
    assert "Warning only" not in out
    large = read_size("large").values
    values = _block_values(tree.text())
    assert {k for k, v in values.items() if v != large[k]} == {"POSTGRES_CONFIG_FILE"}


# ------------------------------------------------------------ US-6: documentation --


def _section(text: str, start: str, stop: str) -> str:
    return text[text.index(start) : text.index(stop)]


def test_the_runbook_and_env_example_document_the_block() -> None:
    runbook = _RUNBOOK.read_text(encoding="utf-8")
    section = _section(runbook, "### 2\u2011ز", "### 2\u2011ح")
    for needle in (
        "deploy/auto_size.py",
        "--role dev",
        "--role server",
        "--dry-run",
        "resource-budget.sh --block",
        "aizzak auto-size",
    ):
        assert needle in section, needle
    for line in section.splitlines():
        if "--env-file deploy/sizes" in line:
            assert "--env-file .env" in line.split("--env-file deploy/sizes")[0], line
    example = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert az.BLOCK_START not in example.splitlines()
    assert az.BLOCK_END not in example.splitlines()
    assert "deploy/auto_size.py" in example
    live = {ln.partition("=")[0] for ln in example.splitlines() if re.match(r"^[A-Z_0-9]+=", ln)}
    assert live & set(read_size("large").values) == {"POSTGRES_CONFIG_FILE"}
