"""`deploy/release/bundle.sh` builds the release bundle from the tag (install-bundle US-1).

The script is run for real in a throwaway git repository (`support_install.release_repo`) that holds
the listed files and some TRAPS: tracked files that must never ship, untracked secrets lying in the
working directory, and a tracked file edited without a commit. What comes out of the archive is
compared with an independent reference, not with the script's own lists.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.unit.support_install import (
    BUNDLE_SH,
    COMMIT,
    PREFIXES,
    TRACKED_TRAPS,
    UNTRACKED_TRAPS,
    VERSION,
    digest_of,
    digests_file,
    git,
    listed_paths,
    release_repo,
    run_bundle,
)

TAG = f"v{VERSION}"
TOP = f"aizzak-{VERSION}"


@dataclass
class Built:
    repo: Path
    digests: Path
    out: Path
    proc: subprocess.CompletedProcess[str]

    @property
    def archive(self) -> Path:
        return self.out / f"aizzak-{VERSION}.tar.gz"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    base = tmp_path_factory.mktemp("bundle")
    repo = release_repo(base)
    digests = digests_file(repo)
    out = base / "out"
    out.mkdir()
    proc = run_bundle(repo, "--digests", str(digests), "--out", str(out), VERSION)
    return Built(repo, digests, out, proc)


def members(archive: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(archive, "r:gz") as tar:
        return tar.getmembers()


def listing(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


# ── AC-1.1 ───────────────────────────────────────────────────────────────────


def test_a_clean_build_exits_0_and_prints_the_path_sha_and_count(built: Built) -> None:
    assert built.proc.returncode == 0, built.proc.stderr
    assert built.archive.is_file()
    digest = hashlib.sha256(built.archive.read_bytes()).hexdigest()
    assert f"bundle: sha256 {digest}" in built.proc.stdout
    assert str(built.archive) in built.proc.stdout
    files = [m for m in members(built.archive) if m.isfile()]
    assert f"bundle: {len(files)} files" in built.proc.stdout


def test_the_file_list_is_the_listed_paths_plus_install_sh_and_version(built: Built) -> None:
    expected = set(
        git(
            built.repo,
            "ls-tree",
            "-r",
            "--name-only",
            TAG,
            "--",
            *[p.rstrip("/") for p in listed_paths()],
        ).split()
    )
    expected |= {"install.sh", "VERSION"}
    got = {m.name.removeprefix(TOP + "/") for m in members(built.archive) if m.isfile()}
    assert got == {*expected}, sorted(got ^ expected)
    assert len({m.name.split("/")[0] for m in members(built.archive)}) == 1
    assert all(m.name == TOP or m.name.startswith(TOP + "/") for m in members(built.archive))


def test_install_sh_is_executable_and_is_the_one_at_the_tag(built: Built) -> None:
    member = next(m for m in members(built.archive) if m.name == f"{TOP}/install.sh")
    assert member.mode & 0o111 == 0o111
    with tarfile.open(built.archive, "r:gz") as tar:
        extracted = tar.extractfile(member)
        assert extracted is not None
        body = extracted.read()
    at_tag = subprocess.run(
        ["git", "show", f"{TAG}:deploy/release/install.sh"],
        cwd=built.repo,
        capture_output=True,
        check=True,
    ).stdout
    assert body == at_tag
    assert not any(m.name.endswith("upgrade.sh") for m in members(built.archive))


def test_the_archive_has_no_link_no_absolute_path_and_no_dotdot(built: Built) -> None:
    for member in members(built.archive):
        assert not (member.issym() or member.islnk()), member.name
        assert not member.name.startswith("/") and ".." not in member.name.split("/"), member.name
        assert (member.uid, member.gid) == (0, 0), member.name
        assert member.mode & 0o022 == 0, (member.name, oct(member.mode))


# ── AC-1.2 ───────────────────────────────────────────────────────────────────

# An independent statement of FR-3: it is NOT copied from bundle.sh.
_FORBIDDEN = (
    re.compile(r"(^|/)docker-compose\.(build|test|wsl-gpu)\.yml$"),
    re.compile(r"(^|/)(src|tests)/"),
    re.compile(r"(^|/)deploy/(load|nginx/certs)(/|$)"),
    re.compile(r"(^|/)deploy/postgres/(testdb|sizes/auto\.conf)"),
    re.compile(r"(^|/)deploy/backup/results"),
    re.compile(r"(^|/)\.env($|\.(?!example$))"),
    re.compile(r"(^|/)init\.json$"),
    re.compile(r"\.auto-size-tmp-"),
    re.compile(r"__pycache__"),
)


def test_no_trap_and_no_forbidden_path_is_in_the_archive(built: Built) -> None:
    names = [m.name.removeprefix(TOP + "/") for m in members(built.archive)]
    for trap in (*TRACKED_TRAPS, *UNTRACKED_TRAPS):
        assert trap not in names, trap
    assert ".env.example" in names
    bad = [n for n in names if any(rx.search(n) for rx in _FORBIDDEN)]
    assert bad == []


def test_the_forbidden_reference_can_actually_fail() -> None:
    for sample in ("src/app/x.py", ".env", ".env.local", "deploy/load/a", "a/__pycache__/b.pyc"):
        assert any(rx.search(sample) for rx in _FORBIDDEN), sample
    assert not any(rx.search(".env.example") for rx in _FORBIDDEN)


def test_the_content_is_the_tag_not_the_working_tree(built: Built) -> None:
    member = next(m for m in members(built.archive) if m.name == f"{TOP}/docker-compose.yml")
    with tarfile.open(built.archive, "r:gz") as tar:
        extracted = tar.extractfile(member)
        assert extracted is not None
        body = extracted.read().decode("utf-8")
    assert "LOCAL EDIT" not in body
    assert "LOCAL EDIT" in (built.repo / "docker-compose.yml").read_text(encoding="utf-8")


# ── AC-1.3 ───────────────────────────────────────────────────────────────────


def test_version_is_five_lines_with_the_commit_and_three_pinned_images(built: Built) -> None:
    member = next(m for m in members(built.archive) if m.name == f"{TOP}/VERSION")
    with tarfile.open(built.archive, "r:gz") as tar:
        extracted = tar.extractfile(member)
        assert extracted is not None
        text = extracted.read().decode("utf-8")
    lines = text.split("\n")
    assert lines[-1] == "" and len(lines) == 6, text
    assert lines[0] == VERSION
    assert lines[1] == f"commit {git(built.repo, 'rev-parse', TAG + '^{commit}')}"
    for line, prefix in zip(lines[2:5], PREFIXES, strict=True):
        assert re.fullmatch(
            rf"anastawahia/aizzak:{re.escape(prefix)}-{re.escape(VERSION)}@sha256:[0-9a-f]{{64}}",
            line,
        ), line
        assert line.endswith(digest_of(prefix))
    for word in ("dev", "latest", "digest-unknown"):
        assert word not in text


# ── AC-1.4: every refusal writes nothing ─────────────────────────────────────


def _bad_digests(built: Built, tmp: Path, mutate: str) -> Path:
    lines = built.digests.read_text(encoding="utf-8").splitlines()
    if mutate == "missing-line":
        lines = lines[:2]
    elif mutate == "unknown":
        lines[1] = lines[1].replace(digest_of("embedding"), "digest-unknown")
    elif mutate == "short-digest":
        lines[0] = lines[0].replace(digest_of("app"), digest_of("app")[:-1])
    elif mutate == "wrong-commit":
        lines[2] = lines[2].replace(lines[2].split()[2], COMMIT)
    elif mutate == "duplicate":
        lines[1] = lines[0]
    elif mutate == "wrong-version":
        lines[0] = lines[0].replace(f"-{VERSION} ", "-2026.10.98 ")
    elif mutate == "extra-field":
        lines[0] += " extra"
    path = tmp / f"digests-{mutate}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "argv, code",
    [
        (["--digests", "{d}", f"v{VERSION}"], 2),
        (["--digests", "{d}", f"app-{VERSION}"], 2),
        (["--digests", "{d}", ""], 2),
        (["--digests", "{d}"], 2),
        (["--digests", "{d}", "2026.13.1"], 2),
        (["--digests", "{d}", "--bogus", VERSION], 2),
        (["--out", "{o}", VERSION], 2),
        (["--digests", "{d}", "--out", "{o}", VERSION, "2026.10.1"], 2),
        (["--digests", "{d}", "--out", "{o}", "2026.10.98"], 4),
        (["--digests", "/nonexistent/digests.txt", "--out", "{o}", VERSION], 5),
        (["--digests", "{d}", "--out", "/nonexistent/out", VERSION], 6),
    ],
)
def test_each_refusal_has_its_code_and_writes_nothing(
    built: Built, tmp_path: Path, argv: list[str], code: int
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    args = [a.format(d=built.digests, o=out) for a in argv]
    proc = run_bundle(built.repo, *args)
    assert proc.returncode == code, proc.stderr
    assert proc.stderr.startswith("bundle: ERROR") or code == 2
    assert listing(out) == []


@pytest.mark.parametrize(
    "mutate",
    [
        "missing-line",
        "unknown",
        "short-digest",
        "wrong-commit",
        "duplicate",
        "wrong-version",
        "extra-field",
    ],
)
def test_a_bad_digests_file_is_refused_with_5(built: Built, tmp_path: Path, mutate: str) -> None:
    out = tmp_path / "out"
    out.mkdir()
    digests = _bad_digests(built, tmp_path, mutate)
    proc = run_bundle(built.repo, "--digests", str(digests), "--out", str(out), VERSION)
    assert proc.returncode == 5, proc.stderr
    assert listing(out) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_output_directory_is_refused_with_6(built: Built, tmp_path: Path) -> None:
    out = tmp_path / "ro"
    out.mkdir()
    out.chmod(0o555)
    try:
        proc = run_bundle(built.repo, "--digests", str(built.digests), "--out", str(out), VERSION)
    finally:
        out.chmod(0o755)
    assert proc.returncode == 6, proc.stderr
    assert listing(out) == []


def test_an_existing_bundle_is_never_replaced(built: Built, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    existing = out / f"aizzak-{VERSION}.tar.gz"
    existing.write_bytes(b"precious")
    proc = run_bundle(built.repo, "--digests", str(built.digests), "--out", str(out), VERSION)
    assert proc.returncode == 6, proc.stderr
    assert "already exists" in proc.stderr
    assert existing.read_bytes() == b"precious"
    assert listing(out) == [existing.name]


def test_a_second_run_over_the_real_output_is_also_refused(built: Built) -> None:
    before = (listing(built.out), hashlib.sha256(built.archive.read_bytes()).hexdigest())
    proc = run_bundle(built.repo, "--digests", str(built.digests), "--out", str(built.out), VERSION)
    assert proc.returncode == 6
    assert (listing(built.out), hashlib.sha256(built.archive.read_bytes()).hexdigest()) == before


def test_help_exits_0_and_names_every_option(built: Built) -> None:
    proc = run_bundle(built.repo, "--help")
    assert proc.returncode == 0
    for word in ("--digests", "--out", "<VERSION>"):
        assert word in proc.stderr


# ── content refusals (7) and the environment (3) ─────────────────────────────


def _rerun(tmp_path: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    out = tmp_path / "out"
    out.mkdir()
    proc = run_bundle(repo, "--digests", str(digests_file(repo)), "--out", str(out), VERSION)
    assert listing(out) == [], "a refused build wrote into the output directory"
    return proc


def test_a_listed_path_missing_at_the_tag_is_refused_with_7_and_named(tmp_path: Path) -> None:
    repo = release_repo(tmp_path, extra_list="deploy/not-there.conf\n")
    proc = _rerun(tmp_path, repo)
    assert proc.returncode == 7, proc.stderr
    assert "deploy/not-there.conf" in proc.stderr


def test_a_forbidden_path_in_the_list_is_refused_with_7(tmp_path: Path) -> None:
    repo = release_repo(tmp_path, extra_list="src/app/x.py\n")
    proc = _rerun(tmp_path, repo)
    assert proc.returncode == 7, proc.stderr
    assert "src/app/x.py" in proc.stderr


def test_a_symbolic_link_under_a_listed_path_is_refused_with_7(tmp_path: Path) -> None:
    repo = release_repo(tmp_path)
    (repo / "deploy" / "sizes" / "link.env").symlink_to("small.env")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    git(repo, "tag", "-f", TAG)
    proc = _rerun(tmp_path, repo)
    assert proc.returncode == 7, proc.stderr
    assert "link.env" in proc.stderr


def test_a_tag_older_than_the_list_is_refused_with_7(tmp_path: Path) -> None:
    repo = release_repo(tmp_path)
    git(repo, "rm", "-q", "deploy/release/bundle-files.txt")
    git(repo, "commit", "-q", "-m", "no list")
    git(repo, "tag", "-f", TAG)
    proc = _rerun(tmp_path, repo)
    assert proc.returncode == 7, proc.stderr
    assert "older than the bundle" in proc.stderr


def test_a_directory_that_is_not_the_repository_root_is_refused_with_3(tmp_path: Path) -> None:
    script = tmp_path / "deploy" / "release" / "bundle.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(BUNDLE_SH, script)
    digests = tmp_path / "d.txt"
    digests.write_text("x\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(script), "--digests", str(digests), VERSION],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 3, proc.stderr


# ── AC-1.6: the same inputs give the same bytes ──────────────────────────────


def test_two_builds_a_moment_apart_are_byte_identical(built: Built, tmp_path: Path) -> None:
    second = tmp_path / "second"
    second.mkdir()
    # a change in the working tree, and a later clock, must not reach the archive
    (built.repo / "docker-compose.gpu.yml").touch()
    proc = run_bundle(built.repo, "--digests", str(built.digests), "--out", str(second), VERSION)
    assert proc.returncode == 0, proc.stderr
    assert (second / built.archive.name).read_bytes() == built.archive.read_bytes()


def test_the_archive_mtime_is_the_commit_time_not_now(built: Built) -> None:
    epoch = int(git(built.repo, "log", "-1", "--format=%ct", TAG))
    assert {m.mtime for m in members(built.archive)} == {epoch}


# ── the exit-code table in the header ────────────────────────────────────────


def _header_codes(text: str) -> set[int]:
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("# Exit codes"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("set -euo pipefail"))
    codes = set()
    for line in lines[start + 1 : end]:
        match = re.match(r"^#\s+(\d{1,2})\s{2}\S", line)
        if match:
            codes.add(int(match.group(1)))
    return codes


# Every code this module exercises above, and no other.
_EXERCISED = {0, 2, 3, 4, 5, 6, 7}


def test_the_header_table_lists_the_exit_codes_the_tests_exercise() -> None:
    text = BUNDLE_SH.read_text(encoding="utf-8")
    assert _header_codes(text) == _EXERCISED | {1}


def test_the_header_check_fails_in_both_directions() -> None:
    text = BUNDLE_SH.read_text(encoding="utf-8")
    dropped = re.sub(r"^#\s+5\s{2}.*\n", "", text, count=1, flags=re.M)
    assert _header_codes(dropped) != _EXERCISED | {1}
    added = text.replace("#   1  an unexpected", "#   9  invented\n#   1  an unexpected", 1)
    assert _header_codes(added) != _EXERCISED | {1}


# ── QA-4 and QA-5 ────────────────────────────────────────────────────────────


def test_help_works_outside_a_repository(tmp_path: Path) -> None:
    """QA-4: --help prints the usage and exits 0 before any check of the repository."""
    script = tmp_path / "deploy" / "release" / "bundle.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(BUNDLE_SH, script)
    proc = subprocess.run(
        ["bash", str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--digests" in proc.stderr


def test_a_digests_file_with_crlf_line_endings_builds_the_same_bundle(
    built: Built, tmp_path: Path
) -> None:
    """QA-5: a file saved on Windows is read as if with LF, so the archive is byte-identical."""
    crlf = tmp_path / "digests-crlf.txt"
    crlf.write_bytes(built.digests.read_bytes().replace(b"\n", b"\r\n"))
    out = tmp_path / "out"
    out.mkdir()
    proc = run_bundle(built.repo, "--digests", str(crlf), "--out", str(out), VERSION)
    assert proc.returncode == 0, proc.stderr
    assert (out / built.archive.name).read_bytes() == built.archive.read_bytes()
