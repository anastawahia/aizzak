"""Unified worker dispatcher (5.1-ج · 08-local-runbook §4: "نقطة الدخول
الموحّدة ``app/workers/main.py`` تختار العامل حسب وسيط/متغيّر بيئة").

Each worker also still runs standalone (``python -m app.workers.<name>``, 08
§4's primary invocation form, entirely unchanged by this file's addition) --
this module is the SAME image's alternate single entrypoint for deploy
topologies that prefer one Docker ``CMD``
(``WORKER=<name> python -m app.workers.main``) over a different ``command:``
per Compose service. ``outbox_relay`` is included among the selectable names
because 08 §4 lists it alongside the three Streams workers as one of "أوامر
العمّال"; in practice D-26 still runs it as its own single-instance Compose
service with its OWN dedicated command (``workers/outbox_relay.py``'s own
docstring) -- this dispatcher merely makes that choice available too, it
does not require anyone to use it for the relay specifically.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

from app.workers import knowledge_worker, media_worker, memory_worker, outbox_relay

_RUNNERS: dict[str, Callable[[], Awaitable[None]]] = {
    "knowledge": knowledge_worker.run,
    "media": media_worker.run,
    "memory": memory_worker.run,
    "outbox_relay": outbox_relay.run,
}


def _valid_names() -> str:
    return ", ".join(sorted(_RUNNERS))


def _select_worker() -> str:
    """``WORKER`` env var first (the Compose/Docker ``CMD`` shape this
    module's docstring describes); ``sys.argv[1]`` otherwise (a plain CLI
    invocation, ``python -m app.workers.main knowledge``). Neither present
    -> a clear, immediate error naming every valid choice."""
    env_choice = os.environ.get("WORKER")
    if env_choice:
        return env_choice
    if len(sys.argv) > 1:
        return sys.argv[1]
    raise SystemExit(
        f"no worker selected: set WORKER=<name> or pass one argument "
        f"-- valid names: {_valid_names()}"
    )


async def run() -> None:
    """Dispatch to exactly one worker's own ``run()`` coroutine, selected by
    ``_select_worker``. An unrecognised name is a clear, immediate error
    listing every valid choice -- never a silent no-op."""
    name = _select_worker()
    runner = _RUNNERS.get(name)
    if runner is None:
        raise SystemExit(f"unknown worker {name!r} -- valid names: {_valid_names()}")
    await runner()


if __name__ == "__main__":
    asyncio.run(run())
