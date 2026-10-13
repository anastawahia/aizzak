"""``.github/workflows/release.yml`` -- build and push images on a ``v*`` tag.

server-gpu-plan.md 1.4 / prebuilt-images design.md §3.4. The repository is
PUBLIC, so this file is read as text and as parsed YAML for the properties that
keep a registry credential away from a fork, a log or a shell: the trigger, the
only two secrets, how they are passed, and that all release logic is delegated
to ``deploy/release/build-push.sh``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"
_CI = _WORKFLOWS / "ci.yml"

_ALLOWED_SECRETS = {"DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"}


def _load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _jobs(path: Path = _RELEASE) -> dict[str, dict[str, Any]]:
    jobs = _load(path)["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return steps


def _run_scripts() -> list[str]:
    return [
        s["run"] for job in _jobs().values() for s in _steps(job) if isinstance(s.get("run"), str)
    ]


# ── AC-4.1 ──────────────────────────────────────────────────────────────────


def test_it_fires_only_on_pushed_v_tags() -> None:
    wf = _load(_RELEASE)
    # YAML 1.1: the bare key `on` parses as the boolean True.
    trigger = wf.get("on", wf.get(True))
    assert trigger == {"push": {"tags": ["v*"]}}


# ── AC-4.2 ──────────────────────────────────────────────────────────────────


def test_registry_credentials_come_only_from_two_secrets_via_env_and_stdin() -> None:
    for job_name, job in _jobs().items():
        for step in _steps(job):
            env = step.get("env") or {}
            env_text = repr(env)
            outside_env = repr({k: v for k, v in step.items() if k != "env"})
            assert "secrets." not in outside_env, f"{job_name}: secret outside env: {step}"
            for name in re.findall(r"secrets\.([A-Za-z0-9_]+)", env_text):
                assert name in _ALLOWED_SECRETS, name
    scripts = "\n".join(_run_scripts())
    assert "secrets." not in scripts
    login = [ln for ln in scripts.splitlines() if "docker login" in ln]
    assert login
    for line in login:
        assert "--password-stdin" in line
        assert not re.search(r"(\s-p\s|--password\s)", line)


# ── AC-4.3 ──────────────────────────────────────────────────────────────────


def test_publishing_is_delegated_to_the_script() -> None:
    scripts = _run_scripts()
    publishing = [
        s for s in scripts if "deploy/release/build-push.sh" in s and "--preflight" not in s
    ]
    assert len(publishing) == 1
    assert '"${GITHUB_REF_NAME#v}"' in publishing[0]
    joined = "\n".join(scripts)
    for forbidden in ("docker build", "docker push", "buildx build"):
        assert forbidden not in joined


def test_the_tag_is_read_from_the_environment_not_interpolated() -> None:
    for script in _run_scripts():
        assert "${{" not in script, "an expression inside run: is a script-injection path"


# ── AC-4.4 ──────────────────────────────────────────────────────────────────


def test_publish_needs_quality_and_every_job_is_time_boxed() -> None:
    jobs = _jobs()
    publish = jobs["publish"]
    assert {"quality", "preflight"} <= set(publish["needs"])
    assert publish["environment"] == "release"
    for name, job in jobs.items():
        assert job["timeout-minutes"] <= 60, name


# ── drift guard · bash · permissions ────────────────────────────────────────


def _signature(job: dict[str, Any]) -> list[tuple[Any, Any, Any]]:
    return [(s.get("uses"), s.get("run"), s.get("with")) for s in _steps(job)]


def test_release_quality_is_ci_quality() -> None:
    assert _signature(_jobs()["quality"]) == _signature(_jobs(_CI)["quality"])


def test_every_run_step_is_valid_bash() -> None:
    for script in _run_scripts():
        syntax = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert syntax.returncode == 0, syntax.stderr


def test_permissions_are_read_only_and_releases_never_cancel() -> None:
    wf = _load(_RELEASE)
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert not any("permissions" in job for job in _jobs().values())
