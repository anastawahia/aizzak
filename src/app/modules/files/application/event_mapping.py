"""Map files domain events onto Outbox records (D-18 · 04-event-catalog §1/§2).

The seam between a PURE domain event (``domain/events.py``: no framework
imports, no wire concerns) and the row the ``outbox_relay`` will publish. It
belongs to the application layer because it mints identity (``new_uuid7``)
and reads the request's ``ExecutionContext`` — neither of which the domain
may touch. The ``media`` precedent (``modules/media/application/
event_mapping.py``) is followed faithfully: exhaustive ``match`` +
``assert_never``, one event id minted per record, ``correlationid`` set from
the context here rather than left to the adapter.

**Not every ``FileEvent`` becomes a wire event.** 04 §5 marks ``FileUploaded``
with a promotion asterisk (*) but ``FileDeleted`` without one — it stays
internal-only, so ``to_outbox_record`` returns ``None`` for it and callers
(``CompleteUploadService`` today; a future ``SoftDeleteFile``-backed service)
must filter ``None`` out before calling ``EventOutbox.append``, exactly the
way ``RunMediaJob`` already returns zero events for an already-terminal job.

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
from app.modules.files.domain.events import FileDeleted, FileEvent, FileUploaded

# The producing module's name (04 §1: `"source": "files"`), its stream (04
# §2: `stream.files`, consumer group `cg.knowledge`), and the aggregate these
# events are about.
SOURCE = "files"
STREAM = "stream.files"
AGGREGATE_TYPE = "file"


def to_outbox_record(ctx: ExecutionContext, event: FileEvent) -> OutboxRecord | None:
    """Build the publishable record for one files domain event, or ``None``
    for one that never crosses the wire.

    The ``match`` is EXHAUSTIVE over ``FileEvent`` (``assert_never`` on the
    fallthrough): adding a third files event turns this function red under
    mypy instead of silently dropping it. ``data`` mirrors
    ``docs/design/events/schemas/files.file.uploaded.v1.json`` field for
    field — exactly its four required properties, no more (the schema's
    optional ``checksum`` is deliberately not propagated here; 04 §4's own
    catalog entry for this event lists only these four).
    """
    match event:
        case FileUploaded():
            event_type = "files.file.uploaded.v1"
            data: Json = {
                "file_id": event.file_id,
                "content_type": event.content_type,
                "size_bytes": event.size_bytes,
                "storage_key": event.storage_key,
            }
        case FileDeleted():
            return None
        case _:  # pragma: no cover - mypy proves this is unreachable
            assert_never(event)

    event_id = new_uuid7()
    return OutboxRecord(
        event_id=event_id,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=event.file_id,
        event_type=event_type,
        stream=STREAM,
        payload=build_envelope(
            event_id=event_id,
            source=SOURCE,
            event_type=event_type,
            subject=event.file_id,
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
