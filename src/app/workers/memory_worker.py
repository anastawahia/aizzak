"""``memory_worker`` process entrypoint (5.1-ج · 08-local-runbook §4:
``python -m app.workers.memory_worker``).

Deliberately thin (``workers/outbox_relay.py``'s own precedent): all
composition lives in ``workers/bootstrap.py``; this file only sequences
build → run → teardown. ``build_memory_worker_from_env()`` no longer raises
as of 2.10 (``bootstrap.py``'s own module docstring: the ``EmbeddingProvider``
gap it used to name, ``external_embedding.py`` being 0 bytes, is closed) --
this entrypoint boots the memory worker for real.
"""

from __future__ import annotations

import asyncio

from app.workers.bootstrap import build_memory_worker_from_env
from app.workers.lifecycle import run_worker


async def run() -> None:
    """Build the memory worker, run it until cancelled, then close every
    resource ``build_memory_worker_from_env`` handed back -- ``finally`` so a
    cancellation (graceful shutdown, e.g. SIGTERM under Compose) still tears
    down the engine and the Redis client rather than leaking connections."""
    consumer, subscriptions, disposables = build_memory_worker_from_env()
    try:
        # ت-2: `run_worker`, not `consumer.run`, so SIGTERM becomes an
        # ordinary cancellation and this process removes its own Redis
        # consumer entry on the way out (`workers/lifecycle.py`) -- without
        # it the `finally` below never ran at all under `docker stop`.
        await run_worker(consumer, subscriptions)
    finally:
        for dispose in disposables:
            await dispose()


if __name__ == "__main__":
    asyncio.run(run())
