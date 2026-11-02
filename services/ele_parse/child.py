"""The child process: ONE request, then exit (``python -m services.ele_parse.child``).

Reads one JSON object on stdin, writes one on stdout. It runs under the limits ``sandbox.py``
set before ``exec`` (address space, CPU seconds, no file writes, few descriptors), so whatever a
hostile PDF does is bounded to this process.

Arguments: ``--memory-mb``, ``--cpu-seconds``, ``--nofile`` (the limits it applies to itself
first). Request: ``{"op": "summary", "path": ...}`` or ``{"op": "pages", "path": ..., "first": n,
"last": m, "token_sample": k}``. Answer: ``{"ok": true, "result": ...}`` or ``{"ok": false,
"code": not_pdf|corrupt|encrypted|bad_range, "detail": ...}``. Exit code 3 is "out of memory";
anything else unexpected is exit 4 with only the exception type on stderr (a PDF's text never
reaches a log).
"""

from __future__ import annotations

import argparse
import importlib
import json
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from services.ele_parse import contract, sandbox

EXIT_MEMORY = 3
EXIT_FAILED = 4


def handle(request: dict[str, Any]) -> dict[str, Any]:
    """The pure part: a request in, an answer out. PyMuPDF is imported here, on first use, which
    in the child is after ``main`` has applied the limits."""
    inspect = importlib.import_module("services.ele_parse.inspect")
    try:
        with inspect.open_document(Path(str(request["path"]))) as document:
            if request["op"] == "summary":
                return {"ok": True, "result": inspect.summarize(document)}
            if request["op"] == "pages":
                pages = inspect.inspect_pages(
                    document,
                    int(request["first"]),
                    int(request["last"]),
                    int(request["token_sample"]),
                )
                return {"ok": True, "result": {"pages": pages}}
            return {"ok": False, "code": "bad_request", "detail": "unknown op"}
    except inspect.Rejected as exc:
        return {"ok": False, "code": exc.code, "detail": exc.detail}
    except contract.ContractError as exc:
        return {"ok": False, "code": exc.code, "detail": exc.detail}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ele-parse-child")
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--nofile", type=int, default=64)
    args = parser.parse_args(argv)
    # The limits come first: nothing below touches a PDF or imports PyMuPDF before they hold.
    sandbox.apply_limits(sandbox.Limits(args.memory_mb, args.cpu_seconds, args.nofile))
    # A write to a regular file would raise SIGXFSZ under RLIMIT_FSIZE=0 and kill us; make it
    # an ordinary error instead. Nothing here writes a file anyway (no bytecode, read-only fs).
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    sys.dont_write_bytecode = True
    try:
        request = json.loads(sys.stdin.buffer.read())
        answer = handle(request)
        sys.stdout.write(json.dumps(answer, sort_keys=True, separators=(",", ":")))
        sys.stdout.flush()
    except MemoryError:
        return EXIT_MEMORY
    except Exception as exc:
        sys.stderr.write(type(exc).__name__)
        return EXIT_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
