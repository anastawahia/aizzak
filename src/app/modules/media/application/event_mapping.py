"""Map media domain events onto Outbox records (D-18 · 04-event-catalog §1/§2).

The seam between a PURE domain event (``domain/events.py``: no framework
imports, no wire concerns) and the row the ``outbox_relay`` will publish. It
belongs to the application layer because it mints identity (``new_uuid7``) and
reads the request's ``ExecutionContext`` — neither of which the domain may
touch.

Each module owns this mapping for its own events; there is no shared "event
registry" to grow, because no module may import another's domain
(12-module-authoring-guide §3). What IS shared is the envelope assembly
(``framework.events.envelope``), so the wire format cannot drift between
producers even though the mappings are separate.

**``correlationid`` is set from the context here, not left to the adapter.**
``SqlEventOutbox`` writes a ``correlation_id`` COLUMN, but that column is the
relay's own bookkeeping: the relay republishes ``payload`` *verbatim*, so a
correlation id that lives only in the column never reaches a consumer. The
trace would break exactly at the async boundary where it is most needed.
"""

from __future__ import annotations

from typing import assert_never

from app.framework.context.execution_context import ExecutionContext
from app.framework.events.envelope import build_envelope
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.types import Json
from app.modules.media.domain.events import (
    MediaEvent,
    MediaFailed,
    MediaGenerated,
    MediaRequested,
)

# The producing module's name (04 §1: `"source": "knowledge"` — «اسم الوحدة
# المُنتِجة»), its stream (04 §2: `stream.media`, consumer group `cg.media`),
# and the aggregate these events are about.
SOURCE = "media"
STREAM = "stream.media"
AGGREGATE_TYPE = "media_job"


def to_outbox_record(ctx: ExecutionContext, event: MediaEvent) -> OutboxRecord:
    """Build the complete, publishable record for one media domain event.

    The ``match`` is EXHAUSTIVE over ``MediaEvent`` (``assert_never`` on the
    fallthrough), so adding a fourth media event turns *this* function red under
    mypy rather than silently dropping the new event on the floor — the failure
    mode a dict lookup or an ``if/elif`` chain would have hidden until
    production.

    ``event_id`` is minted once and used in three places at once: the outbox
    row's primary key, the envelope's CloudEvents ``id``, and the consumer's
    idempotency key (``platform.processed_events``, DD-09). Minting a second id
    for any of them would break redelivery detection.
    """
    match event:
        case MediaRequested():
            event_type = "media.job.requested.v1"
            data: Json = {
                "job_id": event.job_id,
                "kind": event.kind,
                "prompt": event.prompt,
                "agent_key": event.agent_key,
                "params": event.params,
            }
        case MediaGenerated():
            event_type = "media.job.generated.v1"
            data = {"job_id": event.job_id, "result_file_id": event.result_file_id}
        case MediaFailed():
            event_type = "media.job.failed.v1"
            data = {"job_id": event.job_id, "reason": event.reason}
        case _:  # pragma: no cover - mypy proves this is unreachable
            assert_never(event)

    event_id = new_uuid7()
    return OutboxRecord(
        event_id=event_id,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=event.job_id,
        event_type=event_type,
        stream=STREAM,
        payload=build_envelope(
            event_id=event_id,
            source=SOURCE,
            event_type=event_type,
            subject=event.job_id,
            # The DOMAIN's instant, not `utc_now()` here: `occurred_at` is when
            # the thing happened, and re-reading the clock at mapping time would
            # publish a timestamp that drifts from the aggregate's own row.
            occurred_at=event.occurred_at,
            # The event's own workspace, which the use-case took from this same
            # context -- an event never crosses tenants.
            workspace_id=event.workspace_id,
            data=data,
            correlation_id=ctx.correlation_id,
        ),
    )
