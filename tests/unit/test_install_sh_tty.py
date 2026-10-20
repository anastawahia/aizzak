"""QA-7: no docker call made under `timeout` may inherit a terminal on stdin.

`timeout` puts its child in a background process group; a docker CLI that reads the terminal there
is stopped by SIGTTIN, silently, until the timeout kills it. The unit tests' fake docker has no
terminal, so this test gives `install.sh` a real pseudo-terminal as stdin and has a tiny fake
`docker` report what its own stdin is. It sources the script and calls `compose` the way the mint
(`exec -T vault`), the smoke (`exec -T app`) and the password step (`exec -T postgres`, fed by a
pipe) do; no full install runs.
"""

from __future__ import annotations

import os
import pty
import select
import stat
import time
from pathlib import Path

import pytest

from tests.unit.support_install import INSTALL_SH

_FAKE_DOCKER = """#!/bin/sh
# Reports what stdin is; a pipe or a file is read to the end, a terminal is never read.
if [ -t 0 ]; then
  echo "$*: TERMINAL"
else
  echo "$*: NOT-A-TERMINAL line=$(cat)"
fi
"""

_DRIVER = """
set -e
source "$1"
ROOT="$2"
compose 5 exec -T vault sh -c mint
compose 5 exec -T app python smoke
printf 'the-password\\n' | compose 5 exec -T postgres bash -c load
"""


def _run_on_terminal(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    pid, master = pty.fork()
    if pid == 0:  # child: the pty slave is its stdin, stdout and controlling terminal
        os.execvpe("bash", ["bash", "-c", _DRIVER, "_", *args], env)
    chunks: list[bytes] = []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if select.select([master], [], [], 0.5)[0]:
            try:
                data = os.read(master, 4096)
            except OSError:  # the child closed the terminal
                break
            if not data:
                break
            chunks.append(data)
    _, status = os.waitpid(pid, 0)
    os.close(master)
    return os.waitstatus_to_exitcode(status), b"".join(chunks).decode(errors="replace")


def test_docker_calls_under_timeout_never_get_a_terminal_as_stdin(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(_FAKE_DOCKER, encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    rc, out = _run_on_terminal([str(INSTALL_SH), str(tmp_path)], env)
    lines = [line.strip() for line in out.splitlines() if ": " in line and "exec" in line]
    assert rc == 0, out
    assert len(lines) == 3, out
    assert "TERMINAL" not in " ".join(x.replace("NOT-A-TERMINAL", "") for x in lines), out
    assert lines[0].endswith("NOT-A-TERMINAL line="), out  # mint
    assert lines[1].endswith("NOT-A-TERMINAL line="), out  # smoke
    assert lines[2].endswith("NOT-A-TERMINAL line=the-password"), out  # the pipe still delivers


@pytest.mark.parametrize("marker", ["timeout -k 10"])
def test_the_gpu_preflight_call_has_no_terminal_stdin(marker: str) -> None:
    """The preflight runs under `timeout` too; its stdin must be /dev/null."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    calls = [line for line in text.splitlines() if marker in line and "gpu-preflight" in line]
    assert len(calls) == 1 and "</dev/null" in calls[0], calls
