"""deploy/nginx/gen-cert.sh: presence, never content (cloudflare-tls FR-7, AC-2.2, AC-2.3, AC-2.6).

The script runs under `sh` with AIZZAK_CERT_DIR pointing at a tmp dir and, unless a
test says otherwise, a fake `openssl` first on PATH that records its argv.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Literal

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "deploy" / "nginx" / "gen-cert.sh"

_RECORD = """#!/bin/sh
echo "$@" >> "$FAKE_OPENSSL_LOG"
exit 0
"""
_FAIL_AFTER_KEY = """#!/bin/sh
echo "$@" >> "$FAKE_OPENSSL_LOG"
while [ $# -gt 0 ]; do
    if [ "$1" = "-keyout" ]; then echo partial > "$2"; fi
    shift
done
exit 1
"""
_SLOW = """#!/bin/sh
touch "$FAKE_READY"
sleep 1
exit 0
"""
_PLANT_DURING = """#!/bin/sh
case "${FAKE_PLANT_KIND:-file}" in
    dir) mkdir "$FAKE_PLANT" ;;
    dirlink) mkdir "$FAKE_PLANT.d" && ln -s "$FAKE_PLANT.d" "$FAKE_PLANT" ;;
    *) echo operator > "$FAKE_PLANT" ;;
esac
exit 0
"""
_FAILING_LN = """#!/bin/sh
echo "ln: failed to create hard link: Operation not permitted" >&2
exit 1
"""


def _run(
    cert_dir: Path,
    *,
    fake_openssl: Literal["record", "fail-after-key", "plant-during", "slow", None],
    bin_dir: Path | None = None,
    plant: str | None = None,
    plant_kind: str = "file",
    failing_ln: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = _env(
        cert_dir, fake_openssl, bin_dir=bin_dir, plant=plant, plant_kind=plant_kind,
        failing_ln=failing_ln,
    )  # fmt: skip
    return subprocess.run(
        ["sh", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=60
    )


def _env(
    cert_dir: Path,
    fake_openssl: str | None,
    *,
    bin_dir: Path | None = None,
    plant: str | None = None,
    plant_kind: str = "file",
    failing_ln: bool = False,
) -> dict[str, str]:
    env = {"PATH": os.environ["PATH"], "AIZZAK_CERT_DIR": str(cert_dir)}
    if fake_openssl is not None:
        fake_dir = bin_dir or cert_dir.parent / "fakebin"
        fake_dir.mkdir(exist_ok=True)
        fake = fake_dir / "openssl"
        fake.write_text(
            {
                "record": _RECORD,
                "fail-after-key": _FAIL_AFTER_KEY,
                "plant-during": _PLANT_DURING,
                "slow": _SLOW,
            }[fake_openssl]
        )
        fake.chmod(0o755)
        if failing_ln:
            (fake_dir / "ln").write_text(_FAILING_LN)
            (fake_dir / "ln").chmod(0o755)
        env["PATH"] = f"{fake_dir}{os.pathsep}{env['PATH']}"
        env["FAKE_OPENSSL_LOG"] = str(cert_dir.parent / "openssl.log")
        if plant is not None:
            env["FAKE_PLANT"] = str(cert_dir / plant)
            env["FAKE_PLANT_KIND"] = plant_kind
        env["FAKE_READY"] = str(cert_dir.parent / "ready")
    return env


def _snapshot(d: Path) -> dict[str, tuple[str, int]]:
    return {
        p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(d.iterdir())
        if p.is_file() and not p.is_symlink()
    }


def _certs(tmp_path: Path) -> Path:
    d = tmp_path / "certs"
    d.mkdir()
    return d


def test_both_files_present_are_left_alone_and_openssl_is_never_called(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    (d / "server.crt").write_text("operator cert")
    (d / "server.key").write_text("operator key")
    before = _snapshot(d)
    res = _run(d, fake_openssl="record")
    assert res.returncode == 0
    assert "already present" in res.stdout
    assert _snapshot(d) == before
    assert sorted(p.name for p in d.iterdir()) == ["server.crt", "server.key"]
    assert not (tmp_path / "openssl.log").exists()


@pytest.mark.parametrize("alone", ["server.crt", "server.key"])
def test_one_file_alone_stops_naming_its_partner_and_writes_nothing(
    tmp_path: Path, alone: str
) -> None:
    d = _certs(tmp_path)
    (d / alone).write_text("operator material")
    before = _snapshot(d)
    res = _run(d, fake_openssl="record")
    missing = "server.key" if alone == "server.crt" else "server.crt"
    assert res.returncode == 1
    assert alone in res.stderr and missing in res.stderr
    assert "nothing was written" in res.stderr
    assert _snapshot(d) == before
    assert [p.name for p in d.iterdir()] == [alone]
    assert not (tmp_path / "openssl.log").exists()


def test_a_dangling_symlink_counts_as_present(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    (d / "server.crt").write_text("operator cert")
    (d / "server.key").symlink_to(tmp_path / "nowhere")
    res = _run(d, fake_openssl="record")
    assert res.returncode == 0
    assert "already present" in res.stdout
    assert (d / "server.key").is_symlink()
    assert not (tmp_path / "openssl.log").exists()


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs a real openssl")
def test_an_empty_directory_gets_the_development_pair_as_today(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    res = _run(d, fake_openssl=None)
    assert res.returncode == 0, res.stderr
    assert sorted(p.name for p in d.iterdir()) == ["server.crt", "server.key"]
    assert stat.S_IMODE((d / "server.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((d / "server.crt").stat().st_mode) == 0o644
    text = subprocess.run(
        ["openssl", "x509", "-in", str(d / "server.crt"), "-noout", "-text"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "DNS:localhost" in text and "DNS:app" in text and "IP Address:127.0.0.1" in text
    again = _run(d, fake_openssl=None)
    assert again.returncode == 0
    assert "already present" in again.stdout


def test_a_failed_generation_leaves_neither_file(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    res = _run(d, fake_openssl="fail-after-key")
    assert res.returncode != 0
    assert list(d.iterdir()) == []
    # and the next run is not refused as a "half pair"
    retry = _run(d, fake_openssl="record")
    assert "ERROR" not in retry.stderr
    assert "generating" in retry.stdout


def test_a_symlink_planted_at_the_old_fixed_name_is_not_followed(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("precious")
    victim.chmod(0o644)
    for name in (".server.key.new", ".server.crt.new"):
        (d / name).symlink_to(victim)
    # the fake writes "partial" into whatever -keyout names, then fails
    res = _run(d, fake_openssl="fail-after-key")
    assert res.returncode != 0
    assert victim.read_text() == "precious"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    ok = _run(d, fake_openssl="record")
    assert ok.returncode == 0, ok.stderr
    assert victim.read_text() == "precious"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert not (d / "server.key").is_symlink() and not (d / "server.crt").is_symlink()
    assert (d / ".server.key.new").is_symlink() and (d / ".server.crt.new").is_symlink()


@pytest.mark.parametrize("appears", ["server.key", "server.crt"])
def test_a_file_that_appears_while_generating_is_never_overwritten(
    tmp_path: Path, appears: str
) -> None:
    d = _certs(tmp_path)
    res = _run(d, fake_openssl="plant-during", plant=appears)
    assert res.returncode == 1
    assert appears in res.stderr and "ERROR" in res.stderr
    assert (d / appears).read_text() == "operator\n"
    # no temporary file and no half pair of ours is left behind
    assert [p.name for p in d.iterdir()] == [appears]


@pytest.mark.parametrize("kind", ["dir", "dirlink"])
@pytest.mark.parametrize("appears", ["server.key", "server.crt"])
def test_a_directory_that_appears_as_the_target_is_refused_not_filled(
    tmp_path: Path, appears: str, kind: str
) -> None:
    d = _certs(tmp_path)
    res = _run(d, fake_openssl="plant-during", plant=appears, plant_kind=kind)
    assert res.returncode == 1
    assert f"{appears} appeared" in res.stderr
    target = d / appears
    assert target.is_dir()
    # nothing was linked INSIDE the directory, and nothing of ours is left
    inner = target.resolve()
    assert list(inner.iterdir()) == []
    assert not [p for p in d.iterdir() if p.name.startswith(".server.")]
    other = "server.crt" if appears == "server.key" else "server.key"
    assert not (d / other).exists()


def test_a_failing_ln_is_not_blamed_on_a_file_that_appeared(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    res = _run(d, fake_openssl="record", failing_ln=True)
    assert res.returncode == 1
    assert "could not publish server.key" in res.stderr
    assert "appeared" not in res.stderr
    assert list(d.iterdir()) == []


def test_a_termination_signal_removes_the_temporary_files(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    env = _env(d, "slow")
    proc = subprocess.Popen(
        ["sh", str(_SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    ready = tmp_path / "ready"
    deadline = time.monotonic() + 20
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    assert len(list(d.iterdir())) == 2  # the two temporary files exist while openssl runs
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=20)
    assert proc.returncode == 1
    assert list(d.iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_read_only_directory_says_so_and_exits_non_zero(tmp_path: Path) -> None:
    d = _certs(tmp_path)
    d.chmod(0o555)
    try:
        res = _run(d, fake_openssl="record")
    finally:
        d.chmod(0o755)
    assert res.returncode == 1
    assert "ERROR" in res.stderr and "read-only" in res.stderr and str(d) in res.stderr
    assert list(d.iterdir()) == []


def test_the_default_directory_is_the_one_compose_mounts() -> None:
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    volumes = compose["services"]["nginx-certs"]["volumes"]
    assert "./deploy/nginx/certs:/certs" in volumes
    assert 'CERT_DIR="${AIZZAK_CERT_DIR:-/certs}"' in _SCRIPT.read_text(encoding="utf-8")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, check=False
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_no_tls_material_is_tracked_and_the_directory_stays_ignored() -> None:
    tracked = _git("ls-files", "deploy/nginx/certs", "*.key", "*.csr", "*.pem")
    assert tracked.returncode == 0
    assert tracked.stdout.strip() == ""
    for name in ("server.key", "origin.key", "origin.csr", ".server.key.new"):
        ignored = _git("check-ignore", "-q", f"deploy/nginx/certs/{name}")
        assert ignored.returncode == 0, name
    guard = (_REPO_ROOT / "tests" / "unit" / "test_release_bundle_guard.py").read_text(
        encoding="utf-8"
    )
    assert '"deploy/nginx/certs"' in guard
