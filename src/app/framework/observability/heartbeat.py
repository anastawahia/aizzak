"""Process-liveness heartbeat for the loop-shaped processes (ت-3,
``docs/operational-findings.md`` §3).

**The gap this closes.** Six of thirteen live services had no healthcheck, and
four of them are processes with no HTTP listener to probe: the three
``worker-*`` consumers and ``outbox-relay``. For those, "the container is
``Up``" says nothing about whether the loop is still turning -- the finding's
own words: «عاملٌ حيٌّ لا يستهلك = غير مرئيّ تماماً», established live by
having to dig the answer out of ``/proc/1/status`` and Redis ``CLIENT LIST``
by hand.

**Why a file and not an HTTP endpoint.** Adding an aiohttp/uvicorn listener to
a consumer would put a second concurrency model inside a process whose whole
shape is one blocking ``XREADGROUP``, and would open a port on a process that
deliberately has none. A file's mtime carries exactly one bit of information --
*when did this loop last complete a cycle* -- which is precisely the question,
and ``app.ops.healthcheck`` reads it with no network, no port and no server.

**Why mtime and not file contents.** ``Path.touch`` creates-or-stamps in one
step; there is no partial-write window for a concurrent reader to observe, and
nothing to parse (so nothing to mis-parse). The Dockerfile's "the app writes
nothing to the filesystem" note still holds in spirit: this is one zero-byte
file under ``/tmp``, in the container's own writable layer, never a mount and
never state anything reads back after a restart.

**Per-process file, not per-container.** The name is supplied by the caller
(``memory``/``knowledge``/``media``/``outbox-relay``) rather than defaulted to
one shared path, because Compose is not the only topology: RunPod runs
``worker`` AND ``outbox-relay`` inside a single container
(``deploy/runpod/supervisord.conf`` tier 3), where one shared path would let a
dead process ride on a live sibling's beat -- the exact false-healthy this
module exists to prevent.

**A failed beat never kills the process.** An observability aid that can take
a worker down is a net loss of availability, so ``OSError`` is caught. It is
NOT swallowed (10 §5's «لا ابتلاع صامت»): the first failure and the recovery
are both logged, and only the repeats in between are suppressed -- a beat runs
every ``block_ms`` (5 s by default), so logging every failure would turn a
read-only ``/tmp`` into 17 000 log lines a day.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from app.framework.observability.logging import get_logger

_logger = get_logger(__name__)

_SUFFIX = ".heartbeat"

# Every process that beats, named once. The BEATER (`workers/bootstrap.py`)
# and the CHECKER (`app.ops.healthcheck`, invoked by name from
# docker-compose.yml) both resolve their path through `heartbeat_path`, so a
# name that exists on only one side is a file nobody writes and a check that
# fails forever. Validating the checker's argument against this tuple turns
# that silent mismatch into an immediate, listed error.
#
# The three worker names are the `WORKER` values docker-compose.yml sets;
# `outbox-relay` is spelled with the hyphen its Compose SERVICE uses, not the
# underscore of `app.workers.outbox_relay`, because the operator reading
# `docker compose ps` sees the former.
#
# `wal-shipper` (capacity step 2.5) is the fifth, and it is the clearest case
# of all for this mechanism: it is a loop with no listener whose failure mode
# is not an error but a SILENCE. If it stops, `archive_command` keeps
# succeeding into a spool nobody drains, the spool grows without limit, and
# the first symptom anyone sees is a full data volume taking the whole
# platform down -- caused by the backup system. "The container is Up" says
# nothing about that; a beat per completed shipping cycle does.
HEARTBEAT_PROCESS_NAMES = ("memory", "knowledge", "media", "outbox-relay", "wal-shipper")


class Heartbeat(Protocol):
    """What a loop calls once per completed cycle. Deliberately no ``read``
    side: the process that beats never asks how old its own beat is -- that
    question belongs to the out-of-process checker (``app.ops.healthcheck``),
    which is the only thing that can answer it while the loop is wedged."""

    def beat(self) -> None: ...


class NullHeartbeat:
    """The no-op implementation, used when no heartbeat directory is
    configured (``HEARTBEAT_DIR=``). Keeps every call site free of ``if
    self._heartbeat is not None`` -- the loop bodies below are hot paths whose
    readability matters more than one attribute lookup."""

    def beat(self) -> None:
        return None


class FileHeartbeat:
    """Stamps one file's mtime per cycle. Not frozen/slots-only because it
    carries the one bit of state the "log the transition, not every repeat"
    policy in the module docstring needs."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._failing = False

    @property
    def path(self) -> Path:
        return self._path

    def beat(self) -> None:
        try:
            # `parents=True` on every beat rather than once at construction:
            # the directory lives under /tmp, which a host can clear
            # underneath a long-running container, and re-creating it costs
            # one stat on the happy path.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()
        except OSError:
            if not self._failing:
                self._failing = True
                _logger.warning(
                    "heartbeat.write_failed", extra={"path": str(self._path)}, exc_info=True
                )
            return
        if self._failing:
            self._failing = False
            _logger.info("heartbeat.write_recovered", extra={"path": str(self._path)})


def heartbeat_path(directory: str, name: str) -> Path | None:
    """``<directory>/<name>.heartbeat``, or ``None`` when ``directory`` is
    empty -- the documented "heartbeat disabled" switch (``HEARTBEAT_DIR=``).
    The ONE place the filename is spelled, so the beating process and
    ``app.ops.healthcheck`` cannot drift into checking a file nobody writes."""
    if not directory.strip():
        return None
    return Path(directory) / f"{name}{_SUFFIX}"


def build_heartbeat(directory: str, name: str) -> Heartbeat:
    """The composition-root-facing constructor: a real ``FileHeartbeat`` when
    a directory is configured, ``NullHeartbeat`` when it is not."""
    path = heartbeat_path(directory, name)
    return NullHeartbeat() if path is None else FileHeartbeat(path)


def age_seconds(path: Path, *, now: float | None = None) -> float | None:
    """Seconds since the last beat, or ``None`` if the file does not exist yet
    (a process that has not completed its first cycle -- which is why the
    Compose healthchecks give these services a ``start_period``, and why this
    returns ``None`` rather than ``inf``: "never beat" and "beat long ago" are
    different states and the checker reports them differently).

    Clamped at 0: a clock that steps backwards must not report a beat from the
    future as a negative age that silently passes every freshness test.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - mtime)
