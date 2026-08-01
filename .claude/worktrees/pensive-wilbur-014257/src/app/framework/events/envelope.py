"""CloudEvents 1.0 envelope for the transactional Outbox (DD-07 · 04-event-catalog §1).

Every *global* event crosses its Redis stream as a full CloudEvents 1.0
envelope, stored verbatim in ``platform.outbox.payload`` and republished
unchanged by the ``outbox_relay`` (04 §1: "المظروف كامل يُخزَّن في
``platform.outbox.payload`` ويُنشر كما هو"). Building it in exactly ONE place
is what keeps the wire format identical across the four producing modules —
``files``, ``knowledge``, ``media``, ``memory`` — none of which can import each
other (12-module-authoring-guide §3), so a per-module copy would drift by
construction.

Kernel-pure: stdlib + framework types only. This module knows nothing about any
module's events; each producer maps its own domain event onto the
``event_type``/``subject``/``data`` arguments and owns its payload schema under
``docs/design/events/schemas/``.

Three CloudEvents **extension** attributes carry what the spec's core
attributes cannot (04 §1): ``workspaceid`` (tenant routing + the worker's RLS
scope), ``correlationid`` and ``causationid`` (tracing). The CloudEvents spec
constrains extension names to lowercase alphanumerics — hence no underscores,
unlike every other identifier in this codebase. Absent optional extensions are
**omitted**, never emitted as ``null``: the published envelope schema is
``additionalProperties: false`` with typed (non-nullable) extension properties,
so a ``null`` would fail validation rather than read as "unknown".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.framework.errors import AppError
from app.framework.types import Json, Uuid

SPEC_VERSION = "1.0"
DATA_CONTENT_TYPE = "application/json"
SCHEMA_BASE = "https://schemas.platform/"

# Copied VERBATIM from the published envelope schema's `type` pattern
# (docs/design/events/schemas/envelope.cloudevents.json). `test_event_envelope`
# asserts the two strings are still identical, so this copy cannot drift away
# from the contract it enforces.
EVENT_TYPE_PATTERN = r"^[a-z]+\.[a-z_]+\.[a-z_]+\.v[0-9]+$"
_EVENT_TYPE_RE = re.compile(EVENT_TYPE_PATTERN)


def _rfc3339(moment: datetime) -> str:
    """CloudEvents ``time``: RFC 3339 UTC (04 §1's example ends in ``Z``).

    A NAIVE datetime is rejected rather than assumed-UTC (DD-03: timestamps are
    timezone-aware UTC everywhere). Assuming would be the worse failure — the
    event would publish a plausible-looking but silently wrong instant, and
    nothing downstream could ever detect it.
    """
    if moment.tzinfo is None:
        raise AppError(
            "event timestamps must be timezone-aware (DD-03)",
            code="common.internal",
        )
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_envelope(
    *,
    event_id: Uuid,
    source: str,
    event_type: str,
    subject: Uuid,
    occurred_at: datetime,
    workspace_id: Uuid,
    data: Json,
    correlation_id: Uuid | None = None,
    causation_id: Uuid | None = None,
) -> Json:
    """Assemble one CloudEvents 1.0 envelope (04 §1, field for field).

    ``event_id`` doubles as the outbox row's primary key AND the consumer-side
    idempotency key (``platform.processed_events.event_id``, DD-09) — hence one
    UUIDv7 minted by the producer, never two.

    ``dataschema`` is derived from ``event_type`` rather than passed in: the
    published schemas are named ``<event_type>.json`` by convention
    (``media.job.requested.v1.json``), so deriving it removes the only way for
    an event to advertise a schema that does not describe it.

    ``event_type`` is validated against the published envelope schema's own
    pattern. A typo'd type is not a cosmetic defect — streams and consumer
    groups route on it, so a malformed one produces an event nothing will ever
    consume, discovered (if ever) in production. The check costs one regex per
    *heavy* operation, which is the only kind of operation that becomes a
    global event at all (EVT-10).
    """
    if not _EVENT_TYPE_RE.match(event_type):
        raise AppError(
            f"invalid event type {event_type!r}: expected <module>.<aggregate>.<event>.vN",
            code="common.internal",
        )

    envelope: Json = {
        "specversion": SPEC_VERSION,
        "id": event_id,
        "source": source,
        "type": event_type,
        "subject": subject,
        "time": _rfc3339(occurred_at),
        "datacontenttype": DATA_CONTENT_TYPE,
        "dataschema": f"{SCHEMA_BASE}{event_type}.json",
        "workspaceid": workspace_id,
    }
    if correlation_id is not None:
        envelope["correlationid"] = correlation_id
    if causation_id is not None:
        envelope["causationid"] = causation_id
    # `data` last so the stored/published JSON reads envelope-then-payload,
    # matching the catalog's §1 example.
    envelope["data"] = data
    return envelope
