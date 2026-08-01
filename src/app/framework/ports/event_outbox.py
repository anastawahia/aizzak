"""EventOutbox driven port — the PRODUCER half of D-18 (01-data-model §4.1 ·
04-event-catalog §3.1).

A business module never publishes to Redis itself (02-port-contracts §1.8:
"الوحدات لا تنشر مباشرة بل تكتب Outbox") — it appends the CloudEvents envelope
to ``platform.outbox``, and the single-instance ``outbox_relay`` (Phase 5.1)
forwards it with ``XADD`` and stamps ``published_at``. So the two halves of the
split are two ports: **this** one is written by producers; ``EventPublisher``
(``ports/event_publisher.py``) is used only by the relay.

**Fields deliberately absent from ``OutboxRecord``:**

* ``workspace_id`` and ``correlation_id`` — already carried by the
  ``ExecutionContext`` every ``append`` takes, so the adapter reads them from
  there. Duplicating them on the record would let a caller pass a workspace id
  that disagrees with the tenant session it is being written under, which is
  precisely the inconsistency the shared context exists to prevent.
* ``causation_id`` — nothing in the platform produces one yet (no event is
  currently emitted *because of* another event). Adding an always-``None``
  field would be unconsumed surface (12-module-authoring-guide); it is purely
  additive whenever the first chained producer appears, and both the column and
  the envelope's ``causationid`` extension already accept it.
* ``created_at``/``published_at``/``attempts`` — relay-owned or
  database-defaulted; see ``infrastructure/persistence/outbox.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json, Uuid


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """One row-to-be in ``platform.outbox`` (01 §4.1, column for column).

    ``event_id`` is the row's primary key, the CloudEvents ``id`` inside
    ``payload``, and the consumer's idempotency key (DD-09) — all one UUIDv7.
    ``payload`` is the COMPLETE envelope (``events/envelope.build_envelope``),
    stored and republished verbatim.
    """

    event_id: Uuid
    aggregate_type: str
    aggregate_id: Uuid
    event_type: str
    stream: str
    payload: Json


class EventOutbox(Protocol):
    """Append envelopes to the outbox within the caller's transaction.

    ``append`` takes a SEQUENCE because a single use-case can emit more than
    one event, and they must land together or not at all — 04 §3.1's first
    guarantee ("الأثر النطاقي + صف Outbox في نفس المعاملة ⇒ لا فقد").

    An empty sequence is a no-op, not an error: use-cases return
    ``tuple[Aggregate, tuple[Event, ...]]`` and some paths legitimately produce
    zero events (e.g. ``RunMediaJob`` short-circuiting on an already-terminal
    job), so every caller would otherwise need the same guard.
    """

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None: ...
