"""``electrical`` worker process entrypoint (WireTwin CMP-09: ``python -m
app.workers.electrical_worker``).

Deliberately thin (``memory_worker.py``'s precedent): all composition lives in
``workers/electrical_bootstrap.py``; this file only sequences build -> run -> teardown.
"""

from __future__ import annotations

import asyncio

from app.workers.electrical_bootstrap import build_electrical_worker_from_env
from app.workers.lifecycle import run_worker


async def run() -> None:
    """Build the electrical worker, run it until cancelled, then close every resource
    ``build_electrical_worker_from_env`` handed back -- ``finally`` so a cancellation (graceful
    shutdown under Compose) still closes the engine, the HTTP client and Redis."""
    consumer, subscriptions, disposables = await build_electrical_worker_from_env()
    try:
        await run_worker(consumer, subscriptions)
    finally:
        for dispose in disposables:
            await dispose()


if __name__ == "__main__":
    asyncio.run(run())
