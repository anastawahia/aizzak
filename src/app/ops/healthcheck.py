"""Docker healthcheck for the processes that expose no port (ت-3,
``docs/operational-findings.md`` §3).

``python -m app.ops.healthcheck <name>`` where ``<name>`` is one of
``memory`` / ``knowledge`` / ``media`` / ``outbox-relay``. Exit code IS the
answer, the way ``HEALTHCHECK`` reads it:

* ``0`` -- the process completed a loop cycle within
  ``HEARTBEAT_MAX_AGE_S``.
* ``1`` -- the stamp is stale, or was never written. Docker turns this into
  ``(unhealthy)`` after ``retries`` consecutive failures, which is why the
  Compose services also carry a ``start_period`` covering first boot.
* ``2`` -- a CONFIGURATION fault: no heartbeat directory, or an unknown
  process name. Deliberately not ``0``: a healthcheck that cannot check
  anything must never report healthy (Docker treats any non-zero as a
  failure, so this still surfaces -- the distinct code is for the human
  reading ``docker inspect``'s log).

**Why this reads a file rather than asking Redis.** The question is whether
THIS process's loop is turning. Redis can answer a neighbouring question --
``XINFO CONSUMERS`` shows a consumer's idle time -- but the consumer name
carries the process's pid, which a sidecar check cannot know, and the reading
proves the GROUP has a recent reader rather than that this container's process
is it. That is exactly how ت-7's orphan container hid: two live consumers in
``cg.memory``, one of them running deleted code. A per-process file cannot be
satisfied by a sibling.

**Why it is safe to run every 30 s.** It loads ``Settings`` (one ``.env``
parse) and calls ``stat`` once. No database, no Redis, no HTTP, no import of
the application graph.
"""

from __future__ import annotations

import argparse
import sys

from app.framework.observability.heartbeat import (
    HEARTBEAT_PROCESS_NAMES,
    age_seconds,
    heartbeat_path,
)
from app.infrastructure.config.env_settings import load_settings

_EXIT_OK = 0
_EXIT_UNHEALTHY = 1
_EXIT_MISCONFIGURED = 2


def check(name: str) -> tuple[int, str]:
    """Returns ``(exit_code, one-line message)``. Split out from ``main`` so
    the decision table is testable without ``SystemExit``/``argv`` games."""
    if name not in HEARTBEAT_PROCESS_NAMES:
        return (
            _EXIT_MISCONFIGURED,
            f"unknown process {name!r} -- valid names: {', '.join(HEARTBEAT_PROCESS_NAMES)}",
        )

    health = load_settings().health
    path = heartbeat_path(health.heartbeat_dir, name)
    if path is None:
        return (
            _EXIT_MISCONFIGURED,
            "HEARTBEAT_DIR is empty: the heartbeat is disabled, so this check "
            "has nothing to read. Set it, or remove the healthcheck.",
        )

    age = age_seconds(path)
    if age is None:
        # No file at all. During `start_period` this is the normal state of a
        # process that has not finished its first cycle; after it, it means
        # the loop never completed one.
        return _EXIT_UNHEALTHY, f"no heartbeat yet at {path}"
    if age > health.heartbeat_max_age_s:
        return (
            _EXIT_UNHEALTHY,
            f"heartbeat stale: {age:.0f}s old > {health.heartbeat_max_age_s}s ({path})",
        )
    return _EXIT_OK, f"heartbeat {age:.0f}s old <= {health.heartbeat_max_age_s}s"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.healthcheck",
        description="Exit 0 while the named process's loop is still turning (module docstring).",
    )
    parser.add_argument(
        "name",
        help=f"which process to check -- one of: {', '.join(HEARTBEAT_PROCESS_NAMES)}",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    code, message = check(args.name)
    # stdout on success, stderr on failure -- `docker inspect` keeps the last
    # few healthcheck outputs either way, and a human tailing logs gets the
    # failures on the stream they expect them on.
    print(message, file=sys.stdout if code == _EXIT_OK else sys.stderr)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
