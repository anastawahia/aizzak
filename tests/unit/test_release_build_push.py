"""``deploy/release/build-push.sh`` -- the one release script (server-gpu-plan.md 1.3).

The script is run for real, in a throwaway git repository whose ``origin`` is a
local bare repository, with fake ``docker``, ``gh`` and ``curl`` first on PATH.
Each fake appends every call to ``calls.jsonl``, and what the tests assert is
that sequence of calls: no Docker, no network, no registry. Behaviour of the
fakes is steered by ``FAKE_*`` variables (see ``_FAKE_TOOLS``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "release" / "build-push.sh"
_VERSION = "2026.10.1"
_APP_REF = f"anastawahia/aizzak:app-{_VERSION}"
_EMB_REF = f"anastawahia/aizzak:embedding-{_VERSION}"

_FAKE_TOOLS = r"""
import hashlib, json, os, sys

tool = os.path.basename(sys.argv[0])
a = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps({"tool": tool, "argv": a, "cwd": os.getcwd()}) + "\n")

def has(var, text):
    return any(p and p in text for p in os.environ.get(var, "").split(","))

_JQ = ('if length==0 then "none" else '
       '(sort_by(.createdAt)|last|"\\(.status)/\\(.conclusion)") end')
if tool == "gh":
    want = {"--repo": "anastawahia/aizzak", "--workflow": "ci.yml",
            "--json": "status,conclusion,createdAt", "--jq": _JQ}
    for flag, value in want.items():
        if flag not in a or a[a.index(flag) + 1] != value:
            print("fake gh: unexpected or missing " + flag, file=sys.stderr)
            sys.exit(64)
    print(os.environ.get("FAKE_GH_OUTPUT", "completed/success"))
    sys.exit(int(os.environ.get("FAKE_GH_RC", "0")))

if tool == "curl":
    counter = os.environ["FAKE_CALLS"] + ".curl"
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    codes = os.environ.get("FAKE_CURL_CODES", "404").split(",")
    print(codes[min(n, len(codes) - 1)], end="")
    sys.exit(0)

# docker
if a[:2] == ["buildx", "version"]:
    sys.exit(1 if os.environ.get("FAKE_DOCKER_NO_BUILDX") else 0)
if a[:2] == ["buildx", "inspect"]:
    print("Platforms: " + os.environ.get("FAKE_DOCKER_PLATFORMS", "linux/amd64, linux/arm64"))
    sys.exit(0)
if a[:3] == ["buildx", "imagetools", "inspect"]:
    ref = a[-1]
    if "--format" in a:
        if has("FAKE_DOCKER_FAIL_INSPECT", ref):
            print("ERROR: inspect failed", file=sys.stderr)
            sys.exit(1)
        if has("FAKE_DOCKER_BAD_DIGEST", ref):
            print("sha256:xyz")
            sys.exit(0)
        print("sha256:" + hashlib.sha256(ref.encode()).hexdigest())
        sys.exit(0)
    if ref in os.environ.get("FAKE_DOCKER_EXISTING", "").split(","):
        print("Name: " + ref)
        sys.exit(0)
    print("ERROR: " + ref + ": not found", file=sys.stderr)
    sys.exit(1)
if a[:2] == ["buildx", "build"]:
    tags = [a[i + 1] for i, x in enumerate(a) if x == "--tag"]
    if has("FAKE_DOCKER_FAIL_BUILD", " ".join(tags)):
        print("ERROR: build failed", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if a[:1] == ["push"]:
    if has("FAKE_DOCKER_FAIL_PUSH", a[1]):
        print("denied", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
sys.exit("fake docker: unexpected call %r" % (a,))
"""


@dataclass
class Run:
    proc: subprocess.CompletedProcess[str]
    calls: list[dict[str, object]]
    repo: Path
    head: str

    @property
    def out(self) -> str:
        return self.proc.stdout

    @property
    def err(self) -> str:
        return self.proc.stderr

    def tool_calls(self, tool: str) -> list[list[str]]:
        return [c["argv"] for c in self.calls if c["tool"] == tool]  # type: ignore[misc]

    def builds(self) -> list[list[str]]:
        return [a for a in self.tool_calls("docker") if a[:2] == ["buildx", "build"]]

    def pushes(self) -> list[list[str]]:
        return [a for a in self.tool_calls("docker") if a[:1] == ["push"]]


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "master", str(origin))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    (repo / "deploy" / "release").mkdir(parents=True)
    shutil.copy(_SCRIPT, repo / "deploy" / "release" / "build-push.sh")
    (repo / "deploy" / "release" / "build-push.sh").chmod(0o755)
    (repo / "Dockerfile").write_text("FROM scratch\n")
    (repo / "services" / "embedding").mkdir(parents=True)
    (repo / "services" / "embedding" / "Dockerfile").write_text("FROM scratch\n")
    (repo / ".gitignore").write_text("ignored.txt\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "master")
    return repo


def _run(
    tmp_path: Path,
    *args: str,
    env: dict[str, str] | None = None,
    prepare: object = None,
    cwd_rel: str = "",
) -> Run:
    repo = _make_repo(tmp_path)
    if callable(prepare):
        prepare(repo)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("docker", "gh", "curl"):
        fake = bin_dir / tool
        fake.write_text(f"#!{sys.executable}\n{_FAKE_TOOLS}", encoding="utf-8")
        fake.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    calls.touch()
    base = {
        k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS" and not k.startswith("FAKE_")
    }
    full_env = {
        **base,
        "PATH": f"{bin_dir}{os.pathsep}{base['PATH']}",
        "FAKE_CALLS": str(calls),
        **(env or {}),
    }
    proc = subprocess.run(
        ["bash", str(repo / "deploy" / "release" / "build-push.sh"), *args],
        cwd=repo / cwd_rel,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    logged = [json.loads(line) for line in calls.read_text().splitlines()]
    return Run(proc, logged, repo, _git(repo, "rev-parse", "HEAD"))


# ── AC-2.1 · AC-2.2 · AC-2.3 ────────────────────────────────────────────────


@pytest.mark.parametrize("version", ["2026.10.1", "2026.1.12"])
def test_valid_tags_are_accepted(tmp_path: Path, version: str) -> None:
    run = _run(tmp_path, "--check-tag", version)
    assert run.proc.returncode == 0, run.err
    assert run.calls == []


@pytest.mark.parametrize(
    "version",
    [
        "dev",
        "latest",
        "",
        "v2026.10.1",
        "2026.10",
        "2026.10.1-rc",
        "2026.01.1",
        "2026.10.1 x",
        "app-2026.10.1",
    ],
)
def test_invalid_tags_are_refused_before_any_docker_call(tmp_path: Path, version: str) -> None:
    run = _run(tmp_path, version)
    assert run.proc.returncode == 2
    assert "YYYY.M.N" in run.err
    assert run.calls == []


def test_image_list_has_two_images_and_reserves_cuda_for_2_4(tmp_path: Path) -> None:
    run = _run(tmp_path, "--list-images")
    assert run.proc.returncode == 0
    assert run.out.splitlines() == [
        "app Dockerfile",
        "embedding services/embedding/Dockerfile",
        "embedding-cuda reserved:2.4",
    ]
    assert run.calls == []


# ── AC-3.1 ──────────────────────────────────────────────────────────────────


def _modify_tracked(repo: Path) -> None:
    (repo / "Dockerfile").write_text("FROM scratch\n# changed\n")


def _add_untracked(repo: Path) -> None:
    (repo / "stray.txt").write_text("x\n")


@pytest.mark.parametrize("prepare", [_modify_tracked, _add_untracked])
def test_dirty_tree_is_refused(tmp_path: Path, prepare: object) -> None:
    run = _run(tmp_path, _VERSION, prepare=prepare)
    assert run.proc.returncode == 3
    assert "dirty" in run.err
    assert run.calls == []


def test_ignored_untracked_file_does_not_block(tmp_path: Path) -> None:
    def prepare(repo: Path) -> None:
        (repo / "ignored.txt").write_text("x\n")

    run = _run(tmp_path, _VERSION, prepare=prepare)
    assert run.proc.returncode == 0, run.err


# ── AC-3.2 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("output", "rc"),
    [("completed/failure", "0"), ("in_progress/", "0"), ("none", "0"), ("", "1")],
)
def test_ci_not_green_is_refused(tmp_path: Path, output: str, rc: str) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_GH_OUTPUT": output, "FAKE_GH_RC": rc})
    assert run.proc.returncode == 5
    assert run.builds() == []
    gh = run.tool_calls("gh")[0]
    assert "--commit" in gh and run.head in gh


# ── AC-3.3 ──────────────────────────────────────────────────────────────────


def test_missing_tag_prints_usage(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.proc.returncode == 2
    assert "usage:" in run.err
    assert run.calls == []


def test_bad_tag_prints_usage_before_any_other_check(tmp_path: Path) -> None:
    run = _run(tmp_path, "latest", prepare=_modify_tracked)
    assert run.proc.returncode == 2
    assert "usage:" in run.err
    assert run.calls == []


# ── AC-3.4 · AC-3.5 ─────────────────────────────────────────────────────────


def test_build_calls_are_amd64_with_the_right_dockerfile_context_and_tag(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION)
    assert run.proc.returncode == 0, run.err
    builds = run.builds()
    assert len(builds) == 2
    expected = {
        "Dockerfile": _APP_REF,
        "services/embedding/Dockerfile": _EMB_REF,
    }
    for argv in builds:
        assert argv[argv.index("--platform") + 1] == "linux/amd64"
        for flag in ("--load", "--pull", "--provenance=false", "--sbom=false"):
            assert flag in argv, flag
        assert argv[-1] == "."
        dockerfile = argv[argv.index("--file") + 1]
        assert argv[argv.index("--tag") + 1] == expected.pop(dockerfile)
        labels = {argv[i + 1] for i, x in enumerate(argv) if x == "--label"}
        assert len(labels) == 4
        assert f"org.opencontainers.image.revision={run.head}" in labels
        assert f"org.opencontainers.image.version={_VERSION}" in labels
        assert not [x for x in argv if re.search(r"(:|-)(dev|latest)\b", x)]
    assert not expected
    # D-5: every build precedes the first push.
    docker = [
        a for a in run.tool_calls("docker") if a[:1] == ["push"] or a[:2] == ["buildx", "build"]
    ]
    kinds = [a[0] for a in docker]
    assert kinds == ["buildx", "buildx", "push", "push"]


def test_success_prints_one_digest_line_per_image_and_the_commit(tmp_path: Path) -> None:
    digest_file = tmp_path / "digests.txt"
    run = _run(tmp_path, "--digest-file", str(digest_file), _VERSION)
    assert run.proc.returncode == 0, run.err
    lines = run.out.splitlines()
    assert f"release: commit {run.head}" in lines
    refs = [
        ln
        for ln in lines
        if re.fullmatch(r"anastawahia/aizzak:(app|embedding)-2026\.10\.1@sha256:[0-9a-f]{64}", ln)
    ]
    assert sorted(r.split("-")[0].split(":")[1] for r in refs) == ["app", "embedding"]
    assert lines[-1] == f"release: OK {_VERSION} (2 images)"
    rows = digest_file.read_text().splitlines()
    assert len(rows) == 2
    for row in rows:
        ref, digest, sha = row.split()
        assert ref in (_APP_REF, _EMB_REF)
        assert digest == "sha256:" + hashlib.sha256(ref.encode()).hexdigest()
        assert sha == run.head


# ── AC-3.6 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("existing", [_APP_REF, _EMB_REF])
def test_existing_tag_is_refused(tmp_path: Path, existing: str) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_DOCKER_EXISTING": existing})
    assert run.proc.returncode == 7
    assert existing in run.err
    assert run.builds() == [] and run.pushes() == []


def test_an_older_version_does_not_block_the_next(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        _VERSION,
        env={"FAKE_DOCKER_EXISTING": "anastawahia/aizzak:app-2026.10.0"},
    )
    assert run.proc.returncode == 0, run.err


# ── AC-3.7 ──────────────────────────────────────────────────────────────────


def test_second_push_failure_names_it_and_lists_what_was_pushed(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_DOCKER_FAIL_PUSH": "embedding-"})
    assert run.proc.returncode == 9
    assert _EMB_REF in run.err
    assert re.search(re.escape(_APP_REF) + r"@sha256:[0-9a-f]{64}", run.err)
    assert "ABANDONED" in run.err and "2026.10.2" in run.err
    assert "release: OK" not in run.out


# ── AC-3.8 ──────────────────────────────────────────────────────────────────


def test_script_text_hygiene() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    first_code = next(ln for ln in lines[1:] if ln.strip() and not ln.lstrip().startswith("#"))
    assert first_code == "set -euo pipefail"
    assert ".env" not in text
    assert "docker login" not in text
    assert "--password" not in text
    case_labels = re.findall(r"^\s+([^\s()#][^()]*)\)\s*$", text, flags=re.MULTILINE)
    assert case_labels
    assert not [c for c in case_labels if re.search(r"password|token|secret", c, re.IGNORECASE)]
    assert os.access(_SCRIPT, os.X_OK)


# ── US-3 edges ──────────────────────────────────────────────────────────────


def test_head_not_on_master_is_refused(tmp_path: Path) -> None:
    def prepare(repo: Path) -> None:
        (repo / "local.txt").write_text("x\n")
        _git(repo, "add", "local.txt")
        _git(repo, "commit", "-m", "local only")

    run = _run(tmp_path, _VERSION, prepare=prepare)
    assert run.proc.returncode == 4
    assert run.calls == []


def test_workflow_flag_is_refused_outside_actions(tmp_path: Path) -> None:
    run = _run(tmp_path, "--gates-checked-by-workflow", _VERSION)
    assert run.proc.returncode == 2
    assert run.calls == []


def test_workflow_flag_skips_the_ci_lookup_inside_actions(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        "--gates-checked-by-workflow",
        _VERSION,
        env={"GITHUB_ACTIONS": "true"},
    )
    assert run.proc.returncode == 0, run.err
    assert run.tool_calls("gh") == []


def test_preflight_checks_tag_tree_and_master_only(tmp_path: Path) -> None:
    run = _run(tmp_path, "--preflight", _VERSION)
    assert run.proc.returncode == 0, run.err
    assert run.tool_calls("gh") == [] and run.tool_calls("docker") == []


def test_preflight_refuses_a_bad_tag(tmp_path: Path) -> None:
    run = _run(tmp_path, "--preflight", "latest")
    assert run.proc.returncode == 2
    assert run.calls == []


def test_visibility_is_checked_before_the_first_build(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION)
    order = [c["tool"] if c["tool"] != "docker" else tuple(c["argv"][:2]) for c in run.calls]  # type: ignore[index]
    assert order.index("curl") < order.index(("buildx", "build"))


def test_runs_from_a_subdirectory_with_the_repo_root_as_context(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, cwd_rel="deploy")
    assert run.proc.returncode == 0, run.err
    build_calls = [c for c in run.calls if c["argv"][:2] == ["buildx", "build"]]  # type: ignore[index]
    assert build_calls
    assert {Path(str(c["cwd"])).resolve() for c in build_calls} == {run.repo.resolve()}


def test_missing_buildx_is_refused_before_building(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_DOCKER_NO_BUILDX": "1"})
    assert run.proc.returncode == 6
    assert run.builds() == []


def test_build_failure_pushes_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_DOCKER_FAIL_BUILD": "embedding-"})
    assert run.proc.returncode == 8
    assert run.pushes() == []


def test_public_repository_is_refused_before_building(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_CURL_CODES": "200"})
    assert run.proc.returncode == 7
    assert "PUBLIC" in run.err
    assert run.builds() == []
    assert run.tool_calls("curl")


@pytest.mark.parametrize("code", ["500", "000"])
def test_unverifiable_repository_visibility_is_refused(tmp_path: Path, code: str) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_CURL_CODES": code})
    assert run.proc.returncode == 7
    assert run.builds() == []


def test_repository_turning_public_after_push_fails_loudly(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_CURL_CODES": "404,200"})
    assert run.proc.returncode == 9
    assert "PUBLIC" in run.err
    assert "release: OK" not in run.out


# ── review fixes: once `docker push` succeeded, the tag is recorded ──────────


def _assert_abandoned_with(run: Run, pushed_refs: list[str]) -> None:
    assert run.proc.returncode == 9
    assert "ABANDONED" in run.err and "2026.10.2" in run.err
    assert "release: OK" not in run.out
    for ref in pushed_refs:
        assert ref in run.err


@pytest.mark.parametrize("fault", ["FAKE_DOCKER_FAIL_INSPECT", "FAKE_DOCKER_BAD_DIGEST"])
@pytest.mark.parametrize("which", ["first", "second"])
def test_failure_after_a_successful_push_still_reports_the_tag_as_pushed(
    tmp_path: Path, fault: str, which: str
) -> None:
    ref = _APP_REF if which == "first" else _EMB_REF
    digest_file = tmp_path / "digests.txt"
    run = _run(tmp_path, "--digest-file", str(digest_file), _VERSION, env={fault: ref})
    expected = [ref] if which == "first" else [_APP_REF, ref]
    _assert_abandoned_with(run, expected)
    assert f"pushed {ref}" in run.err
    assert f"{ref}@digest-unknown" in run.err
    rows = [r.split()[0:2] for r in digest_file.read_text().splitlines()]
    assert [ref, "digest-unknown"] in rows
    # the first image failing stops the release before the second is pushed
    assert len(run.pushes()) == (1 if which == "first" else 2)


def test_digest_file_write_failure_aborts_and_never_prints_ok(tmp_path: Path) -> None:
    run = _run(tmp_path, "--digest-file", str(tmp_path / "missing-dir" / "d.txt"), _VERSION)
    _assert_abandoned_with(run, [_APP_REF])
    assert "digest file" in run.err


# ── S-1: after a push only an exact 404 is accepted ─────────────────────────


@pytest.mark.parametrize("code", ["200", "429", "500", "503", "000"])
@pytest.mark.parametrize("after", [1, 2])
def test_visibility_after_push_fails_closed(tmp_path: Path, code: str, after: int) -> None:
    codes = ",".join(["404"] * after + [code])
    run = _run(tmp_path, _VERSION, env={"FAKE_CURL_CODES": codes})
    pushed = [_APP_REF] if after == 1 else [_APP_REF, _EMB_REF]
    _assert_abandoned_with(run, pushed)
    assert (code in run.err) or ("PUBLIC" in run.err)
    assert len(run.pushes()) == after


def test_pre_push_404_note_says_the_repository_must_exist_and_be_private(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION)
    assert run.proc.returncode == 0
    assert "404" in run.err and "PRIVATE" in run.err


def test_pre_push_non_404_message_says_404_also_means_missing(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_CURL_CODES": "429"})
    assert run.proc.returncode == 7
    assert "does not exist" in run.err and "PRIVATE" in run.err


# ── review #6: the amd64 branch ─────────────────────────────────────────────


def test_builder_without_amd64_is_refused(tmp_path: Path) -> None:
    run = _run(tmp_path, _VERSION, env={"FAKE_DOCKER_PLATFORMS": "linux/arm64"})
    assert run.proc.returncode == 6
    assert run.builds() == []
