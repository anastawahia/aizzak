"""Throwaway-container harness for the public file route (server-gpu-plan.md 6.2).

Runs the REAL ``deploy/nginx/nginx.conf`` and ``app-locations.conf`` (mounted
read-only, unmodified) in front of a real MinIO, on a network of its own, and
measures what the route does to a presigned URL. Opt-in, like ``live_edge``:
it takes about a minute, which would break the unit suite's time budget, so
the whole module is skipped unless ALL of these hold:

* ``RUN_EDGE_FILES_TEST=1``;
* ``docker`` is on ``PATH``;
* the ``nginx`` and ``minio`` images named by ``docker-compose.yml`` are
  already present (every container is started with ``--pull never``).

Topology, all on one ``--internal`` network (no published port, no way out,
no link to the live stack, no named volume):

    client -> edge (nginx, the repo's files) -> recorder (alias ``minio``)
                  |                                 -> store (real MinIO)
                  +-> app stand-in (alias ``app``)

The ``recorder`` is a plain nginx in front of the store that logs what
actually reaches MinIO (method, raw URI, ``Host``, ``X-Forwarded-Host``), so
"never reaches MinIO" is observed rather than inferred.

Cleanup removes every container and the network carrying this run's label.
It never uses ``-v``, ``prune`` or ``down -v``.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from minio import Minio

from app.framework.settings.settings import Limits
from app.infrastructure.storage.minio_storage import MinioStorage

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NGINX_CONF = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_LOCATIONS_CONF = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"
_GEN_CERT = _REPO_ROOT / "deploy" / "nginx" / "gen-cert.sh"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"

_LABEL = "aizzak.harness=public-file-links"
_NGINX_IMAGE = "nginx:1.27"
_BUCKET = "workspace-files"
_OWN_NAME = "api.aizzak.test"
_PUBLIC_NAME = "api.aizzak.com"
_ALLOWED_ORIGIN = "https://aizzak-agent.web.app"
# The edge ceiling is the single-PUT limit (PLT-01, INC0-Q-3), not the 1 GiB limit of a
# whole file: a big file reaches the bucket as parts, each one a request below it.
_MAX_UPLOAD = Limits().max_single_put_bytes
_PROBE_KEY = "smoke/presign-put-probe.txt"


def _minio_image() -> str:
    services = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]
    return str(services["minio"]["image"])


def _skip_reason() -> str:
    if os.environ.get("RUN_EDGE_FILES_TEST") != "1":
        return "opt-in container harness: set RUN_EDGE_FILES_TEST=1 (about a minute)"
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    for image in (_NGINX_IMAGE, _minio_image()):
        probe = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, check=False
        )
        if probe.returncode != 0:
            return f"image {image} is not present locally (containers run with --pull never)"
    return ""


_SKIP = _skip_reason()
pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "enabled")


def _docker(
    *args: str, check: bool = True, timeout: float = 180
) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and done.returncode != 0:
        # Name the command and its stderr: a bare exit status is useless here.
        # Credentials only ever belong to the throwaway store of this run.
        raise subprocess.CalledProcessError(
            done.returncode, ["docker", *args[:6]], output=done.stdout, stderr=done.stderr
        )
    return done


# --------------------------------------------------------------------------- #
# Parsed curl answer                                                           #
# --------------------------------------------------------------------------- #
@dataclass
class Answer:
    status: int
    headers: list[tuple[str, str]]
    body_path: str
    _harness: Harness

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k == name.lower()]

    def body(self) -> str:
        return self._harness.client_exec("cat", self.body_path).stdout


def _parse_headers(raw: str) -> tuple[int, list[tuple[str, str]]]:
    blocks = [b for b in raw.replace("\r\n", "\n").split("\n\n") if b.strip()]
    final = [b for b in blocks if not re.match(r"HTTP/\S+ 100", b)][-1]
    lines = final.split("\n")
    status = int(lines[0].split()[1])
    headers = [
        (k.strip().lower(), v.strip())
        for k, _, v in (line.partition(":") for line in lines[1:])
        if k.strip()
    ]
    return status, headers


class Harness:
    def __init__(self, tmp: Path) -> None:
        self.run_id = secrets.token_hex(4)
        self.tmp = tmp
        self.net = f"pfl-{self.run_id}"
        self.access_key = "ak" + secrets.token_hex(6)
        self.secret_key = secrets.token_hex(16)
        self.subnet = ""
        self._counter = 0
        self._signers: dict[str, MinioStorage] = {}
        self._clients: dict[str, Minio] = {}

    # ---- plumbing ---------------------------------------------------------
    def name(self, role: str) -> str:
        return f"pfl-{self.run_id}-{role}"

    def _run(
        self,
        role: str,
        image: str,
        *args: str,
        aliases: tuple[str, ...] = (),
        ip: str | None = None,
        options: tuple[str, ...] = (),
    ) -> None:
        cmd = [
            "run",
            "-d",
            "--rm",
            "--pull",
            "never",
            "--name",
            self.name(role),
            "--label",
            _LABEL,
            "--label",
            f"aizzak.run={self.run_id}",
            "--network",
            self.net,
        ]
        for alias in aliases:
            cmd += ["--network-alias", alias]
        if ip:
            cmd += ["--ip", ip]
        _docker(*cmd, *options, image, *args)

    def exec(self, role: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _docker("exec", self.name(role), *args, check=check)

    def client_exec(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.exec("client", *args, check=check)

    def sweep(self, *, only_this_run: bool) -> None:
        flt = f"label=aizzak.run={self.run_id}" if only_this_run else f"label={_LABEL}"
        ids = _docker("ps", "-aq", "--filter", flt, check=False).stdout.split()
        if ids:
            # Stop first: the containers run with --rm, and only the daemon's
            # auto-remove on stop also deletes MinIO's anonymous data volume
            # (`rm -f` without -v would leave it behind). No -v, no prune.
            _docker("stop", "-t", "1", *ids, check=False)
            _docker("rm", "-f", *ids, check=False)
        nets = _docker("network", "ls", "-q", "--filter", flt, check=False).stdout.split()
        for net in nets:
            _docker("network", "rm", net, check=False)

    # ---- topology ---------------------------------------------------------
    def start(self) -> None:
        self.sweep(only_this_run=False)
        for _ in range(20):
            a, b = random.randint(200, 250), random.randint(0, 255)
            subnet = f"10.{a}.{b}.0/24"
            made = _docker(
                "network",
                "create",
                "--internal",
                "--subnet",
                subnet,
                "--label",
                _LABEL,
                "--label",
                f"aizzak.run={self.run_id}",
                self.net,
                check=False,
            )
            if made.returncode == 0:
                self.subnet = f"10.{a}.{b}"
                break
        else:
            pytest.fail("no free private subnet for the harness network")

        self._write_conf()
        self._make_cert()
        self._run(
            "store",
            _minio_image(),
            "server",
            "/data",
            aliases=("minio-store",),
            options=(
                "-e",
                f"MINIO_ROOT_USER={self.access_key}",
                "-e",
                f"MINIO_ROOT_PASSWORD={self.secret_key}",
            ),
        )
        self._until(
            lambda: (
                self.exec(
                    "store",
                    "mc",
                    "alias",
                    "set",
                    "local",
                    "http://localhost:9000",
                    self.access_key,
                    self.secret_key,
                    check=False,
                ).returncode
                == 0
            )
        )
        self._until(lambda: self.exec("store", "mc", "ready", "local", check=False).returncode == 0)
        self.exec("store", "mc", "mb", f"local/{_BUCKET}")
        self.ensure_recorder()
        self._run(
            "app",
            _NGINX_IMAGE,
            aliases=("app",),
            options=("-v", f"{self.tmp / 'app.conf'}:/etc/nginx/nginx.conf:ro"),
        )
        self._run(
            "edge",
            _NGINX_IMAGE,
            aliases=(_OWN_NAME, _PUBLIC_NAME),
            options=(
                "--memory",
                "512m",
                "-v",
                f"{_NGINX_CONF}:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{_LOCATIONS_CONF}:/etc/nginx/app-locations.conf:ro",
                "-v",
                f"{self.tmp / 'certs'}:/etc/nginx/certs:ro",
            ),
        )
        self._run("client", _NGINX_IMAGE, "sleep", "infinity")
        self._until(lambda: self.get(f"https://{_OWN_NAME}/health", check=False) == 200)

    def ensure_recorder(self, ip_suffix: int = 10) -> None:
        present = _docker(
            "ps", "-q", "--filter", f"name=^{self.name('recorder')}$", check=False
        ).stdout.strip()
        if present:
            return
        self._run(
            "recorder",
            _NGINX_IMAGE,
            aliases=("minio",),
            ip=f"{self.subnet}.{ip_suffix}",
            options=("-v", f"{self.tmp / 'recorder.conf'}:/etc/nginx/nginx.conf:ro"),
        )

    def stop_recorder(self) -> None:
        _docker("rm", "-f", self.name("recorder"), check=False)

    def _write_conf(self) -> None:
        (self.tmp / "recorder.conf").write_text(
            """
worker_processes 1;
pid /tmp/nginx.pid;
events { worker_connections 256; }
http {
    log_format rec escape=json
        '{"method":"$request_method","uri":"$request_uri","host":"$http_host",'
        '"xfh":"$http_x_forwarded_host","status":$status}';
    access_log /dev/stdout rec;
    client_body_temp_path /tmp/cb;
    proxy_temp_path /tmp/pt;
    server {
        listen 9000;
        client_max_body_size 0;
        location / {
            proxy_pass http://minio-store:9000;
            proxy_http_version 1.1;
            proxy_set_header Host $http_host;
            proxy_set_header Connection "";
            proxy_request_buffering off;
            proxy_buffering off;
            add_header X-Harness-Upstream minio always;
        }
    }
}
""",
            encoding="utf-8",
        )
        (self.tmp / "app.conf").write_text(
            """
worker_processes 1;
pid /tmp/nginx.pid;
events { worker_connections 256; }
http {
    access_log off;
    client_body_temp_path /tmp/cb;
    server {
        listen 8000;
        location = /health { add_header X-Harness-Upstream app always; return 200 "ok"; }
        location / { add_header X-Harness-Upstream app always; return 404 "app"; }
    }
}
""",
            encoding="utf-8",
        )

    def _make_cert(self) -> None:
        certs = self.tmp / "certs"
        certs.mkdir()
        certs.chmod(0o777)
        _docker(
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--label",
            _LABEL,
            "-e",
            "AIZZAK_CERT_DIR=/certs",
            "-v",
            f"{certs}:/certs",
            "-v",
            f"{_GEN_CERT}:/gen-cert.sh:ro",
            _NGINX_IMAGE,
            "sh",
            "/gen-cert.sh",
        )

    @staticmethod
    def _until(check: Callable[[], bool], timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if check():
                    return
            except subprocess.CalledProcessError:
                pass
            time.sleep(0.5)
        raise AssertionError("the harness did not become ready in time")

    # ---- requests ---------------------------------------------------------
    def curl(self, url: str, *opts: str, out: str | None = None, check: bool = True) -> Answer:
        self._counter += 1
        body = out or f"/tmp/body-{self._counter}"
        done = self.client_exec(
            "curl",
            "-sS",
            "-k",
            "--max-time",
            "180",
            "-D",
            "-",
            "-o",
            body,
            *opts,
            url,
            check=check,
        )
        status, headers = _parse_headers(done.stdout)
        return Answer(status, headers, body, self)

    def get(self, url: str, *opts: str, check: bool = True) -> int:
        try:
            return self.curl(url, *opts, check=check).status
        except (subprocess.CalledProcessError, IndexError, ValueError):
            return 0

    def make_file(self, size: int) -> tuple[str, str]:
        """A random file of ``size`` bytes inside the client; returns (path, sha256)."""
        self._counter += 1
        path = f"/tmp/file-{self._counter}"
        self.client_exec("sh", "-c", f"head -c {size} /dev/urandom > {path}")
        digest = self.client_exec("sha256sum", path).stdout.split()[0]
        return path, digest

    # ---- signing, with the app's own storage adapter -----------------------
    def _signer(self, host: str) -> MinioStorage:
        if host not in self._signers:
            client = Minio(
                host,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=True,
                region="us-east-1",
            )
            self._clients[host] = client
            self._signers[host] = MinioStorage(client, _BUCKET, client)
        return self._signers[host]

    def put_url(
        self, host: str, key: str, ttl_s: int = 900, content_type: str = "text/plain"
    ) -> str:
        return asyncio.run(self._signer(host).presign_put(key, ttl_s, content_type))

    def part_url(self, host: str, key: str, upload_id: str, part_number: int) -> str:
        """The URL the app hands out for one part (the real ``presign_upload_part``)."""
        signer = self._signer(host)
        return asyncio.run(signer.presign_upload_part(key, upload_id, part_number, 900))

    def direct_url(self, method: str, key: str, **query: str) -> str:
        """A URL for the store on the internal network, signed for ``minio-store:9000``.

        The app calls MinIO there and never through the edge, so this is how the
        harness plays the server-side calls of a multipart upload: create, complete.
        """
        client = Minio(
            "minio-store:9000",
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=False,
            region="us-east-1",
        )
        return str(
            client.get_presigned_url(
                method,
                _BUCKET,
                key,
                expires=timedelta(seconds=300),
                extra_query_params=dict(query) or None,
            )
        )

    def get_url(self, host: str, key: str, ttl_s: int = 300) -> str:
        return asyncio.run(self._signer(host).presign_get(key, ttl_s))

    def method_url(self, host: str, method: str, key: str, **query: str) -> str:
        self._signer(host)
        return self._clients[host].get_presigned_url(
            method,
            _BUCKET,
            key,
            expires=timedelta(seconds=300),
            extra_query_params=dict(query) or None,
        )

    # ---- what MinIO and the edge saw ---------------------------------------
    def stored_sha(self, key: str) -> str | None:
        stat = self.exec("store", "mc", "stat", f"local/{_BUCKET}/{key}", check=False)
        if stat.returncode != 0:
            return None
        cat = self.exec("store", "sh", "-c", f"mc cat local/{_BUCKET}/{key} | sha256sum")
        return cat.stdout.split()[0]

    def recorded(self, needle: str = "") -> list[dict[str, object]]:
        out = _docker("logs", self.name("recorder"), check=False)
        lines = [ln for ln in (out.stdout + out.stderr).splitlines() if ln.startswith("{")]
        parsed = [json.loads(ln) for ln in lines]
        return [r for r in parsed if needle in str(r["uri"])]

    def edge_logs(self) -> tuple[list[str], list[str]]:
        out = _docker("logs", self.name("edge"), check=False)
        return out.stdout.splitlines(), out.stderr.splitlines()


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Harness]:
    h = Harness(tmp_path_factory.mktemp("pfl"))
    try:
        h.start()
        yield h
    finally:
        h.sweep(only_this_run=True)


@pytest.fixture(autouse=True)
def _recorder_is_up(request: pytest.FixtureRequest) -> None:
    if "harness" in request.fixturenames:
        request.getfixturevalue("harness").ensure_recorder()


def _key(prefix: str = "t") -> str:
    return f"{prefix}/{secrets.token_hex(6)}"


def _put(h: Harness, url: str, path: str, *opts: str, content_type: str = "text/plain") -> Answer:
    return h.curl(
        url, "-X", "PUT", "-H", f"Content-Type: {content_type}", "-T", path, *opts, check=False
    )


# --------------------------------------------------------------------------- #
# AC-1.1                                                                       #
# --------------------------------------------------------------------------- #
def test_a_signed_put_through_the_edge_stores_the_same_bytes(harness: Harness) -> None:
    key = _key()
    path, digest = harness.make_file(1_048_576)
    answer = _put(harness, harness.put_url(_OWN_NAME, key), path)
    assert answer.status == 200
    assert harness.stored_sha(key) == digest
    seen = harness.recorded(key)
    assert [(r["method"], r["host"]) for r in seen] == [("PUT", _OWN_NAME)]


# --------------------------------------------------------------------------- #
# AC-1.2                                                                       #
# --------------------------------------------------------------------------- #
def test_a_url_signed_for_another_name_is_refused_by_minio(harness: Harness) -> None:
    key = _key()
    path, _ = harness.make_file(1024)
    other = harness.put_url("other.aizzak.test", key)
    answer = _put(harness, other.replace("other.aizzak.test", _OWN_NAME, 1), path)
    assert answer.status == 403
    assert "SignatureDoesNotMatch" in answer.body()
    assert harness.stored_sha(key) is None


# --------------------------------------------------------------------------- #
# AC-1.3                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("http_flag", [(), ("--http1.1",)], ids=["h2", "http1.1"])
def test_the_body_limit_is_exact_at_50_mb(harness: Harness, http_flag: tuple[str, ...]) -> None:
    for size in (0, _MAX_UPLOAD):
        key = _key()
        path, digest = harness.make_file(size)
        answer = _put(harness, harness.put_url(_OWN_NAME, key), path, *http_flag)
        assert answer.status == 200, f"{size} bytes"
        sha = harness.stored_sha(key)
        assert sha == digest, f"{size} bytes stored differently"

    key = _key()
    path, _ = harness.make_file(_MAX_UPLOAD + 1)
    answer = _put(harness, harness.put_url(_OWN_NAME, key), path, *http_flag)
    assert answer.status == 413
    body = answer.body()
    assert "<Error>" not in body and "nginx" in body.lower()
    assert harness.recorded(key) == []
    assert harness.stored_sha(key) is None


# --------------------------------------------------------------------------- #
# AC-1.5                                                                       #
# --------------------------------------------------------------------------- #
def _memory_peak(h: Harness) -> int | None:
    for name in ("memory.peak", "memory.max_usage_in_bytes"):
        done = h.exec("edge", "cat", f"/sys/fs/cgroup/{name}", check=False)
        if done.returncode == 0 and done.stdout.strip().isdigit():
            return int(done.stdout.strip())
    return None


@pytest.mark.parametrize("http_flag", [(), ("--http1.1",)], ids=["h2", "http1.1"])
def test_a_50_mb_upload_streams_without_temp_files(
    harness: Harness, http_flag: tuple[str, ...]
) -> None:
    key = _key()
    path, digest = harness.make_file(_MAX_UPLOAD)
    url = harness.put_url(_OWN_NAME, key)
    result: dict[str, Answer] = {}

    def upload() -> None:
        result["answer"] = _put(harness, url, path, "--limit-rate", "10M", *http_flag)

    worker = threading.Thread(target=upload)
    worker.start()
    leaked = 0
    samples = 0
    while worker.is_alive():
        found = harness.exec(
            "edge",
            "sh",
            "-c",
            "find /var/cache/nginx/client_temp -type f 2>/dev/null | wc -l",
        ).stdout.strip()
        leaked = max(leaked, int(found or "0"))
        samples += 1
        time.sleep(0.1)
    worker.join()
    assert result["answer"].status == 200
    assert harness.stored_sha(key) == digest
    assert samples >= 5, "the upload finished too fast to sample"
    assert leaked == 0, f"nginx spooled the body to disk ({leaked} files)"

    peak = _memory_peak(harness)
    print(f"\n[AC-1.5 {http_flag or 'h2'}] samples={samples} temp_files={leaked} peak={peak}")
    if peak is not None:
        assert peak < 512 * 1024 * 1024
    events = harness.exec("edge", "cat", "/sys/fs/cgroup/memory.events", check=False).stdout
    assert not re.search(r"^oom(_kill)? [1-9]", events, flags=re.MULTILINE), events
    state = _docker("inspect", "-f", "{{.State.OOMKilled}}", harness.name("edge")).stdout.strip()
    assert state == "false"


# --------------------------------------------------------------------------- #
# AC-1.6                                                                       #
# --------------------------------------------------------------------------- #
def test_cors_comes_from_the_edge_once(harness: Harness) -> None:
    key = _key()
    path, _ = harness.make_file(1024)
    url = harness.put_url(_OWN_NAME, key)
    preflight = (
        "-X",
        "OPTIONS",
        "-H",
        "Access-Control-Request-Method: PUT",
        "-H",
        "Access-Control-Request-Headers: content-type",
    )

    good = harness.curl(url, *preflight, "-H", f"Origin: {_ALLOWED_ORIGIN}")
    assert good.status == 204
    assert good.all("access-control-allow-origin") == [_ALLOWED_ORIGIN]
    sent = _put(harness, url, path, "-H", f"Origin: {_ALLOWED_ORIGIN}")
    assert sent.status == 200
    assert sent.all("access-control-allow-origin") == [_ALLOWED_ORIGIN]

    evil = harness.curl(url, *preflight, "-H", "Origin: https://evil.example")
    assert evil.all("access-control-allow-origin") == []
    sent_evil = _put(harness, url, path, "-H", "Origin: https://evil.example")
    assert sent_evil.all("access-control-allow-origin") == []


# --------------------------------------------------------------------------- #
# AC-1.7                                                                       #
# --------------------------------------------------------------------------- #
def test_an_expired_upload_url_is_refused(harness: Harness) -> None:
    key = _key()
    path, _ = harness.make_file(1024)
    url = harness.put_url(_OWN_NAME, key, ttl_s=1)
    time.sleep(3)
    answer = _put(harness, url, path)
    assert answer.status == 403
    assert "AccessDenied" in answer.body()
    assert harness.stored_sha(key) is None


# --------------------------------------------------------------------------- #
# AC-2.1                                                                       #
# --------------------------------------------------------------------------- #
def test_a_signed_get_returns_the_bytes_type_and_edge_headers(harness: Harness) -> None:
    key = _key()
    path, digest = harness.make_file(1_048_576)
    assert (
        _put(harness, harness.put_url(_OWN_NAME, key), path, content_type="application/pdf").status
        == 200
    )

    answer = harness.curl(harness.get_url(_OWN_NAME, key), out="/tmp/back.bin")
    assert answer.status == 200
    assert harness.client_exec("sha256sum", "/tmp/back.bin").stdout.split()[0] == digest
    assert answer.all("content-type") == ["application/pdf"]
    for header in ("x-content-type-options", "referrer-policy", "x-frame-options"):
        assert len(answer.all(header)) == 1, f"{header}: {answer.all(header)}"
    assert answer.all("strict-transport-security") == []

    public = harness.curl(harness.get_url(_PUBLIC_NAME, key), out="/tmp/back2.bin")
    assert public.status == 200
    assert public.all("strict-transport-security") == ["max-age=31536000"]

    # What MinIO itself sends (straight to the recorder, bypassing the edge),
    # so the comments in app-locations.conf describe measured headers.
    direct = harness.get_url("minio:9000", key).replace("https://", "http://", 1)
    raw = harness.curl(direct, "-H", f"Origin: {_ALLOWED_ORIGIN}", out="/dev/null")
    minio_headers = {
        k: v
        for k, v in raw.headers
        if k.startswith("access-control-")
        or k in ("strict-transport-security", "x-content-type-options")
    }
    print(f"\n[AC-2.1] MinIO's own headers (Origin {_ALLOWED_ORIGIN}): {minio_headers}")


# --------------------------------------------------------------------------- #
# AC-3.4                                                                       #
# --------------------------------------------------------------------------- #
def test_the_edge_boots_and_recovers_without_minio(harness: Harness) -> None:
    harness.stop_recorder()
    _docker("restart", harness.name("edge"))
    Harness._until(lambda: harness.get(f"https://{_OWN_NAME}/health", check=False) == 200, 30)

    started = time.monotonic()
    down = harness.curl(f"https://{_OWN_NAME}/workspace-files/{_key()}", check=False)
    assert down.status == 502
    assert time.monotonic() - started <= 3

    harness.ensure_recorder(ip_suffix=11)
    started = time.monotonic()
    status = 0
    while time.monotonic() - started <= 10:
        status = harness.get(f"https://{_OWN_NAME}/workspace-files/{_key()}", check=False)
        if status == 403:
            break
        time.sleep(0.5)
    assert status == 403, (
        f"MinIO did not answer through the edge after {time.monotonic() - started:.1f}s"
    )
    print(f"\n[AC-3.4] recovered in {time.monotonic() - started:.1f}s without restarting the edge")


# --------------------------------------------------------------------------- #
# AC-5.1                                                                       #
# --------------------------------------------------------------------------- #
def test_other_buckets_minio_paths_and_traversal_never_reach_minio(harness: Harness) -> None:
    token = secrets.token_hex(4)
    paths = [
        f"/aizzak-backups/x?t={token}",
        f"/aizzak-test/x?t={token}",
        f"/minio/health/live?t={token}",
        f"/minio/admin/v3/info?t={token}",
        f"/workspace-files/../aizzak-backups/x?t={token}",
        f"/workspace-files/%2e%2e/aizzak-backups/x?t={token}",
    ]
    for path in paths:
        answer = harness.curl(f"https://{_OWN_NAME}{path}", "--path-as-is", check=False)
        assert answer.all("x-harness-upstream") == ["app"], f"{path}: {answer.status}"

    # Raw traversal that nginx normalises INTO the prefix (design 3.2.6): the
    # request does reach MinIO, which must not hand anything out.
    for path in (
        f"/aizzak-backups/../workspace-files/x?t={token}",
        f"/aizzak-backups%2F..%2Fworkspace-files/x?t={token}",
    ):
        answer = harness.curl(f"https://{_OWN_NAME}{path}", "--path-as-is", check=False)
        print(f"\n[AC-5.1] raw {path.split('?')[0]} -> {answer.status}")
        assert not 200 <= answer.status < 300

    seen = [r["uri"] for r in harness.recorded(token)]
    assert not any(
        "/aizzak-" in str(u).split("..")[0] and "workspace-files" not in str(u) for u in seen
    )
    assert all("workspace-files" in str(u) for u in seen)


# --------------------------------------------------------------------------- #
# AC-5.2                                                                       #
# --------------------------------------------------------------------------- #
def test_only_get_head_and_put_on_an_object_reach_minio(harness: Harness) -> None:
    key = _key()
    path, digest = harness.make_file(1024)
    assert _put(harness, harness.put_url(_OWN_NAME, key), path).status == 200

    delete = harness.curl(harness.method_url(_OWN_NAME, "DELETE", key), "-X", "DELETE", check=False)
    multipart = harness.curl(
        harness.method_url(_OWN_NAME, "POST", key, uploads=""), "-X", "POST", check=False
    )
    assert delete.status in (403, 405) and multipart.status in (403, 405)
    assert "<Error>" not in delete.body() and "<Error>" not in multipart.body()
    assert harness.stored_sha(key) == digest

    root = harness.curl(f"https://{_OWN_NAME}/workspace-files/", check=False)
    policy = harness.curl(
        f"https://{_OWN_NAME}/workspace-files/?policy",
        "-X",
        "PUT",
        "-d",
        "{}",
        check=False,
    )
    assert root.status == 404 and policy.status == 404
    assert [r["method"] for r in harness.recorded(key)] == ["PUT"]

    probe_path, _ = harness.make_file(64)
    assert _put(harness, harness.put_url(_OWN_NAME, _PROBE_KEY), probe_path).status == 200
    assert harness.curl(harness.get_url(_OWN_NAME, _PROBE_KEY)).status == 200
    head_url = harness.method_url(_OWN_NAME, "HEAD", _PROBE_KEY)
    assert harness.curl(head_url, "-I").status == 200
    methods = [r["method"] for r in harness.recorded(_PROBE_KEY)]
    assert sorted(methods) == ["GET", "HEAD", "PUT"]


# --------------------------------------------------------------------------- #
# AC-5.3                                                                       #
# --------------------------------------------------------------------------- #
def test_the_host_minio_checks_is_the_client_s_own(harness: Harness) -> None:
    key = _key()
    path, _ = harness.make_file(1024)
    forwarded = ("-H", "X-Forwarded-Host: evil.example")

    own = _put(harness, harness.put_url(_OWN_NAME, key), path, *forwarded)
    assert own.status == 200

    forged = harness.put_url("evil.example", key)
    forged = forged.replace("evil.example", _OWN_NAME, 1)
    other = _put(harness, forged, path, *forwarded)
    assert other.status == 403

    for row in harness.recorded(key):
        assert row["host"] == _OWN_NAME
        assert row["xfh"] == ""


# --------------------------------------------------------------------------- #
# AC-5.4                                                                       #
# --------------------------------------------------------------------------- #
def test_the_access_log_never_holds_a_signature(harness: Harness) -> None:
    key = _key("log")
    path, _ = harness.make_file(1024)
    for i in range(10):
        assert _put(harness, harness.put_url(_OWN_NAME, f"{key}-{i}"), path).status == 200
        assert harness.curl(harness.get_url(_OWN_NAME, f"{key}-{i}")).status == 200

    big, _ = harness.make_file(_MAX_UPLOAD + 1)
    assert _put(harness, harness.put_url(_OWN_NAME, f"{key}-413"), big).status == 413

    out, err = harness.edge_logs()
    access = [ln for ln in out if '"logger":"nginx.access"' in ln]
    mine = [ln for ln in access if key in ln or '"/workspace-files/' in ln]
    assert len([ln for ln in mine if f"/{key}-" in ln]) >= 20
    for line in access:
        assert "X-Amz-Signature" not in line and "X-Amz-Credential" not in line
        assert "?" not in json.loads(line)["path"]
    leaking = [ln for ln in err if "X-Amz-Signature" in ln]
    print(f"\n[AC-5.4] error-log lines carrying X-Amz-Signature after a 413: {len(leaking)}")


# --------------------------------------------------------------------------- #
# SR-1 -- CopyObject through a presigned PUT                                   #
# --------------------------------------------------------------------------- #
def test_a_copy_source_header_never_reaches_minio(harness: Harness) -> None:
    victim = f"victim-ws/{secrets.token_hex(4)}"
    harness.exec("store", "mc", "mb", "--ignore-existing", "local/aizzak-backups")
    harness.exec("store", "sh", "-c", f"echo secret | mc pipe local/{_BUCKET}/{victim}")
    harness.exec("store", "sh", "-c", "echo dump | mc pipe local/aizzak-backups/dump/db.dump")
    path, _ = harness.make_file(1024)
    sources = (
        f"/{_BUCKET}/{victim}",
        f"{_BUCKET}/{victim}",
        "/aizzak-backups/dump/db.dump",
    )
    for header in (
        "x-amz-copy-source",
        "X-Amz-Copy-Source-Range",
        "x-amz-meta-snowball-auto-extract",
    ):
        for source in sources:
            key = _key("attacker")
            value = "true" if "snowball" in header else source
            answer = _put(
                harness, harness.put_url(_OWN_NAME, key), path, "-H", f"{header}: {value}"
            )
            assert answer.status == 403, f"{header}: {source}"
            assert "<Error>" not in answer.body()
            assert harness.recorded(key) == []
            assert harness.stored_sha(key) is None
    # The same URL without the header still works: the guard is not a blanket refusal.
    key = _key("attacker")
    assert _put(harness, harness.put_url(_OWN_NAME, key), path).status == 200


# --------------------------------------------------------------------------- #
# INC0-Q-3 -- presigned UploadPart through the edge                            #
# --------------------------------------------------------------------------- #
def _start_upload(h: Harness, key: str) -> str:
    """CreateMultipartUpload, server-side (app -> minio:9000), as the app does it."""
    answer = h.curl(h.direct_url("POST", key, uploads=""), "-X", "POST", check=False)
    assert answer.status == 200
    found = re.search(r"<UploadId>([^<]+)</UploadId>", answer.body())
    assert found is not None
    return found.group(1)


def test_a_signed_part_through_the_edge_reaches_minio_and_completes(harness: Harness) -> None:
    key = _key("part")
    upload_id = _start_upload(harness, key)
    path, digest = harness.make_file(5_242_880)
    url = harness.part_url(_OWN_NAME, key, upload_id, 1)
    answer = _put(harness, url, path, content_type="application/octet-stream")
    assert answer.status == 200
    etags = answer.all("etag")
    assert len(etags) == 1
    seen = harness.recorded(key)
    assert [(r["method"], r["host"]) for r in seen] == [("PUT", _OWN_NAME)]
    assert "partNumber=1" in str(seen[0]["uri"])
    # A part is not an object: nothing is readable until the server completes the upload.
    assert harness.stored_sha(key) is None

    etag = etags[0].strip('"')
    xml = (
        "<CompleteMultipartUpload><Part><PartNumber>1</PartNumber>"
        f"<ETag>{etag}</ETag></Part></CompleteMultipartUpload>"
    )
    harness.client_exec("sh", "-c", f"printf '%s' '{xml}' > /tmp/complete.xml")
    complete = harness.curl(
        harness.direct_url("POST", key, uploadId=upload_id),
        "-X",
        "POST",
        "--data-binary",
        "@/tmp/complete.xml",
        check=False,
    )
    assert complete.status == 200
    assert harness.stored_sha(key) == digest


def test_a_part_over_the_edge_limit_is_refused_by_the_edge(harness: Harness) -> None:
    key = _key("part")
    upload_id = _start_upload(harness, key)
    path, _ = harness.make_file(_MAX_UPLOAD + 1)
    url = harness.part_url(_OWN_NAME, key, upload_id, 1)
    answer = _put(harness, url, path, content_type="application/octet-stream")
    assert answer.status == 413
    assert "<Error>" not in answer.body()
    assert harness.recorded(key) == []


def test_creating_completing_and_aborting_an_upload_never_come_through_the_edge(
    harness: Harness,
) -> None:
    key = _key("part")
    upload_id = _start_upload(harness, key)
    create = harness.curl(
        harness.method_url(_OWN_NAME, "POST", key, uploads=""), "-X", "POST", check=False
    )
    complete = harness.curl(
        harness.method_url(_OWN_NAME, "POST", key, uploadId=upload_id), "-X", "POST", check=False
    )
    abort = harness.curl(
        harness.method_url(_OWN_NAME, "DELETE", key, uploadId=upload_id),
        "-X",
        "DELETE",
        check=False,
    )
    for answer in (create, complete, abort):
        assert answer.status in (403, 405)
        assert "<Error>" not in answer.body()
    assert harness.recorded(key) == []


def test_a_copy_source_on_a_part_never_reaches_minio(harness: Harness) -> None:
    victim = f"victim-ws/{secrets.token_hex(4)}"
    harness.exec("store", "sh", "-c", f"echo secret | mc pipe local/{_BUCKET}/{victim}")
    key = _key("part")
    upload_id = _start_upload(harness, key)
    path, _ = harness.make_file(1024)
    url = harness.part_url(_OWN_NAME, key, upload_id, 1)
    answer = _put(
        harness,
        url,
        path,
        "-H",
        f"x-amz-copy-source: /{_BUCKET}/{victim}",
        content_type="application/octet-stream",
    )
    assert answer.status == 403
    assert "<Error>" not in answer.body()
    assert harness.recorded(key) == []
