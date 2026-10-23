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
concluded correlation ids were shipping. Every place that sets them is named
in ``log_context``'s docstring below.
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
# The agent turn a line belongs to (logging-restructure phase 1, م-10). Bound
# by the orchestrator as a turn opens, so the retrieval, provider and agent
# lines of one turn can be gathered by conversation as well as by request.
conversation_id_var: ContextVar[str | None] = ContextVar("conversation_id", default=None)
agent_key_var: ContextVar[str | None] = ContextVar("agent_key", default=None)


def bind_turn(*, agent_key: str, conversation_id: str | None = None) -> None:
    """Bind the agent turn for every line written from here on in this task.

    A plain ``set()``, NOT ``log_context``, and the shape is forced: an agent
    turn is an async generator, and the SSE layer pulls each of its events in
    a task of its own (``api/v1/sse.py``), so a value set inside the generator
    would live in one pull's context copy and be gone by the next, and a
    restoring ``reset`` would raise for a token made in another context. Bound
    instead BEFORE the stream is handed out, in the request's own task, it is
    inherited by every task the stream later runs in. Safe for the reason the
    HTTP middleware gives: each request runs in its own context copy. The
    orchestrator is the only caller, and it runs in the API process only.

    Unlike ``log_context``, ``None`` CLEARS ``conversation_id``: a WebSocket
    runs turn after turn in one task, and a new thread (no id until the
    orchestrator opens it, which binds again then) must not inherit the id of
    the turn before it.
    """
    agent_key_var.set(agent_key)
    conversation_id_var.set(conversation_id)


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

    The binding sites, so this is greppable from here:

    * ``api/main.py``'s correlation middleware -- request id and correlation id,
      taken from the edge's headers or minted.
    * ``api/v1/dependencies.py``'s ``current_principal`` -- the workspace id,
      from the PRINCIPAL and never from the client.
    * ``infrastructure/messaging/consumers/engine.py`` -- all three, from the
      envelope, around one handler dispatch. This helper's caller.
    * ``agents/orchestrator.py`` -- the conversation id and agent key, through
      ``bind_turn`` above.

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
