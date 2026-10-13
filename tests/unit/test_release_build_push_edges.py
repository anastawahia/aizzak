"""Extra edge cases for ``deploy/release/build-push.sh`` (QA, prebuilt-images).

Reuses the fake-``docker``/``gh``/``curl`` harness of ``test_release_build_push``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_release_build_push import _APP_REF, _EMB_REF, _VERSION, _run

# ── --help and option parsing (exit codes are part of the documented contract) ──


def test_help_exits_zero_without_touching_anything(tmp_path: Path) -> None:
    run = _run(tmp_path, "--help")
    assert run.proc.returncode == 0
    assert "usage:" in run.err
    assert run.calls == []


@pytest.mark.parametrize(
    "args",
    [
        ("--bogus", _VERSION),
        (_VERSION, "2026.10.2"),
        ("--digest-file",),
        ("--check-tag", "--list-images"),
    ],
)
def test_bad_options_are_usage_errors_with_no_calls(tmp_path: Path, args: tuple[str, ...]) -> None:
    run = _run(tmp_path, *args)
    assert run.proc.returncode == 2
    assert "usage:" in run.err
    assert run.calls == []


@pytest.mark.parametrize(
    "version", ["2026.13.1", "2026.10.0", "2026.10.01", "1999.10.1", "2026.10.1\n", "2026.10.1;x"]
)
def test_malformed_versions_the_stories_did_not_list_are_refused(
    tmp_path: Path, version: str
) -> None:
    run = _run(tmp_path, version)
    assert run.proc.returncode == 2
    assert run.calls == []


# ── AC-3.2 / AC-3.6 / AC-3.7 corners ────────────────────────────────────────


def test_existing_tag_for_one_prefix_builds_and_pushes_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_DOCKER_EXISTING": _EMB_REF})
    assert run.proc.returncode == 7
    assert run.builds() == [] and run.pushes() == []


def test_first_push_failure_reports_nothing_pushed_and_never_ok(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_DOCKER_FAIL_PUSH": _APP_REF})
    assert run.proc.returncode == 9
    assert _APP_REF in run.err
    assert "already pushed" not in run.err
    assert "release: OK" not in run.out
    assert len(run.pushes()) == 1


def test_digest_file_keeps_only_the_image_pushed_before_a_failure(tmp_path: Path) -> None:
    digest_file = tmp_path / "digests.txt"
    run = _run(
        tmp_path,
        "--digest-file",
        str(digest_file),
        _VERSION,
        env={"FAKE_DOCKER_FAIL_PUSH": _EMB_REF},
    )
    assert run.proc.returncode == 9
    rows = digest_file.read_text().splitlines()
    assert [r.split()[0] for r in rows] == [_APP_REF]


def test_relative_digest_file_is_resolved_against_the_callers_directory(tmp_path: Path) -> None:
    run = _run(tmp_path, "--digest-file", "d.txt", _VERSION, cwd_rel="deploy")
    assert run.proc.returncode == 0, run.err
    assert (run.repo / "deploy" / "d.txt").read_text().count("\n") == 2


def test_workflow_flag_inside_actions_still_requires_the_other_checks(tmp_path: Path) -> None:
    def dirty(repo: Path) -> None:
        (repo / "stray.txt").write_text("x\n")

    run = _run(
        tmp_path,
        "--gates-checked-by-workflow",
        _VERSION,
        env={"GITHUB_ACTIONS": "true"},
        prepare=dirty,
    )
    assert run.proc.returncode == 3
    assert run.builds() == []
