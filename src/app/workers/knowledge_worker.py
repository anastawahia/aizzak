"""``knowledge_worker`` process entrypoint (5.1-ج · 08-local-runbook §4:
``python -m app.workers.knowledge_worker``).

Deliberately thin (``workers/outbox_relay.py``'s own precedent): all
composition lives in ``workers/bootstrap.py``; this file only sequences
build → run → teardown. ``build_knowledge_worker_from_env()`` is now
``async`` (step 15 of ``deferred-adapters-plan.md``: it binds MinIO storage
the same async way ``CompositionRoot.connect_storage`` does) and still
raises today (``bootstrap.py``'s "Honest-failure rule" -- the index handler
needs a ``DocumentContentResolver`` that has no content-extractor DISPATCH
composition yet, a separately tracked debt item that step 15 does not
close) — this entrypoint does not catch that, so the process fails fast and
loudly on boot rather than starting half-wired.
"""

from __future__ import annotations

import asyncio

from app.workers.bootstrap import build_knowledge_worker_from_env


async def run() -> None:
    """Build the knowledge worker, run it until cancelled, then close every
    resource ``build_knowledge_worker_from_env`` handed back -- ``finally``
    so a cancellation (graceful shutdown, e.g. SIGTERM under Compose) still
    tears down the engine and the Redis client rather than leaking
    connections."""
    consumer, subscriptions, disposables = await build_knowledge_worker_from_env()
    try:
        await consumer.run(subscriptions)
    finally:
        for dispose in disposables:
            await dispose()


if __name__ == "__main__":
    asyncio.run(run())
