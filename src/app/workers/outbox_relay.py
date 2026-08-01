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

from app.workers.bootstrap import build_relay_from_env


async def run() -> None:
    """Build the relay, run it until cancelled, then close every resource
    ``build_relay_from_env`` handed back — ``finally`` so a cancellation
    (graceful shutdown, e.g. SIGTERM under Compose) still tears down the
    engine and the Redis client rather than leaking connections.
    """
    relay, disposables = build_relay_from_env()
    try:
        await relay.run_forever()
    finally:
        for dispose in disposables:
            await dispose()


if __name__ == "__main__":
    asyncio.run(run())
