"""deploy/nginx/gen-cert.sh: presence, never content (cloudflare-tls FR-7, AC-2.2, AC-2.3, AC-2.6).

The script runs under `sh` with AIZZAK_CERT_DIR pointing at a tmp dir and, unless a
test says otherwise, a fake `openssl` first on PATH that records its argv.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
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


def _run(
    cert_dir: Path,
    *,
    fake_openssl: Literal["record", "fail-after-key", None],
    bin_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "AIZZAK_CERT_DIR": str(cert_dir)}
    if fake_openssl is not None:
        fake_dir = bin_dir or cert_dir.parent / "fakebin"
        fake_dir.mkdir(exist_ok=True)
        fake = fake_dir / "openssl"
        fake.write_text(_RECORD if fake_openssl == "record" else _FAIL_AFTER_KEY)
        fake.chmod(0o755)
        env["PATH"] = f"{fake_dir}{os.pathsep}{env['PATH']}"
        env["FAKE_OPENSSL_LOG"] = str(cert_dir.parent / "openssl.log")
    return subprocess.run(
        ["sh", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=60
    )


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
