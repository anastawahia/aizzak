"""The `[config]` acceptance criteria of install-bundle: what Docker Compose says about an unpacked
bundle (AC-1.5, AC-5.4) and the principle behind the boot order (design.md 1).

Only `docker compose config` runs: no container, no daemon, no network. Every run happens in a
copy of the bundle in a temporary directory with an `.env` built here, an empty DOCKER_CONFIG and
none of the caller's COMPOSE_* variables. The tests skip, with the reason, when Docker is absent
(NFR-11). `AIZZAK_BUNDLE=<path to aizzak-<V>.tar.gz>` runs the same checks on a real bundle
(AC-1.7, `pytest -k bundle`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.unit.support_install import (
    CARRIERS,
    SECRET_ID,
    Harness,
    bundle_tree,
    env_tool,
    run_function,
)

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="Docker is not installed (NFR-11)"
)

_HOSTS = {"8/16": ("8", "16"), "16/32": ("16", "32"), "32/64": ("32", "64"), "8/12": ("8", "12")}
# (mode, monitoring) -> services, measured on 2026-10-19 (requirements.md 5.7)
_SERVICES = {("gpu", "on"): 36, ("gpu", "off"): 24, ("cpu", "on"): 35, ("cpu", "off"): 24}


def _path() -> str:
    """A short PATH: Docker's CLI is slower with the Windows directories WSL appends."""
    docker = shutil.which("docker")
    here = str(Path(docker).parent) if docker else "/usr/bin"
    return f"{here}:/usr/local/bin:/usr/bin:/bin"


def _compose(
    root: Path, *args: str, check: bool = False, timeout: int = 90
) -> subprocess.CompletedProcess[str]:
    empty = root.parent / "docker-config"
    empty.mkdir(exist_ok=True)
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        env={
            "PATH": _path(),
            "HOME": str(root.parent),
            "DOCKER_CONFIG": str(empty),
        },
    )


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An unpacked bundle: the real one if AIZZAK_BUNDLE names it, else one built from the tree."""
    where = tmp_path_factory.mktemp("config-base")
    real = os.environ.get("AIZZAK_BUNDLE")
    if real:
        with tarfile.open(real, "r:gz") as tar:
            tar.extractall(where, filter="data")
        (root,) = [p for p in where.iterdir() if p.is_dir()]
        return root
    return bundle_tree(where)


def _copy(base: Path, into: Path) -> Path:
    target = into / base.name
    shutil.copytree(base, target, symlinks=True)
    return target


def _write_env(root: Path, mode_name: str, monitoring: str, size: str) -> None:
    """The .env install.sh would have at the end of step 3 (the real env_tool, no fakes)."""
    done = env_tool(
        _shim(root),
        "compose", str(root / ".env"), str(root / ".env.example"),
        "--release", _release(root), "--commit", "a" * 40, "--mode", mode_name,
        "--monitoring", monitoring, "--size", size, "--domain", "example.com",
        "--firebase", "demo-proj", *_images(root),
    )  # fmt: skip
    assert done.returncode == 0, done.stderr


def _shim(root: Path) -> Harness:
    """Just enough of a Harness for run_function and env_tool: the tree and an environment."""
    env = {"PATH": _path(), "HOME": str(root.parent)}
    return Harness(root=root, fake=root.parent, sysctl=root.parent, sysctl_d=root.parent, env=env)


def _release(root: Path) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").splitlines()[0]


def _images(root: Path) -> list[str]:
    lines = (root / "VERSION").read_text(encoding="utf-8").splitlines()[2:]
    return [x for flag, line in zip(("--image-app", "--image-embedding", "--image-cuda"), lines,
                                    strict=True) for x in (flag, line)]  # fmt: skip


def _auto(
    root: Path, mode_name: str, monitoring: str, host: tuple[str, str]
) -> subprocess.CompletedProcess[str]:
    profiles = "local-llm,monitoring" if monitoring == "on" else "local-llm"
    return subprocess.run(
        ["python3", "-I", "deploy/auto_size.py", "--role", "server", "--host-cpus", host[0],
         "--host-memory-gb", host[1], "--mode", mode_name, "--profiles", profiles,
         "--env-file", str(root / ".env")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": _path(),
            "COMPOSE_FILE": "docker-compose.yml",
            "GPU_COMPOSE_FILE": "docker-compose.gpu.yml",
            "SIZES_DIR": "deploy/sizes",
        },
        check=False,
    )  # fmt: skip


def _run_all(jobs: list[Callable[[], None]]) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(job) for job in jobs]:
            future.result()


# ── AC-1.5 ───────────────────────────────────────────────────────────────────


def test_every_combination_of_mode_monitoring_and_size_is_a_valid_bundle_project(
    base: Path, tmp_path: Path
) -> None:
    """(GPU, CPU) x (monitoring, none) x (no size, small, medium, large, auto) = 20 projects."""
    combos = [
        (m, mon, size)
        for m in ("gpu", "cpu")
        for mon in ("on", "off")
        for size in ("none", "small", "medium", "large", "auto")
    ]
    assert len(combos) == 20

    def check(index: int, mode_name: str, monitoring: str, size: str) -> None:
        root = _copy(base, tmp_path / f"c{index}")
        _write_env(root, mode_name, monitoring, "auto" if size == "none" else size)
        if size in ("small", "medium", "large"):
            done = env_tool(
                _shim(root),
                "size-block", str(root / ".env"), str(root / "deploy" / "sizes" / f"{size}.env"),
                size,
            )  # fmt: skip
            assert done.returncode == 0, done.stderr
        elif size == "auto":
            done = _auto(root, mode_name, monitoring, _HOSTS["16/32"])
            assert done.returncode == 0, done.stdout + done.stderr
        quiet = _compose(root, "config", "-q")
        assert quiet.returncode == 0, (mode_name, monitoring, size, quiet.stderr)
        services = _compose(root, "config", "--services").stdout.split()
        assert len(services) == _SERVICES[(mode_name, monitoring)], (mode_name, monitoring, size)
        images = _compose(root, "config", "--images").stdout.split()
        assert images and not [i for i in images if i.endswith((":dev", ":latest"))], images
        released = {i for i in images if i.startswith("anastawahia/aizzak:")}
        assert len(released) == 2, released

    jobs = []
    for i, (m, mon, size) in enumerate(combos):
        (tmp_path / f"c{i}").mkdir()
        jobs.append(lambda i=i, m=m, mon=mon, size=size: check(i, m, mon, size))
    _run_all(jobs)


# ── AC-5.4 ───────────────────────────────────────────────────────────────────


def test_the_real_generator_in_the_bundle_fits_three_hosts_and_refuses_the_small_one(
    base: Path, tmp_path: Path
) -> None:
    combos = [(host, m) for host in _HOSTS for m in ("gpu", "cpu")]

    def check(index: int, host_name: str, mode_name: str) -> None:
        root = _copy(base, tmp_path / f"h{index}")
        _write_env(root, mode_name, "on", "auto")
        before = (root / ".env").read_bytes()
        done = _auto(root, mode_name, "on", _HOSTS[host_name])
        conf = root / "deploy" / "postgres" / "sizes" / "auto.conf"
        if host_name == "8/12":
            assert done.returncode == 1, (host_name, mode_name, done.stdout[-400:], done.stderr)
            assert (root / ".env").read_bytes() == before and not conf.exists()
            return
        assert done.returncode == 0, (host_name, mode_name, done.stdout[-400:], done.stderr)
        assert conf.is_file() and "# >>> aizzak auto-size >>>" in (root / ".env").read_text()
        budget = subprocess.run(
            ["bash", "deploy/resource-budget.sh", "--block", ".env", "--host-cpus",
             _HOSTS[host_name][0], "--host-memory-gb", _HOSTS[host_name][1]],
            cwd=root,
            capture_output=True,
            text=True,
            env={"PATH": _path(),
                 "COMPOSE_FILE": "docker-compose.yml", "GPU_COMPOSE_FILE": "docker-compose.gpu.yml",
                 "SIZES_DIR": "deploy/sizes"},
            check=False,
        )  # fmt: skip
        assert budget.returncode == 0, budget.stdout[-400:] + budget.stderr
        assert _compose(root, "config", "-q").returncode == 0

    jobs = []
    for i, (host, m) in enumerate(combos):
        (tmp_path / f"h{i}").mkdir()
        jobs.append(lambda i=i, host=host, m=m: check(i, host, m))
    _run_all(jobs)


# ── the principle behind the boot order (design.md 1) ────────────────────────


def test_writing_the_key_changes_the_hash_of_the_carriers_and_of_nothing_else(
    base: Path, tmp_path: Path
) -> None:
    """vault and vault-bootstrap are created before the key exists; if their hash moved when it
    is written, Compose would recreate them. It does not, and exactly the carriers move."""
    root = _copy(base, tmp_path / "t")
    _write_env(root, "gpu", "on", "auto")

    def hashes() -> dict[str, str]:
        out = _compose(root, "config", "--hash", "*", check=True).stdout.split("\n")
        return dict(line.split(" ", 1) for line in out if line.strip())

    before = hashes()
    done = run_function(
        _shim(root),
        'env_tool set-secret-id "$1" --ttl-hours 720',
        str(root / ".env"),
        env={"AIZZAK_INSTALL_SECRET_ID": SECRET_ID},
    )
    assert done.returncode == 0, done.stderr
    after = hashes()
    moved = {name for name in before if before[name] != after[name]}
    assert moved == set(CARRIERS) - {"backup"}, sorted(moved ^ (set(CARRIERS) - {"backup"}))
    assert "vault" not in moved and "vault-bootstrap" not in moved
