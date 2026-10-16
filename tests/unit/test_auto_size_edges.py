"""QA edge cases for deploy/auto_size.py (compose-auto-size): boundaries, leaks, conflict forms.

Complements tests/unit/test_auto_size.py. Everything runs on a copy of `.env.example` under
`tmp_path`; the repository's own `.env` and `auto.conf` are never opened.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "deploy" / "auto_size.py"
_SENTINEL = "SENTINEL_SECRET_VALUE_8842"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("auto_size_edges", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["auto_size_edges"] = module
    spec.loader.exec_module(module)
    return module


az = _load()


@pytest.fixture
def env(tmp_path: Path) -> Path:
    shutil.copy(REPO_ROOT / ".env.example", tmp_path / ".env")
    with open(tmp_path / ".env", "a", encoding="utf-8") as handle:
        handle.write(f"GUARDED_PASSWORD={_SENTINEL}\n")
    (tmp_path / "deploy" / "postgres" / "sizes").mkdir(parents=True)
    return tmp_path / ".env"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(env: Path, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = az.main([*args, "--env-file", str(env)], environ={})
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# AC-1.6 boundary: GPU mode needs 14.22 GB; the allowance is 90% of the host.
def test_the_refusal_threshold_is_exact_in_gpu_mode(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _sha(env)
    code, _, err = _run(
        env, capsys, "--host-cpus", "8", "--host-memory-gb", "15.79", "--mode", "gpu"
    )
    assert code == 1 and "gpu" in err
    assert _sha(env) == before
    code, _, _ = _run(env, capsys, "--host-cpus", "8", "--host-memory-gb", "15.8", "--mode", "gpu")
    assert code == 0


# AC-5.6: without a block and without --role the role is server, so the dev host is refused.
def test_no_block_and_no_role_means_server(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _sha(env)
    code, _, err = _run(env, capsys, "--host-cpus", "10", "--host-memory-gb", "12.67")
    assert code == 1 and "role server" in err and "11.40" in err
    assert _sha(env) == before


# AC-3.4: any way of writing a key outside the block collides, and the value never leaks.
@pytest.mark.parametrize(
    "line",
    [
        "export QDRANT_MEM=6543m",
        " QDRANT_MEM = 6543m",
        'QDRANT_MEM="6543m"',
        "QDRANT_MEM=6543m # note",
        "APP_REPLICAS=6543",
    ],
)
def test_every_spelling_of_a_conflicting_key_is_refused(
    env: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    with open(env, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    before = _sha(env)
    code, out, err = _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3
    assert line.partition("=")[0].split()[-1] in err
    assert "6543" not in out + err
    assert _sha(env) == before
    assert not (env.parent / "deploy" / "postgres" / "sizes" / "auto.conf").exists()


def test_a_commented_out_key_is_not_a_conflict(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with open(env, "a", encoding="utf-8") as handle:
        handle.write("#QDRANT_MEM=1g\n")
    assert _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")[0] == 0


# AC-3.1 / AC-3.7: the guarded value is absent from every output path, not only the happy one.
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--host-cpus", "16", "--host-memory-gb", "32", "--dry-run"], 0),
        (["--host-cpus", "16", "--host-memory-gb", "5"], 1),
        (["--host-cpus", "16", "--host-memory-gb", "32", "--mode", "gpu"], 0),
    ],
    ids=["dry-run", "refused", "gpu"],
)
def test_a_guarded_value_never_reaches_any_output(
    env: Path, capsys: pytest.CaptureFixture[str], args: list[str], expected: int
) -> None:
    code, out, err = _run(env, capsys, *args)
    assert code == expected
    assert _SENTINEL not in out + err


def test_a_malformed_block_error_does_not_echo_the_file(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with open(env, "a", encoding="utf-8") as handle:
        handle.write(f"{az.BLOCK_START}\nTOKEN={_SENTINEL}\n")
    code, out, err = _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3
    assert _SENTINEL not in out + err


# FR-11: .env is only replaced after auto.conf is safely on disk, never half-written.
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_sizes_directory_leaves_env_untouched(env: Path) -> None:
    sizes = env.parent / "deploy" / "postgres" / "sizes"
    before = _sha(env)
    sizes.chmod(0o500)
    try:
        done = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "--env-file",
                str(env),
                "--host-cpus",
                "16",
                "--host-memory-gb",
                "32",
            ],
            capture_output=True,
            text=True,
            env={k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ},
            check=False,
        )
    finally:
        sizes.chmod(0o700)
    assert done.returncode != 0
    assert _sha(env) == before
    assert _SENTINEL not in done.stdout + done.stderr
    assert not list(sizes.iterdir()), "a partial auto.conf or temp file was left behind"


def test_a_symlinked_env_stays_a_symlink(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    real = env.parent / "real.env"
    env.rename(real)
    env.symlink_to(real.name)
    assert _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    assert env.is_symlink()
    assert real.read_text(encoding="utf-8").count(az.BLOCK_START) == 1


def test_an_empty_env_gets_just_the_block(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env.write_text("", encoding="utf-8")
    assert _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")[0] == 0
    text = env.read_text(encoding="utf-8")
    assert text.startswith(az.BLOCK_START + "\n")
    assert text.count(az.BLOCK_START) == text.count(az.BLOCK_END) == 1


# AC-4.3: the named sizes are untouched by this branch (skipped where `master` is not available).
def test_the_named_size_files_match_master() -> None:
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "master"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("no local master ref")
    done = subprocess.run(
        ["git", "diff", "--stat", "master", "--", "deploy/sizes", "deploy/postgres/sizes"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0
    changed = [ln for ln in done.stdout.splitlines() if "|" in ln]
    assert changed == [], changed
