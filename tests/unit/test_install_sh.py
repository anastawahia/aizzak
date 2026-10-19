"""`deploy/release/install.sh`, driven with a fake `docker` (install-bundle US-3 ... US-7).

Three full runs are shared by many assertions (module-scoped fixtures); everything else is a short
run that stops where it is being tested, or a single shell function sourced out of the script. A
test machine is not root, so `prepare` and `run_steps` are called after `source` instead of
`main`, and `require_root`, `require_trusted` and `lock_bundle` are tested on their own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.unit.support_install import (
    FLOW_ARGS,
    INSTALL_SH,
    PREFIXES,
    SECRET_ID,
    SECRET_KEYS,
    VAULT_TOKEN,
    VERSION,
    Harness,
    clone_harness,
    digest_of,
    env_tool,
    make_harness,
    mode,
    parse_env,
    run_entry,
    run_function,
    run_steps,
    set_preflight,
    sha,
    snapshot,
    stub_tool,
)

SCRIPT = INSTALL_SH.read_text(encoding="utf-8")
MINT_SCRIPT = re.search(r"readonly MINT_SCRIPT='([^\n]*)'\n", SCRIPT)
assert MINT_SCRIPT is not None
MINT = MINT_SCRIPT.group(1)


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def env_file(h: Harness) -> Path:
    return h.root / ".env"


def secret_values(h: Harness) -> dict[str, str]:
    values = parse_env(env_file(h).read_text(encoding="utf-8"))
    return {key: values[key] for key in SECRET_KEYS}


def everything_visible(h: Harness, *procs: subprocess.CompletedProcess[str]) -> str:
    """All the text a secret could leak into: the script's output and every recorded call."""
    parts = [p.stdout + p.stderr for p in procs]
    for call in h.calls():
        parts.append(json.dumps(call["argv"]) + json.dumps(call["env"]))
    tools = h.fake / "tools.log"
    if tools.exists():
        parts.append(tools.read_text(encoding="utf-8"))
    return "\n".join(parts)


@dataclass
class Flow:
    h: Harness
    proc: subprocess.CompletedProcess[str]
    before: dict[str, str]
    calls: list[list[str]]


def flow(
    tmp: Path,
    *extra: str,
    preflight_rc: int = 0,
    env: dict[str, str] | None = None,
    overrides: str = "POLL_S=0.01 BOOT_DEADLINE_S=20 HEALTH_DEADLINE_S=1",
) -> Flow:
    h = make_harness(tmp, preflight_rc=preflight_rc)
    before = snapshot(h.root)
    proc = run_steps(h, *FLOW_ARGS, *extra, env=env, overrides=overrides)
    return Flow(h, proc, before, h.docker_calls())


@pytest.fixture(scope="module")
def gpu_flow(tmp_path_factory: pytest.TempPathFactory) -> Flow:
    """The base run: a GPU server, size auto, monitoring on, umask 022."""
    return flow(tmp_path_factory.mktemp("gpu"))


@pytest.fixture(scope="module")
def cpu_flow(tmp_path_factory: pytest.TempPathFactory) -> Flow:
    """A CPU server (gpu-preflight exits 10), a named size, monitoring off."""
    return flow(
        tmp_path_factory.mktemp("cpu"), "--size", "medium", "--no-monitoring", preflight_rc=10
    )


@dataclass
class Repeat:
    h: Harness
    first_secrets: dict[str, str]
    env_sha: str
    env_mode: int
    env_text: str
    mark: int
    proc: subprocess.CompletedProcess[str]


@pytest.fixture(scope="module")
def repeat(gpu_flow: Flow, tmp_path_factory: pytest.TempPathFactory) -> Repeat:
    """AC-4.6 / AC-7.3: the operator edits LOG_LEVEL and runs chmod 600, then installs again."""
    h = clone_harness(gpu_flow.h, tmp_path_factory.mktemp("repeat"))
    path = env_file(h)
    text = path.read_text(encoding="utf-8")
    assert "LOG_LEVEL=" in text
    path.write_text(re.sub(r"(?m)^LOG_LEVEL=.*$", "LOG_LEVEL=DEBUG", text), encoding="utf-8")
    path.chmod(0o600)
    secrets_before, sha_before = secret_values(h), sha(path)
    mark = len(h.calls())
    proc = run_steps(h, *FLOW_ARGS)
    return Repeat(
        h, secrets_before, sha_before, mode(path), path.read_text(encoding="utf-8"), mark, proc
    )


@pytest.fixture(scope="module")
def plain_repeat(gpu_flow: Flow, tmp_path_factory: pytest.TempPathFactory) -> Repeat:
    """AC-7.3: the same inputs again on a converged stack, with .env exactly as it was left."""
    h = clone_harness(gpu_flow.h, tmp_path_factory.mktemp("plain"))
    path = env_file(h)
    secrets_before, sha_before = secret_values(h), sha(path)
    mark = len(h.calls())
    proc = run_steps(h, *FLOW_ARGS)
    return Repeat(
        h, secrets_before, sha_before, mode(path), path.read_text(encoding="utf-8"), mark, proc
    )


# ── the pieces of the script that need no run ───────────────────────────────


def test_the_script_is_valid_bash() -> None:
    assert subprocess.run(["bash", "-n", str(INSTALL_SH)], check=False).returncode == 0


def test_help_prints_the_usage_and_exits_0_without_root(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    done = run_entry(h, "--help")
    assert done.returncode == 0, done.stderr
    for word in ("--domain", "--firebase-project", "--size", "--cpu", "--no-monitoring"):
        assert word in done.stdout
    assert snapshot(h.root) == snapshot(h.root)


@pytest.mark.parametrize(
    "domain, ok",
    [
        ("example.com", True),
        ("xn--mgbh0fb.example", True),
        ("a.b.example.org", True),
        ("https://example.com", False),
        ("example.com/x", False),
        ("example.com:8443", False),
        ("", False),
        ("مثال.com", False),
        ("localhost", False),
        ("1.2.3.4", False),
        ("-bad.example.com", False),
        ("example.com.", False),
        ("a..b.com", False),
        ("x" * 64 + ".com", False),
    ],
)
def test_valid_domain_table(tmp_path: Path, domain: str, ok: bool) -> None:
    h = make_harness(tmp_path)
    done = run_function(h, 'valid_domain "$1"', domain)
    assert (done.returncode == 0) is ok, (domain, done.stderr)


@pytest.mark.parametrize(
    "value, ok",
    [
        ("aizzak-agent", True),
        ("demo-proj", True),
        ("demo proj", False),
        ("a/b", False),
        ("", False),
        ("Demo-proj", False),
        ("x", False),
    ],
)
def test_valid_firebase_table(tmp_path: Path, value: str, ok: bool) -> None:
    done = run_function(make_harness(tmp_path), 'valid_firebase "$1"', value)
    assert (done.returncode == 0) is ok


def test_valid_size_accepts_auto_and_the_files_in_deploy_sizes(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    for name, ok in (
        ("auto", True),
        ("small", True),
        ("medium", True),
        ("large", True),
        ("huge", False),
        ("../x", False),
        ("", False),
    ):
        done = run_function(h, 'ROOT="$PWD"; valid_size "$1"', name)
        assert (done.returncode == 0) is ok, name


@pytest.mark.parametrize(
    "argv, fragment",
    [
        ([], "--domain NAME is required"),
        (["--domain", "https://example.com", "--firebase-project", "demo-proj"], "xn--"),
        (["--domain", "مثال.com", "--firebase-project", "demo-proj"], "xn--"),
        (["--domain", "example.com"], "does not start without it"),
        (
            ["--domain", "example.com", "--firebase-project", "demo proj"],
            "does not start without it",
        ),
        (["--domain", "example.com", "--firebase-project", "a/b"], "does not start without it"),
        ([*FLOW_ARGS, "--size", "huge"], "--size 'huge'"),
        ([*FLOW_ARGS, "--cpu", "--cpu"], "given twice"),
        ([*FLOW_ARGS, "--bogus"], "unknown option"),
        ([*FLOW_ARGS, "stray"], "unexpected argument"),
        ([*FLOW_ARGS, "--token", "x"], "unknown option"),
        (["--domain"], "needs a value"),
    ],
)
def test_bad_usage_exits_2_before_anything_is_written(
    tmp_path: Path, argv: list[str], fragment: str
) -> None:
    h = make_harness(tmp_path)
    before = snapshot(h.root)
    done = run_entry(h, *argv)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert fragment in done.stderr
    assert snapshot(h.root) == before
    assert h.calls() == []


def test_the_script_accepts_exactly_the_six_documented_options() -> None:
    def options(text: str) -> set[str]:
        usage = re.search(r"usage\(\) \{.*?USAGE\n", text, re.S)
        assert usage is not None
        return set(re.findall(r"^\s+(--[a-z-]+)", usage.group(0), re.M))

    def accepted(text: str) -> set[str]:
        case = re.search(r"(--domain \| [^)]*)\) ;;", text)
        assert case is not None
        return {part.strip() for part in case.group(1).split("|")}

    six = {"--domain", "--firebase-project", "--size", "--cpu", "--no-monitoring", "--gpu-smoke"}
    assert options(SCRIPT) == six and accepted(SCRIPT) == six
    assert options(re.sub(r"(?m)^  --gpu-smoke .*\n", "", SCRIPT)) != six  # a dropped option shows
    assert (
        accepted(SCRIPT.replace("--gpu-smoke)", "--gpu-smoke | --key)", 1)) != six
    )  # an added one


def test_the_script_never_reads_a_credential_file() -> None:
    assert "config.json" not in SCRIPT
    assert not re.search(r"(~|\$HOME|\$\{HOME\}|/)\.docker\b", SCRIPT)


# ── AC-2.4: what the script must never do ────────────────────────────────────

_FORBIDDEN = (
    r"docker\s+volume\s+rm",
    r"\bprune\b",
    r"\bdown\b",
    r"--force-recreate",
    r"operator\s+init",
    r"source\s+\S*\.env\b",
    r"^\s*\.\s+\S*\.env\b",
    r"compose\s+pull",
)


def _hits(text: str) -> list[str]:
    return [p for p in _FORBIDDEN if re.search(p, text, re.M)]


@pytest.mark.parametrize("script", ["install.sh", "bundle.sh"])
def test_no_destructive_command_and_no_env_sourcing(script: str) -> None:
    text = (INSTALL_SH.parent / script).read_text(encoding="utf-8")
    assert _hits(text) == []


def test_the_forbidden_patterns_find_what_they_are_for() -> None:
    for sample in (
        "docker volume rm x",
        "docker system prune",
        "compose down -v",
        "up --force-recreate",
        "vault operator init",
        "source .env",
        ". ./.env",
        "docker compose pull",
    ):
        assert _hits(sample), sample
    assert _hits("download the image; a countdown") == []


def test_a_bare_config_is_never_called(gpu_flow: Flow) -> None:
    """`docker compose config` prints every password; only the three safe forms exist."""
    for argv in gpu_flow.calls:
        if "config" in argv:
            tail = argv[argv.index("config") + 1 :]
            assert tail and tail[0] in ("--images", "--services", "--hash"), argv
    assert _bare_config_lines(SCRIPT) == []
    assert _bare_config_lines("x=$(compose 60 config)\n") != []
    assert _bare_config_lines("x=$(compose 60 config -q)\n") != []
    assert _bare_config_lines("x=$(compose 60 config --images | sort)\n") == []


def _bare_config_lines(text: str) -> list[str]:
    calls = [ln for ln in text.splitlines() if re.search(r"\bcompose \d+ config\b", ln)]
    return [ln for ln in calls if not re.search(r"config (--images|--services|--hash)", ln)]


# ── AC-3.1: the pre-flight decides the mode ──────────────────────────────────


def _mode_after_checks(tmp: Path, rc: int, *flags: str) -> subprocess.CompletedProcess[str]:
    h = make_harness(tmp, preflight_rc=rc)
    code = 'prepare "$@"; step_checks_host >/dev/null; echo "MODE=$MODE"'
    return run_function(h, code, *FLOW_ARGS, *flags)


@pytest.mark.parametrize(
    "rc, flags, expected",
    [
        (0, (), "gpu"),
        (0, ("--cpu",), "cpu"),
        (10, (), "cpu"),
        (12, ("--cpu",), "cpu"),
        (10, ("--cpu",), "cpu"),
    ],
)
def test_preflight_codes_that_let_the_install_continue(
    tmp_path: Path, rc: int, flags: tuple[str, ...], expected: str
) -> None:
    done = _mode_after_checks(tmp_path, rc, *flags)
    assert done.returncode == 0, done.stderr
    assert f"MODE={expected}" in done.stdout


@pytest.mark.parametrize(
    "rc, flags",
    [
        (11, ()),
        (12, ()),
        (13, ()),
        (14, ()),
        (11, ("--cpu",)),
        (13, ("--cpu",)),
        (14, ("--cpu",)),
        (2, ()),
    ],
)
def test_preflight_codes_that_stop_the_install_write_nothing(
    tmp_path: Path, rc: int, flags: tuple[str, ...]
) -> None:
    h = make_harness(tmp_path, preflight_rc=rc)
    before = snapshot(h.root)
    done = run_steps(h, *FLOW_ARGS, *flags)
    assert done.returncode == 10, (done.stdout, done.stderr)
    assert f"exit {rc}" in done.stderr
    assert snapshot(h.root) == before
    assert not (h.root / ".env").exists()
    assert [c for c in h.docker_calls() if c[:1] == ["compose"]] == []
    assert list(h.sysctl_d.iterdir()) == []


# ── AC-3.2 ───────────────────────────────────────────────────────────────────


def _fake_python(h: Harness, refuse: str) -> None:
    link = h.fake / "bin" / "python3"
    link.unlink()
    link.write_text(
        f'#!/bin/sh\ncase "$*" in *{refuse}*) exit 1;; esac\nexec {sys.executable} "$@"\n',
        encoding="utf-8",
    )
    link.chmod(0o755)


@pytest.mark.parametrize(
    "refuse, fragment", [("version_info", "older than 3.10"), ("import*yaml", "PyYAML")]
)
def test_a_missing_python_or_pyyaml_stops_with_11_before_the_root_check(
    tmp_path: Path, refuse: str, fragment: str
) -> None:
    h = make_harness(tmp_path)
    _fake_python(h, refuse)
    before = snapshot(h.root)
    done = run_entry(h, *FLOW_ARGS)
    assert done.returncode == 11, (done.stdout, done.stderr)
    assert fragment in done.stderr
    assert snapshot(h.root) == before


def test_too_little_free_space_stops_a_first_install_with_12_in_gb(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    before = snapshot(h.root)
    done = run_steps(h, *FLOW_ARGS, env={"FAKE_DF_KB": str(10 * 1024 * 1024)})
    assert done.returncode == 12, (done.stdout, done.stderr)
    assert "only 10 GB free" in done.stderr and "40 GB" in done.stderr
    assert snapshot(h.root) == before


def test_the_same_shortage_does_not_refuse_a_repeat(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    done = run_function(
        h,
        'ROOT="$PWD"; FIRST_INSTALL=0; MODE=gpu; step_disk',
        env={"FAKE_DF_KB": str(10 * 1024 * 1024)},
    )
    assert done.returncode == 0 and "10 GB free" in done.stdout


# ── AC-3.3: the registry ─────────────────────────────────────────────────────


def test_unauthorized_stops_with_13_and_tells_how_to_log_in(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    before = snapshot(h.root)
    done = run_steps(
        h, *FLOW_ARGS, env={"FAKE_MANIFEST_ERR": "Error: unauthorized: authentication required"}
    )
    assert done.returncode == 13, done.stderr
    assert "docker login" in done.stderr and "read-only" in done.stderr.lower()
    assert snapshot(h.root) == before


def test_a_network_failure_stops_with_14_and_says_no_network(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    before = snapshot(h.root)
    done = run_steps(
        h,
        *FLOW_ARGS,
        env={
            "FAKE_MANIFEST_ERR": "Get https://registry-1.docker.io/v2/: dial tcp: i/o timeout",
        },
    )
    assert done.returncode == 14, done.stderr
    assert "no network" in done.stderr
    assert snapshot(h.root) == before


# ── AC-3.4: not a bundle ─────────────────────────────────────────────────────


def _checkout_layout(tmp_path: Path) -> Harness:
    h = make_harness(tmp_path)
    (h.root / "VERSION").unlink()
    target = h.root / "deploy" / "release"
    target.mkdir(parents=True)
    shutil.move(h.root / "install.sh", target / "install.sh")
    (h.root / ".env").write_text("KEEP=me\n", encoding="utf-8")
    return h


def test_running_it_from_a_checkout_exits_3_and_touches_nothing(tmp_path: Path) -> None:
    h = _checkout_layout(tmp_path)
    before = snapshot(h.root)
    done = subprocess.run(
        ["bash", str(h.root / "deploy" / "release" / "install.sh"), *FLOW_ARGS],
        cwd=h.root,
        capture_output=True,
        text=True,
        env=h.env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert done.returncode == 3, done.stderr
    assert "run it from the unpacked bundle" in done.stderr
    assert snapshot(h.root) == before


def test_a_version_file_with_a_git_directory_is_still_not_a_bundle(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    (h.root / ".git").mkdir()
    done = run_entry(h, *FLOW_ARGS)
    assert done.returncode == 3 and "run it from the unpacked bundle" in done.stderr


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace(VERSION, "2026.99.1", 1),
        lambda t: "\n".join(t.splitlines()[:4]) + "\n",
        lambda t: t.replace("commit ", "comit ", 1),
        lambda t: t.replace(digest_of("app"), digest_of("app")[:-1]),
        lambda t: "\n".join([*t.splitlines()[:2], *reversed(t.splitlines()[2:])]) + "\n",
        lambda t: t.replace("@sha256:", ":latest@sha256:", 1),
    ],
)
def test_a_damaged_version_file_exits_3(tmp_path: Path, edit: Callable[[str], str]) -> None:
    h = make_harness(tmp_path)
    text = (h.root / "VERSION").read_text(encoding="utf-8")
    (h.root / "VERSION").write_text(edit(text), encoding="utf-8")
    done = run_entry(h, *FLOW_ARGS)
    assert done.returncode == 3, done.stderr
    assert "VERSION is damaged" in done.stderr


# ── AC-3.5: root, the kernel and what survives a reboot ──────────────────────


def test_a_non_root_run_stops_with_4_and_says_sudo(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    before = snapshot(h.root)
    done = run_entry(h, *FLOW_ARGS)
    if os.geteuid() == 0:
        pytest.skip("this test needs a non-root user")
    assert done.returncode == 4, done.stderr
    assert "sudo ./install.sh" in done.stderr
    assert snapshot(h.root) == before
    assert list(h.sysctl_d.iterdir()) == []
    assert h.docker_calls() == []


def test_the_kernel_step_writes_the_knob_and_one_persist_file_and_repeats_to_the_byte(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    first = run_function(h, 'ROOT="$PWD"; step_kernel')
    assert first.returncode == 0, first.stderr
    assert (h.sysctl / "vm" / "overcommit_memory").read_text().strip() == "1"
    files = list(h.sysctl_d.iterdir())
    assert [f.name for f in files] == ["90-aizzak.conf"]
    before = (sha(files[0]), files[0].stat().st_mtime_ns)
    second = run_function(h, 'ROOT="$PWD"; step_kernel')
    assert second.returncode == 0, second.stderr
    assert (sha(files[0]), files[0].stat().st_mtime_ns) == before


def test_a_kernel_failure_stops_with_20(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    shutil.rmtree(h.sysctl_d)
    done = run_function(h, 'ROOT="$PWD"; step_kernel')
    assert done.returncode == 20, done.stderr


# ── the root-only guards, one by one ─────────────────────────────────────────


def test_require_root_refuses_a_normal_user(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("this test needs a non-root user")
    done = run_function(make_harness(tmp_path), "require_root")
    assert done.returncode == 4 and "sudo ./install.sh" in done.stderr


def test_require_trusted_refuses_links_and_foreign_owners(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    me = str(os.geteuid())
    other = str(os.geteuid() + 1)
    link = h.root / "link"
    link.symlink_to(h.root / "deploy")
    assert (
        run_function(h, 'require_trusted "$@"', me, str(h.root), str(h.root / "deploy")).returncode
        == 0
    )
    refused_link = run_function(h, 'require_trusted "$@"', me, str(link))
    assert refused_link.returncode == 3 and "symbolic link" in refused_link.stderr
    refused_owner = run_function(h, 'require_trusted "$@"', other, str(h.root))
    assert refused_owner.returncode == 3 and "not owned by uid" in refused_owner.stderr


def test_lock_bundle_refuses_a_second_installer(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys, time\nfd = os.open(sys.argv[1], os.O_RDONLY)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('held', flush=True)\ntime.sleep(30)",
            str(h.root),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        done = run_function(h, 'ROOT="$PWD"; lock_bundle')
    finally:
        holder.kill()
        holder.wait()
    assert done.returncode == 3 and "another install.sh is running" in done.stderr
    assert list(h.root.glob("*.lock")) == []
    assert run_function(h, 'ROOT="$PWD"; lock_bundle').returncode == 0


# ── small helpers of the script ──────────────────────────────────────────────


def test_relay_strips_control_characters_and_keeps_text(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    done = run_function(h, r"printf 'ok\033[31m red\r\nline\ttab\007\n' | relay")
    assert done.stdout == "ok[31m red\nline\ttab\n"


def test_redact_hides_tokens_and_uuids_and_shortens(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    text = f"denied {VAULT_TOKEN} for {SECRET_ID}\n" + "more\n" * 9
    done = run_function(h, "redact", stdin=text)
    assert VAULT_TOKEN not in done.stdout and SECRET_ID not in done.stdout
    assert "<redacted>" in done.stdout
    assert len(done.stdout.splitlines()) == 5


def test_scrub_environment_removes_what_compose_would_prefer_to_the_env_file(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    done = run_function(
        h,
        'ROOT="$PWD"; scrub_environment; echo "COMPOSE_FILE=${COMPOSE_FILE-unset}"; '
        'echo "LOG_LEVEL=${LOG_LEVEL-unset}"; echo "PATH_OK=${PATH:+yes}"',
        env={"COMPOSE_FILE": "ci.yml", "COMPOSE_X": "1", "LOG_LEVEL": "DEBUG", "UNRELATED": "keep"},
    )
    assert done.returncode == 0, done.stderr
    assert "COMPOSE_FILE=unset" in done.stdout and "LOG_LEVEL=unset" in done.stdout
    assert "PATH_OK=yes" in done.stdout
    assert re.search(r"removed from the environment.*COMPOSE_FILE", done.stdout)
    assert "UNRELATED" not in done.stdout and "COMPOSE_X" in done.stdout


# ── AC-4.1: the base run ─────────────────────────────────────────────────────


def test_the_base_run_is_ready(gpu_flow: Flow) -> None:
    assert gpu_flow.proc.returncode == 0, gpu_flow.proc.stdout[-2000:] + gpu_flow.proc.stderr
    assert "install: READY" in gpu_flow.proc.stdout


def test_env_is_0644_under_umask_022_and_nothing_else_secret_appears(gpu_flow: Flow) -> None:
    path = env_file(gpu_flow.h)
    assert mode(path) == 0o644
    new = set(snapshot(gpu_flow.h.root)) - set(gpu_flow.before)
    assert new == {".env", "deploy/postgres/sizes/auto.conf"}, new
    assert not list(gpu_flow.h.root.rglob(".install-tmp-*"))


def test_no_value_is_left_as_change_me_and_the_13_are_48_hex_and_distinct(gpu_flow: Flow) -> None:
    values = parse_env(env_file(gpu_flow.h).read_text(encoding="utf-8"))
    assert [k for k, v in values.items() if v.startswith("change-me")] == []
    secrets = secret_values(gpu_flow.h)
    assert all(re.fullmatch(r"[0-9a-f]{48}", v) for v in secrets.values())
    assert len(set(secrets.values())) == 13


def test_the_13_are_derived_from_the_template_and_match_the_list(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    template = h.root / ".env.example"
    derived = [
        key
        for key, value in parse_env(template.read_text(encoding="utf-8")).items()
        if value.startswith("change-me")
    ]
    assert derived == list(SECRET_KEYS)
    # the same derivation, shown to fail both ways: one placeholder filled in, one added
    text = template.read_text(encoding="utf-8")
    fewer = text.replace("GRAFANA_ADMIN_PASSWORD=change-me-grafana", "GRAFANA_ADMIN_PASSWORD=x")
    more = text + "NEW_PASSWORD=change-me-new\n"
    for variant, expect in ((fewer, 12), (more, 14)):
        (tmp_path / "t.env").write_text(variant, encoding="utf-8")
        done = run_function(
            h,
            'env_tool compose "$1" "$2" "${@:3}"',
            str(tmp_path / "new.env"),
            str(tmp_path / "t.env"),
            "--release",
            VERSION,
            "--commit",
            "a" * 40,
            "--mode",
            "cpu",
            "--monitoring",
            "on",
            "--size",
            "auto",
            "--domain",
            "example.com",
            "--firebase",
            "demo-proj",
            "--image-app",
            "anastawahia/aizzak:app-1@sha256:" + "a" * 64,
            "--image-embedding",
            "anastawahia/aizzak:embedding-1@sha256:" + "b" * 64,
            "--image-cuda",
            "anastawahia/aizzak:embedding-cuda-1@sha256:" + "c" * 64,
        )
        assert done.returncode == 0, done.stderr
        created = parse_env((tmp_path / "new.env").read_text(encoding="utf-8"))
        generated = [k for k, v in created.items() if re.fullmatch(r"[0-9a-f]{48}", v)]
        assert len(generated) == expect, (expect, generated)
        (tmp_path / "new.env").unlink()


def test_every_other_template_key_keeps_its_value(gpu_flow: Flow) -> None:
    template = parse_env((gpu_flow.h.root / ".env.example").read_text(encoding="utf-8"))
    made = parse_env(env_file(gpu_flow.h).read_text(encoding="utf-8"))
    managed = set(SECRET_KEYS) | {
        "MINIO_PUBLIC_ENDPOINT",
        "MINIO_PUBLIC_SECURE",
        "OAUTH_REDIRECT_BASE_URL",
        "FIREBASE_PROJECT_ID",
        "VAULT_ROLE_ID",
        "POSTGRES_CONFIG_FILE",  # the one key the size blocks share with the template
    }
    for key, value in template.items():
        if key not in managed and key in made:
            assert made[key] == value, key


# ── AC-4.2: no secret anywhere it should not be ─────────────────────────────


def test_no_secret_is_in_any_output_argument_or_variable(gpu_flow: Flow, cpu_flow: Flow) -> None:
    for run in (gpu_flow, cpu_flow):
        seen = everything_visible(run.h, run.proc)
        for key, value in secret_values(run.h).items():
            assert value not in seen, key
        assert VAULT_TOKEN not in seen
        assert SECRET_ID not in seen


def test_the_metrics_exporter_password_crosses_stdin_and_nothing_else(gpu_flow: Flow) -> None:
    value = secret_values(gpu_flow.h)["METRICS_EXPORTER_PASSWORD"]
    calls = [
        c
        for c in gpu_flow.h.calls()
        if c["argv"][:1] == ["compose"] and "postgres" in c["argv"] and "exec" in c["argv"]
    ]
    assert len(calls) == 1
    assert calls[0]["stdin_sha256"] == sha_text(value + "\n")
    assert "15-metrics-exporter.sh" in " ".join(calls[0]["argv"])


# ── AC-4.3 / AC-4.4 / AC-4.5 ─────────────────────────────────────────────────

_COMPOSE_OPTS = ("--release", VERSION, "--commit", "a" * 40)


def _step_env(
    h: Harness, mode_name: str, *flags: str, domain: str = "example.com"
) -> subprocess.CompletedProcess[str]:
    return run_function(
        h,
        f'prepare "$@"; MODE={mode_name}; step_env',
        "--domain",
        domain,
        "--firebase-project",
        "demo-proj",
        *flags,
    )


def test_the_domain_is_lower_cased_into_the_three_keys(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    done = _step_env(h, "cpu", domain="Example.COM")
    assert done.returncode == 0, done.stderr
    values = parse_env(env_file(h).read_text(encoding="utf-8"))
    assert values["MINIO_PUBLIC_ENDPOINT"] == "example.com"
    assert values["MINIO_PUBLIC_SECURE"] == "true"
    assert values["OAUTH_REDIRECT_BASE_URL"] == "https://example.com"
    assert values["FIREBASE_PROJECT_ID"] == "demo-proj"


@pytest.mark.parametrize("mode_name", ["gpu", "cpu"])
@pytest.mark.parametrize("flags", [(), ("--no-monitoring",)])
def test_the_four_combinations_write_compose_selectors_and_pinned_images(
    tmp_path: Path, mode_name: str, flags: tuple[str, ...]
) -> None:
    h = make_harness(tmp_path)
    assert _step_env(h, mode_name, *flags).returncode == 0
    text = env_file(h).read_text(encoding="utf-8")
    values = parse_env(text)
    gpu = mode_name == "gpu"
    assert values["COMPOSE_FILE"] == (
        "docker-compose.yml:docker-compose.gpu.yml" if gpu else "docker-compose.yml"
    )
    assert values["COMPOSE_PROFILES"] == ("local-llm" if flags else "local-llm,monitoring")
    version_lines = (h.root / "VERSION").read_text(encoding="utf-8").splitlines()[2:]
    for prefix, line in zip(PREFIXES, version_lines, strict=True):
        key = prefix.upper().replace("-", "_")
        tag = line.split(":", 1)[1]
        assert values[f"{key}_IMAGE_REPO"] == "anastawahia/aizzak"
        assert values[f"{key}_IMAGE_TAG"] == tag
    assert values["VAULT_TOKEN"] == ""
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}", values["VAULT_ROLE_ID"]
    )
    for word in ("wsl-ollama", "docker-compose.build.yml", "docker-compose.test.yml"):
        assert word not in "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))


# ── AC-4.6 / AC-7.3: a repeat is a no-op ─────────────────────────────────────


def test_the_repeat_succeeds(repeat: Repeat) -> None:
    assert repeat.proc.returncode == 0, repeat.proc.stdout[-1500:] + repeat.proc.stderr


def test_a_repeat_keeps_every_secret_the_edit_the_bytes_and_the_mode(repeat: Repeat) -> None:
    path = env_file(repeat.h)
    assert secret_values(repeat.h) == repeat.first_secrets
    assert "LOG_LEVEL=DEBUG" in path.read_text(encoding="utf-8")
    assert sha(path) == repeat.env_sha
    assert mode(path) == 0o600 == repeat.env_mode


def test_a_repeat_on_a_converged_stack_touches_nothing(plain_repeat: Repeat) -> None:
    repeat = plain_repeat
    assert repeat.proc.returncode == 0, repeat.proc.stdout[-1500:] + repeat.proc.stderr
    calls = repeat.h.docker_calls_since(repeat.mark)
    assert calls, "the repeat made no call at all"
    assert not [c for c in calls if "up" in c], "a converged repeat must not call up"
    assert not [c for c in calls if "exec" in c and "vault" in c]
    assert not [c for c in calls if c[:1] == ["pull"]]
    assert "already converged: no container touched" in repeat.proc.stdout
    assert "already in .env" in repeat.proc.stdout
    path = env_file(repeat.h)
    assert sha(path) == repeat.env_sha and mode(path) == repeat.env_mode
    assert secret_values(repeat.h) == repeat.first_secrets


def test_no_call_anywhere_recreates_or_initialises(
    gpu_flow: Flow, repeat: Repeat, plain_repeat: Repeat
) -> None:
    for call in [
        *gpu_flow.h.docker_calls(),
        *repeat.h.docker_calls(),
        *plain_repeat.h.docker_calls(),
    ]:
        joined = " ".join(call)
        assert "--force-recreate" not in joined and "operator init" not in joined


def test_the_ttl_constant_equals_the_one_vault_bootstrap_sets() -> None:
    bootstrap = (INSTALL_SH.parents[1] / "vault" / "bootstrap.sh").read_text(encoding="utf-8")
    ttl = re.search(r"secret_id_ttl=(\d+)h", bootstrap)
    constant = re.search(r"readonly SECRET_ID_TTL_H=(\d+)", SCRIPT)
    assert ttl is not None and constant is not None
    assert ttl.group(1) == constant.group(1) == "720"


# ── AC-4.7: a repeat that asks for something else is refused ─────────────────


_OTHER_DOMAIN = ["--domain", "other.com", "--firebase-project", "demo-proj"]
_OTHER_FIREBASE = ["--domain", "example.com", "--firebase-project", "other-proj"]


@pytest.mark.parametrize(
    "args, fragment",
    [
        (_OTHER_DOMAIN, "MINIO_PUBLIC_ENDPOINT"),
        (_OTHER_FIREBASE, "FIREBASE_PROJECT_ID"),
        ([*FLOW_ARGS, "--size", "medium"], "size"),
        ([*FLOW_ARGS, "--cpu"], "COMPOSE_FILE"),
        ([*FLOW_ARGS, "--no-monitoring"], "COMPOSE_PROFILES"),
    ],
)
def test_changing_an_input_after_install_is_refused_naming_the_key(
    repeat: Repeat, tmp_path: Path, args: list[str], fragment: str
) -> None:
    h = clone_harness(repeat.h, tmp_path)
    before = (sha(env_file(h)), mode(env_file(h)))
    done = run_steps(h, *args)
    assert done.returncode == 30, (done.stdout[-600:], done.stderr)
    assert fragment in done.stderr
    assert (sha(env_file(h)), mode(env_file(h))) == before


# ── AC-5.1 / AC-5.2 / AC-5.3: the size ───────────────────────────────────────


def _size_run(
    tmp: Path,
    *flags: str,
    auto_rc: int | None = None,
    budget_rc: int | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Harness, subprocess.CompletedProcess[str], Path]:
    h = make_harness(tmp)
    log = tmp / "stubs.log"
    if auto_rc is not None:
        stub_tool(h.root, "deploy/auto_size.py", auto_rc, log)
    if budget_rc is not None:
        stub_tool(h.root, "deploy/resource-budget.sh", budget_rc, log)
    done = run_steps(h, *FLOW_ARGS, *flags, env=env)
    return h, done, log


def _stub_calls(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.parametrize("rc, code", [(1, 40), (2, 41), (3, 41)])
def test_the_generator_exit_codes_map_to_40_and_41_before_any_pull(
    tmp_path: Path, rc: int, code: int
) -> None:
    h, done, log = _size_run(tmp_path, auto_rc=rc)
    assert done.returncode == code, (done.stdout[-800:], done.stderr)
    assert [c for c in h.docker_calls() if c[:1] == ["pull"]] == []
    call = _stub_calls(log)[0]
    assert call["argv"][:6] == [
        "--role",
        "server",
        "--mode",
        "gpu",
        "--profiles",
        "local-llm,monitoring",
    ]
    assert call["isolated"] == 1
    env = call["env"]
    assert isinstance(env, dict)
    assert env["COMPOSE_FILE"] == "docker-compose.yml"
    assert env["GPU_COMPOSE_FILE"] == "docker-compose.gpu.yml"
    assert env["SIZES_DIR"] == "deploy/sizes"


def test_a_generator_error_leaves_env_as_step_3_wrote_it(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    stub_tool(h.root, "deploy/auto_size.py", 3, tmp_path / "stubs.log")
    done = run_function(
        h,
        'prepare "$@"; MODE=gpu; step_env >/dev/null; a=$(sha256sum .env); '
        '( step_size ) >/dev/null 2>&1 || echo "RC=$?"; b=$(sha256sum .env); '
        '[[ $a == "$b" ]] && echo SAME',
        *FLOW_ARGS,
    )
    assert "SAME" in done.stdout, (done.stdout, done.stderr)


def test_a_passing_generator_lets_the_install_continue(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    stub_tool(h.root, "deploy/auto_size.py", 0, tmp_path / "stubs.log")
    done = run_function(
        h,
        'prepare "$@"; MODE=gpu; step_env >/dev/null; step_size >/dev/null; echo CONTINUED',
        *FLOW_ARGS,
    )
    assert "CONTINUED" in done.stdout


def test_a_named_size_writes_its_block_and_no_auto_block(cpu_flow: Flow) -> None:
    assert cpu_flow.proc.returncode == 0, cpu_flow.proc.stdout[-1500:] + cpu_flow.proc.stderr
    text = env_file(cpu_flow.h).read_text(encoding="utf-8")
    assert "# >>> aizzak size >>>" in text and "# >>> aizzak auto-size >>>" not in text
    block = text.split("# >>> aizzak size >>>")[1].split("# <<< aizzak size <<<")[0]
    assert "# size: medium (deploy/sizes/medium.env)" in block
    wanted = parse_env((cpu_flow.h.root / "deploy" / "sizes" / "medium.env").read_text())
    got = parse_env(block)
    assert got == wanted and len(wanted) > 20


def test_the_named_budget_is_called_with_the_size_and_without_monitoring(cpu_flow: Flow) -> None:
    calls = cpu_flow.h.tool_calls("resource-budget")
    assert calls and calls[0]["argv"] == ["--size", "medium", "--without-monitoring"]


def test_with_monitoring_the_budget_gets_no_without_flag(tmp_path: Path) -> None:
    _, done, log = _size_run(tmp_path, "--size", "medium", budget_rc=1)
    assert done.returncode == 40
    assert _stub_calls(log)[0]["argv"] == ["--size", "medium"]


def test_a_shell_exported_compose_file_does_not_reach_the_budget_and_stops_before_pull(
    tmp_path: Path,
) -> None:
    ci = "docker-compose.yml:docker-compose.test.yml:docker-compose.build.yml"
    h, done, log = _size_run(
        tmp_path, "--size", "medium", budget_rc=1, env={"COMPOSE_FILE": ci, "SIZES_DIR": "/evil"}
    )
    assert done.returncode == 40, done.stderr
    call = _stub_calls(log)[0]
    env = call["env"]
    assert isinstance(env, dict)
    assert env["COMPOSE_FILE"] == "docker-compose.yml"
    assert env["GPU_COMPOSE_FILE"] == "docker-compose.gpu.yml"
    assert env["SIZES_DIR"] == "deploy/sizes"
    assert "COMPOSE_FILE" in done.stdout
    assert [c for c in h.docker_calls() if c[:1] == ["pull"]] == []


# ── AC-6.1 ... AC-6.4: images and the first boot ─────────────────────────────


def _index(calls: list[list[str]], *needles: str) -> int:
    for i, call in enumerate(calls):
        if all(n in call for n in needles):
            return i
    raise AssertionError(f"no call with {needles}")


def test_the_boot_runs_in_the_documented_order(gpu_flow: Flow) -> None:
    calls = gpu_flow.calls
    pull = _index(calls, "pull")
    verify = _index(calls, "image", "inspect", "--format")
    up_vault = _index(calls, "up", "vault")
    up_bootstrap = _index(calls, "up", "vault-bootstrap")
    mint = _index(calls, "exec", "vault")
    minio = _index(calls, "up", "minio-bootstrap")
    migrate = _index(calls, "up", "migrate")
    metrics = _index(calls, "exec", "postgres")
    ollama = _index(calls, "up", "ollama-pull")
    final = max(i for i, c in enumerate(calls) if c[-2:] == ["up", "-d"])
    order = [pull, verify, up_vault, up_bootstrap, mint, minio, migrate, metrics, ollama, final]
    assert order == sorted(order) and len(set(order)) == len(order), order


def test_nothing_is_created_before_the_key_exists_except_vault_and_its_bootstrap(
    gpu_flow: Flow,
) -> None:
    calls = gpu_flow.calls
    mint = _index(calls, "exec", "vault")
    for call in calls[:mint]:
        if "up" in call or "run" in call or "create" in call:
            named = [a for a in call[call.index("up") + 1 :] if not a.startswith("-")]
            assert named and set(named) <= {"vault", "vault-bootstrap"}, call


def test_the_key_is_minted_inside_vault_and_written_once(gpu_flow: Flow) -> None:
    mint_calls = [c for c in gpu_flow.calls if "exec" in c and "vault" in c]
    assert len(mint_calls) == 1
    argv = mint_calls[0]
    assert argv[-3:-1] == ["sh", "-c"] and argv[-1] == MINT
    assert "VAULT_ADDR=http://127.0.0.1:8200" in argv[-1] and "/vault/init/init.json" in argv[-1]
    text = env_file(gpu_flow.h).read_text(encoding="utf-8")
    assert text.count("\nVAULT_SECRET_ID=") == 1 and f"VAULT_SECRET_ID={SECRET_ID}\n" in text
    assert mode(env_file(gpu_flow.h)) == 0o644


@pytest.mark.parametrize("output", ["", "not-a-uuid", "5b2a1c9e-4d3f-4a6b-8c7d-0e1f2a3b4c5"])
def test_a_key_that_is_not_a_uuid_stops_with_61_before_any_carrier(
    tmp_path: Path, output: str
) -> None:
    run = flow(tmp_path, env={"FAKE_MINT_OUTPUT": output})
    assert run.proc.returncode == 61, (run.proc.stdout[-800:], run.proc.stderr)
    assert "not a UUID" in run.proc.stderr
    block = env_file(run.h).read_text(encoding="utf-8").split("# >>> aizzak install >>>")[1]
    assert "VAULT_SECRET_ID=" not in block, "a key that is not a UUID was written"
    assert [c for c in run.calls if "up" in c and "minio-bootstrap" in c] == []
    assert [c for c in run.calls if "up" in c and "migrate" in c] == []


def test_a_mint_failure_stops_with_61_and_hides_the_token(tmp_path: Path) -> None:
    run = flow(tmp_path, env={"FAKE_MINT_RC": "1"})
    assert run.proc.returncode == 61
    assert VAULT_TOKEN not in run.proc.stdout + run.proc.stderr
    assert "<redacted>" in run.proc.stderr


def test_a_damaged_key_in_env_is_never_replaced(repeat: Repeat, tmp_path: Path) -> None:
    h = clone_harness(repeat.h, tmp_path)
    path = env_file(h)
    path.write_text(
        path.read_text().replace(f"VAULT_SECRET_ID={SECRET_ID}", "VAULT_SECRET_ID=junk")
    )
    before = sha(path)
    done = run_steps(h, *FLOW_ARGS)
    assert done.returncode == 61 and "damaged" in done.stderr
    assert sha(path) == before


def test_a_digest_that_is_not_the_one_in_version_stops_with_51_and_no_up(tmp_path: Path) -> None:
    run = flow(tmp_path, env={"FAKE_BAD_DIGEST_FOR": "aizzak"})
    assert run.proc.returncode == 51, (run.proc.stdout[-800:], run.proc.stderr)
    assert "anastawahia/aizzak@sha256:" in run.proc.stderr
    assert digest_of("app") in run.proc.stderr and "f" * 64 in run.proc.stderr
    assert [c for c in run.calls if "up" in c] == []


def test_a_failed_pull_stops_with_50(tmp_path: Path) -> None:
    run = flow(tmp_path, env={"FAKE_PULL_FAIL": "postgres"})
    assert run.proc.returncode == 50, run.proc.stderr
    assert "postgres" in run.proc.stderr
    assert [c for c in run.calls if "up" in c] == []


def test_only_missing_images_are_pulled_and_never_a_compose_level_pull(
    gpu_flow: Flow, repeat: Repeat
) -> None:
    assert not [
        c
        for c in gpu_flow.calls
        if c[:2] == ["compose", "pull"] or ("pull" in c[:3] and c[:1] == ["compose"])
    ]
    pulled = [c[1] for c in gpu_flow.calls if c[:1] == ["pull"]]
    assert pulled and len(pulled) == len(set(pulled))
    assert not [c for c in repeat.h.docker_calls_since(repeat.mark) if c[:1] == ["pull"]]
    release = [p for p in pulled if p.startswith("anastawahia/aizzak@sha256:")]
    assert len(release) == 2  # GPU mode: app + embedding-cuda, never the CPU embedding image


def test_a_migrate_failure_is_reported_by_name_and_the_rerun_resumes(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    faults = json.dumps({"migrate": {"exit": 1, "times": 1}})
    first = run_steps(h, *FLOW_ARGS, env={"FAKE_FAULTS": faults})
    assert first.returncode == 60, (first.stdout[-600:], first.stderr)
    assert "migrate" in first.stderr and "docker compose logs migrate" in first.stderr
    secrets_after_first = secret_values(h)
    second = run_steps(h, *FLOW_ARGS)
    assert second.returncode == 0, (second.stdout[-800:], second.stderr)
    assert secret_values(h) == secrets_after_first
    mints = [c for c in h.docker_calls() if "exec" in c and "vault" in c]
    assert len(mints) == 1, "the second run minted a second key"


def test_a_service_that_never_becomes_healthy_is_named(tmp_path: Path) -> None:
    run = flow(
        tmp_path,
        env={"FAKE_FAULTS": json.dumps({"app": {"health": "unhealthy"}})},
        overrides="POLL_S=0.05 BOOT_DEADLINE_S=2 HEALTH_DEADLINE_S=1",
    )
    assert run.proc.returncode == 60, (run.proc.stdout[-600:], run.proc.stderr)
    assert "boot step app" in run.proc.stderr and "docker compose logs" in run.proc.stderr


def test_an_ollama_pull_failure_stops_the_install_and_says_what_chat_would_do(
    tmp_path: Path,
) -> None:
    run = flow(
        tmp_path,
        env={"FAKE_FAULTS": json.dumps({"ollama-pull": {"exit": 1, "times": 9}})},
    )
    assert run.proc.returncode == 60, (run.proc.stdout[-600:], run.proc.stderr)
    assert "ollama-pull" in run.proc.stderr
    assert (
        "chat would answer 502" in run.proc.stderr and "re-run sudo ./install.sh" in run.proc.stderr
    )


def test_the_metrics_exporter_step_failing_stops_with_60(tmp_path: Path) -> None:
    run = flow(tmp_path, env={"FAKE_PG_RC": "1"})
    assert run.proc.returncode == 60 and "metrics_exporter" in run.proc.stderr


# ── AC-7.1: the checks ───────────────────────────────────────────────────────


@pytest.fixture
def converged(gpu_flow: Flow, tmp_path: Path) -> Harness:
    return clone_harness(gpu_flow.h, tmp_path)


def _curl_urls(h: Harness) -> list[str]:
    return [c["argv"][-1] for c in h.calls() if c["tool"] == "curl"]


def test_a_healthy_stack_is_ready_and_asks_the_published_port(converged: Harness) -> None:
    done = run_steps(converged, *FLOW_ARGS)
    assert done.returncode == 0, done.stderr
    assert _curl_urls(converged)[:2] == [
        "http://127.0.0.1:80/health",
        "http://127.0.0.1:80/health/ready",
    ]
    smoke = [c for c in converged.docker_calls() if "exec" in c and "app" in c]
    assert smoke
    assert smoke[-1][-2:] == ["python", "/app/deploy/smoke/stack_smoke.py"]


def test_a_moved_http_port_is_the_one_probed(converged: Harness) -> None:
    path = env_file(converged)
    path.write_text(path.read_text().replace("HOST_PORT_HTTP=80", "HOST_PORT_HTTP=20080"))
    done = run_steps(converged, *FLOW_ARGS)
    assert done.returncode == 0, done.stderr
    assert _curl_urls(converged)[0] == "http://127.0.0.1:20080/health"


def test_a_502_stops_with_70_and_the_report_says_not_ready(converged: Harness) -> None:
    done = run_steps(converged, *FLOW_ARGS, env={"FAKE_CURL": json.dumps({"health": 502})})
    assert done.returncode == 70, done.stderr
    assert "NOT READY" in done.stdout and "failed: /health" in done.stdout
    assert "install: READY" not in done.stdout


def test_a_failing_smoke_stops_with_71(converged: Harness) -> None:
    done = run_steps(converged, *FLOW_ARGS, env={"FAKE_SMOKE_RC": "1"})
    assert done.returncode == 71, done.stderr
    assert "NOT READY" in done.stdout and "failed: stack_smoke" in done.stdout


# ── AC-7.2: the report ───────────────────────────────────────────────────────


def _lines(run: Flow) -> list[str]:
    return run.proc.stdout.splitlines()


def test_the_report_with_monitoring_and_a_gpu(gpu_flow: Flow) -> None:
    out = gpu_flow.proc.stdout
    assert "install: READY -- AIZZAK 2026.10.99 at https://example.com" in out
    assert "checks      /health 200, /health/ready 200, stack_smoke PASS" in out
    assert re.search(r"size        auto \(16 vCPU, 32 GB\)", out)
    assert "GPU: yes" in out and "monitoring: on" in out and "NO ALERTS" not in out
    assert "aizzak_vault-init" in out and "its contents are not shown" in out
    assert out.split("step 8/8: report")[1].count("chmod 600 .env") == 1


def test_the_report_without_monitoring_or_a_gpu(cpu_flow: Flow) -> None:
    out = cpu_flow.proc.stdout
    assert "size        medium (deploy/sizes/medium.env)" in out
    assert "GPU: no (gpu-preflight exit 10)" in out
    assert "monitoring: off -- NO ALERTS: watch https://example.com/health from outside" in out
    assert out.split("step 8/8: report")[1].count("chmod 600 .env") == 1


def test_the_forced_cpu_is_reported_as_the_choice(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    done = run_function(
        h,
        'prepare "$@"; MODE=cpu; SIZE_DESC=x; PREFLIGHT_RC=0; ROOT="$PWD"; report READY',
        *FLOW_ARGS,
        "--cpu",
    )
    assert "GPU: no (--cpu)" in done.stdout


def test_the_expiry_is_the_minting_time_plus_720_hours(gpu_flow: Flow) -> None:
    text = env_file(gpu_flow.h).read_text(encoding="utf-8")
    minted = re.search(r"# VAULT_SECRET_ID minted (\S+?);", text)
    assert minted is not None
    when = datetime.strptime(minted.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    expected = (when + timedelta(hours=720)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert f"secret_id expires {expected} (720h after minting)" in gpu_flow.proc.stdout
    assert "deploy/vault/bootstrap.sh" in text and "renewal is server-gpu-plan.md 6.5" in text


def test_a_0600_env_is_reported_as_done_with_one_mention(repeat: Repeat) -> None:
    out = repeat.proc.stdout
    assert ".env        mode 0600 (chmod 600 .env: done)" in out
    assert out.count("chmod 600 .env") == 1


def test_the_report_is_plain_text_on_stdout_and_writes_no_file(gpu_flow: Flow) -> None:
    assert not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", gpu_flow.proc.stdout)
    new = set(snapshot(gpu_flow.h.root)) - set(gpu_flow.before)
    assert new == {".env", "deploy/postgres/sizes/auto.conf"}


# ── the entry point ──────────────────────────────────────────────────────────


def test_the_script_can_be_sourced_without_running_main(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    done = run_function(h, "declare -F main prepare run_steps | wc -l")
    assert done.stdout.strip() == "3" and h.docker_calls() == []
    assert (h.root / ".env").exists() is False


def test_install_sh_is_executable_and_starts_with_the_bash_shebang() -> None:
    assert os.access(INSTALL_SH, os.X_OK)
    assert SCRIPT.startswith("#!/usr/bin/env bash\n")


def test_set_preflight_helper_can_make_the_stub_exit_anything(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    set_preflight(h.root, 14)
    done = subprocess.run(["bash", str(h.root / "deploy" / "gpu-preflight.sh")], check=False)
    assert done.returncode == 14
    assert env_tool(h, "get-public", str(h.root / ".env.example"), "LOG_LEVEL").returncode in (0, 1)


# ── env_tool: the .env program, one subcommand at a time ─────────────────────

_IMAGES = (
    "--image-app",
    "anastawahia/aizzak:app-2026.10.99@sha256:" + "a" * 64,
    "--image-embedding",
    "anastawahia/aizzak:embedding-2026.10.99@sha256:" + "b" * 64,
    "--image-cuda",
    "anastawahia/aizzak:embedding-cuda-2026.10.99@sha256:" + "c" * 64,
)


def compose_args(
    *,
    release: str = VERSION,
    mode_name: str = "gpu",
    monitoring: str = "on",
    size: str = "auto",
    domain: str = "example.com",
    firebase: str = "demo-proj",
) -> tuple[str, ...]:
    return (
        "--release", release, "--commit", "a" * 40, "--mode", mode_name, "--monitoring",
        monitoring, "--size", size, "--domain", domain, "--firebase", firebase, *_IMAGES,
    )  # fmt: skip


@pytest.fixture
def tool(tmp_path: Path) -> Harness:
    return make_harness(tmp_path)


def run_compose(h: Harness, path: Path, **options: str) -> subprocess.CompletedProcess[str]:
    return env_tool(h, "compose", str(path), str(h.root / ".env.example"), *compose_args(**options))


@pytest.fixture
def installed(tool: Harness, tmp_path: Path) -> tuple[Harness, Path]:
    """A .env as install.sh leaves it after step 3 and the key (block, no size block)."""
    path = tmp_path / "installed.env"
    shutil.copy2(tool.root / ".env.example", tmp_path / ".env.example")  # size-block reads it
    done = run_compose(tool, path)
    assert done.returncode == 0 and done.stdout.strip() == "created", done.stderr
    return tool, path


def test_compose_created_writes_the_template_with_the_changes_and_an_install_block(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    text = path.read_text(encoding="utf-8")
    template = (h.root / ".env.example").read_text(encoding="utf-8")
    assert text.startswith(template.rstrip("\n").split("\n")[0])
    start = text.index("# >>> aizzak install >>>")
    assert text.endswith("# <<< aizzak install <<<\n")
    body = text[:start].rstrip("\n").splitlines()
    original = template.rstrip("\n").splitlines()
    assert len(body) == len(original)
    changed = {a.split("=")[0] for a, b in zip(body, original, strict=True) if a != b}
    assert changed == set(SECRET_KEYS) | {
        "MINIO_PUBLIC_ENDPOINT", "MINIO_PUBLIC_SECURE", "OAUTH_REDIRECT_BASE_URL",
        "FIREBASE_PROJECT_ID", "VAULT_ROLE_ID",
    }  # fmt: skip
    assert mode(path) == 0o644
    assert list(path.parent.glob(".install-tmp-*")) == []


def test_compose_unchanged_writes_nothing_not_even_the_mtime(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    before = (sha(path), path.stat().st_mtime_ns, mode(path))
    done = run_compose(h, path)
    assert done.returncode == 0 and done.stdout.strip() == "unchanged"
    assert (sha(path), path.stat().st_mtime_ns, mode(path)) == before


def test_compose_adopts_a_hand_made_env_replacing_only_the_template_values(
    tool: Harness, tmp_path: Path
) -> None:
    template = (tool.root / ".env.example").read_text(encoding="utf-8")
    hand = (
        template.replace("HOST_PORT_HTTP=80", "HOST_PORT_HTTP=20080")
        .replace("MINIO_ROOT_USER=change-me-minio", "MINIO_ROOT_USER=operator-chosen-user")
        .replace("GRAFANA_ADMIN_PASSWORD=change-me-grafana", "GRAFANA_ADMIN_PASSWORD=")
        + "COMPOSE_PROJECT_NAME=aizzak-install-proof\n"
    )
    path = tmp_path / "hand.env"
    path.write_text(hand, encoding="utf-8")
    path.chmod(0o640)
    done = run_compose(tool, path)
    assert done.returncode == 0 and done.stdout.strip() == "adopted", done.stderr
    values = parse_env(path.read_text(encoding="utf-8"))
    assert values["HOST_PORT_HTTP"] == "20080"
    assert values["COMPOSE_PROJECT_NAME"] == "aizzak-install-proof"
    assert values["MINIO_ROOT_USER"] == "operator-chosen-user"
    assert re.fullmatch(r"[0-9a-f]{48}", values["GRAFANA_ADMIN_PASSWORD"])
    assert [k for k in SECRET_KEYS if values[k].startswith("change-me")] == []
    assert mode(path) == 0o640, "an existing file keeps its mode"
    assert run_compose(tool, path).stdout.strip() == "unchanged"


@pytest.mark.parametrize(
    "line, key",
    [
        ("COMPOSE_FILE=docker-compose.yml\n", "COMPOSE_FILE"),
        ("COMPOSE_PROFILES=monitoring\n", "COMPOSE_PROFILES"),
        ("APP_IMAGE_TAG=app-1\n", "APP_IMAGE_TAG"),
        ("VAULT_SECRET_ID=planted-secret-value\n", "VAULT_SECRET_ID"),
        ("VAULT_TOKEN=hvs.planted-token-value\n", "VAULT_TOKEN"),
    ],
)
def test_compose_refuses_a_key_the_install_block_owns_or_a_vault_token(
    tool: Harness, tmp_path: Path, line: str, key: str
) -> None:
    template = (tool.root / ".env.example").read_text(encoding="utf-8")
    text = template.replace("VAULT_TOKEN=\n", "") + line
    path = tmp_path / "x.env"
    path.write_text(text, encoding="utf-8")
    done = run_compose(tool, path)
    assert done.returncode == 3, done.stderr
    assert key in done.stderr and "line" in done.stderr
    assert "planted" not in done.stderr, "a value was echoed"
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    "old, new, key",
    [
        ("METRICS_EXPORTER_PASSWORD=change-me-metrics-exporter", "METRICS_EXPORTER_PASSWORD=short",
         "METRICS_EXPORTER_PASSWORD"),
        ("MINIO_PUBLIC_ENDPOINT=localhost:19000", "MINIO_PUBLIC_ENDPOINT=elsewhere.example",
         "MINIO_PUBLIC_ENDPOINT"),
        ("FIREBASE_PROJECT_ID=", "FIREBASE_PROJECT_ID=another-project", "FIREBASE_PROJECT_ID"),
    ],
)  # fmt: skip
def test_compose_refuses_an_operator_value_that_contradicts_the_request(
    tool: Harness, tmp_path: Path, old: str, new: str, key: str
) -> None:
    text = (tool.root / ".env.example").read_text(encoding="utf-8").replace(old, new, 1)
    path = tmp_path / "x.env"
    path.write_text(text, encoding="utf-8")
    done = run_compose(tool, path)
    assert done.returncode == 3 and key in done.stderr
    assert "another-project" not in done.stderr and "elsewhere" not in done.stderr
    assert path.read_text(encoding="utf-8") == text


def _mutate(path: Path, old: str, new: str) -> str:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    changed = text.replace(old, new, 1)
    path.write_text(changed, encoding="utf-8")
    return changed


@pytest.mark.parametrize(
    "mutation, options, fragment",
    [
        (("APP_RW_PASSWORD=", "APP_RW_PASSWORD=change-me-again #"), {}, "APP_RW_PASSWORD"),
        (("OUTBOX_RELAY_PASSWORD=", "OUTBOX_RELAY_PASSWORD= #"), {}, "OUTBOX_RELAY_PASSWORD"),
        (("VAULT_ROLE_ID=", "VAULT_ROLE_ID=aizzak-local-app #"), {}, "VAULT_ROLE_ID"),
        (None, {"release": "2026.10.100"}, "upgrade.sh"),
        (None, {"mode_name": "cpu"}, "COMPOSE_FILE"),
        (None, {"monitoring": "off"}, "COMPOSE_PROFILES"),
        (None, {"domain": "other.com"}, "MINIO_PUBLIC_ENDPOINT"),
        (("APP_IMAGE_TAG=", "APP_IMAGE_TAG=app-other@sha256:" + "d" * 64 + " #"), {},
         "APP_IMAGE_TAG"),
        (("# <<< aizzak install <<<", "OTHER=1\n# <<< aizzak install <<<"), {}, "OTHER"),
        (("# <<< aizzak install <<<", "# <<< aizzak install <<<\nCOMPOSE_FILE=x"), {},
         "COMPOSE_FILE"),
        (("# >>> aizzak install >>>", "# >>> aizzak install >>>\n# >>> aizzak install >>>"), {},
         "malformed"),
    ],
)  # fmt: skip
def test_a_repeat_that_contradicts_the_install_is_refused_and_writes_nothing(
    installed: tuple[Harness, Path],
    mutation: tuple[str, str] | None,
    options: dict[str, str],
    fragment: str,
) -> None:
    h, path = installed
    if mutation is not None:
        old, new = mutation
        text = path.read_text(encoding="utf-8")
        if old.endswith("="):  # blank the whole value of a key
            text = re.sub(rf"(?m)^{re.escape(old)}.*$", new.rstrip(" #"), text, count=1)
            path.write_text(text, encoding="utf-8")
        else:
            _mutate(path, old, new)
    before = (sha(path), path.stat().st_mtime_ns)
    done = run_compose(h, path, **options)
    assert done.returncode == 3, (done.stdout, done.stderr)
    assert fragment in done.stderr
    assert (sha(path), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize(
    "block, size, ok",
    [
        ("auto", "auto", True),
        ("auto", "medium", False),
        ("medium", "medium", True),
        ("medium", "auto", False),
        ("medium", "small", False),
        ("both", "auto", False),
    ],
)
def test_the_size_rules_of_a_repeat(
    installed: tuple[Harness, Path], block: str, size: str, ok: bool
) -> None:
    h, path = installed
    auto = "# >>> aizzak auto-size >>>\n# role: server\nAPP_MEM=1g\n# <<< aizzak auto-size <<<\n"
    named = (
        "# >>> aizzak size >>>\n# size: medium (deploy/sizes/medium.env)\n# <<< aizzak size <<<\n"
    )
    extra = {"auto": auto, "medium": named, "both": auto + named}[block]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(extra)
    before = sha(path)
    done = run_compose(h, path, size=size)
    assert (done.returncode == 0) is ok, done.stderr
    if not ok:
        assert "size" in done.stderr and sha(path) == before


def test_compose_refuses_a_file_that_is_not_utf8_by_line_number(
    tool: Harness, tmp_path: Path
) -> None:
    path = tmp_path / "x.env"
    path.write_bytes(b"A=1\nB=\xff\xfe\n")
    done = run_compose(tool, path)
    assert done.returncode == 3 and "line 2" in done.stderr
    assert path.read_bytes() == b"A=1\nB=\xff\xfe\n"


def test_size_block_writes_the_size_file_repeats_to_the_byte_and_refuses_a_clash(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    first = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert first.returncode == 0 and first.stdout.strip() == "written", first.stderr
    text = path.read_text(encoding="utf-8")
    assert text.count("# >>> aizzak size >>>") == 1
    assert "# size: medium (deploy/sizes/medium.env)" in text
    before = (sha(path), path.stat().st_mtime_ns)
    again = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert again.stdout.strip() == "unchanged"
    assert (sha(path), path.stat().st_mtime_ns) == before

    clash = path.parent / "clash.env"
    clash.write_text("APP_MEM=planted-value\n", encoding="utf-8")
    refused = env_tool(h, "size-block", str(clash), str(size_file), "medium")
    assert refused.returncode == 3 and "APP_MEM" in refused.stderr
    assert "planted-value" not in refused.stderr
    assert clash.read_text(encoding="utf-8") == "APP_MEM=planted-value\n"


def test_size_block_accepts_the_one_key_the_template_shares_and_refuses_an_auto_block(
    tool: Harness, tmp_path: Path
) -> None:
    template = (tool.root / ".env.example").read_text(encoding="utf-8")
    shutil.copy2(tool.root / ".env.example", tmp_path / ".env.example")
    path = tmp_path / ".env"
    path.write_text(template, encoding="utf-8")
    size_file = tool.root / "deploy" / "sizes" / "small.env"
    assert "POSTGRES_CONFIG_FILE=" in template and "POSTGRES_CONFIG_FILE=" in size_file.read_text()
    assert env_tool(tool, "size-block", str(path), str(size_file), "small").returncode == 0
    path.write_text(template + "# >>> aizzak auto-size >>>\n# <<< aizzak auto-size <<<\n")
    refused = env_tool(tool, "size-block", str(path), str(size_file), "small")
    assert refused.returncode == 3 and "auto-size" in refused.stderr


def test_secret_id_state_has_three_answers_and_prints_nothing(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    assert env_tool(h, "secret-id-state", str(path)).returncode == 1
    text = path.read_text(encoding="utf-8")
    for line, code in (
        (f"VAULT_SECRET_ID={SECRET_ID}\n", 0),
        ("VAULT_SECRET_ID=\n", 1),
        ("VAULT_SECRET_ID=not-a-uuid\n", 2),
        (f"VAULT_SECRET_ID={SECRET_ID}\nVAULT_SECRET_ID={SECRET_ID}\n", 2),
    ):
        path.write_text(
            text.replace("# <<< aizzak install <<<", line + "# <<< aizzak install <<<"),
            encoding="utf-8",
        )
        done = env_tool(h, "secret-id-state", str(path))
        assert done.returncode == code and done.stdout == "" and done.stderr == "", line


def test_set_secret_id_inserts_two_lines_in_the_block_and_keeps_the_mode(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    path.chmod(0o640)
    done = env_tool(
        h,
        "set-secret-id",
        str(path),
        "--ttl-hours",
        "720",
        env={"AIZZAK_INSTALL_SECRET_ID": SECRET_ID},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "" and SECRET_ID not in done.stderr
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "# <<< aizzak install <<<"
    assert lines[-2] == f"VAULT_SECRET_ID={SECRET_ID}"
    assert re.fullmatch(
        r"# VAULT_SECRET_ID minted \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ; secret_id_ttl 720h "
        r"\(deploy/vault/bootstrap\.sh\); renewal is server-gpu-plan\.md 6\.5",
        lines[-3],
    )
    assert mode(path) == 0o640
    assert env_tool(h, "minted-at", str(path)).stdout.strip() == lines[-3].split()[3].rstrip(";")
    again = env_tool(h, "set-secret-id", str(path), env={"AIZZAK_INSTALL_SECRET_ID": SECRET_ID})
    assert again.returncode == 3 and "already set" in again.stderr


def test_set_secret_id_replaces_an_empty_line_and_refuses_bad_input(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    _mutate(path, "# <<< aizzak install <<<", "VAULT_SECRET_ID=\n# <<< aizzak install <<<")
    ok = env_tool(h, "set-secret-id", str(path), env={"AIZZAK_INSTALL_SECRET_ID": SECRET_ID})
    assert ok.returncode == 0, ok.stderr
    assert len(re.findall(r"(?m)^VAULT_SECRET_ID=", path.read_text(encoding="utf-8"))) == 1
    fresh = path.parent / "fresh.env"
    fresh.write_text("A=1\n", encoding="utf-8")
    no_block = env_tool(h, "set-secret-id", str(fresh), env={"AIZZAK_INSTALL_SECRET_ID": SECRET_ID})
    assert no_block.returncode == 3 and "no install block" in no_block.stderr
    bad = env_tool(h, "set-secret-id", str(path), env={"AIZZAK_INSTALL_SECRET_ID": "nope"})
    assert bad.returncode == 3 and "nope" not in bad.stderr
    missing = env_tool(h, "set-secret-id", str(path))
    assert missing.returncode == 3


def test_get_prints_a_value_get_public_refuses_anything_that_looks_secret(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    secret = secret_value_of(path, "APP_RW_PASSWORD")
    assert env_tool(h, "get", str(path), "APP_RW_PASSWORD").stdout.strip() == secret
    assert env_tool(h, "get", str(path), "NO_SUCH_KEY").returncode == 1
    refused = env_tool(h, "get-public", str(path), "APP_RW_PASSWORD")
    assert refused.returncode == 2 and secret not in refused.stdout + refused.stderr
    for key in ("VAULT_SECRET_ID", "MINIO_ROOT_USER", "VAULT_TOKEN"):
        assert env_tool(h, "get-public", str(path), key).returncode == 2, key
    assert env_tool(h, "get-public", str(path), "MINIO_PUBLIC_ENDPOINT").stdout.strip() == (
        "example.com"
    )


def secret_value_of(path: Path, key: str) -> str:
    return parse_env(path.read_text(encoding="utf-8"))[key]


def test_a_failed_write_leaves_env_and_no_temp_file(installed: tuple[Harness, Path]) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    h, path = installed
    before = (sha(path), mode(path))
    path.parent.chmod(0o555)
    try:
        done = env_tool(h, "size-block", str(path), str(h.root / "deploy/sizes/small.env"), "small")
    finally:
        path.parent.chmod(0o755)
    assert done.returncode == 4 and "Traceback" not in done.stderr
    assert (sha(path), mode(path)) == before
    assert list(path.parent.glob(".install-tmp-*")) == []


def test_env_tool_refuses_an_unknown_subcommand(tool: Harness) -> None:
    done = env_tool(tool, "format-disk")
    assert done.returncode == 2
