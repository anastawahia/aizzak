"""``outbox_relay`` process entrypoint (5.1-ب · 08-local-runbook §4:
``python -m app.workers.outbox_relay``).

Deliberately thin (08 §4: "نقطة الدخول الموحّدة... تختار العامل" — but THIS
process is not multiplexed through ``workers/main.py``'s dispatcher at all;
D-26 runs it as its own single-instance Compose service, ``outbox-relay``,
with its own command). All composition lives in ``workers/bootstrap.py``;
this file only sequences build → run → teardown.
"""

from __future__ import annotations

import asyncio

from app.framework.observability import get_logger
from app.workers.bootstrap import build_relay_from_env

_logger = get_logger(__name__)


async def run() -> None:
    """Build the relay, provision its consumer-group topology, run it until
    cancelled, then close every resource ``build_relay_from_env`` handed
    back — ``finally`` so a cancellation (graceful shutdown, e.g. SIGTERM
    under Compose) still tears down the engine and the Redis client rather
    than leaking connections.

    ``await ensure_topology()`` runs BEFORE ``run_forever`` and inside the
    SAME ``try`` — so a topology-provisioning failure still reaches the
    ``finally`` and tears down what was already built, and so it can never
    be bypassed: there is no path from process start to the relay's first
    publish that skips it (stream-topology-plan.md §3). All composition
    stays in ``workers/bootstrap.py``; this function only sequences build →
    provision → run → teardown.

    **The stream trimmer (capacity 5.5) runs beside the relay as a background
    task, never the other way round.** The relay's loop stays what the
    process IS: its exceptions end the process exactly as before, unwrapped,
    and its heartbeat is still the container's health. The trimmer never
    raises out of its own loop (``StreamTrimmer.run_forever``), is cancelled
    in the ``finally`` before the client it uses is closed, and -- should it
    ever end anyway -- says so in the log rather than vanishing: a trimmer
    that silently stopped would leave only the ``MAXLEN`` backstop, which is
    exactly the state 5.5 exists to leave.
    """
    relay, ensure_topology, trimmer, disposables = build_relay_from_env()
    trim_task: asyncio.Task[None] | None = None
    try:
        await ensure_topology()
        if trimmer is not None:
            trim_task = asyncio.create_task(trimmer.run_forever(), name="stream-trimmer")
            trim_task.add_done_callback(_log_trimmer_exit)
        await relay.run_forever()
    finally:
        if trim_task is not None:
            trim_task.cancel()
            # `wait`, never `await trim_task`: this runs while the relay's own
            # exception may be unwinding, and awaiting the task would re-raise
            # whatever ended it in that exception's place. How the trimmer
            # ended is already in the log (`_log_trimmer_exit`).
            await asyncio.wait([trim_task])
        for dispose in disposables:
            await dispose()


def _log_trimmer_exit(task: asyncio.Task[None]) -> None:
    """``run_forever`` only ends by cancellation; any other ending is a bug
    worth a line with its traceback."""
    if task.cancelled():
        return
    exc = task.exception()
    _logger.error("stream_trim.stopped", exc_info=exc)


if __name__ == "__main__":
    asyncio.run(run())
