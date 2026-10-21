"""`deploy/release/install.sh`, driven with a fake `docker` (install-bundle US-3 ... US-7).

Three full runs are shared by many assertions (module-scoped fixtures); everything else is a short
run that stops where it is being tested, or a single shell function sourced out of the script. A
test machine is not root, so `prepare` and `run_steps` are called after `source` instead of
`main`, and `require_root`, `require_trusted` and `lock_bundle` are tested on their own.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

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
    compose_model,
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
) -> Flow:
    h = make_harness(tmp, preflight_rc=preflight_rc)
    before = snapshot(h.root)
    proc = run_steps(h, *FLOW_ARGS, *extra, env=env)
    return Flow(h, proc, before, h.docker_calls())


# Steps 1 to 5 replaced by nothing, for a test that is about the boot (step 6) or the checks
# (step 7): it starts from a tree whose .env and size block were written for real by `staged`.
_FROM_BOOT = (
    "POLL_S=0.01 BOOT_DEADLINE_S=20 HEALTH_DEADLINE_S=0\n"
    "step_checks_host() { MODE=gpu; PREFLIGHT_RC=0; }\n"
    "step_kernel() { :; }; step_env() { :; }; step_budget() { :; }\n"
    "step_size() { SIZE_DESC=staged; }; step_pull() { :; }; step_verify_images() { :; }"
)


def boot_run(
    h: Harness, *, env: dict[str, str] | None = None, deadline: int = 20
) -> subprocess.CompletedProcess[str]:
    return run_steps(
        h,
        *FLOW_ARGS,
        env={"FAKE_ALL_IMAGES": "1", **(env or {})},
        overrides=_FROM_BOOT.replace("BOOT_DEADLINE_S=20", f"BOOT_DEADLINE_S={deadline}"),
    )


def _stage(tmp: Path) -> Harness:
    """A tree after steps 3 and 4 and before any container: .env written (GPU, no monitoring, so
    fewer services to start), size block in place."""
    h = make_harness(tmp)
    done = run_function(
        h, 'prepare "$@"; MODE=gpu; step_env >/dev/null; step_size >/dev/null', *FLOW_ARGS,
        "--no-monitoring",
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return h


def _gpu(tmp: Path) -> Flow:
    """The base run: a GPU server, size auto, monitoring on, umask 022, nothing pulled yet."""
    return flow(tmp)


def _cpu(tmp: Path) -> Flow:
    """A CPU server (gpu-preflight exits 10), a named size, monitoring off."""
    return flow(
        tmp, "--size", "medium", "--no-monitoring", preflight_rc=10, env={"FAKE_ALL_IMAGES": "1"}
    )


BASE_FLAGS = ("--no-monitoring",)


def _cpu_auto(tmp: Path) -> Flow:
    """A CPU server, size auto, no monitoring: the cheapest converged stack, the base of the
    repeats and of the check tests."""
    return flow(tmp, *BASE_FLAGS, preflight_rc=10, env={"FAKE_ALL_IMAGES": "1"})


@dataclass
class Repeat:
    h: Harness
    first_secrets: dict[str, str]
    env_sha: str
    env_mode: int
    env_text: str
    mark: int
    proc: subprocess.CompletedProcess[str]


def _again(base: Flow, tmp: Path, *, edit: bool, flags: tuple[str, ...] = BASE_FLAGS) -> Repeat:
    """Install a second time on a copy of the converged stack. With `edit`, the operator has
    changed LOG_LEVEL and run chmod 600 first (AC-4.6); without it, nothing is touched (AC-7.3)."""
    h = clone_harness(base.h, tmp)
    path = env_file(h)
    if edit:
        text = path.read_text(encoding="utf-8")
        assert "LOG_LEVEL=" in text
        path.write_text(re.sub(r"(?m)^LOG_LEVEL=.*$", "LOG_LEVEL=DEBUG", text), encoding="utf-8")
        path.chmod(0o600)
    secrets_before, sha_before = secret_values(h), sha(path)
    mark = len(h.calls())
    proc = run_steps(h, *FLOW_ARGS, *flags, env={"FAKE_ALL_IMAGES": "1"})
    return Repeat(
        h, secrets_before, sha_before, mode(path), path.read_text(encoding="utf-8"), mark, proc
    )


@dataclass
class Out:
    """A finished scenario: the machine it ran on and what each run printed."""

    h: Harness
    procs: list[subprocess.CompletedProcess[str]]
    scratch: Path | None = None

    @property
    def proc(self) -> subprocess.CompletedProcess[str]:
        return self.procs[-1]


def _boot_out(
    staged: Harness,
    tmp: Path,
    *,
    env: dict[str, str] | None = None,
    deadline: int = 20,
    edit: Callable[[Harness], None] | None = None,
    reruns: int = 0,
    scratch: bool = False,
) -> Out:
    """Step 6 on a copy of the staged tree (the rest of the install is stubbed out)."""
    h = clone_harness(staged, tmp / "m")
    if edit is not None:
        edit(h)
    where: Path | None = None
    run_env = dict(env or {})
    if scratch:
        where = tmp / "scratch"
        where.mkdir()
        run_env["TMPDIR"] = str(where)
    procs = [boot_run(h, env=run_env, deadline=deadline)]
    procs += [boot_run(h) for _ in range(reruns)]
    return Out(h, procs, where)


def _converged_out(
    base: Flow, tmp: Path, *, env: dict[str, str] | None = None, port: str | None = None
) -> Out:
    h = clone_harness(base.h, tmp / "m")
    if port:
        path = env_file(h)
        path.write_text(path.read_text().replace("HOST_PORT_HTTP=80", f"HOST_PORT_HTTP={port}"))
    return Out(h, [boot_run(h, env=env)])


def _refused_out(base: Flow, tmp: Path, args: list[str], gpu: bool) -> Out:
    h = clone_harness(base.h, tmp / "m")
    if gpu:
        set_preflight(h.root, 0)
    return Out(h, [run_steps(h, *args)])


def _step_out(staged: Harness, tmp: Path, code: str, env: dict[str, str]) -> Out:
    h = clone_harness(staged, tmp / "m")
    return Out(h, [run_function(h, code, *FLOW_ARGS, env=env)])


def _size_out(
    tmp: Path,
    *flags: str,
    auto_rc: int | None = None,
    budget_rc: int | None = None,
    env: dict[str, str] | None = None,
) -> Out:
    h = make_harness(tmp / "m")
    log = tmp / "stubs.log"
    if auto_rc is not None:
        stub_tool(h.root, "deploy/auto_size.py", auto_rc, log)
    if budget_rc is not None:
        stub_tool(h.root, "deploy/resource-budget.sh", budget_rc, log)
    return Out(h, [run_steps(h, *FLOW_ARGS, *flags, env=env)], log)


def _damage_key(h: Harness) -> None:
    path = env_file(h)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("# <<< aizzak install <<<", "VAULT_SECRET_ID=junk\n# <<< aizzak install <<<")
    )


_OTHER_DOMAIN = ["--domain", "other.com", "--firebase-project", "demo-proj", *BASE_FLAGS]
_OTHER_FIREBASE = ["--domain", "example.com", "--firebase-project", "other-proj", *BASE_FLAGS]
# the base is a CPU install without monitoring: a GPU, or monitoring, is another install
_REFUSED = {
    "MINIO_PUBLIC_ENDPOINT": (_OTHER_DOMAIN, False),
    "FIREBASE_PROJECT_ID": (_OTHER_FIREBASE, False),
    "size": ([*FLOW_ARGS, *BASE_FLAGS, "--size", "medium"], False),
    "COMPOSE_FILE": ([*FLOW_ARGS, *BASE_FLAGS], True),
    "COMPOSE_PROFILES": ([*FLOW_ARGS, "--cpu"], False),
}
_CI_COMPOSE = "docker-compose.yml:docker-compose.test.yml:docker-compose.build.yml"
_PROBES = (
    'prepare "$@"; MODE=gpu; step_pull >/dev/null; step_verify_images',
    'prepare "$@"; MODE=gpu; step_pull >/dev/null',
)


def _jobs(staged_run: Future[Harness], base_run: Future[Flow]) -> dict[str, Callable[[Path], Out]]:
    """Every short scenario the tests below look at. They are separate processes with their own
    directories, so they run side by side (NFR-10); a test only reads what its scenario printed."""

    def staged() -> Harness:
        return staged_run.result()  # waits for the tree this scenario starts from

    def base() -> Flow:
        return base_run.result()

    jobs: dict[str, Callable[[Path], Out]] = {
        "mint-empty": lambda t: _boot_out(staged(), t, env={"FAKE_MINT_OUTPUT": ""}),
        "mint-junk": lambda t: _boot_out(staged(), t, env={"FAKE_MINT_OUTPUT": "not-a-uuid"}),
        "mint-fails": lambda t: _boot_out(staged(), t, env={"FAKE_MINT_RC": "1"}, scratch=True),
        "damaged-key": lambda t: _boot_out(staged(), t, edit=_damage_key),
        "migrate-once": lambda t: _boot_out(
            staged(), t, env={"FAKE_FAULTS": "migrate:exit=1,times=1"}, reruns=1
        ),
        "migrate-always": lambda t: _boot_out(
            staged(), t, env={"FAKE_FAULTS": "migrate:exit=1,times=9"}
        ),
        "ollama": lambda t: _boot_out(
            staged(), t, env={"FAKE_FAULTS": "ollama-pull:exit=1,times=9"}
        ),
        "metrics": lambda t: _boot_out(staged(), t, env={"FAKE_PG_RC": "1"}),
        "ready": lambda t: _converged_out(base(), t, port="20080"),
        "502": lambda t: _converged_out(base(), t, env={"FAKE_CURL": "health=502"}),
        "smoke": lambda t: _converged_out(base(), t, env={"FAKE_SMOKE_RC": "1"}),
        "bad-digest": lambda t: _step_out(
            staged(), t, _PROBES[0], {"FAKE_ALL_IMAGES": "1", "FAKE_BAD_DIGEST_FOR": "aizzak"}
        ),
        "pull-fails": lambda t: _step_out(staged(), t, _PROBES[1], {"FAKE_PULL_FAIL": "postgres"}),
        "gen-1": lambda t: _size_out(t, auto_rc=1),
        "gen-2": lambda t: _size_out(t, auto_rc=2),
        "gen-3": lambda t: _size_out(t, auto_rc=3),
        "budget-monitored": lambda t: _size_out(t, "--size", "medium", budget_rc=1),
        "budget-ci-env": lambda t: _size_out(
            t,
            "--size",
            "medium",
            budget_rc=1,
            env={"COMPOSE_FILE": _CI_COMPOSE, "SIZES_DIR": "/evil"},
        ),
    }
    for key, (args, gpu) in _REFUSED.items():
        jobs[f"refused-{key}"] = lambda t, a=args, g=gpu: _refused_out(base(), t, a, g)
    return jobs


@dataclass
class Runs:
    gpu: Flow
    cpu: Flow
    base: Flow
    staged: Harness
    repeat: Repeat
    plain: Repeat
    ran: dict[str, Out]


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> Runs:
    """Every run the tests below look at, started together. They are separate processes in
    separate directories, so they overlap (NFR-10); the tests only read the results."""
    new = tmp_path_factory.mktemp
    compose_model()  # cached once, before any thread asks
    with ThreadPoolExecutor(max_workers=min(10, os.cpu_count() or 4)) as pool:
        gpu_job = pool.submit(_gpu, new("gpu"))
        cpu_job = pool.submit(_cpu, new("cpu"))
        base_job = pool.submit(_cpu_auto, new("base"))
        staged_job = pool.submit(_stage, new("staged"))
        jobs = _jobs(staged_job, base_job)
        dirs = {name: new(name.replace(":", "_")) for name in jobs}
        futures = {name: pool.submit(job, dirs[name]) for name, job in jobs.items()}
        base = base_job.result()
        repeat_job = pool.submit(_again, base, new("repeat"), edit=True)
        plain_job = pool.submit(_again, base, new("plain"), edit=False)
        return Runs(
            gpu_job.result(),
            cpu_job.result(),
            base,
            staged_job.result(),
            repeat_job.result(),
            plain_job.result(),
            {name: future.result() for name, future in futures.items()},
        )


@pytest.fixture
def ran(runs: Runs) -> dict[str, Out]:
    return runs.ran


@pytest.fixture
def gpu_flow(runs: Runs) -> Flow:
    return runs.gpu


@pytest.fixture
def cpu_flow(runs: Runs) -> Flow:
    return runs.cpu


@pytest.fixture
def base_flow(runs: Runs) -> Flow:
    return runs.base


@pytest.fixture
def repeat(runs: Runs) -> Repeat:
    return runs.repeat


@pytest.fixture
def plain_repeat(runs: Runs) -> Repeat:
    return runs.plain


# ── the pieces of the script that need no run ───────────────────────────────


def test_the_script_is_valid_bash() -> None:
    assert subprocess.run(["bash", "-n", str(INSTALL_SH)], check=False).returncode == 0


def test_help_prints_the_usage_and_exits_0_without_root(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    before = snapshot(h.root)
    done = run_entry(h, "--help")
    assert done.returncode == 0, done.stderr
    for word in ("--domain", "--firebase-project", "--size", "--cpu", "--no-monitoring"):
        assert word in done.stdout
    assert snapshot(h.root) == before
    assert h.calls() == []


def _table(h: Harness, function: str, values: list[str]) -> list[bool]:
    """Call `function VALUE` for every value in ONE shell: yes/no per value."""
    code = f'for v in "$@"; do if {function} "$v"; then echo yes; else echo no; fi; done'
    done = run_function(h, 'ROOT="$PWD"; ' + code, *values)
    assert done.returncode == 0, done.stderr
    return [line == "yes" for line in done.stdout.splitlines()]


_DOMAINS = {
    "example.com": True,
    "xn--mgbh0fb.example": True,
    "a.b.example.org": True,
    "https://example.com": False,
    "example.com/x": False,
    "example.com:8443": False,
    "": False,
    "\u0645\u062b\u0627\u0644.com": False,
    "localhost": False,
    "1.2.3.4": False,
    "-bad.example.com": False,
    "example.com.": False,
    "a..b.com": False,
    "x" * 64 + ".com": False,
}
_FIREBASE = {
    "aizzak-agent": True, "demo-proj": True, "demo proj": False, "a/b": False, "": False,
    "Demo-proj": False, "x": False,
}  # fmt: skip
_SIZES = {"auto": True, "small": True, "medium": True, "large": True, "huge": False, "../x": False,
          "": False}  # fmt: skip


@pytest.mark.parametrize(
    "function, table",
    [("valid_domain", _DOMAINS), ("valid_firebase", _FIREBASE), ("valid_size", _SIZES)],
)
def test_the_validators_accept_and_refuse_exactly_these(
    tmp_path: Path, function: str, table: dict[str, bool]
) -> None:
    got = _table(make_harness(tmp_path), function, list(table))
    assert dict(zip(table, got, strict=True)) == table


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


def _preflight_modes(tmp: Path, cases: list[tuple[int, int]]) -> list[tuple[str, int]]:
    """For each (gpu-preflight exit code, 1 if --cpu): the mode step 1 picks and its exit code.
    One shell, one subshell per case: only the stub and FORCE_CPU change between them."""
    h = make_harness(tmp)
    code = (
        'prepare "$@" >/dev/null\n'
        "while read -r rc cpu; do\n"
        '  printf "#!/bin/sh\\nexit %s\\n" "$rc" >deploy/gpu-preflight.sh\n'
        "  status=0\n"
        '  ( FORCE_CPU=$cpu; step_checks_host >/dev/null 2>&1; echo "$MODE" ) 2>/dev/null \\\n'
        "    || status=$?\n"
        '  echo "rc=$status"\n'
        'done <<<"$CASES"'
    )
    lines = "\n".join(f"{rc} {cpu}" for rc, cpu in cases)
    done = run_function(h, code, *FLOW_ARGS, env={"CASES": lines})
    assert done.returncode == 0, done.stderr
    out = iter(done.stdout.splitlines())
    results = []
    for line in out:
        if line.startswith("rc="):
            results.append(("", int(line[3:])))
        else:
            results.append((line, int(next(out)[3:])))
    return results


def test_preflight_codes_choose_the_mode_or_stop_the_install(tmp_path: Path) -> None:
    # (exit code of gpu-preflight.sh, --cpu given) -> (mode chosen, exit code of step 1)
    expected = {
        (0, 0): ("gpu", 0), (0, 1): ("cpu", 0), (10, 0): ("cpu", 0), (10, 1): ("cpu", 0),
        (12, 1): ("cpu", 0), (12, 0): ("", 10), (11, 0): ("", 10), (13, 0): ("", 10),
        (14, 0): ("", 10), (11, 1): ("", 10), (13, 1): ("", 10), (14, 1): ("", 10),
        (2, 0): ("", 10),
    }  # fmt: skip
    got = _preflight_modes(tmp_path, list(expected))
    assert dict(zip(expected, got, strict=True)) == expected


@pytest.mark.parametrize("rc", [11, 12, 13, 14, 2])
def test_a_stopped_preflight_writes_nothing(tmp_path: Path, rc: int) -> None:
    h = make_harness(tmp_path, preflight_rc=rc)
    before = snapshot(h.root)
    done = run_steps(h, *FLOW_ARGS)
    assert done.returncode == 10, (done.stdout, done.stderr)
    assert f"exit {rc}" in done.stderr
    assert snapshot(h.root) == before
    assert not (h.root / ".env").exists()
    assert [c for c in h.docker_calls() if c[:1] == ["compose"]] == []
    assert list(h.sysctl_d.iterdir()) == []


# ── AC-3.2 ───────────────────────────────────────────────────────────────────


def _fake_python(h: Harness, rc: int) -> None:
    """A python3 whose self-check (`-c`) exits `rc`, as install.sh defines it: 3 = older than 3.10,
    4 = no PyYAML. Every other use is the real interpreter."""
    link = h.fake / "bin" / "python3"
    link.unlink()
    link.write_text(
        f'#!/bin/sh\ncase "$*" in *" -c "*) exit {rc};; esac\nexec {sys.executable} "$@"\n',
        encoding="utf-8",
    )
    link.chmod(0o755)


@pytest.mark.parametrize(
    "check_rc, fragment", [(3, "older than 3.10"), (4, "PyYAML"), (9, "self-check")]
)
def test_a_missing_python_or_pyyaml_stops_with_11_before_the_root_check(
    tmp_path: Path, check_rc: int, fragment: str
) -> None:
    h = make_harness(tmp_path)
    _fake_python(h, check_rc)
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
        done = env_tool(
            h,
            "compose",
            str(tmp_path / "new.env"),
            str(tmp_path / "t.env"),
            *compose_args(),
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


@pytest.mark.parametrize("fragment", list(_REFUSED))
def test_changing_an_input_after_install_is_refused_naming_the_key(
    ran: dict[str, Out], base_flow: Flow, fragment: str
) -> None:
    out = ran[f"refused-{fragment}"]
    assert out.proc.returncode == 30, (out.proc.stdout[-600:], out.proc.stderr)
    assert fragment in out.proc.stderr
    assert (sha(env_file(out.h)), mode(env_file(out.h))) == (
        sha(env_file(base_flow.h)),
        mode(env_file(base_flow.h)),
    )


# ── AC-5.1 / AC-5.2 / AC-5.3: the size ───────────────────────────────────────


def _stub_calls(log: Path | None) -> list[dict[str, object]]:
    if log is None or not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.parametrize("rc, code", [(1, 40), (2, 41), (3, 41)])
def test_the_generator_exit_codes_map_to_40_and_41_before_any_pull(
    ran: dict[str, Out], rc: int, code: int
) -> None:
    out = ran[f"gen-{rc}"]
    assert out.proc.returncode == code, (out.proc.stdout[-800:], out.proc.stderr)
    assert [c for c in out.h.docker_calls() if c[:1] == ["pull"]] == []
    call = _stub_calls(out.scratch)[0]
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


def test_with_monitoring_the_budget_gets_no_without_flag(ran: dict[str, Out]) -> None:
    out = ran["budget-monitored"]
    assert out.proc.returncode == 40
    assert _stub_calls(out.scratch)[0]["argv"] == ["--size", "medium"]


def test_a_shell_exported_compose_file_does_not_reach_the_budget_and_stops_before_pull(
    ran: dict[str, Out],
) -> None:
    out = ran["budget-ci-env"]
    assert out.proc.returncode == 40, out.proc.stderr
    env = _stub_calls(out.scratch)[0]["env"]
    assert isinstance(env, dict)
    assert env["COMPOSE_FILE"] == "docker-compose.yml"
    assert env["GPU_COMPOSE_FILE"] == "docker-compose.gpu.yml"
    assert env["SIZES_DIR"] == "deploy/sizes"
    assert "COMPOSE_FILE" in out.proc.stdout
    assert [c for c in out.h.docker_calls() if c[:1] == ["pull"]] == []


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


@pytest.mark.parametrize("scenario", ["mint-empty", "mint-junk"])
def test_a_key_that_is_not_a_uuid_stops_with_61_before_any_carrier(
    ran: dict[str, Out], scenario: str
) -> None:
    out = ran[scenario]
    calls = out.h.docker_calls()
    assert out.proc.returncode == 61, (out.proc.stdout[-800:], out.proc.stderr)
    assert "not a UUID" in out.proc.stderr
    block = env_file(out.h).read_text(encoding="utf-8").split("# >>> aizzak install >>>")[1]
    assert "VAULT_SECRET_ID=" not in block, "a key that is not a UUID was written"
    assert [c for c in calls if "up" in c and "minio-bootstrap" in c] == []
    assert [c for c in calls if "up" in c and "migrate" in c] == []


def test_a_mint_failure_stops_with_61_hides_the_token_and_leaves_no_file(
    ran: dict[str, Out],
) -> None:
    out = ran["mint-fails"]
    assert out.proc.returncode == 61
    assert VAULT_TOKEN not in out.proc.stdout + out.proc.stderr
    assert "<redacted>" in out.proc.stderr
    assert out.scratch is not None and list(out.scratch.iterdir()) == [], (
        "the file holding Vault's stderr was left behind"
    )


def test_a_damaged_key_in_env_is_never_replaced(ran: dict[str, Out], runs: Runs) -> None:
    out = ran["damaged-key"]
    # QA (round 1): not one byte changed from the damaged file the run started with
    started_with = (
        env_file(runs.staged)
        .read_text(encoding="utf-8")
        .replace("# <<< aizzak install <<<", "VAULT_SECRET_ID=junk\n# <<< aizzak install <<<")
    )
    assert env_file(out.h).read_text(encoding="utf-8") == started_with
    assert out.proc.returncode == 61 and "damaged" in out.proc.stderr
    assert "VAULT_SECRET_ID=junk" in env_file(out.h).read_text(encoding="utf-8")
    assert len(re.findall(r"(?m)^VAULT_SECRET_ID=", env_file(out.h).read_text())) == 1
    assert not [c for c in out.h.docker_calls() if "exec" in c and "vault" in c]


def test_a_digest_that_is_not_the_one_in_version_stops_with_51_and_no_up(
    ran: dict[str, Out],
) -> None:
    out = ran["bad-digest"]
    assert out.proc.returncode == 51, (out.proc.stdout[-800:], out.proc.stderr)
    assert "anastawahia/aizzak@sha256:" in out.proc.stderr
    assert digest_of("app") in out.proc.stderr and "f" * 64 in out.proc.stderr
    assert [c for c in out.h.docker_calls() if "up" in c] == []


def test_a_failed_pull_stops_with_50(ran: dict[str, Out]) -> None:
    out = ran["pull-fails"]
    assert out.proc.returncode == 50, out.proc.stderr
    assert "postgres" in out.proc.stderr
    assert [c for c in out.h.docker_calls() if "up" in c] == []


def test_only_missing_images_are_pulled_and_never_a_compose_level_pull(
    gpu_flow: Flow, tmp_path: Path
) -> None:
    assert not [c for c in gpu_flow.calls if c[:2] == ["compose", "pull"]]
    pulled = [c[1] for c in gpu_flow.calls if c[:1] == ["pull"]]
    assert pulled and len(pulled) == len(set(pulled))
    release = [p for p in pulled if p.startswith("anastawahia/aizzak@sha256:")]
    assert len(release) == 2  # GPU mode: app + embedding-cuda, never the CPU embedding image
    # the same step on the stack that was just pulled: everything is there, so nothing is pulled
    again = clone_harness(gpu_flow.h, tmp_path)
    done = run_function(again, 'prepare "$@"; MODE=gpu; step_pull >/dev/null', *FLOW_ARGS)
    assert done.returncode == 0, done.stderr
    assert not [c for c in again.docker_calls() if c[:1] == ["pull"]]
    assert [c for c in again.docker_calls() if c[:2] == ["image", "inspect"]]


def test_a_migrate_failure_is_reported_by_name_and_the_rerun_resumes(
    ran: dict[str, Out], runs: Runs
) -> None:
    out = ran["migrate-once"]
    first, second = out.procs
    assert first.returncode == 60, (first.stdout[-600:], first.stderr)
    assert "migrate" in first.stderr and "docker compose logs migrate" in first.stderr
    assert second.returncode == 0, (second.stdout[-800:], second.stderr)
    mints = [c for c in out.h.docker_calls() if "exec" in c and "vault" in c]
    assert len(mints) == 1, "the second run minted a second key"
    # QA (round 1): the two runs generated no password of their own
    assert secret_values(out.h) == secret_values(runs.staged)


def test_a_service_that_never_becomes_healthy_is_named(tmp_path: Path) -> None:
    """`wait_for` on a service whose container runs but never turns healthy, with the Docker side
    replaced by two lines: it gives up after its own limit and names the service."""
    code = (
        'ROOT="$PWD"; BOOT_START=$SECONDS; BOOT_DEADLINE_S=20; POLL_S=0.05\n'
        "STATE_HASH[app]=abc\n"
        "stack_state() { STATE_INSPECT='app|abc|running|unhealthy|0|always'; }\n"
        "wait_for app 0"
    )
    done = run_function(make_harness(tmp_path), code)
    assert done.returncode == 60, (done.stdout, done.stderr)
    assert "boot step app: not ready after 0s" in done.stderr
    assert "docker compose logs app" in done.stderr


def test_an_ollama_pull_failure_stops_the_install_and_says_what_chat_would_do(
    ran: dict[str, Out],
) -> None:
    proc = ran["ollama"].proc
    assert proc.returncode == 60, (proc.stdout[-600:], proc.stderr)
    assert "ollama-pull" in proc.stderr
    assert "chat would answer 502" in proc.stderr and "re-run sudo ./install.sh" in proc.stderr


def test_the_metrics_exporter_step_failing_stops_with_60(ran: dict[str, Out]) -> None:
    proc = ran["metrics"].proc
    assert proc.returncode == 60 and "metrics_exporter" in proc.stderr


# ── AC-7.1: the checks ───────────────────────────────────────────────────────


def _curl_urls(h: Harness) -> list[str]:
    return [c["argv"][-1] for c in h.calls() if c["tool"] == "curl"]


def test_a_healthy_stack_is_ready_and_probes_the_port_compose_published(
    ran: dict[str, Out],
) -> None:
    out = ran["ready"]
    assert out.proc.returncode == 0, out.proc.stderr
    assert "install: READY" in out.proc.stdout
    assert _curl_urls(out.h)[:2] == [
        "http://127.0.0.1:20080/health",
        "http://127.0.0.1:20080/health/ready",
    ]
    smoke = [c for c in out.h.docker_calls() if "exec" in c and "app" in c]
    assert smoke
    assert smoke[-1][-2:] == ["python", "/app/deploy/smoke/stack_smoke.py"]


def test_a_502_stops_with_70_and_the_report_says_not_ready(ran: dict[str, Out]) -> None:
    proc = ran["502"].proc
    assert proc.returncode == 70, proc.stderr
    assert "NOT READY" in proc.stdout and "failed: /health" in proc.stdout
    assert "install: READY" not in proc.stdout


def test_a_failing_smoke_stops_with_71(ran: dict[str, Out]) -> None:
    proc = ran["smoke"].proc
    assert proc.returncode == 71, proc.stderr
    assert "NOT READY" in proc.stdout and "failed: stack_smoke" in proc.stdout


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


# ── QA additions (install-bundle test-plan.md) ───────────────────────────────


@pytest.mark.parametrize(
    "scenario, code",
    [("migrate-always", 60), ("metrics", 60), ("mint-fails", 61)],
)
def test_no_secret_is_printed_or_passed_on_a_failure_path(
    ran: dict[str, Out], scenario: str, code: int
) -> None:
    """AC-4.2 on the paths where the script dies: the 13 passwords, the Vault token and the minted
    key must be absent from the output, every argument and every variable a call received."""
    out = ran[scenario]
    assert out.proc.returncode == code, (out.proc.stdout[-600:], out.proc.stderr)
    seen = everything_visible(out.h, out.proc)
    for key, value in secret_values(out.h).items():
        assert value not in seen, key
    assert VAULT_TOKEN not in seen and SECRET_ID not in seen


def test_a_crlf_env_still_counts_as_an_installed_server(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    assert _step_env(h, "cpu").returncode == 0
    path = env_file(h)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    done = run_function(
        h, 'prepare "$@"; step_checks_host >/dev/null; echo "FIRST=$FIRST_INSTALL"', *FLOW_ARGS
    )
    assert "FIRST=0" in done.stdout, done.stdout


def test_a_0600_env_is_never_rewritten_through_a_more_open_temp_file(
    tool: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SR-2 / AC-SR2.1: owner-only at creation, at fchmod (empty) and at fsync, fchmod once."""
    path = tmp_path / "hand.env"
    path.write_text((tool.root / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    path.chmod(0o600)
    calls = _watch_temp(monkeypatch)
    done = run_compose(tool, path)
    assert done.returncode == 0 and done.stdout.strip() == "adopted", done.stderr
    _assert_owner_only_throughout(calls)
    assert [c.args for c in calls if c.name == "fchmod"] == [(0o600,)]
    assert mode(path) == 0o600


def test_a_stale_temp_file_from_a_killed_run_is_removed_by_the_next_write(
    installed: tuple[Harness, Path],
) -> None:
    """QA-3: a `.install-tmp-<pid>` left by `kill -9` would keep a readable copy of the secrets."""
    h, path = installed
    stale = path.parent / ".install-tmp-12345"
    stale.write_text("SECRETS\n", encoding="utf-8")
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert done.returncode == 0, done.stderr
    assert not stale.exists()
    assert list(path.parent.glob(".install-tmp-*")) == []


# ── SR-2: the temp file of atomic_write ──────────────────────────────────────

ACCESS = "system.posix_acl_access"
DEFAULT = "system.posix_acl_default"
USER_OBJ, USER, GROUP_OBJ, MASK, OTHER = 0x01, 0x02, 0x04, 0x10, 0x20
NOBODY = 65534
NOID = 0xFFFFFFFF


class TempCall(NamedTuple):
    """One call on the `.install-tmp-*` file: the state it found (the state it made, for `open`)."""

    name: str
    args: tuple[int, ...]
    perm: int  # st_mode & 0o777
    size: int
    acl: bytes | None  # the access ACL of the temp file, None when it has none
    fd: int


def _acl(*entries: tuple[int, ...]) -> bytes:
    """A POSIX ACL in the kernel's xattr format: (tag, perm[, id]) entries, sorted by tag, id."""
    full = sorted((e[0], e[1], e[2] if len(e) == 3 else NOID) for e in entries)
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, p, i) for t, p, i in full)


def _acl_mask(raw: bytes) -> int:
    for offset in range(4, len(raw), 8):
        tag, perm, _ = struct.unpack_from("<HHI", raw, offset)
        if tag == MASK:
            return perm
    raise AssertionError("an extended ACL without a mask entry")


def _set_acl(path: Path, name: str, raw: bytes) -> None:
    try:
        os.setxattr(path, name, raw)
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            pytest.skip("no POSIX ACLs on this file system")
        raise


def _access_acl(target: int | Path) -> bytes | None:
    try:
        return os.getxattr(target, ACCESS)
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOTSUP):
            return None
        raise


def _watch_temp(monkeypatch: pytest.MonkeyPatch) -> list[TempCall]:
    """Spy on os.open / fchown / setxattr / removexattr / fchmod / fsync for the `.install-tmp-*`
    file only. Taken right AFTER the real call for `open` (the moment of creation) and right
    BEFORE it for the others (the state the call finds). `setxattr` and `removexattr` are
    recorded with no args."""
    calls: list[TempCall] = []
    real_open, real_fchown, real_fchmod, real_fsync = os.open, os.fchown, os.fchmod, os.fsync
    real_setxattr, real_removexattr = os.setxattr, os.removexattr

    def is_temp(fd: object) -> bool:
        if not isinstance(fd, int):
            return False
        try:
            return Path(os.readlink(f"/proc/self/fd/{fd}")).name.startswith(".install-tmp-")
        except OSError:
            return False

    def record(name: str, args: tuple[int, ...], fd: int) -> None:
        st = os.fstat(fd)
        calls.append(TempCall(name, args, st.st_mode & 0o777, st.st_size, _access_acl(fd), fd))

    def spy_open(path: str | os.PathLike[str], flags: int, mode_: int = 0o777, **kw: int) -> int:
        fd = real_open(path, flags, mode_, **kw)
        if Path(path).name.startswith(".install-tmp-"):
            record("open", (flags, mode_), fd)
        return fd

    def spy_fchown(fd: int, uid: int, gid: int) -> None:
        if is_temp(fd):
            record("fchown", (uid, gid), fd)
        real_fchown(fd, uid, gid)

    def spy_setxattr(fd: int, attr: str, value: bytes, flags: int = 0) -> None:
        if is_temp(fd):
            record("setxattr", (), fd)
        real_setxattr(fd, attr, value, flags)

    def spy_removexattr(fd: int, attr: str) -> None:
        if is_temp(fd):
            record("removexattr", (), fd)
        real_removexattr(fd, attr)

    def spy_fchmod(fd: int, mode_: int) -> None:
        if is_temp(fd):
            record("fchmod", (mode_,), fd)
            assert (os.lstat(os.readlink(f"/proc/self/fd/{fd}")).st_mode & 0o077) == (
                os.fstat(fd).st_mode & 0o077
            )
        real_fchmod(fd, mode_)

    def spy_fsync(fd: int) -> None:
        if is_temp(fd):
            record("fsync", (), fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "fchown", spy_fchown)
    monkeypatch.setattr(os, "setxattr", spy_setxattr)
    monkeypatch.setattr(os, "removexattr", spy_removexattr)
    monkeypatch.setattr(os, "fchmod", spy_fchmod)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    return calls


def _assert_owner_only_throughout(calls: list[TempCall]) -> None:
    """A 0600 target: owner-only at every call, and empty until fchmod."""
    names = [c.name for c in calls]
    assert names.count("open") == 1 and "fchmod" in names and "fsync" in names, names
    for c in calls:
        assert c.perm & 0o077 == 0, f"temp file was {c.perm:04o} at {c.name}"
        if c.name != "fsync":
            assert c.size == 0, f"bytes were written before {c.name}"


def test_set_secret_id_on_a_0600_env_never_opens_a_wider_temp_file(
    installed: tuple[Harness, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    h, path = installed
    path.chmod(0o600)
    calls = _watch_temp(monkeypatch)
    done = env_tool(h, "set-secret-id", str(path), env={"AIZZAK_INSTALL_SECRET_ID": SECRET_ID})
    assert done.returncode == 0, done.stderr
    _assert_owner_only_throughout(calls)
    assert [c.args for c in calls if c.name == "fchmod"] == [(0o600,)]
    assert f"VAULT_SECRET_ID={SECRET_ID}" in path.read_text(encoding="utf-8")
    assert mode(path) == 0o600


def test_a_new_env_temp_file_is_born_with_its_final_mode(
    tool: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-SR2.2: 0666 at open, nothing else: the kernel applies the umask (or the default ACL)."""
    path = tmp_path / "new.env"
    calls = _watch_temp(monkeypatch)
    umasks: list[int] = []
    real_umask = os.umask

    def spy_umask(value: int) -> int:
        umasks.append(value)
        return real_umask(value)

    monkeypatch.setattr(os, "umask", spy_umask)
    done = run_compose(tool, path)
    assert done.returncode == 0 and done.stdout.strip() == "created", done.stderr
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    assert [(c.name, c.args) for c in calls if c.name != "fsync"] == [("open", (flags, 0o666))]
    assert calls[0].perm == 0o644 and calls[0].size == 0
    assert len(umasks) == 2 and umasks[0] == 0o022, "atomic_write must not touch the umask"
    assert mode(path) == 0o644


@pytest.mark.parametrize("umask", ["022", "027", "002", "077", "277"])
def test_a_new_env_takes_0666_minus_the_umask(tool: Harness, tmp_path: Path, umask: str) -> None:
    path = tmp_path / "new.env"
    template = str(tool.root / ".env.example")
    done = run_function(
        tool, 'umask "$1"; shift; env_tool "$@"', umask, "compose", str(path), template,
        *compose_args(),
    )  # fmt: skip
    assert done.returncode == 0 and done.stdout.strip() == "created", done.stderr
    assert mode(path) == 0o666 & ~int(umask, 8)
    assert list(tmp_path.glob(".install-tmp-*")) == []


@pytest.mark.parametrize(
    "default",
    [
        _acl((USER_OBJ, 7), (GROUP_OBJ, 0), (OTHER, 0)),
        _acl((USER_OBJ, 7), (USER, 4, NOBODY), (GROUP_OBJ, 5), (MASK, 5), (OTHER, 0)),
    ],
    ids=["owner-only", "named-user"],
)
def test_a_new_env_gets_what_the_directory_default_acl_gives(
    tool: Harness, tmp_path: Path, default: bytes
) -> None:
    """SR2-1: a new .env is what any new file in the directory is, default ACL included."""
    _set_acl(tmp_path, DEFAULT, default)
    path, ref = tmp_path / "new.env", tmp_path / "ref"
    done = run_compose(tool, path)
    assert done.returncode == 0 and done.stdout.strip() == "created", done.stderr
    ref.touch()
    assert mode(path) == mode(ref)
    assert _access_acl(path) == _access_acl(ref)
    if len(default) == 28:
        assert mode(path) == 0o600


def test_an_existing_env_gets_its_owner_then_its_mode_before_the_first_byte(
    installed: tuple[Harness, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    h, path = installed
    path.chmod(0o640)
    before = path.stat()
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    calls = _watch_temp(monkeypatch)
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert done.returncode == 0, done.stderr
    assert [(c.name, c.args) for c in calls] == [
        ("open", (os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)),
        ("fchown", (before.st_uid, before.st_gid)),
        ("removexattr", ()),
        ("fchmod", (0o640,)),
        ("fsync", ()),
    ]
    assert [c.size for c in calls[:4]] == [0, 0, 0, 0]
    assert mode(path) == 0o640


def test_an_existing_env_keeps_its_access_acl(
    installed: tuple[Harness, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SR2-2 (a): a 0600 .env with `user:65534:r` (seen as 0640) keeps it through a rewrite."""
    h, path = installed
    path.chmod(0o600)
    acl = _acl((USER_OBJ, 6), (USER, 4, NOBODY), (GROUP_OBJ, 0), (MASK, 4), (OTHER, 0))
    _set_acl(path, ACCESS, acl)
    before_acl, before_mode = _access_acl(path), mode(path)
    assert before_acl is not None
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    calls = _watch_temp(monkeypatch)
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert done.returncode == 0, done.stderr
    assert [c.name for c in calls] == ["open", "fchown", "setxattr", "fchmod", "fsync"]
    for c in calls[:3]:
        assert c.perm & 0o077 == 0 and c.size == 0, c
    assert calls[3].acl == before_acl
    assert _access_acl(path) == before_acl and mode(path) == before_mode


def test_an_existing_env_does_not_take_the_directory_default_acl(
    installed: tuple[Harness, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SR2-2 (b): a 0640 .env without an ACL, in a directory with a default ACL."""
    h, path = installed
    path.chmod(0o640)
    default = _acl((USER_OBJ, 7), (USER, 4, NOBODY), (GROUP_OBJ, 5), (MASK, 5), (OTHER, 0))
    _set_acl(path.parent, DEFAULT, default)
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    calls = _watch_temp(monkeypatch)
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert done.returncode == 0, done.stderr
    opened = next(c for c in calls if c.name == "open")
    assert opened.acl is not None and _acl_mask(opened.acl) == 0
    assert next(c for c in calls if c.name == "fchmod").acl is None
    with pytest.raises(OSError) as raised:
        os.getxattr(path, ACCESS)
    assert raised.value.errno == errno.ENODATA
    assert mode(path) == 0o640


def test_an_existing_env_keeps_its_group_through_a_rewrite(
    installed: tuple[Harness, Path],
) -> None:
    h, path = installed
    others = [g for g in os.getgroups() if g != path.stat().st_gid]
    if not others:
        pytest.skip("the user has no second group")
    os.chown(path, -1, others[0])
    path.chmod(0o640)
    before = path.stat()
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert done.returncode == 0, done.stderr
    after = path.stat()
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert mode(path) == 0o640


@pytest.mark.parametrize("euid", [1000, 0])
def test_a_refused_fchown_drops_the_group_bits_unless_root(
    installed: tuple[Harness, Path], monkeypatch: pytest.MonkeyPatch, euid: int
) -> None:
    """AC-SR2.8: nothing is given to the creator's group; root still fails closed."""
    h, path = installed
    path.chmod(0o640)
    digest = sha(path)
    size_file = h.root / "deploy" / "sizes" / "medium.env"

    def refuse(fd: int, uid: int, gid: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "fchown", refuse)
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert list(path.parent.glob(".install-tmp-*")) == []
    if euid:
        assert done.returncode == 0, done.stderr
        assert mode(path) == 0o600
    else:
        assert done.returncode == 4 and "PermissionError while handling .env" in done.stderr
        assert "Traceback" not in done.stderr
        assert sha(path) == digest and mode(path) == 0o640


def test_a_non_root_writer_keeps_the_group_when_only_the_owner_is_refused(
    installed: tuple[Harness, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    h, path = installed
    path.chmod(0o640)
    before = path.stat()
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    seen: list[tuple[int, int]] = []
    real_fchown = os.fchown

    def fake(fd: int, uid: int, gid: int) -> None:
        seen.append((uid, gid))
        if uid != -1:
            raise PermissionError(1, "Operation not permitted")
        real_fchown(fd, uid, gid)

    monkeypatch.setattr(os, "fchown", fake)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert done.returncode == 0, done.stderr
    assert seen == [(before.st_uid, before.st_gid), (-1, before.st_gid)]
    assert mode(path) == 0o640 and path.stat().st_gid == before.st_gid


@pytest.mark.parametrize("new", [False, True], ids=["existing", "new"])
@pytest.mark.parametrize("dangling", [False, True])
def test_a_link_planted_at_the_temp_path_is_refused_and_nothing_is_written_through_it(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dangling: bool,
    new: bool,
) -> None:
    if new:
        h, path = request.getfixturevalue("tool"), tmp_path / "new.env"
    else:
        h, path = request.getfixturevalue("installed")
    victim = tmp_path / "victim"
    if not dangling:
        victim.write_text("VICTIM\n", encoding="utf-8")
    digest, perm = (None, None) if new else (sha(path), mode(path))
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    seen: list[tuple[int, int]] = []
    real_open = os.open

    def plant(p: str | os.PathLike[str], flags: int, mode_: int = 0o777, **kw: int) -> int:
        if Path(p).name.startswith(".install-tmp-"):
            seen.append((flags, mode_))
            os.symlink(victim, p)
        return real_open(p, flags, mode_, **kw)

    monkeypatch.setattr(os, "open", plant)
    if new:
        done = run_compose(h, path)
    else:
        done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    wanted = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    assert len(seen) == 1 and seen[0][0] & wanted == wanted
    assert seen[0][1] == (0o666 if new else 0o600)
    assert done.returncode == 4 and "FileExistsError while handling .env" in done.stderr
    assert "Traceback" not in done.stderr
    if dangling:
        assert not victim.exists()
    else:
        assert victim.read_text(encoding="utf-8") == "VICTIM\n"
    if new:
        assert not path.exists()
    else:
        assert (sha(path), mode(path)) == (digest, perm)


@pytest.mark.parametrize("failing", ["fchown", "fchmod", "removexattr"])
def test_the_temp_file_descriptor_is_closed_when_fchown_or_fchmod_raises(
    installed: tuple[Harness, Path], monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    """QA (SR-2 design §4.8): the fd lives inside the `with`, so a refusal never leaks it."""
    h, path = installed
    path.chmod(0o640)
    digest = sha(path)
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    calls = _watch_temp(monkeypatch)

    def refuse(*_: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, failing, refuse)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    done = env_tool(h, "size-block", str(path), str(size_file), "medium")
    assert done.returncode == 4 and "PermissionError while handling .env" in done.stderr
    opened = [c.fd for c in calls if c.name == "open"]
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])  # EBADF: closed (nothing else in the process reopened it)
    assert sha(path) == digest and mode(path) == 0o640
    assert list(path.parent.glob(".install-tmp-*")) == []


def test_a_symlinked_env_is_rewritten_in_place_and_stays_a_link(
    installed: tuple[Harness, Path], tmp_path: Path
) -> None:
    """realpath behaviour unchanged: the real file is replaced, the link and its mode survive."""
    h, real = installed
    real.chmod(0o640)
    link = tmp_path / "link.env"
    link.symlink_to(real)
    size_file = h.root / "deploy" / "sizes" / "medium.env"
    before = sha(real)
    done = env_tool(h, "size-block", str(link), str(size_file), "medium")
    assert done.returncode == 0, done.stderr
    assert link.is_symlink() and link.resolve() == real.resolve()
    assert sha(real) != before and mode(real) == 0o640
    assert list(tmp_path.glob(".install-tmp-*")) == []
