"""Map memory domain events onto Outbox records (D-18 · 04-event-catalog §1/§2).

The seam between a PURE domain event (``domain/events.py``: no framework
imports, no wire concerns) and the row the ``outbox_relay`` will publish. It
belongs to the application layer because it mints identity (``new_uuid7``)
and reads the request's ``ExecutionContext`` — neither of which the domain
may touch. The ``media``/``files`` precedent (``modules/media/application/
event_mapping.py``) is followed faithfully: exhaustive ``match`` +
``assert_never``, one event id minted per record, ``correlationid`` set from
the context here rather than left to the adapter.

**Not every ``MemoryEvent`` becomes a wire event.** 04 §5 marks
``MemoryStored`` with a promotion asterisk (*) but ``MemoryForgotten``
without one — it stays internal-only (INV-M2's eventual Qdrant cleanup is a
future worker concern, not a wire event in v1), so ``to_outbox_record``
returns ``None`` for it and callers (``RememberInteractionService`` today; a
future ``ForgetMemory``-backed service) must filter ``None`` out before
calling ``EventOutbox.append`` — the ``files.FileDeleted`` precedent exactly.

``data`` omits the published schema's optional ``content_ref``
(``docs/design/events/schemas/memory.item.stored.v1.json``): ``MemoryStored``
itself carries no such field (the domain event only ever has
``memory_id``/``workspace_id``/``agent_key``/``occurred_at``), so there is
nothing to propagate.

**``correlationid`` is set from the context here, not left to the adapter.**
``SqlEventOutbox`` writes a ``correlation_id`` COLUMN, but that column is the
relay's own bookkeeping: the relay republishes ``payload`` *verbatim*, so a
correlation id that lives only in the column never reaches a consumer.
"""

from __future__ import annotations

from typing import assert_never

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.types import Json
from app.modules.memory.domain.events import MemoryEvent, MemoryForgotten, MemoryStored

# The producing module's name (04 §1: `"source": "memory"`), its stream (04
# §2: `stream.memory`, consumer group `cg.memory`), and the aggregate these
# events are about.
SOURCE = "memory"
STREAM = "stream.memory"
AGGREGATE_TYPE = "memory_item"


def to_outbox_record(ctx: ExecutionContext, event: MemoryEvent) -> OutboxRecord | None:
    """Build the publishable record for one memory domain event, or ``None``
    for one that never crosses the wire.

    The ``match`` is EXHAUSTIVE over ``MemoryEvent`` (``assert_never`` on the
    fallthrough): adding a third memory event turns this function red under
    mypy instead of silently dropping it.
    """
    match event:
        case MemoryStored():
            event_type = "memory.item.stored.v1"
            data: Json = {"memory_id": event.memory_id, "agent_key": event.agent_key}
        case MemoryForgotten():
            return None
        case _:  # pragma: no cover - mypy proves this is unreachable
            assert_never(event)

    event_id = new_uuid7()
    return OutboxRecord(
        event_id=event_id,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=event.memory_id,
        event_type=event_type,
        stream=STREAM,
        payload=build_envelope(
            event_id=event_id,
            source=SOURCE,
            event_type=event_type,
            subject=event.memory_id,
            # The DOMAIN's instant, not `utc_now()` here: `occurred_at` is
            # when the thing happened, and re-reading the clock at mapping
            # time would publish a timestamp that drifts from the
            # aggregate's own row.
            occurred_at=event.occurred_at,
            # The event's own workspace, which the use-case took from this
            # same context -- an event never crosses tenants.
            workspace_id=event.workspace_id,
            data=data,
            correlation_id=ctx.correlation_id,
        ),
    )
