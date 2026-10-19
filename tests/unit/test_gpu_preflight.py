"""``deploy/gpu-preflight.sh`` -- the GPU server pre-flight (server-gpu-plan.md 2.1).

The script is run for real, with fake ``nvidia-smi`` and ``docker`` first on a
PATH that holds nothing else but the handful of system tools the script itself
needs (so a real driver or a real Docker on the machine running the tests can
never be reached). Every fake appends each call to ``calls.jsonl``; what the
tests assert is the exit code, the output, and that sequence of calls.

Five recording fakes (``apt-get``, ``yum``, ``dnf``, ``sudo`` and ``systemctl``) must never be
called: the script checks, it does not install. ``nvidia-ctk`` is called for ``--version``
alone (SR-2: the toolkit's minimum version) and for nothing else.
``timeout`` is the real one from coreutils. The fakes' behaviour is steered by
``FAKE_*`` variables (see ``_FAKE_TOOLS``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "deploy" / "gpu-preflight.sh"
_DOC = _ROOT / "docs" / "deploy-server.md"

_RUNC_ONLY = '{"io.containerd.runc.v2":{"path":"runc"},"runc":{"path":"runc"}}'
_WITH_NVIDIA = (
    '{"io.containerd.runc.v2":{"path":"runc"},'
    '"nvidia":{"path":"nvidia-container-runtime"},"runc":{"path":"runc"}}'
)
_INSTALLERS = ("apt-get", "yum", "dnf", "sudo", "systemctl")
_SMOKE_IMAGE = "nvidia/cuda:12.4.1-base-ubuntu22.04"
_THIS_HOST_GPU = "NVIDIA GeForce RTX 4060 Laptop GPU, 592.82"
_SYSTEM_TOOLS = ("bash", "cat", "mktemp", "rm", "timeout")

_FAKE_TOOLS = r"""
import json, os, sys, time

tool = os.path.basename(sys.argv[0])
a = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps({"tool": tool, "argv": a}) + "\n")


def env(name, default=""):
    return os.environ.get(name, default)


def fail(message, rc):
    print(message, file=sys.stderr)
    sys.exit(int(rc))


if tool == "nvidia-smi":
    if env("FAKE_SMI_HANG"):
        time.sleep(60)
    if env("FAKE_SMI_RC", "0") != "0":
        fail("NVIDIA-SMI has failed because it couldn't communicate with the driver.",
             env("FAKE_SMI_RC"))
    if a != ["--query-gpu=name,driver_version", "--format=csv,noheader"]:
        fail("fake nvidia-smi: unexpected call %r" % (a,), 64)
    for gpu in env("FAKE_SMI_GPUS", "NVIDIA GeForce RTX 4060 Laptop GPU, 592.82").split(";"):
        print(gpu)
    sys.exit(0)

if tool == "nvidia-ctk":
    if a != ["--version"]:
        fail("fake nvidia-ctk: only --version may be called, got %r" % (a,), 64)
    if env("FAKE_CTK_RC", "0") != "0":
        fail("nvidia-ctk: broken", env("FAKE_CTK_RC"))
    print(env("FAKE_CTK_OUT", "NVIDIA Container Toolkit CLI version 1.17.8\ncommit: 0123abc"))
    sys.exit(0)

if tool != "docker":  # an installer: recorded above, must never be called
    sys.exit(0)

if a[:1] == ["info"]:
    if a[1:] != ["--format", "{{.ServerVersion}}|{{json .Runtimes}}"]:
        fail("fake docker: unexpected info call %r" % (a,), 64)
    if env("FAKE_INFO_RC", "0") != "0":
        fail(env("FAKE_INFO_ERR", "Cannot connect to the Docker daemon"), env("FAKE_INFO_RC"))
    print(env("FAKE_SERVER_VERSION", "29.6.1") + "|" + env("FAKE_RUNTIMES"))
    sys.exit(0)
if a[:3] == ["compose", "version", "--short"]:
    if env("FAKE_COMPOSE_RC", "0") != "0":
        fail("docker: 'compose' is not a docker command.", env("FAKE_COMPOSE_RC"))
    print(env("FAKE_COMPOSE_OUT", "v5.3.1"))
    sys.exit(0)
if a[:1] == ["run"]:
    if env("FAKE_RUN_RC", "0") != "0":
        fail(env("FAKE_RUN_ERR", "docker: Error response from daemon: failed"), env("FAKE_RUN_RC"))
    print(env("FAKE_RUN_OUT", "NVIDIA GeForce RTX 4060 Laptop GPU"))
    sys.exit(0)
sys.exit("fake docker: unexpected call %r" % (a,))
"""


@dataclass
class Run:
    proc: subprocess.CompletedProcess[str]
    calls: list[dict[str, object]]
    seconds: float

    @property
    def rc(self) -> int:
        return self.proc.returncode

    @property
    def out(self) -> str:
        return self.proc.stdout

    @property
    def err(self) -> str:
        return self.proc.stderr

    def argvs(self, tool: str) -> list[list[str]]:
        return [c["argv"] for c in self.calls if c["tool"] == tool]  # type: ignore[misc]

    def docker_runs(self) -> list[list[str]]:
        return [a for a in self.argvs("docker") if a[:1] == ["run"]]

    def ctk_calls(self) -> list[list[str]]:
        return self.argvs("nvidia-ctk")

    def installer_calls(self) -> list[str]:
        return [str(c["tool"]) for c in self.calls if c["tool"] in _INSTALLERS]


def _run(
    tmp_path: Path,
    *args: str,
    tools: tuple[str, ...] = ("nvidia-smi", "docker"),
    ctk: bool = True,
    env: dict[str, str] | None = None,
    script: Path = _SCRIPT,
) -> Run:
    fake_bin = tmp_path / "fake"
    system_bin = tmp_path / "system"
    fake_bin.mkdir(parents=True)
    system_bin.mkdir(parents=True)
    for name in _SYSTEM_TOOLS:
        found = shutil.which(name)
        assert found, f"the test host has no {name}"
        (system_bin / name).symlink_to(found)
    for tool in (*tools, *_INSTALLERS, *(("nvidia-ctk",) if ctk else ())):
        fake = fake_bin / tool
        fake.write_text(f"#!{sys.executable}\n{_FAKE_TOOLS}", encoding="utf-8")
        fake.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    calls.touch()
    full_env = {
        "PATH": f"{fake_bin}{os.pathsep}{system_bin}",
        "FAKE_CALLS": str(calls),
        "FAKE_RUNTIMES": _WITH_NVIDIA,
        **(env or {}),
    }
    started = time.monotonic()
    proc = subprocess.run(
        [str(script), *args],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    seconds = time.monotonic() - started
    logged = [json.loads(line) for line in calls.read_text().splitlines()]
    return Run(proc, logged, seconds)


# name -> (arguments, _run keywords, expected exit code)
_SCENARIOS: dict[str, tuple[tuple[str, ...], dict[str, object], int]] = {
    "driver_missing": ((), {"tools": ("docker",)}, 10),
    "toolkit_missing": ((), {"env": {"FAKE_RUNTIMES": _RUNC_ONLY}}, 12),
    "compose_old": ((), {"env": {"FAKE_COMPOSE_OUT": "v2.19.1"}}, 13),
    "all_ok": ((), {}, 0),
    "all_ok_smoke": (("--smoke",), {}, 0),
}


# ── AC-1.1 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_never_installs_anything(tmp_path: Path, scenario: str) -> None:
    args, kwargs, expected = _SCENARIOS[scenario]
    run = _run(tmp_path, *args, **kwargs)  # type: ignore[arg-type]
    assert run.rc == expected, run.out + run.err
    assert run.installer_calls() == []
    assert all(call == ["--version"] for call in run.ctk_calls()), run.ctk_calls()


def test_script_text_hygiene() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    first_code = next(ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#"))
    assert first_code == "set -euo pipefail"
    body = "\n".join(lines[1:])
    assert ".env" not in text, "the script reads no environment file"
    assert not re.search(r"^\s*set\s+-\S*x", body, re.MULTILINE), "no xtrace: it would echo values"
    assert not re.search(r"\b(env|printenv)\b", body), "no environment dump"
    assert not re.search(r"\.\s+\./|\bsource\b", body), "nothing is sourced"
    assert os.access(_SCRIPT, os.X_OK)
    assert stat.S_IMODE(_SCRIPT.stat().st_mode) & 0o111 == 0o111
    tracked = subprocess.run(
        ["git", "ls-files", "-s", "--", str(_SCRIPT)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    if tracked:
        assert tracked.startswith("100755 "), tracked


def test_install_and_service_commands_appear_only_in_the_advice_text() -> None:
    names = re.compile(r"\b(apt-get|yum|dnf|systemctl|nvidia-ctk|sudo)\b")
    code = [
        ln.strip()
        for ln in _SCRIPT.read_text(encoding="utf-8").splitlines()
        if names.search(ln) and not ln.lstrip().startswith("#")
    ]
    advice = [ln for ln in code if ln.startswith("readonly FIX_ADVICE=")]
    assert len(advice) == 1, code
    # Past the advice, nvidia-ctk appears only to ask its version (SR-2), never to act.
    others = [ln for ln in code if ln not in advice]
    assert others, "the version probe is gone"
    for line in others:
        assert not re.search(r"\b(apt-get|yum|dnf|systemctl|sudo)\b", line), line
        assert "nvidia-ctk" in line and re.search(
            r"command -v nvidia-ctk|nvidia-ctk --version|row ctk FAIL \"nvidia-ctk is not", line
        ), line


# ── AC-1.2 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "label"),
    [
        pytest.param({"tools": ("docker",)}, "missing", id="missing"),
        pytest.param({"env": {"FAKE_SMI_RC": "9"}}, "nonzero", id="nonzero"),
        pytest.param({"env": {"FAKE_SMI_GPUS": ""}}, "empty", id="empty-list"),
    ],
)
def test_no_usable_driver_is_exit_10(tmp_path: Path, kwargs: dict[str, object], label: str) -> None:
    run = _run(tmp_path, "--smoke", **kwargs)  # type: ignore[arg-type]
    assert run.rc == 10, run.out + run.err
    assert "NVIDIA driver" in run.out
    assert "FAIL 10" in run.out
    assert run.docker_runs() == [], "no `docker run` without a GPU, even with --smoke"


# ── AC-1.3 ──────────────────────────────────────────────────────────────────


def test_missing_runtime_is_exit_12_with_the_advice_not_the_action(tmp_path: Path) -> None:
    run = _run(tmp_path, env={"FAKE_RUNTIMES": _RUNC_ONLY})
    assert run.rc == 12, run.out + run.err
    assert "NVIDIA Container Toolkit" in run.out
    assert "nvidia-ctk runtime configure --runtime=docker" in run.out
    assert "restart docker" in run.out
    assert "--smoke" in run.out, "the CDI-host hint"
    assert run.installer_calls() == [], "advice is printed, never run"


def test_a_runtime_named_nvidia_something_is_not_the_nvidia_runtime(tmp_path: Path) -> None:
    only_cdi = '{"nvidia-cdi":{"path":"x"},"runc":{"path":"runc"}}'
    assert _run(tmp_path, env={"FAKE_RUNTIMES": only_cdi}).rc == 12


# ── AC-1.4 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "rc", "expected"),
    [
        ("v2.19.1", "0", 13),
        ("2.19.0", "0", 13),
        ("v2.20.0", "0", 0),
        ("2.29.7", "0", 0),
        ("v5.3.1", "0", 0),
        ("2.29.7-desktop.1", "0", 0),
        ("1.99.99", "0", 13),
        ("2.9.0", "0", 13),
        ("2.100.0", "0", 0),
        ("garbage", "0", 13),
        ("", "0", 13),
        ("v5.3.1", "1", 13),
    ],
)
def test_compose_version_is_compared_semantically(
    tmp_path: Path, answer: str, rc: str, expected: int
) -> None:
    run = _run(tmp_path, env={"FAKE_COMPOSE_OUT": answer, "FAKE_COMPOSE_RC": rc})
    assert run.rc == expected, run.out + run.err


# ── AC-1.5 ──────────────────────────────────────────────────────────────────


def test_all_green_without_smoke_exits_0_and_starts_no_container(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.rc == 0, run.out + run.err
    assert _THIS_HOST_GPU in run.out, "the card and the driver version are printed"
    assert "5.3.1" in run.out, "and the Compose version"
    assert "OK (ready for docker-compose.gpu.yml)" in run.out
    assert run.docker_runs() == []


def test_smoke_runs_exactly_one_pinned_gpu_container(tmp_path: Path) -> None:
    run = _run(tmp_path, "--smoke")
    assert run.rc == 0, run.out + run.err
    runs = run.docker_runs()
    assert len(runs) == 1
    assert runs[0][:6] == ["run", "--rm", "--gpus", "all", _SMOKE_IMAGE, "nvidia-smi"]
    assert runs[0][6:] == ["--query-gpu=name", "--format=csv,noheader"]
    tag = _SMOKE_IMAGE.split(":", 1)[1]
    assert re.match(r"\d+\.\d+\.\d+", tag)
    assert "latest" not in _SMOKE_IMAGE
    assert "NVIDIA GeForce RTX 4060 Laptop GPU (from inside" in run.out


def test_the_smoke_image_in_the_script_is_the_pinned_one() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert f'readonly SMOKE_IMAGE="{_SMOKE_IMAGE}"' in text
    assert "latest" not in text


def test_smoke_failure_is_exit_14_and_relays_dockers_message(tmp_path: Path) -> None:
    message = "docker: Error response from daemon: could not select device driver"
    run = _run(tmp_path, "--smoke", env={"FAKE_RUN_RC": "125", "FAKE_RUN_ERR": message})
    assert run.rc == 14, run.out + run.err
    assert message in run.out
    assert "FAIL 14" in run.out
    assert len(run.docker_runs()) == 1


def test_a_failed_pull_is_also_exit_14(tmp_path: Path) -> None:
    message = "Unable to find image locally\ndocker: error pulling image: no route to host"
    run = _run(tmp_path, "--smoke", env={"FAKE_RUN_RC": "125", "FAKE_RUN_ERR": message})
    assert run.rc == 14
    assert "error pulling image: no route to host" in run.out


# ── AC-1.6 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_no_environment_value_reaches_the_output(tmp_path: Path, scenario: str) -> None:
    canary = f"canary-{uuid.uuid4()}"
    args, kwargs, expected = _SCENARIOS[scenario]
    env = {**kwargs.get("env", {}), "CANARY_SECRET": canary}  # type: ignore[dict-item]
    kwargs = {**kwargs, "env": env}
    run = _run(tmp_path, *args, **kwargs)  # type: ignore[arg-type]
    assert run.rc == expected
    assert canary not in run.out
    assert canary not in run.err


# ── edges of US-1 ───────────────────────────────────────────────────────────


def test_docker_permission_denied_is_exit_11_not_toolkit(tmp_path: Path) -> None:
    denied = (
        "permission denied while trying to connect to the Docker daemon socket "
        "at unix:///var/run/docker.sock"
    )
    run = _run(tmp_path, env={"FAKE_INFO_RC": "1", "FAKE_INFO_ERR": denied})
    assert run.rc == 11, run.out + run.err
    assert "permission denied" in run.out
    assert "has no `nvidia` runtime" not in run.out, "not reported as a missing toolkit"
    assert "fix:" not in run.out


def test_docker_server_down_is_exit_11(tmp_path: Path) -> None:
    run = _run(tmp_path, env={"FAKE_INFO_RC": "1"})
    assert run.rc == 11
    assert "does not answer" in run.out


def test_no_docker_cli_is_exit_11(tmp_path: Path) -> None:
    run = _run(tmp_path, tools=("nvidia-smi",))
    assert run.rc == 11
    assert "docker CLI" in run.out


def test_smoke_evidence_overrides_a_missing_runtime(tmp_path: Path) -> None:
    """D-10: a CDI host has no `nvidia` runtime and a working GPU. It still has to have a
    readable `nvidia-ctk` >= the minimum: the override needs the ctk check to be OK."""
    run = _run(tmp_path, "--smoke", env={"FAKE_RUNTIMES": _RUNC_ONLY})
    assert run.rc == 0, run.out + run.err
    assert len(run.docker_runs()) == 1
    assert "OK (ready for docker-compose.gpu.yml)" in run.out
    assert "FAIL" not in run.out
    assert run.installer_calls() == []


def test_no_runtime_and_no_nvidia_ctk_is_exit_12_even_with_smoke(tmp_path: Path) -> None:
    """Owner decision 2026-10-18: a host without `nvidia-ctk` on PATH (Docker Desktop, say)
    exits 12 on purpose, because the toolkit version cannot be read; --smoke does not help."""
    run = _run(tmp_path, "--smoke", ctk=False, env={"FAKE_RUNTIMES": _RUNC_ONLY})
    assert run.rc == 12, run.out + run.err
    assert run.docker_runs() == []
    assert "nvidia-ctk is not on PATH" in run.out
    assert "on purpose" in run.out
    assert "smoke    SKIP" in run.out
    assert "the live check (--smoke) below decides" not in run.out


def test_a_failing_smoke_with_a_missing_runtime_is_still_exit_12(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        "--smoke",
        env={"FAKE_RUNTIMES": _RUNC_ONLY, "FAKE_RUN_RC": "125", "FAKE_RUN_ERR": "no gpu here"},
    )
    assert run.rc == 12, run.out + run.err
    assert "no gpu here" in run.out
    assert "nvidia-ctk runtime configure --runtime=docker" in run.out


def test_every_gpu_is_printed(tmp_path: Path) -> None:
    gpus = "NVIDIA A100-SXM4-40GB, 550.54.15;NVIDIA L4, 550.54.15;NVIDIA T4, 550.54.15"
    run = _run(tmp_path, env={"FAKE_SMI_GPUS": gpus})
    assert run.rc == 0
    for name in ("NVIDIA A100-SXM4-40GB", "NVIDIA L4", "NVIDIA T4"):
        assert name in run.out


def test_no_gpu_with_an_old_compose_is_exit_13(tmp_path: Path) -> None:
    run = _run(tmp_path, tools=("docker",), env={"FAKE_COMPOSE_OUT": "v2.19.1"})
    assert run.rc == 13, run.out + run.err


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({"tools": ("docker",), "env": {"FAKE_INFO_RC": "1"}}, 11, id="docker>driver"),
        pytest.param(
            {"env": {"FAKE_INFO_RC": "1", "FAKE_COMPOSE_OUT": "v2.19.1"}}, 11, id="docker>compose"
        ),
        pytest.param(
            {"tools": ("docker",), "env": {"FAKE_COMPOSE_RC": "1"}}, 13, id="compose>driver"
        ),
        pytest.param(
            {"env": {"FAKE_RUNTIMES": _RUNC_ONLY, "FAKE_COMPOSE_OUT": "2.1.0"}},
            13,
            id="compose>toolkit",
        ),
        pytest.param(
            {"tools": ("docker",), "env": {"FAKE_RUNTIMES": _RUNC_ONLY}}, 10, id="driver>toolkit"
        ),
    ],
)
def test_the_highest_precedence_failure_decides_the_exit_code(
    tmp_path: Path, kwargs: dict[str, object], expected: int
) -> None:
    run = _run(tmp_path, **kwargs)  # type: ignore[arg-type]
    assert run.rc == expected, run.out + run.err


def test_smoke_is_not_run_when_compose_is_too_old(tmp_path: Path) -> None:
    run = _run(tmp_path, "--smoke", env={"FAKE_COMPOSE_OUT": "v2.19.1"})
    assert run.rc == 13
    assert run.docker_runs() == []


def test_a_hung_driver_probe_is_cut_off_by_the_timeout(tmp_path: Path) -> None:
    """NFR-11: every probe runs under `timeout`. A one-second copy keeps the test quick."""
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "readonly PROBE_TIMEOUT_S=5 " in text
    quick = tmp_path / "gpu-preflight-quick.sh"
    quick.write_text(text.replace("readonly PROBE_TIMEOUT_S=5 ", "readonly PROBE_TIMEOUT_S=1 "))
    quick.chmod(0o755)
    run = _run(tmp_path / "run", script=quick, env={"FAKE_SMI_HANG": "1"})
    assert run.rc == 10, run.out + run.err
    assert "timed out after 1s" in run.out
    assert run.seconds < 20


# ── SR-2: the toolkit's minimum version ───────────────────────────────────────


def _ctk(version_line: str) -> dict[str, str]:
    return {"FAKE_CTK_OUT": version_line}


@pytest.mark.parametrize(
    ("line", "ok"),
    [
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.8", True, id="exactly-the-minimum"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.20.1", True, id="newer-minor"),
        pytest.param("NVIDIA Container Toolkit CLI version 2.0.0", True, id="newer-major"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.10", True, id="numeric-not-text"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.7", False, id="one-patch-below"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.16.2", False, id="CVE-2024-0132"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.9.30", False, id="numeric-minor"),
    ],
)
def test_the_toolkit_version_is_compared_semantically(tmp_path: Path, line: str, ok: bool) -> None:
    run = _run(tmp_path, env=_ctk(line))
    assert run.rc == (0 if ok else 12), run.out + run.err
    assert ("ctk      OK" in run.out) == ok, run.out
    assert run.ctk_calls() == [["--version"]]
    if not ok:
        assert "older than 1.17.8" in run.out
        assert "FAIL 12 (NVIDIA Container Toolkit version" in run.out


def test_an_old_toolkit_is_not_overridden_by_a_passing_smoke(tmp_path: Path) -> None:
    """The smoke container succeeds on a vulnerable toolkit too, so it cannot vouch for it --
    and it is not even started on one."""
    run = _run(tmp_path, "--smoke", env=_ctk("NVIDIA Container Toolkit CLI version 1.16.2"))
    assert run.rc == 12, run.out + run.err
    assert run.docker_runs() == []
    assert "smoke    SKIP" in run.out


def test_a_missing_nvidia_ctk_is_exit_12_not_a_pass(tmp_path: Path) -> None:
    run = _run(tmp_path, ctk=False)
    assert run.rc == 12, run.out + run.err
    assert "nvidia-ctk is not on PATH" in run.out


@pytest.mark.parametrize(
    "env",
    [
        pytest.param(_ctk("nvidia-ctk: something unexpected"), id="no-version-in-the-text"),
        pytest.param(_ctk(""), id="empty"),
        pytest.param({"FAKE_CTK_RC": "3"}, id="nonzero"),
    ],
)
def test_an_unreadable_nvidia_ctk_answer_is_exit_12(tmp_path: Path, env: dict[str, str]) -> None:
    run = _run(tmp_path, env=env)
    assert run.rc == 12, run.out + run.err
    assert "gave no usable answer" in run.out


@pytest.mark.parametrize(
    ("line", "ok"),
    [
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.8-rc.1", False, id="rc-dot"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.8~rc1", False, id="rc-tilde"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.8-beta.2", False, id="beta"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.8-alpha", False, id="alpha"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.8-1", True, id="package-revision"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.17.8-0ubuntu1", True, id="ubuntu"),
        pytest.param("NVIDIA Container Toolkit CLI version 1.18.0-rc.1", True, id="later-rc"),
        pytest.param("version 1.17.8", True, id="short-line"),
        pytest.param("NVIDIA Container Toolkit CLI 1.17.8 1.17.8", False, id="no-version-word"),
        pytest.param(
            "NVIDIA Container Toolkit CLI version 12345678901234567890.1.1", False, id="20-digits"
        ),
        pytest.param(
            "NVIDIA Container Toolkit CLI version 1.17.18446744073709551624", False, id="wrap"
        ),
    ],
)
def test_the_toolkit_version_regex_is_anchored_and_prereleases_are_below(
    tmp_path: Path, line: str, ok: bool
) -> None:
    """SR-13: `1.17.8-rc.1` is not 1.17.8; a distro revision `-1` is; absurd numbers are junk."""
    run = _run(tmp_path, env=_ctk(line))
    assert run.rc == (0 if ok else 12), run.out + run.err


@pytest.mark.parametrize(
    ("env", "code"),
    [
        pytest.param(
            _ctk("NVIDIA Container Toolkit CLI version 1.17.8-rc1\x1b[2J\x1b]0;t\x07\x1b[32mOK"),
            12,
            id="ctk-suffix",
        ),
        pytest.param(
            {"FAKE_SERVER_VERSION": "25.0.4\x1b[2J\x1b]0;t\x07\x1b[32mOK"}, 11, id="engine"
        ),
        pytest.param({"FAKE_SERVER_VERSION": "26.0.0-rc1\x1b[2J"}, 11, id="engine-rc"),
    ],
)
def test_control_sequences_from_tool_output_never_reach_the_terminal(
    tmp_path: Path, env: dict[str, str], code: int
) -> None:
    """SR-17: a FAIL row prints text the tool produced; an ESC in it must come out as `?`."""
    run = _run(tmp_path, env=env)
    assert run.rc == code, run.out + run.err
    assert "\x1b" not in run.out + run.err
    assert "\x07" not in run.out + run.err


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        pytest.param("25.0.4", False, id="CVE-2024-29018"),
        pytest.param("25.0.5", False, id="25-branch-still-below-26"),
        pytest.param("26.0.0", True, id="exactly-the-minimum"),
        pytest.param("26.0.0-rc1", False, id="rc-of-the-minimum"),
        pytest.param("29.0.0-rc.1", True, id="later-rc"),
        pytest.param("29.6.1", True, id="this-host"),
        pytest.param("100.0.0", True, id="numeric-not-text"),
        pytest.param("9.0.0", False, id="numeric-not-text-low"),
        pytest.param("", False, id="unreadable-empty"),
        pytest.param("garbage", False, id="unreadable-text"),
    ],
)
def test_the_docker_engine_minimum_is_exit_11(tmp_path: Path, version: str, ok: bool) -> None:
    """SR-10: CVE-2024-29018 -- DNS egress from `internal` networks before Engine 26.0.0."""
    run = _run(tmp_path, env={"FAKE_SERVER_VERSION": version})
    assert run.rc == (0 if ok else 11), run.out + run.err
    if not ok:
        assert "FAIL 11 (Docker)" in run.out
        assert run.ctk_calls() == [], "the toolkit is not probed on an unusable Docker"


def test_the_engine_minimum_lives_in_one_named_variable() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert len(re.findall(r'^readonly MIN_DOCKER_ENGINE="26\.0\.0"$', text, re.MULTILINE)) == 1
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert code.count("26.0.0") == 1


def test_the_toolkit_version_is_not_probed_without_a_driver_or_docker(tmp_path: Path) -> None:
    run = _run(tmp_path, tools=("docker",), env=_ctk("NVIDIA Container Toolkit CLI version 1.0.0"))
    assert run.rc == 10, run.out + run.err
    assert run.ctk_calls() == []
    assert "ctk      SKIP" in run.out


def test_the_minimum_lives_in_one_named_variable_with_a_review_note() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert len(re.findall(r'^readonly MIN_NVIDIA_CTK="1\.17\.8"$', text, re.MULTILINE)) == 1
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert code.count("1.17.8") == 1, "outside comments the number appears only in the variable"
    assert "TODO(7.3)" in text and "security bulletins" in text


def test_help_and_unknown_arguments(tmp_path: Path) -> None:
    helped = _run(tmp_path / "help", "--help")
    assert helped.rc == 0
    assert "usage: gpu-preflight.sh" in helped.out
    assert "2 bad usage" in helped.out, "O-3: --help names exit 2 too"
    assert helped.calls == []
    bad = _run(tmp_path / "bad", "--install")
    assert bad.rc == 2
    assert "unknown argument" in bad.err
    assert bad.calls == []


# ── AC-1.9 ──────────────────────────────────────────────────────────────────


def _script_exit_codes() -> set[int]:
    lines = _SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("# Exit codes"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("set -euo pipefail"))
    codes = set()
    for line in lines[start + 1 : end]:
        match = re.match(r"^#\s+(\d{1,2})\s{2}\S", line)
        if match:
            codes.add(int(match.group(1)))
    return codes


def test_the_server_doc_lists_the_scripts_exit_codes() -> None:
    doc = _DOC.read_text(encoding="utf-8")
    # §1 only: the install section (§3) has a table of its own, with codes that overlap these
    section = doc.split("\n## 1) ", 1)[1].split("\n## 2) ", 1)[0]
    in_doc = {int(m.group(1)) for m in re.finditer(r"^\|\s*(\d{1,2})\s*\|", section, re.MULTILINE)}
    assert _script_exit_codes() == {0, 2, 10, 11, 12, 13, 14}
    assert in_doc == _script_exit_codes()
    assert "nvidia-ctk runtime configure --runtime=docker" in doc
    assert "systemctl restart docker" in doc
    assert "deploy/gpu-preflight.sh" in doc
    assert doc.startswith('<div dir="rtl">')
    assert doc.rstrip().endswith("</div>")
    assert not re.search(r"dckr_pat_|hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}", doc)


def test_every_security_row_sits_in_one_contiguous_table() -> None:
    """SR-11: a blank line between two rows ends the table, and the rows after it render as a
    paragraph with literal pipes -- in the list the owner reads. Lines are read one by one,
    so only a block-level check notices."""
    lines = _DOC.read_text(encoding="utf-8").splitlines()
    rows = [i for i, ln in enumerate(lines) if ln.startswith("| ث\u2011")]
    assert len(rows) >= 9, "the security list lost rows"
    header = next(
        i for i in range(rows[0], -1, -1) if lines[i].startswith("| #") and "الثغرة" in lines[i]
    )
    for i in range(header, rows[-1] + 1):
        assert lines[i].startswith("|"), (
            f"line {i + 1} breaks the security table: {lines[i][:40]!r}"
        )
