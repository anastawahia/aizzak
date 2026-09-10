"""Graceful shutdown for the three ``worker-*`` processes (ت-2,
``docs/operational-findings.md`` §2).

**The defect this closes, measured rather than reasoned about.** Every worker
entrypoint already sequences ``build → run → finally: dispose``, and each
one's docstring says the ``finally`` exists "so a cancellation (graceful
shutdown, e.g. SIGTERM under Compose) still tears down". That was never true.
Python installs no handler for ``SIGTERM``: the default disposition
terminates the process outright, so ``docker compose stop`` / ``restart`` /
``up -d`` (which all send ``SIGTERM`` first) killed the interpreter between
bytecodes. The ``finally`` never ran, no client was ever closed, and -- the
reason this module exists now -- the worker's Redis consumer entry was never
removed, so every single restart left a permanent tombstone inside
``cg.knowledge``/``cg.media``/``cg.memory``. The ghosts measured live on
2026-08-13 are exactly that, once per container recreation.

**What this adds, and what it deliberately does not.** ``SIGTERM``/``SIGINT``
are turned into ordinary loop cancellation, which lets the code every
entrypoint already has do what it always claimed to do, and adds one step of
its own: ``StreamConsumer.deregister`` before the clients close.

**Capacity 5.1 (invariant 4) put a DRAIN in front of that cancellation, and
the reason is arithmetic rather than taste.** Before 5.1 a signal cancelled
the read loop where it stood, so a worker mid-handler lost that one job: the
entry stayed ``pending``, the sweeper eventually reclaimed it, and a
half-written effect was covered by the handler's own transaction. Bounded
concurrency multiplies that by ``WORKER_CONCURRENCY`` -- «التزامنُ بلا هذا
يحوّل كلَّ نشرٍ إلى أربع مهامّ مبتورةٍ بدل واحدة». So a signal now asks the
loop to STOP TAKING (``StreamConsumer.request_stop``) and gives the batch in
flight ``EventSettings.worker_drain_timeout_s`` to finish; only what outlives
that deadline is cancelled, and it is left ``pending`` exactly as every
truncated job was before.

⚠️ **The drain is bounded ON PURPOSE and does not try to outlast a long
handler.** A summary build may legitimately run for
``Limits.summarize_job_max_duration_s`` (1,800 s); waiting that out would turn
every deploy into a half-hour stall, and the sweeper is already the answer for
what does not finish. The deadline is sized for the SHORT handlers -- an
indexing job, a memory item -- which is where the truncation actually costs
work.

⚠️ **And the ladder has to hold, or the drain is theatre:**
``worker_drain_timeout_s`` (30 s) + the deregistration round trips must fit
inside Compose's ``stop_grace_period`` (45 s on the three ``worker-*``
services since 5.1; the DEFAULT of 10 s would have cut this path off at a
third of its deadline). ``tests/unit/test_worker_drain_ladder.py`` is what
keeps the two numbers in that order when either one moves.

**Crash safety is a separate mechanism, on purpose.** ``SIGKILL``, an OOM
kill, and a hard power loss all still skip this path entirely -- nothing in
userspace can help there. That case is covered from the other side, by the
timed ``sweep_stale_consumers`` running inside whichever worker is alive
next (``infrastructure/messaging/consumers/sweeper.py``). Neither mechanism
subsumes the other: this one is exact and immediate but only for clean
exits, that one is eventual but unconditional.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Sequence

from app.framework.observability import get_logger
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription

_logger = get_logger(__name__)

_SIGNALS = (signal.SIGTERM, signal.SIGINT)


async def run_worker(consumer: StreamConsumer, subscriptions: Sequence[Subscription]) -> None:
    """Run ``consumer.run(subscriptions)`` until it finishes, raises, or a
    shutdown signal arrives; deregister this process's consumer entries
    before returning either way.

    A signal is delivered in TWO steps since capacity 5.1 (invariant 4):
    ``StreamConsumer.request_stop`` first, so the loop leaves itself after the
    batch in flight, and only then the cancellation the loop was always
    documented to propagate (``StreamConsumer.run``). A crash inside the loop
    propagates unchanged too, AFTER the deregistration: a worker that dies of
    a bug still owes Redis the same cleanup as one that was asked to stop, and
    the entries it still holds pending are protected by ``deregister``'s own
    refusal rule rather than by skipping the step.

    The drain deadline is the consumer's own (``StreamConsumer.drain_
    timeout_s``, wired from ``EventSettings.worker_drain_timeout_s``), and
    ``0`` -- the default for every direct caller that is not a ``worker-*``
    entrypoint -- reproduces the pre-5.1 path exactly: cancel where it stands.
    """
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    installed: list[signal.Signals] = []
    for sig in _SIGNALS:
        try:
            loop.add_signal_handler(sig, _on_signal, sig, stopping)
        except (NotImplementedError, RuntimeError, ValueError):
            # No signal support on this platform/loop (Windows' proactor
            # loop, a non-main thread). The worker still runs; it just falls
            # back to the pre-existing "killed where it stands" behaviour,
            # which the timed sweep covers. Never a boot failure.
            _logger.info("worker.signal_handler_unavailable", extra={"signal": sig.name})
            continue
        installed.append(sig)

    work = asyncio.create_task(consumer.run(subscriptions), name="worker.run")
    stop = asyncio.create_task(stopping.wait(), name="worker.stop")
    try:
        await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
        if work.done():
            await work  # Re-raise whatever ended the loop.
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        stop.cancel()
        await _drain(consumer, work)
        await _cancel(work)
        # Safe to await inside this `finally`: the cancellation that gets here
        # is one THIS function issued against `work`, never against its own
        # task, so nothing is pending on the current coroutine. `deregister`
        # additionally swallows its own failures (its docstring), so the exit
        # path cannot be masked by a Redis error during cleanup.
        await consumer.deregister(subscriptions)


async def _drain(consumer: StreamConsumer, work: asyncio.Task[None]) -> None:
    """Ask the loop to stop taking, then give it its deadline to finish what
    it already has (capacity 5.1, invariant 4).

    ``asyncio.wait`` and NOT ``wait_for``: on timeout ``wait_for`` cancels the
    task it was waiting on, which is the very truncation this function exists
    to avoid doing SILENTLY -- the cancellation belongs to ``_cancel`` below,
    after this has logged that the deadline was missed and by whom. On the
    ordinary path ``work`` finishes first and ``_cancel`` sees a done task and
    returns immediately.

    A timed-out drain is a WARNING, not an error: leaving entries ``pending``
    for the sweeper is a designed outcome, not a fault -- but it is one an
    operator should be able to correlate with a deploy, which a silent
    cancellation would never let them do.
    """
    if consumer.drain_timeout_s <= 0 or work.done():
        return
    consumer.request_stop()
    _logger.info("worker.drain_started", extra={"timeout_s": consumer.drain_timeout_s})
    await asyncio.wait({work}, timeout=consumer.drain_timeout_s)
    if work.done():
        # The loop can also END during the drain by DYING. `_cancel` below
        # short-circuits on a done task and would never touch the result, so
        # the exception would surface only as asyncio's "Task exception was
        # never retrieved" at collection time -- a traceback with no shutdown
        # around it. Retrieved and logged here instead; not re-raised, because
        # this runs inside `run_worker`'s `finally` and raising there would
        # mask whatever is actually shutting the process down.
        failure = None if work.cancelled() else work.exception()
        if failure is not None:
            _logger.error("worker.drain_loop_failed", exc_info=failure)
        else:
            _logger.info("worker.drain_complete")
        return
    _logger.warning("worker.drain_timed_out", extra={"timeout_s": consumer.drain_timeout_s})


def _on_signal(sig: signal.Signals, stopping: asyncio.Event) -> None:
    """Signal handlers run in the loop thread between callbacks, so setting
    an ``Event`` is all that is safe (and all that is needed) here -- the
    cancellation itself happens in ``run_worker``'s own ``finally``."""
    _logger.info("worker.shutdown_signal", extra={"signal": sig.name})
    stopping.set()


async def _cancel(task: asyncio.Task[None]) -> None:
    """Cancel and reap, swallowing the ``CancelledError`` that cancellation
    itself produces -- but nothing else: a task that fails DURING teardown
    has a real error to report, and it is re-raised by ``await task`` above
    when the loop is what ended, or surfaced here otherwise."""
    if task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
