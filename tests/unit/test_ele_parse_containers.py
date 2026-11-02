"""Throwaway-container harness for the ``ele-parse`` isolation (INC-1 S9, O10, INC1-AC-9).

Runs the REAL image with the same hardening flags ``docker-compose.yml`` gives it, on a network
of its own that is ``--internal``, and measures what the box allows. Opt-in, like
``test_public_file_links_containers.py``: the whole module is skipped unless ALL hold:

* ``RUN_ELE_PARSE_CONTAINER_TEST=1``;
* ``docker`` is on ``PATH``;
* the image ``aizzak/ele-parse:dev`` is already built (``docker build -f
  services/ele_parse/Dockerfile -t aizzak/ele-parse:dev .``). Every container is started with
  ``--pull never``.

Topology: one ``--internal`` network, one ``ele-parse`` container on it, and a probe container on
the same network (the worker's stand-in). Nothing is attached to the live stack's networks, no
named volume is created (the stage is a read-only bind mount of ``tmp_path``), no port is
published. Cleanup removes every container and network carrying this run's label; it never uses
``-v``, ``prune`` or ``down -v``.

What it proves (LLD 5.2): no route out (``1.1.1.1:53`` fails), no write to ``/`` or to the stage,
``id -u`` is not 0, no secret in the environment, a path outside the root or ``../`` is refused,
a heavy request is killed by the limit rather than served, and the probe on the internal
network can reach the service by name.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

_IMAGE = os.environ.get("ELE_PARSE_TEST_IMAGE", "aizzak/ele-parse:dev")
_LABEL = "aizzak.harness=ele-parse-containers"
_STAGE = "/var/lib/aizzak/ele-stage"


def _skip_reason() -> str:
    if os.environ.get("RUN_ELE_PARSE_CONTAINER_TEST") != "1":
        return "opt-in container harness: set RUN_ELE_PARSE_CONTAINER_TEST=1"
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    probe = subprocess.run(["docker", "image", "inspect", _IMAGE], capture_output=True, check=False)
    if probe.returncode != 0:
        return f"image {_IMAGE} is not built locally (containers run with --pull never)"
    return ""


_SKIP = _skip_reason()
pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "enabled")


def _docker(
    *args: str, check: bool = True, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=timeout
    )
    if check and done.returncode != 0:
        raise subprocess.CalledProcessError(
            done.returncode, ["docker", *args[:6]], output=done.stdout, stderr=done.stderr
        )
    return done


# The probe: one HTTP call, printed as "<status> <body>". Runs inside a container, by python.
_HTTP = (
    "import sys,urllib.request,urllib.error\n"
    "url,body=sys.argv[1],sys.argv[2]\n"
    "req=urllib.request.Request(url,data=body.encode() if body else None,"
    "headers={'Content-Type':'application/json'})\n"
    "try:\n"
    "    r=urllib.request.urlopen(req,timeout=60);print(r.status,r.read().decode())\n"
    "except urllib.error.HTTPError as e:\n"
    "    print(e.code,e.read().decode())\n"
)
_CONNECT = (
    "import socket,sys\n"
    "s=socket.socket();s.settimeout(3)\n"
    "try:\n"
    "    s.connect(('1.1.1.1',53));print('CONNECTED')\n"
    "except OSError as e:\n"
    "    print('FAILED',type(e).__name__)\n"
)


def _call(container: str, url: str, body: dict[str, object] | None = None) -> tuple[int, str]:
    done = _docker("exec", container, "python", "-c", _HTTP, url, json.dumps(body) if body else "")
    status, _, text = done.stdout.partition(" ")
    return int(status), text


class _Box:
    def __init__(self, name: str, network: str, stage: Path) -> None:
        self.name = name
        self.network = network
        self.stage = stage

    def start(self, **env: str) -> None:
        flags = [
            "run", "-d", "--pull", "never", "--label", _LABEL, "--name", self.name,
            "--network", self.network, "--network-alias", "ele-parse",
            "--user", "10001:10001", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:size=64m",
            "--pids-limit", "64", "--memory", "2560m", "--cpus", "1.0",
            "-v", f"{self.stage}:{_STAGE}:ro",
            "-e", f"ELE_PARSE_STAGE_ROOT={_STAGE}",
        ]  # fmt: skip
        for key, value in env.items():
            flags += ["-e", f"{key}={value}"]
        _docker(*flags, _IMAGE)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = _docker(
                "exec", self.name, "python", "-c", _HTTP, "http://127.0.0.1:8090/health", "",
                check=False,
            )  # fmt: skip
            if status.stdout.startswith("200"):
                return
            time.sleep(0.5)
        logs = _docker("logs", self.name, check=False)
        raise AssertionError(f"ele-parse did not become healthy: {logs.stdout}{logs.stderr}")


def _make_pdf(stage: Path) -> str:
    """One two-page vector PDF in the stage, named the way the worker names it."""
    pymupdf = pytest.importorskip("pymupdf")
    workspace, document = str(uuid.uuid4()), str(uuid.uuid4())
    directory = stage / workspace
    directory.mkdir(mode=0o755)
    pdf = pymupdf.open()
    for number in range(2):
        page = pdf.new_page(width=420, height=297)
        page.insert_text((40, 60), f"=A1 -K{number + 1} 24V", fontsize=12)
        page.draw_line((40, 80), (300, 80))
    target = directory / f"{document}.pdf"
    pdf.save(target)
    pdf.close()
    target.chmod(0o644)  # the container user is a different uid on the host side of a bind mount
    return f"{workspace}/{document}.pdf"


@pytest.fixture(scope="module")
def stage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("ele-stage")
    path.chmod(0o755)
    return path


@pytest.fixture(scope="module")
def network() -> Iterator[str]:
    name = f"ele-parse-harness-{secrets.token_hex(4)}"
    _docker("network", "create", "--internal", "--label", _LABEL, name)
    try:
        yield name
    finally:
        # Containers first, then the network. No volume exists to remove.
        ids = _docker("ps", "-aq", "--filter", f"label={_LABEL}", check=False).stdout.split()
        if ids:
            _docker("rm", "-f", *ids, check=False)
        _docker("network", "rm", name, check=False)


@pytest.fixture(scope="module")
def box(network: str, stage: Path) -> _Box:
    ele = _Box(f"ele-parse-harness-{secrets.token_hex(4)}", network, stage)
    ele.start()
    return ele


def test_the_container_is_not_root_and_its_root_is_read_only(box: _Box) -> None:
    assert _docker("exec", box.name, "id", "-u").stdout.strip() == "10001"
    write = _docker("exec", box.name, "sh", "-c", "touch /probe", check=False)
    assert write.returncode != 0, "the root filesystem must be read-only"
    assert _docker("exec", box.name, "sh", "-c", "touch /tmp/ok && echo ok").stdout.strip() == "ok"


def test_the_stage_is_read_only(box: _Box) -> None:
    write = _docker("exec", box.name, "sh", "-c", f"touch {_STAGE}/probe", check=False)
    assert write.returncode != 0


def test_no_capability_is_left(box: _Box) -> None:
    pid1 = _docker("exec", box.name, "cat", "/proc/1/status").stdout
    caps = {
        line.split()[0].rstrip(":"): line.split()[1]
        for line in pid1.splitlines()
        if line.startswith("Cap")
    }
    assert caps["CapEff"] == "0" * 16 and caps["CapPrm"] == "0" * 16, caps


def test_there_is_no_route_out_of_the_internal_network(box: _Box) -> None:
    done = _docker("exec", box.name, "python", "-c", _CONNECT)
    assert done.stdout.startswith("FAILED"), done.stdout


def test_the_environment_holds_no_secret(box: _Box) -> None:
    names = [
        line.partition("=")[0] for line in _docker("exec", box.name, "env").stdout.splitlines()
    ]
    forbidden = ("SECRET", "PASSWORD", "VAULT", "DATABASE", "REDIS", "MINIO")
    assert [n for n in names if any(word in n.upper() for word in forbidden)] == []


def test_a_peer_on_the_internal_network_reaches_the_service_by_name(
    box: _Box, network: str
) -> None:
    done = _docker(
        "run", "--rm", "--pull", "never", "--label", _LABEL, "--network", network,
        "--user", "10001:10001", "--read-only", "--cap-drop", "ALL",
        _IMAGE, "python", "-c", _HTTP, "http://ele-parse:8090/health", "",
    )  # fmt: skip
    status, _, body = done.stdout.partition(" ")
    assert status == "200"
    assert json.loads(body)["schema"] == 1


def test_a_staged_pdf_is_summarised_and_the_answer_is_deterministic(box: _Box, stage: Path) -> None:
    name = _make_pdf(stage)
    first = _call(box.name, "http://127.0.0.1:8090/v1/documents/summary", {"file": name})
    second = _call(box.name, "http://127.0.0.1:8090/v1/documents/summary", {"file": name})
    assert first[0] == 200 and json.loads(first[1])["page_count"] == 2
    assert first == second
    pages = _call(
        box.name,
        "http://127.0.0.1:8090/v1/documents/pages",
        {"file": name, "first_page": 1, "last_page": 2},
    )
    assert pages[0] == 200 and len(json.loads(pages[1])["pages"]) == 2


def test_a_page_that_makes_mupdf_warn_is_still_inspected_in_the_read_only_container(
    box: _Box, stage: Path
) -> None:
    """QA-1 in the real sandbox: fd 1 is pointed at /dev/null under ``--read-only`` with every
    capability dropped, and a damaged content stream no longer costs the page."""
    name = _make_pdf(stage)
    pymupdf = pytest.importorskip("pymupdf")
    path = stage / name
    damaged = f"{name.partition('/')[0]}/{uuid.uuid4()}.pdf"
    pdf = pymupdf.open(path)
    pdf.update_stream(
        pdf[0].get_contents()[0], b"BT /helv 12 Tf (hi) Tj ET\n)))) <<<< ] 1 2 foo", compress=False
    )
    pdf.save(stage / damaged)
    pdf.close()
    (stage / damaged).chmod(0o644)
    status, body = _call(
        box.name,
        "http://127.0.0.1:8090/v1/documents/pages",
        {"file": damaged, "first_page": 1, "last_page": 2},
    )
    assert status == 200, body
    assert len(json.loads(body)["pages"]) == 2


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "/etc/passwd",
        "00000000-0000-0000-0000-000000000000/../../x.pdf",
        "not-a-path.pdf",
    ],
)
def test_a_path_outside_the_stage_pattern_is_refused(box: _Box, bad: str) -> None:
    status, body = _call(box.name, "http://127.0.0.1:8090/v1/documents/summary", {"file": bad})
    assert status == 400, body
    assert "bad_path" in body


def test_a_missing_file_is_404(box: _Box) -> None:
    ghost = f"{uuid.uuid4()}/{uuid.uuid4()}.pdf"
    status, _ = _call(box.name, "http://127.0.0.1:8090/v1/documents/summary", {"file": ghost})
    assert status == 404


def test_a_symbolic_link_is_refused(box: _Box, stage: Path) -> None:
    workspace, document = str(uuid.uuid4()), str(uuid.uuid4())
    directory = stage / workspace
    directory.mkdir(mode=0o755)
    (directory / f"{document}.pdf").symlink_to("/etc/passwd")
    status, body = _call(
        box.name,
        "http://127.0.0.1:8090/v1/documents/summary",
        {"file": f"{workspace}/{document}.pdf"},
    )
    assert status == 400, body


def test_a_request_past_the_time_limit_is_killed_not_served(network: str, stage: Path) -> None:
    name = _make_pdf(stage)
    slow = _Box(f"ele-parse-harness-{secrets.token_hex(4)}", network, stage)
    slow.start(ELE_PARSE_PAGE_TIMEOUT_S="0.001", ELE_PARSE_RANGE_TIMEOUT_S="0.002")
    status, body = _call(
        slow.name,
        "http://127.0.0.1:8090/v1/documents/pages",
        {"file": name, "first_page": 1, "last_page": 2},
    )
    assert status == 504, body


def _make_heavy_pdf(stage: Path) -> str:
    """Sixty large pages of dense text: fine under the default limit, past a 100 MB one."""
    pymupdf = pytest.importorskip("pymupdf")
    workspace, document = str(uuid.uuid4()), str(uuid.uuid4())
    directory = stage / workspace
    directory.mkdir(mode=0o755)
    pdf = pymupdf.open()
    for _ in range(60):
        page = pdf.new_page(width=2000, height=1500)
        for row in range(150):
            line = " ".join(f"-K{row}:{col}" for col in range(60))
            page.insert_text((20, 10 + row * 9), line, fontsize=6)
    target = directory / f"{document}.pdf"
    pdf.save(target)
    pdf.close()
    target.chmod(0o644)
    return f"{workspace}/{document}.pdf"


def test_a_request_past_the_memory_limit_is_refused_not_served(
    box: _Box, network: str, stage: Path
) -> None:
    name = _make_heavy_pdf(stage)
    request = {"file": name, "first_page": 1, "last_page": 60}
    url = "http://127.0.0.1:8090/v1/documents/pages"
    # Under the default limit the same file is served...
    assert _call(box.name, url, request)[0] == 200
    # ...and under a 100 MB address-space limit the child is killed and the answer is 507.
    small = _Box(f"ele-parse-harness-{secrets.token_hex(4)}", network, stage)
    small.start(ELE_PARSE_MEMORY_MB="100")
    status, body = _call(small.name, url, request)
    assert status == 507, body
    assert "memory_limit" in body
