"""``knowledge_worker`` process entrypoint (5.1-ج · 08-local-runbook §4:
``python -m app.workers.knowledge_worker``).

Deliberately thin (``workers/outbox_relay.py``'s own precedent): all
composition lives in ``workers/bootstrap.py``; this file only sequences
build → run → teardown. ``build_knowledge_worker_from_env()`` is ``async``
(step 15 of ``deferred-adapters-plan.md``: it binds MinIO storage the same
async way ``CompositionRoot.connect_storage`` does) and **no longer raises**
(step 16, ``docs/log/3.100.md``): ``WorkerDocumentContentResolver``
(``workers/content_resolver.py``) fills the last seam — file fetch + the
3.k1 parser dispatch table + embedding-route resolution — so this process
is wired end to end and nothing about it is deferred any more.

What this entrypoint still deliberately does NOT catch is a genuine boot
failure: Vault unreachable, a malformed MinIO secret (``bind_minio``'s
``ValidationError``), a ``PROVIDER_ROUTING`` naming a provider with no wired
adapter. Those propagate, so the process fails fast and loudly rather than
starting half-wired.

⚠️ Wired is not the same as *booted*: no ``knowledge`` container has been
started since step 16 (that needs a separate, explicit go-ahead — the plan's
own §3 note), so the first live boot is still ahead.
"""

from __future__ import annotations

import asyncio

from app.workers.bootstrap import build_knowledge_worker_from_env
from app.workers.lifecycle import run_worker


async def run() -> None:
    """Build the knowledge worker, run it until cancelled, then close every
    resource ``build_knowledge_worker_from_env`` handed back -- ``finally``
    so a cancellation (graceful shutdown, e.g. SIGTERM under Compose) still
    tears down the engine and the Redis client rather than leaking
    connections."""
    consumer, subscriptions, disposables = await build_knowledge_worker_from_env()
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
