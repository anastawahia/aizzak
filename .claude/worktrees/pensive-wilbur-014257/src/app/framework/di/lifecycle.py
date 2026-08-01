"""Process-teardown seam shared by every composition root (10 §4).

``Disposable`` started life inside ``workers/bootstrap.py`` (5.1-ب), where the
relay entrypoint needed a way to hand its raw clients back to the process that
built them. The API server needs the SAME thing for the SAME reason, so the
alias lives here — one seam, imported by both roots, rather than two
structurally identical aliases drifting apart. ``AsyncEngine.dispose`` /
``Redis.aclose`` / ``AsyncQdrantClient.close`` / ``httpx.AsyncClient.aclose``
are all already exactly this shape as bound methods (every argument they take
has a default), so a root can put them straight into a list.

``dispose_all`` adds the one behaviour a bare ``for``-loop cannot give: a
disposal that RAISES must not strand the ones after it. A shutdown path is the
worst possible place for fail-fast — the process is going away regardless, and
the only thing at stake is whether the OTHER clients get to close their sockets
and let the server side reclaim them. Every failure is logged and swallowed;
nothing here can turn a clean shutdown into a crash.

``CancelledError`` is caught SEPARATELY, remembered, and re-raised once the
whole list has been attempted: teardown often runs inside a task that is itself
being cancelled, and a ``CancelledError`` escaping the first client's
``aclose`` would skip every client after it — the exact leak this function
exists to prevent — while swallowing it outright would break cooperative
cancellation. ``KeyboardInterrupt``/``SystemExit`` are deliberately NOT caught:
an operator interrupting a shutdown wants out now.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable

from app.framework.observability import get_logger

_logger = get_logger(__name__)

# A resource-teardown thunk. See the module docstring for why no wrapping is
# needed to put a client's own `close`/`aclose`/`dispose` bound method here.
Disposable = Callable[[], Awaitable[None]]


async def dispose_all(disposables: Iterable[Disposable]) -> None:
    """Run every teardown thunk, in order, isolating each one's failure.

    Sequential, not ``asyncio.gather``: the list is a handful of client
    shutdowns, ordering between them is irrelevant (disposing an engine and
    closing a Redis client are independent), and a sequential loop keeps the
    failure attribution — which client refused to close — unambiguous in the
    logs.
    """
    cancelled: asyncio.CancelledError | None = None
    for dispose in disposables:
        try:
            await dispose()
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception as exc:
            _logger.warning(
                "lifecycle.dispose_failed",
                extra={"disposable": getattr(dispose, "__qualname__", repr(dispose))},
                exc_info=exc,
            )
    if cancelled is not None:
        raise cancelled
