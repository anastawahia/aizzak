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
its own: ``StreamConsumer.deregister`` before the clients close. It does NOT
add a timeout of its own -- the process supervisor already owns that
(Compose's ``stop_grace_period``, 10 s by default, then ``SIGKILL``), and a
second, shorter deadline here would only create a way for shutdown to be cut
off earlier than the operator configured. The steps on this path are two
Redis round trips per group; if they cannot finish inside the supervisor's
grace period, Redis is down and the tombstone is the least of the problems.

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

    A signal is delivered by cancelling the read loop -- the same
    ``CancelledError`` the loop was already documented to propagate
    (``StreamConsumer.run``), so nothing about the engine changes. A crash
    inside the loop propagates unchanged too, AFTER the deregistration: a
    worker that dies of a bug still owes Redis the same cleanup as one that
    was asked to stop, and the entries it still holds pending are protected
    by ``deregister``'s own refusal rule rather than by skipping the step.
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
        await _cancel(work)
        # Safe to await inside this `finally`: the cancellation that gets here
        # is one THIS function issued against `work`, never against its own
        # task, so nothing is pending on the current coroutine. `deregister`
        # additionally swallows its own failures (its docstring), so the exit
        # path cannot be masked by a Redis error during cleanup.
        await consumer.deregister(subscriptions)


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
