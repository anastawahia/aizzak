"""``python -m services.ele_parse``: serve (default) or ``inspect <file.pdf>`` (LLD 3.3.10).

``inspect`` is the mode that works OFF the stack: it runs the same limited child over any local
PDF and prints ``{"schema": 1, "summary": ..., "pages": [...]}`` on stdout. The characterization
tool and the pinned test files are built from it, and the owner's samples are inspected with it
on a throwaway container with no network (INC1 section 1.4).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from services.ele_parse import contract, sandbox
from services.ele_parse.app import Config, make_server

_CHUNK_PAGES = 200
_RANGE_FAILURES = {(504, "timeout"): "parse_timeout", (507, "memory_limit"): "memory_limit"}


def _failure_reason(exc: contract.ContractError) -> str | None:
    """The stage reason of a range that failed in a way the worker retries page by page
    (timeout, memory limit, parse error); ``None`` for any other refusal."""
    if (exc.status, exc.code) in _RANGE_FAILURES:
        return _RANGE_FAILURES[(exc.status, exc.code)]
    return "parse_error" if (exc.status, exc.code) == (500, "parse_error") else None


def _inspect_range(
    absolute: str,
    first: int,
    last: int,
    config: Config,
    limits: sandbox.Limits,
    failed: list[dict[str, object]],
) -> list[object]:
    """The signals of ``first..last``. A range that fails (or leaves a page out) is asked again
    page by page, as the worker does (NFR-REL-01): only the page that fails is lost, and it is
    listed in ``failed`` with its reason."""
    try:
        result = sandbox.run_child(
            {
                "op": "pages",
                "path": absolute,
                "first": first,
                "last": last,
                "token_sample": config.token_sample,
            },
            timeout_s=config.timeout_for(last - first + 1),
            limits=limits,
        )
    except contract.ContractError as exc:
        reason = _failure_reason(exc)
        if reason is None:
            raise
        if first == last:
            failed.append({"page_no": first, "reason": reason})
            return []
        pages: list[object] = []
        for number in range(first, last + 1):
            pages.extend(_inspect_range(absolute, number, number, config, limits, failed))
        return pages
    found: list[object] = list(result["pages"])
    got = {page["page_no"] for page in found if isinstance(page, dict)}
    # a page the child left out (its signals did not fit the contract's bound) is a page that
    # failed to parse
    failed.extend(
        {"page_no": n, "reason": "parse_error"} for n in range(first, last + 1) if n not in got
    )
    return found


def inspect_file(path: Path, config: Config) -> dict[str, object]:
    """The whole inspection of ``path``: summary, then every page in ranges of 200, each in
    its own limited child. ``failed_pages`` lists the pages that could not be read (the worker
    records them ``failed`` and leaves them out of the evidence, as this report does)."""
    absolute = str(path.resolve())
    limits = sandbox.Limits(config.memory_mb, sandbox.cpu_seconds_for(config.range_timeout_s))
    summary = sandbox.run_child(
        {"op": "summary", "path": absolute},
        timeout_s=config.page_timeout_s,
        limits=limits,
    )
    pages: list[object] = []
    failed: list[dict[str, object]] = []
    count = int(summary["page_count"])
    for first in range(1, count + 1, _CHUNK_PAGES):
        last = min(first + _CHUNK_PAGES - 1, count)
        pages.extend(_inspect_range(absolute, first, last, config, limits, failed))
    report: dict[str, object] = {"schema": contract.SCHEMA, "summary": summary, "pages": pages}
    if failed:
        report["failed_pages"] = failed
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.ele_parse")
    commands = parser.add_subparsers(dest="command")
    inspect_parser = commands.add_parser("inspect", help="inspect one local PDF, JSON on stdout")
    inspect_parser.add_argument("pdf", type=Path)
    args = parser.parse_args(argv)
    config = Config.from_env()
    if args.command == "inspect":
        try:
            report = inspect_file(args.pdf, config)
        except contract.ContractError as exc:
            sys.stderr.write(f"{exc.code}: {exc.detail}\n")
            return 2
        sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    server = make_server(config)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
