"""Ambient correlation identifiers for logging (07-nfr-slo §7, 10 §10).

These context variables are set at the API/worker boundary and read by the log
formatter so every line carries ``correlation_id``/``workspace_id`` without
threading them through business signatures. They are an *observability* aid
only — authoritative identity always travels explicitly in ``ExecutionContext``.

**Capacity 0.6 made them actually load-bearing.** Until then nothing in the
codebase ever *set* them: the formatter read three context variables that were
always ``None``, so every JSON line the platform emitted carried no correlation id
at all. The mechanism was complete and the wiring was missing, which is the
worst of the three possible states -- a reader of ``logging.py`` would have
concluded correlation ids were shipping. They are set in exactly three places
now, and each is named in ``log_context``'s docstring below.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)
# The event a worker is handling right now (capacity 0.6, and the third
# identifier `docs/capacity-plan.md` names for the aggregated store). It has no
# meaning on the API side -- an HTTP request is not an event -- so it is bound
# by the consumer engine alone and simply absent from every app line.
event_id_var: ContextVar[str | None] = ContextVar("event_id", default=None)


@contextmanager
def log_context(
    *,
    correlation_id: str | None = None,
    workspace_id: str | None = None,
    event_id: str | None = None,
    request_id: str | None = None,
) -> Iterator[None]:
    """Bind the given identifiers for the duration of the block, then restore.

    ``ContextVar.set`` returns a token and this restores every one of them on
    exit, INCLUDING on an exception -- which is what makes the helper safe in
    the one place that genuinely needs it: the consumer engine's dispatch loop
    is a single long-lived task that handles message after message in the same
    context, so a bare ``set()`` there would leave the previous message's
    correlation id stamped on every line the loop emitted between deliveries.
    (The HTTP middleware has the opposite shape -- each request already runs in
    its own context copy -- and sets the variables directly.)

    The three binding sites, so this is greppable from here:

    * ``api/main.py``'s correlation middleware -- request id and correlation id,
      taken from the edge's headers or minted.
    * ``api/v1/dependencies.py``'s ``current_context`` -- the workspace id, from
      the PRINCIPAL and never from the client.
    * ``infrastructure/messaging/consumers/engine.py`` -- all three, from the
      envelope, around one handler dispatch. This helper's caller.

    ``None`` for an argument means "leave whatever is bound alone", not "clear
    it": a worker envelope without a ``correlationid`` extension must not erase
    an id an outer scope established.
    """
    tokens = [
        (var, var.set(value))
        for var, value in (
            (correlation_id_var, correlation_id),
            (workspace_id_var, workspace_id),
            (event_id_var, event_id),
            (request_id_var, request_id),
        )
        if value is not None
    ]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
