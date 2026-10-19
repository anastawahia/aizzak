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
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.test_auto_size import _REAL_FILES, _budget, _stat_of

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


@pytest.fixture(autouse=True)
def _never_touch_the_real_files() -> Iterator[None]:
    before = [_stat_of(p) for p in _REAL_FILES]
    yield
    assert [_stat_of(p) for p in _REAL_FILES] == before, "a test touched the real .env/auto.conf"


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


# AC-1.6 boundary: GPU mode needs 14.28 GB; the allowance is 90% of the host.
def test_the_refusal_threshold_is_exact_in_gpu_mode(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _sha(env)
    # decision 7: the server counts monitoring, which COMPOSE_PROFILES names (--profiles here)
    gpu = ("--mode", "gpu", "--profiles", "monitoring")
    code, _, err = _run(env, capsys, "--host-cpus", "8", "--host-memory-gb", "15.86", *gpu)
    assert code == 1 and "gpu" in err
    assert _sha(env) == before
    code, _, _ = _run(env, capsys, "--host-cpus", "8", "--host-memory-gb", "15.87", *gpu)
    assert code == 0


# AC-5.6: without a block and without --role the role is server, so the dev host is refused.
def test_no_block_and_no_role_means_server(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _sha(env)
    code, _, err = _run(
        env, capsys, "--host-cpus", "10", "--host-memory-gb", "12.67", "--profiles", "monitoring"
    )
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


def _conf(env: Path) -> Path:
    return env.parent / "deploy" / "postgres" / "sizes" / "auto.conf"


def _first_run(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(env, capsys, "--host-cpus", "8", "--host-memory-gb", "16")[0] == 0


# review #1: Compose drops a trailing ' # comment' from an unquoted value and ignores
# whatever follows a closing quote; a tab before '#' or a leading '#' is part of the value.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc # note", "abc"),
        ("abc   #note", "abc"),
        ("  pad  ", "pad"),
        ("x#y", "x#y"),
        ("# x", "# x"),
        (" #x", "#x"),
        ("abc\t# tab", "abc\t# tab"),
        ('"q # v" # c', "q # v"),
        ("'s t' junk", "s t"),
        ('"open', '"open'),
    ],
)
def test_values_are_read_the_way_compose_reads_them(raw: str, expected: str) -> None:
    assert az._unquote(raw) == expected


def test_a_trailing_comment_does_not_hide_the_gpu_mode(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with open(env, "a", encoding="utf-8") as handle:
        handle.write("COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml  # GPU host\n")
    code, out, _ = _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 0 and "mode gpu (COMPOSE_FILE in .env)" in out


def test_a_trailing_comment_does_not_hide_the_profiles(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with open(env, "a", encoding="utf-8") as handle:
        handle.write("COMPOSE_PROFILES=wsl-ollama # bridge\n")
    mode, profiles, _ = az.detect(env.read_text(encoding="utf-8"), {}, None, None)
    assert mode == "cpu" and profiles == frozenset({"wsl-ollama"})


def test_a_commented_template_value_is_not_a_conflict(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = env.read_text(encoding="utf-8")
    assert "POSTGRES_CONFIG_FILE=/etc/postgresql/postgresql.conf" in text
    env.write_text(
        text.replace(
            "POSTGRES_CONFIG_FILE=/etc/postgresql/postgresql.conf",
            "POSTGRES_CONFIG_FILE=/etc/postgresql/postgresql.conf # x",
        ),
        encoding="utf-8",
    )
    code, out, err = _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 0
    assert _SENTINEL not in out + err and "# x" not in out + err


# review #2 / QA BUG-1: a failure while preparing .env must not leave a new auto.conf behind
# for the old block to read.
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_failing_env_temp_file_leaves_the_old_auto_conf_alone(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _first_run(env, capsys)
    conf_before, env_before = _sha(_conf(env)), _sha(env)
    env.parent.chmod(0o555)  # the .env temp file cannot be created; the sizes dir still can
    try:
        code, _, err = _run(env, capsys, "--host-cpus", "64", "--host-memory-gb", "128")
    finally:
        env.parent.chmod(0o700)
    assert code == 3 and err.startswith("auto-size:") and "Traceback" not in err
    assert _sha(_conf(env)) == conf_before and _sha(env) == env_before
    assert sorted(p.name for p in _conf(env).parent.iterdir()) == ["auto.conf"]


def test_a_failure_staging_the_second_file_removes_the_first_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_text("1\n", encoding="utf-8")
    real = az.stage
    calls: list[Path] = []

    def flaky(path: Path, text: str, mode: int) -> Path:
        if calls:
            raise OSError(13, "Permission denied", str(path))
        calls.append(path)
        return real(path, text, mode)

    monkeypatch.setattr(az, "stage", flaky)
    with pytest.raises(az.EnvRefused, match=r"cannot write .*two .*Permission denied"):
        az.write_pair((one, "A\n", 0o600), (two, "B\n", 0o600))
    assert one.read_text(encoding="utf-8") == "1\n" and not two.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["one"]


# SR-3 / QA BUG-2 / review #5: bytes that are not UTF-8 are refused by line number only.
def test_a_non_utf8_env_is_refused_by_line_number(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = env.read_bytes()
    env.write_bytes(good + b"A=ok\nB=\xff\xfe" + _SENTINEL.encode() + b"\n")
    line = good.count(b"\n") + 2
    before = _sha(env)
    code, out, err = _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3 and f"line {line} " in err and "Traceback" not in err
    for leaked in ("xff", "\\x", "0x", _SENTINEL):
        assert leaked not in out + err
    assert _sha(env) == before and not _conf(env).exists()


def test_the_budget_script_refuses_a_non_utf8_block_with_exit_2(tmp_path: Path) -> None:
    block = tmp_path / ".env"
    block.write_bytes(b"# ok\n\xff\n")
    done = _budget("--block", str(block))
    assert done.returncode == 2 and "line 2 is not valid UTF-8" in done.stderr
    assert "Traceback" not in done.stderr


# QA BUG-3 / review #6 / SR-10: a damaged '# role:' line is refused, as resource-budget.sh does.
@pytest.mark.parametrize("bad", ["# role: admin", "# role:", "# role: Server"])
def test_a_damaged_role_line_is_refused_by_line_number(
    env: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    _first_run(env, capsys)
    lines = env.read_text(encoding="utf-8").splitlines(keepends=True)
    index = next(i for i, ln in enumerate(lines) if ln.startswith("# role:"))
    lines[index] = bad + "\n"
    env.write_text("".join(lines), encoding="utf-8")
    before = _sha(env)
    code, _, err = _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 3 and f"line {index + 1}" in err
    assert _sha(env) == before


# SR-1 / review #4: the Compose project is where the link is, not where it points.
def test_a_symlink_to_a_file_outside_the_project_uses_the_project_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, shared = tmp_path / "project", tmp_path / "shared"
    (project / "deploy" / "postgres" / "sizes").mkdir(parents=True)
    shared.mkdir()
    (shared / "aizzak.env").write_bytes((REPO_ROOT / ".env.example").read_bytes())
    link = project / ".env"
    link.symlink_to(shared / "aizzak.env")
    code, _, err = _run(link, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 0, err
    assert link.is_symlink()
    assert _conf(link).is_file() and not (shared / "deploy").exists()
    assert (shared / "aizzak.env").read_text(encoding="utf-8").count(az.BLOCK_START) == 1
    block = _budget("--block", str(link), "--host-cpus", "16", "--host-memory-gb", "32")
    assert block.returncode == 0 and "NO CONF" not in block.stdout, block.stdout[-300:]


# SR-2: the group of the old .env survives the swap; the mode too.
def test_the_replaced_env_keeps_its_group_and_mode(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    groups = [g for g in os.getgroups() if g != env.stat().st_gid]
    if not groups:
        pytest.skip("the user belongs to a single group")
    os.chown(env, -1, groups[0])
    env.chmod(0o640)
    _first_run(env, capsys)
    info = env.stat()
    assert info.st_gid == groups[0] and info.st_mode & 0o777 == 0o640


def test_the_temp_files_use_one_fixed_ignored_name(tmp_path: Path) -> None:
    target = tmp_path / "staging.env"
    target.write_text("A=1\n", encoding="utf-8")
    tmp = az.stage(target, "B=2\n", 0o600)
    try:
        assert tmp.name.startswith(az.TEMP_PREFIX) and "bak" not in tmp.name
        assert "staging" not in tmp.name
    finally:
        tmp.unlink()
    for ignore in (".gitignore", ".dockerignore"):
        lines = (REPO_ROOT / ignore).read_text(encoding="utf-8").splitlines()
        assert any(line.endswith(az.TEMP_PREFIX + "*") for line in lines), ignore
    docker = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "deploy/postgres/sizes/auto.conf*" in docker


# SR-7: bounds and a strict match.
@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--host-cpus", "0.5"),
        ("--host-cpus", "5000"),
        ("--host-memory-gb", "0.5"),
        ("--host-memory-gb", "100000"),
        ("--host-memory-gb", "32\n"),
    ],
)
def test_host_overrides_outside_sane_bounds_are_usage_errors(
    env: Path, capsys: pytest.CaptureFixture[str], flag: str, value: str
) -> None:
    other = ("--host-memory-gb", "32") if flag == "--host-cpus" else ("--host-cpus", "16")
    with pytest.raises(SystemExit) as stop:
        _run(env, capsys, flag, value, *other)
    assert stop.value.code == 2


@pytest.mark.parametrize(("cpus", "gb"), [("1", "1"), ("4096", "4096")])
def test_the_bounds_themselves_are_accepted(cpus: str, gb: str) -> None:
    host = az.read_host(cpus, gb)
    assert (host.cpus, host.memory_gb) == (cpus, gb)


# review #7 / #8
def test_the_cpu_ladder_comes_from_the_size_file_headers() -> None:
    assert az._cpu_ladder() == (800, 2400)


def test_the_summary_names_the_real_env_not_a_deleted_temp_dir(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(env, capsys, "--host-cpus", "16", "--host-memory-gb", "32")
    assert code == 0 and "auto-size-" not in out + err.replace(az.TEMP_PREFIX, "")
    assert f"block {env}:" in out


# install-bundle AC-5.5 (FR-26): the generator is safe to run as root from an unpacked bundle.
_FIT = ("--host-cpus", "16", "--host-memory-gb", "32")


@pytest.fixture
def owned_by_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every path look root-owned to check_trusted, so a test can isolate the symlink rule
    from the ownership rule without being root."""
    real = os.lstat

    def lstat(path: str | os.PathLike[str]) -> os.stat_result:
        info = real(path)
        fields = list(info)
        fields[4] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(az.os, "lstat", lstat)


def _tree_bytes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_as_root_a_symlinked_env_is_refused_and_nothing_is_written(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(a) The link is judged with lstat and never followed."""
    real = env.parent / "real.env"
    env.rename(real)
    env.symlink_to(real.name)
    before = _tree_bytes(env.parent)

    code = az.main(["--env-file", str(env), *_FIT], environ={}, euid=0)
    err = capsys.readouterr().err

    assert code == 3
    assert "symbolic link" in err and str(env) in err
    assert _SENTINEL not in err
    assert _tree_bytes(env.parent) == before
    assert not _conf(env).exists()


@pytest.mark.parametrize("name", ["deploy", "deploy/postgres", "deploy/postgres/sizes"])
def test_as_root_a_symlinked_directory_on_the_path_is_refused(
    env: Path, capsys: pytest.CaptureFixture[str], name: str, owned_by_root: None
) -> None:
    target = env.parent / name
    moved = env.parent / "moved"
    target.rename(moved)
    target.symlink_to(moved)

    code = az.main(["--env-file", str(env), *_FIT], environ={}, euid=0)

    assert code == 3
    assert str(target) in capsys.readouterr().err
    assert not list(moved.rglob("auto.conf")), "root wrote through the link"


def test_as_root_an_existing_auto_conf_that_is_a_link_is_refused(
    env: Path, capsys: pytest.CaptureFixture[str], owned_by_root: None
) -> None:
    other = env.parent / "elsewhere.conf"
    other.write_text("x\n", encoding="utf-8")
    _conf(env).symlink_to(other)

    assert az.main(["--env-file", str(env), *_FIT], environ={}, euid=0) == 3
    assert str(_conf(env)) in capsys.readouterr().err
    assert other.read_text(encoding="utf-8") == "x\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="needs a file the caller owns that root does not")
def test_as_root_a_file_owned_by_someone_else_is_refused_and_the_real_uid_is_not(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _tree_bytes(env.parent)

    refused = az.main(["--env-file", str(env), *_FIT], environ={}, euid=0)
    err = capsys.readouterr().err
    assert refused == 3
    assert "not owned by root" in err and _SENTINEL not in err
    assert _tree_bytes(env.parent) == before

    # the same tree, run as the user who owns it, goes through
    assert az.main(["--env-file", str(env), *_FIT], environ={}, euid=os.geteuid()) == 0


def test_check_trusted_is_a_no_op_for_any_other_euid(env: Path) -> None:
    real = env.parent / "real.env"
    env.rename(real)
    env.symlink_to(real.name)
    az.check_trusted(env, 1000)  # a non-root caller keeps today's behaviour: links are fine
    with pytest.raises(az.EnvRefused):
        az.check_trusted(env, 0)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_sizes_directory_names_auto_conf_not_env(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(c) review #20: the message is about the file that was being prepared."""
    sizes = env.parent / "deploy" / "postgres" / "sizes"
    before = _sha(env)
    sizes.chmod(0o555)
    try:
        code = az.main(["--env-file", str(env), *_FIT], environ={})
    finally:
        sizes.chmod(0o700)
    err = capsys.readouterr().err

    assert code == 3
    assert "auto.conf" in err
    assert ".env (" not in err
    assert _sha(env) == before
    assert not list(sizes.iterdir())


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_project_directory_names_env(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of review #20: when .env's own temp file is the one that fails."""
    env.parent.chmod(0o555)
    try:
        code = az.main(["--env-file", str(env), *_FIT], environ={})
    finally:
        env.parent.chmod(0o700)
    err = capsys.readouterr().err

    assert code == 3
    assert ".env" in err and "Traceback" not in err


def test_a_world_readable_env_gets_a_warning_with_the_file_name_only(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(d) SR-11: the state install.sh leaves .env in (Q-4). A warning, never a failure."""
    env.chmod(0o644)

    code, out, err = _run(env, capsys, *_FIT)

    assert code == 0
    assert "auto-size: warning: .env can be read by users other than its owner" in err
    assert "chmod 600 .env" in err
    assert _SENTINEL not in out + err


def test_a_private_env_gets_no_warning(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env.chmod(0o600)

    code, _, err = _run(env, capsys, *_FIT)

    assert code == 0
    assert "can be read by users other than its owner" not in err


def test_the_group_readable_bit_alone_also_warns(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env.chmod(0o640)

    assert "can be read by users" in _run(env, capsys, *_FIT)[2]
