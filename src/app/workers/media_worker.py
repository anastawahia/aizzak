"""``media_worker`` process entrypoint (5.1-ج · 08-local-runbook §4:
``python -m app.workers.media_worker``).

Deliberately thin (``workers/outbox_relay.py``'s own precedent): all
composition lives in ``workers/bootstrap.py``; this file only sequences
build → run → teardown. ``build_media_worker_from_env()`` is ``async`` and
**no longer raises** (step 20 of ``deferred-adapters-plan.md``,
``docs/log/3.104.md``): it binds MinIO the same async way
``build_knowledge_worker_from_env`` does, and composes
``WorkerMediaGenerator`` (``workers/media_generation.py``, step 19) over the
resolved ``image`` route — so this process is wired end to end and nothing
about it is deferred any more. It was the last of the three Streams workers
still blocked; ``bootstrap.py``'s honest-failure rule now covers no builder
at all.

What this entrypoint still deliberately does NOT catch is a genuine boot
failure: Vault unreachable, a malformed MinIO secret (``bind_minio``'s
``ValidationError``), a ``PROVIDER_ROUTING`` whose ``image`` route names a
provider with no wired adapter. Those propagate, so the process fails fast
and loudly rather than starting half-wired.

⚠️ Wired is not the same as *booted*: no ``media`` container has been started
(the plan's §0 forbids changing live system state), so the first live boot
is still ahead — exactly the state ``knowledge`` has been in since step 16.
"""

from __future__ import annotations

import asyncio

from app.workers.bootstrap import build_media_worker_from_env
from app.workers.lifecycle import run_worker


async def run() -> None:
    """Build the media worker, run it until cancelled, then close every
    resource ``build_media_worker_from_env`` handed back -- ``finally`` so a
    cancellation (graceful shutdown, e.g. SIGTERM under Compose) still tears
    down the engine and the Redis client rather than leaking connections."""
    consumer, subscriptions, disposables = await build_media_worker_from_env()
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
