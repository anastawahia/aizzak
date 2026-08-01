"""Usage domain events (pure — 06-domain-models §10, 04-event-catalog §5).

In-memory only: unlike files/knowledge/media, neither event here is
promoted to a Redis Stream / Outbox row in v1 (04-event-catalog §5's closing
note — "``integrations``/``usage`` events stay in-memory only, no global
stream in v1"). ``usage``'s inbound ports are synchronous and called only by
the orchestrator (INV-U4), and its capture path deliberately stays off the
event bus (INV-U5, FR-131/EVT-10). Both events exist for potential future
audit/observability wiring, not because anything in this slice publishes
them.

Deviation from the literal 06-domain-models §10 event shape (coordinator
decision, documented): ``UsageRecorded`` carries ``operation_id`` rather
than ``record_id`` — ``CaptureUsage`` never learns a ``UsageRecord.id``
(that identity is minted by the deferred SQL adapter; see
``domain/entities.py``'s ``UsageRecord`` docstring), so ``operation_id`` —
the idempotency key ``CaptureUsage`` actually has in hand — is the only
stable identifier available to carry on this event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UsageRecorded:
    """A usage charge was newly appended to the ledger (06 §10 EV; internal
    only — no wire schema, 04-event-catalog §5)."""

    operation_id: str
    workspace_id: str
    agent_key: str
    provider: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class LimitExceeded:
    """``EnforceLimit`` denied a request (06 §10 EV; internal only — no wire
    schema). ``scope``/``metric`` carry the *binding* check's string
    values — the specific rule that caused the denial."""

    workspace_id: str
    scope: str
    metric: str
    occurred_at: datetime


UsageEvent = UsageRecorded | LimitExceeded
