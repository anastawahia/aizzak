"""The notification-bridge handler (5.3-د · 03-api-spec §3.2 · 04-event-catalog
§2/§4).

The ``cg.notify`` consumer's business logic, in ONE place and in the framework:
translate a global CloudEvents envelope arriving off a stream into a live
``notification`` push to every WebSocket session of the event's workspace.
04 §2's topology table names ``cg.notify`` as the owner of ``stream.knowledge``
and ``stream.media`` — "جسر الإشعارات → WebSocket" — and this is that bridge's
body. The composition root assembles it onto the generic ``StreamConsumer``
(the same 5.1 engine, so this path inherits idempotency-free at-least-once
delivery, the D4 policy, and the DLQ for free); this module owns only what to
DO with a delivered event.

**Why the framework, not next to the endpoint or in a worker.** The handler's
only collaborator is the ``ConnectionHub`` (also framework, for the reason its
own docstring gives: ``.importlinter`` forbids ``app.api`` from importing
``app.infrastructure``, so the hub — shared by the API's WS endpoint and this
bridge, both in the SAME process — lives where both sides can reach it). The
handler reads a plain ``Json`` envelope and calls ``hub.notify``; it imports no
infrastructure and no module, so it sits cleanly in the kernel.

**"Same process" is necessary, and was once mistaken for sufficient (P0-2,
docs/pre-release-review.md §2, docs/log/3.81.md).** A standalone worker (a
separate deployable) would indeed hold a different, empty hub with none of
this replica's live connections — that half was always right. What it
missed: under either deployment path's default ``WEB_CONCURRENCY=2``, the
"API process" is not one process either — it is several SIBLING gunicorn
workers on the same host, each already living inside the API deployable,
each with its OWN hub. "Runs in the API process" answers WHICH deployable;
it says nothing about WHICH of several OS processes inside that deployable —
that second question is what the notification bridge's consumer GROUP has to
answer instead (one group PER PROCESS, not one shared group — see
``composition_root.py``'s comment above ``_NOTIFY_STREAMS`` and
``build_notification_consumer``'s own docstring for the fix this module's
handler is agnostic to: it never sees a group name at all).

**Deliberately NOT idempotent (04 §3 / 5.2-أ, inverted).** The Streams workers
claim ``(consumer_group, event_id)`` in ``platform.processed_events`` because a
duplicate delivery would mint a duplicate DURABLE effect (a second document, a
second media job). A notification has no durable effect — it is a fire-and-
forget push to a live socket — so a rare redelivery costs at most one duplicate
UI toast, which is enormously cheaper than dragging a Postgres round-trip into
the hot path of every notification. The push is stateless by design.

**A push failure never fails the handler.** ``ConnectionHub.notify`` already
swallows and logs per-session send failures (its own docstring); this handler
adds no failure surface of its own, so it always returns cleanly and the engine
always ``XACK``\\ s. That is the correct direction for a notification: a
delivery this consumer could not complete (nobody connected, a socket that just
died) is not a reason to redeliver the event forever — the durable record of
what happened lives in the producing module's own tables, not in whether a
transient UI push landed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.framework.context.execution_context import ExecutionContext
from app.framework.streaming.hub import ConnectionHub
from app.framework.types import Json

# The four global event types 04 §4 marks for ``cg.notify`` — the worker
# results a live client cares about. Exposed so the composition root maps
# each onto this one handler across the two streams that carry them
# (``stream.knowledge`` / ``stream.media``), and so a test can assert the
# bridge covers exactly the catalog's set, no more, no less.
NOTIFY_EVENT_TYPES: tuple[str, ...] = (
    "knowledge.document.indexed.v1",
    "knowledge.document.indexing_failed.v1",
    "media.job.generated.v1",
    "media.job.failed.v1",
)

# (ctx, envelope) -> None, the engine's ``EventHandler`` shape expressed
# structurally (the framework cannot import the infrastructure type, and does
# not need to — the engine accepts any matching callable).
NotificationHandler = Callable[[ExecutionContext, Json], Awaitable[None]]


def make_notification_handler(hub: ConnectionHub) -> NotificationHandler:
    """Build the ``cg.notify`` handler bound to ``hub``.

    The engine (``consumers/engine.py``) has already validated ``type`` and
    ``workspaceid`` present and built ``ctx`` from the latter before calling
    this, so the workspace routing key is taken from ``ctx.workspace_id`` (the
    engine's own parse, not a second read of the envelope) and the event type
    from ``envelope["type"]``.

    ``data`` is read defensively: 04 §4 gives every global event a ``data``
    object, but a malformed one must not crash a stateless push — a missing or
    non-object ``data`` degrades to ``{}`` so the client still learns the event
    happened (the ``type`` alone is informative), rather than the handler
    raising and the engine redelivering a push that will never improve.
    """

    async def _handle(ctx: ExecutionContext, envelope: Json) -> None:
        event_type = envelope["type"]
        data = envelope.get("data")
        await hub.notify(
            ctx.workspace_id,
            event_type,
            data if isinstance(data, dict) else {},
        )

    return _handle
