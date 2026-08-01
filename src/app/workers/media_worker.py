"""``media_worker`` process entrypoint (5.1-ج · 08-local-runbook §4:
``python -m app.workers.media_worker``).

Deliberately thin (``workers/outbox_relay.py``'s own precedent): all
composition lives in ``workers/bootstrap.py``; this file only sequences
build → run → teardown. ``build_media_worker_from_env()`` raises today
(``bootstrap.py``'s "Honest-failure rule" -- no real ``MediaGenerator``
adapter exists yet) — this entrypoint does not catch that, so the process
fails fast and loudly on boot rather than starting half-wired.
"""

from __future__ import annotations

import asyncio

from app.workers.bootstrap import build_media_worker_from_env


async def run() -> None:
    """Build the media worker, run it until cancelled, then close every
    resource ``build_media_worker_from_env`` handed back -- ``finally`` so a
    cancellation (graceful shutdown, e.g. SIGTERM under Compose) still tears
    down the engine and the Redis client rather than leaking connections."""
    consumer, subscriptions, disposables = build_media_worker_from_env()
    try:
        await consumer.run(subscriptions)
    finally:
        for dispose in disposables:
            await dispose()


if __name__ == "__main__":
    asyncio.run(run())
