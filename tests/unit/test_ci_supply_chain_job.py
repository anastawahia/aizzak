"""The ``supply-chain`` job of ``.github/workflows/ci.yml`` (PLT-19, O1/O7).

The job is the CI half of NFR-LIC-07 and NFR-SEC-04: it runs
``deploy/supply-chain/scan.sh`` and keeps the SBOM. It is a top-level job and not
a step of ``quality`` because ``release.yml`` mirrors ``quality`` step for step
(``test_release_workflow.py``). Quality EVALUATION stays out of CI (SRS D-23).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_SCAN = _ROOT / "deploy" / "supply-chain" / "scan.sh"


def _workflow() -> dict[Any, Any]:
    data = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _job() -> dict[str, Any]:
    job = _workflow()["jobs"].get("supply-chain")
    assert isinstance(job, dict), "ci.yml has no `supply-chain` job"
    return job


def _steps() -> list[dict[str, Any]]:
    steps = _job()["steps"]
    assert isinstance(steps, list)
    return steps


def test_the_job_is_a_sibling_of_quality_not_a_step_in_it() -> None:
    jobs = _workflow()["jobs"]
    assert {"quality", "integration", "supply-chain"} <= set(jobs)
    assert not any("supply-chain" in str(s.get("run", "")) for s in jobs["quality"]["steps"])


def test_the_job_has_read_only_permissions_and_a_time_box() -> None:
    job = _job()
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] <= 30


def test_the_job_does_not_wait_on_or_gate_other_jobs() -> None:
    assert "needs" not in _job()
    assert "environment" not in _job()
    for name, other in _workflow()["jobs"].items():
        assert "supply-chain" not in (other.get("needs") or []), name


def test_one_step_runs_the_scan_script() -> None:
    runs = [s["run"] for s in _steps() if "run" in s]
    assert "deploy/supply-chain/scan.sh" in [r.strip() for r in runs]


def test_the_artifact_upload_runs_even_when_the_scan_fails() -> None:
    uploads = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["if"] == "always()"
    paths = upload["with"]["path"]
    assert "build/supply-chain/sbom.cdx.json" in paths
    assert "build/supply-chain/report.md" in paths


def test_the_report_is_published_even_when_the_scan_fails() -> None:
    reports = [s for s in _steps() if "GITHUB_STEP_SUMMARY" in str(s.get("run", ""))]
    assert len(reports) == 1
    assert reports[0]["if"] == "always()"


def test_the_job_never_runs_the_quality_evaluation() -> None:
    for step in _steps():
        text = f"{step.get('run', '')} {step.get('with', '')}"
        assert "tests/eval" not in text
        assert "golden" not in text.lower()
        assert "pytest" not in text


def test_ci_still_runs_on_push_to_master_and_on_every_pull_request() -> None:
    wf = _workflow()
    trigger = wf.get("on", wf.get(True))  # YAML 1.1 reads the bare key `on` as True
    assert trigger["push"]["branches"] == ["master"]
    assert "pull_request" in trigger


def test_every_run_step_of_the_job_is_valid_bash() -> None:
    for step in _steps():
        if "run" not in step:
            continue
        syntax = subprocess.run(
            ["bash", "-n"],
            input=step["run"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert syntax.returncode == 0, syntax.stderr


def test_the_scan_script_is_executable_and_valid_bash() -> None:
    assert os.access(_SCAN, os.X_OK)
    syntax = subprocess.run(
        ["bash", "-n", str(_SCAN)], capture_output=True, text=True, timeout=15, check=False
    )
    assert syntax.returncode == 0, syntax.stderr
    assert _SCAN.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_the_scan_tools_are_pinned_with_double_equals() -> None:
    lines = [
        line.strip()
        for line in (_SCAN.parent / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert {line.split("==")[0] for line in lines} == {"cyclonedx-bom", "pip-audit"}
    assert all("==" in line and ">" not in line for line in lines)
