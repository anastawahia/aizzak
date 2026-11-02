"""Run one request in a fresh, resource-limited child process (LLD 3.3.10).

Every request gets its own ``python -m services.ele_parse.child``, which calls ``apply_limits``
as the FIRST thing it does -- before it imports PyMuPDF or opens the file: ``RLIMIT_AS``
(memory), ``RLIMIT_CPU``, ``RLIMIT_FSIZE = 0`` (no file writes) and ``RLIMIT_NOFILE``. The limits
travel as arguments of the child. (``subprocess``'s ``preexec_fn`` would set them one step
earlier, between ``fork`` and ``exec``, but Python documents it as unsafe in a threaded parent
-- this server is threaded -- so the child sets its own, which costs nothing the file could
exploit: the PDF is not touched until they are in force.) The parent waits with a timeout and
kills the whole process group when it runs out.

Outcomes are the contract's statuses: a rejection from the child is ``422`` with its code; a
timeout (or the CPU limit) ``504``; running out of memory (``MemoryError``, exit code 3, or a
signal kill) ``507``; anything else unexpected ``500 parse_error``. ``BoundedSemaphore`` in
``app.py`` bounds how many children run at once.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import resource
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from services.ele_parse import contract

_MIB: Final = 1024 * 1024
ROOT: Final = Path(__file__).resolve().parents[2]  # the directory that holds ``services``
DEFAULT_ARGV: Final = (sys.executable, "-m", "services.ele_parse.child")
_NOFILE: Final = 64
_REJECTIONS: Final = frozenset({contract.NOT_PDF, contract.CORRUPT, contract.ENCRYPTED})
_EXIT_MEMORY: Final = 3


@dataclass(frozen=True, slots=True)
class Limits:
    memory_mb: int
    cpu_seconds: int
    nofile: int = _NOFILE


def apply_limits(limits: Limits) -> None:
    """Called by the child before it touches any PDF; the limits cannot be raised again by an
    unprivileged process (the hard limit is lowered with the soft one)."""
    memory = limits.memory_mb * _MIB
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.nofile, limits.nofile))


def child_argv(limits: Limits) -> list[str]:
    """The command line of the real child, limits included."""
    return [
        *DEFAULT_ARGV,
        "--memory-mb",
        str(limits.memory_mb),
        "--cpu-seconds",
        str(limits.cpu_seconds),
        "--nofile",
        str(limits.nofile),
    ]


def run_child(
    request: dict[str, Any],
    *,
    timeout_s: float,
    limits: Limits,
    argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The child's ``result`` for ``request``; ``contract.ContractError`` otherwise. ``argv``
    replaces the real child (tests use a stub that applies the limits itself)."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LANG": "C.UTF-8",
    }
    process = subprocess.Popen(
        list(child_argv(limits) if argv is None else argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env=env,
        start_new_session=True,
        close_fds=True,
    )
    try:
        stdout, _stderr = process.communicate(json.dumps(request).encode(), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill(process)
        raise contract.ContractError(
            504, "timeout", "the request exceeded its time limit"
        ) from None
    code = process.returncode
    if code < 0:
        if -code == signal.SIGXCPU:
            raise contract.ContractError(504, "timeout", "the request exceeded its CPU limit")
        raise contract.ContractError(507, "memory_limit", f"the child was killed by signal {-code}")
    if code == _EXIT_MEMORY:
        raise contract.ContractError(507, "memory_limit", "the request exceeded its memory limit")
    return _answer(code, stdout)


def _answer(code: int, stdout: bytes) -> dict[str, Any]:
    if code != 0:
        raise contract.ContractError(500, "parse_error", f"the child exited with {code}")
    try:
        answer = json.loads(stdout)
    except ValueError:
        raise contract.ContractError(500, "parse_error", "the child sent no answer") from None
    if answer.get("ok") is True and isinstance(answer.get("result"), dict):
        result: dict[str, Any] = answer["result"]
        return result
    reason = str(answer.get("code"))
    if reason in _REJECTIONS or reason == contract.BAD_RANGE:
        raise contract.ContractError(422, reason, str(answer.get("detail", reason)))
    raise contract.ContractError(500, "parse_error", f"the child refused: {reason}")


def _kill(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.communicate()


def cpu_seconds_for(timeout_s: float) -> int:
    """The CPU limit that goes with a wall-clock timeout: the same, rounded up (the wall-clock
    timeout is the usual limit; the CPU one catches a child that is busy beyond it)."""
    return max(1, math.ceil(timeout_s))
