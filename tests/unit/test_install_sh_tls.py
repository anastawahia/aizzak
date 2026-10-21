"""install.sh reports the state of the edge certificate (cloudflare-tls FR-18, AC-5.1..AC-5.3).

Certificates are made by the test, with the host's openssl, in tmp_path; the key file holds a
planted marker, not a key. The report line never changes the exit code.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.support_install import (
    FLOW_ARGS,
    INSTALL_SH,
    Harness,
    make_harness,
    run_function,
    run_steps,
)

_OPENSSL = shutil.which("openssl")
pytestmark = pytest.mark.skipif(_OPENSSL is None, reason="needs the host's openssl")

_MARKER = "PLANTED-KEY-MARKER-0123456789"


def _openssl(*args: str | Path) -> None:
    assert _OPENSSL is not None
    subprocess.run([_OPENSSL, *map(str, args)], check=True, capture_output=True, timeout=60)


def _ca(tmp: Path, subject: str = "/O=AIZZAK Test/CN=AIZZAK Test CA") -> tuple[Path, Path]:
    crt, key = tmp / "ca.crt", tmp / "ca.key"
    _openssl(
        "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
        "-days", "30", "-keyout", key, "-out", crt, "-subj", subject,
    )  # fmt: skip
    return crt, key


def _leaf(tmp: Path, ca: tuple[Path, Path], san: str) -> Path:
    key, csr, crt, ext = tmp / "leaf.key", tmp / "leaf.csr", tmp / "leaf.crt", tmp / "leaf.ext"
    ext.write_text(f"subjectAltName=DNS:{san}\n", encoding="utf-8")
    _openssl(
        "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
        "-keyout", key, "-out", csr, "-subj", f"/CN={san}",
    )  # fmt: skip
    _openssl(
        "x509", "-req", "-in", csr, "-CA", ca[0], "-CAkey", ca[1], "-CAcreateserial",
        "-days", "30", "-out", crt, "-extfile", ext,
    )  # fmt: skip
    return crt


def _self_signed(tmp: Path) -> Path:
    crt = tmp / "dev.crt"
    _openssl(
        "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
        "-days", "30", "-keyout", tmp / "dev.key", "-out", crt,
        "-subj", "/CN=localhost/O=AIZZAK local development",
        "-addext", "subjectAltName=DNS:localhost",
    )  # fmt: skip
    return crt


def _plant(h: Harness, crt: Path | None) -> None:
    d = h.root / "deploy" / "nginx" / "certs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "server.key").write_text(_MARKER, encoding="utf-8")
    if crt is not None:
        shutil.copy(crt, d / "server.crt")


def _fake_openssl(h: Harness, *, expired: bool = False) -> Path:
    """An openssl first on PATH: logs its argv, then runs the real one (optionally moving notAfter
    into the past)."""
    log = h.fake / "openssl-argv.log"
    sed = "| sed 's/^notAfter=.*/notAfter=Jan  1 00:00:00 2020 GMT/'" if expired else ""
    path = h.fake / "bin" / "openssl"
    path.write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\nexec {_OPENSSL} "$@" {sed}\n', encoding="utf-8"
    )
    path.chmod(0o755)
    return log


def _state(h: Harness, *args: str, env: dict[str, str] | None = None) -> str:
    done = run_function(h, 'prepare "$@"; tls_state', *(args or FLOW_ARGS), env=env)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert _clean(done)
    return done.stdout


def _clean(done: subprocess.CompletedProcess[str]) -> bool:
    return _MARKER not in done.stdout and _MARKER not in done.stderr


def test_a_full_run_with_the_development_certificate_reports_it_and_exits_0(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path, preflight_rc=10)
    _plant(h, _self_signed(tmp_path))
    proc = run_steps(h, *FLOW_ARGS, "--cpu", "--no-monitoring", env={"FAKE_ALL_IMAGES": "1"})
    assert proc.returncode == 0, (proc.stdout[-800:], proc.stderr)
    lines = proc.stdout.split("step 8/8: report")[1].splitlines()
    tls = [ln for ln in lines if ln.strip().startswith("TLS:")]
    assert len(tls) == 1
    assert tls[0].strip().startswith("TLS: self-signed development certificate")
    assert "docs/deploy-server.md §4" in tls[0]
    checks = next(i for i, ln in enumerate(lines) if ln.strip().startswith("checks"))
    assert lines[checks + 1] == tls[0]


def test_a_certificate_for_the_domain_names_issuer_and_expiry(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    _plant(h, _leaf(tmp_path, _ca(tmp_path), "example.com"))
    out = _state(h)
    assert out.startswith("certificate for example.com, issuer AIZZAK Test CA, expires 20")
    assert "WARNING" not in out and "Cloudflare" not in out


def test_a_certificate_for_another_name_warns_and_names_the_domain(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    _plant(h, _leaf(tmp_path, _ca(tmp_path), "other.example"))
    out = _state(h)
    assert out.startswith("WARNING: the certificate does not cover example.com (issuer ")
    done = run_function(h, 'prepare "$@"; SIZE_DESC=x; report READY', *FLOW_ARGS, env={})
    assert done.returncode == 0, done.stderr
    assert "install: READY" in done.stdout and "TLS: WARNING" in done.stdout


_PHRASE = "Hostname example.com does match certificate"


@pytest.mark.parametrize(
    "issuer",
    [
        "/O=AIZZAK Test/CN=Evil does match certificate",
        f"/O=AIZZAK Test/CN={_PHRASE}",
        f"/O=AIZZAK Test/OU={_PHRASE}/CN=",
        f"/O=AIZZAK Test/CN=x\n{_PHRASE}",
    ],
    ids=["partial-phrase", "full-phrase-cn", "full-phrase-ou", "newline-then-phrase"],
)
def test_an_issuer_name_carrying_the_phrase_does_not_vouch_for_the_name(
    tmp_path: Path, issuer: str
) -> None:
    h = make_harness(tmp_path)
    try:
        evil = _ca(tmp_path, issuer)
    except subprocess.CalledProcessError:
        pytest.skip("this openssl refuses to build that issuer name")
    _plant(h, _leaf(tmp_path, evil, "other.example"))
    out = _state(h)
    assert out.startswith("WARNING: the certificate does not cover example.com (issuer ")
    assert not out.startswith("certificate for")


def test_openssl_never_sees_the_key_and_the_marker_never_reaches_the_output(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    _plant(h, _leaf(tmp_path, _ca(tmp_path), "example.com"))
    log = _fake_openssl(h)
    _state(h)
    argv = log.read_text(encoding="utf-8")
    assert argv and "server.key" not in argv
    assert "server.key" not in INSTALL_SH.read_text(encoding="utf-8")


def test_no_certificate_yet(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    assert _state(h).startswith("no certificate yet (deploy/nginx/certs/server.crt is missing)")


def test_an_unreadable_certificate_is_a_warning_not_an_exit(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    junk = tmp_path / "junk.crt"
    junk.write_text("not a certificate\n", encoding="utf-8")
    _plant(h, junk)
    assert _state(h).startswith("WARNING: deploy/nginx/certs/server.crt could not be read")


def test_an_expired_certificate_is_a_warning(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    _plant(h, _leaf(tmp_path, _ca(tmp_path), "example.com"))
    _fake_openssl(h, expired=True)
    assert _state(h).startswith("WARNING: the certificate expired on 2020-01-01 (issuer ")


def test_a_symlinked_certificate_is_not_read(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    _plant(h, None)
    (h.root / "deploy" / "nginx" / "certs" / "server.crt").symlink_to(
        _leaf(tmp_path, _ca(tmp_path), "example.com")
    )
    log = _fake_openssl(h)
    assert _state(h).startswith("WARNING: deploy/nginx/certs/server.crt is a symbolic link")
    assert not log.exists()


def test_without_openssl_the_state_is_not_checked(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    _plant(h, _leaf(tmp_path, _ca(tmp_path), "example.com"))
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    done = run_function(h, f'prepare "$@"; PATH="{empty}"; tls_state', *FLOW_ARGS)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "not checked: openssl is not installed on this host"


def test_the_origin_ca_issuer_gets_the_proxied_hint(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    ca = _ca(
        tmp_path,
        "/O=CloudFlare, Inc./OU=CloudFlare Origin SSL ECC Certificate Authority"
        "/L=San Francisco/ST=California/C=US",
    )
    _plant(h, _leaf(tmp_path, ca, "example.com"))
    out = _state(h)
    assert "issuer CloudFlare Origin SSL ECC Certificate Authority," in out
    assert out.endswith(" -- trusted by Cloudflare only: keep the DNS record proxied")
