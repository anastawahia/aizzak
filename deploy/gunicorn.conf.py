"""Gunicorn configuration — the lifecycle of ``PROMETHEUS_MULTIPROC_DIR``.

Wave 0 step 0.2 of ``docs/capacity-plan.md``. Everything else about how this
process is launched stays on the command line in the ``Dockerfile``; this file
exists for one reason, and it is a reason no CLI flag can serve.

``prometheus_client``'s multiprocess mode gives each OS process its own mmap
files in a shared directory, and a scrape sums across every file it finds
there (``api/metrics.py``'s ``_process_metrics``). That is what makes a request
counter correct under ``WEB_CONCURRENCY`` siblings. It also creates two ways
for the numbers to become fiction, and both are hooks only the ARBITER can run:

1. **A dead worker's files linger.** Gunicorn recycles workers (a crash, a
   restart, ``--max-requests``). Its counter files must keep counting — the
   requests really were served — but its GAUGE files must stop, or a pool
   gauge summed with ``livesum`` keeps charging the connection budget for
   connections a dead process is no longer holding. ``mark_process_dead`` is
   exactly that distinction, and ``child_exit`` is the only place it can be
   called with the pid of the worker that just went.
2. **Yesterday's files survive a restart.** The directory outlives the
   process group. Without a sweep at boot, a container that restarted would
   serve the sum of this run and every previous one — a counter that jumps
   backwards in Prometheus's view and a gauge that never comes down.

⚠️ **The directory must not be shared between two different SERVICES.** It is
a per-container path (``/tmp/...`` in this image, wiped with the container),
never a volume. Two services pointing at one directory would sum a metric
across processes that were never meant to be one number.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

# `Any` on both hooks below is gunicorn's own signature: it passes its
# `Arbiter` and `Worker` objects, neither of which ships type information.
_MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"


def on_starting(server: Any) -> None:
    """Arbiter boot: start from an empty directory, or from none at all."""
    configured = os.environ.get(_MULTIPROC_ENV)
    if not configured:
        # Not an error and not a warning: an operator running this image
        # without a scraper has no reason to pay for the mmap files, and
        # `_process_metrics` falls back to the in-process registry, which is
        # exactly right for a single-worker deployment.
        return
    directory = Path(configured)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    server.log.info("prometheus multiprocess directory prepared: %s", directory)


def child_exit(server: Any, worker: Any) -> None:
    """Retire a departed worker's GAUGES, keeping its counters.

    Imported inside the function rather than at module scope on purpose: this
    file is read by the arbiter before any worker exists, and importing
    ``prometheus_client.multiprocess`` at that moment in a deployment with no
    multiprocess directory would be a dependency taken for nothing.
    """
    if not os.environ.get(_MULTIPROC_ENV):
        return
    from prometheus_client import multiprocess  # noqa: PLC0415 - see the docstring

    multiprocess.mark_process_dead(worker.pid)
