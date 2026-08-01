"""Unit tests for the memory OUTBOX seam (5.1-أ): the domain-event → outbox
record mapping (``modules/memory/application/event_mapping.py``).

The envelopes built here are validated against the PUBLISHED schema files
themselves (``docs/design/events/schemas/``) rather than hand-copied
expectations, so a change to the contract that the code does not follow turns
these red -- the ``test_media_outbox_seam.py`` precedent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json
from app.modules.memory.application.event_mapping import (
    AGGREGATE_TYPE,
    SOURCE,
    STREAM,
    to_outbox_record,
)
from app.modules.memory.domain.events import MemoryForgotten, MemoryStored

_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "docs" / "design" / "events" / "schemas"
_OCCURRED_AT = datetime(2026, 7, 19, 9, 30, tzinfo=UTC)


def _load_schema(filename: str) -> Json:
    schema: Json = json.loads((_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    return schema


def _ctx(*, workspace_id: str = "ws-1", correlation_id: str = "corr-1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u-1",
        correlation_id=correlation_id,
        roles=frozenset({"member"}),
    )


def _stored(*, memory_id: str = "mem-1", workspace_id: str = "ws-1") -> MemoryStored:
    return MemoryStored(
        memory_id=memory_id,
        workspace_id=workspace_id,
        agent_key="rag-agent",
        occurred_at=_OCCURRED_AT,
    )


def test_stored_event_maps_to_the_memory_stream_and_aggregate() -> None:
    record = to_outbox_record(_ctx(), _stored(memory_id="mem-7"))

    assert record is not None
    assert record.stream == STREAM == "stream.memory"
    assert record.aggregate_type == AGGREGATE_TYPE == "memory_item"
    assert record.aggregate_id == "mem-7"
    assert record.event_type == "memory.item.stored.v1"


def test_forgotten_event_maps_to_none() -> None:
    """``MemoryForgotten`` is internal-only (04 §5: no promotion asterisk) --
    the caller must filter this out before appending to the outbox."""
    forgotten = MemoryForgotten(memory_id="mem-1", occurred_at=_OCCURRED_AT)

    assert to_outbox_record(_ctx(), forgotten) is None


def test_stored_payload_validates_against_the_published_envelope_and_data_schemas() -> None:
    record = to_outbox_record(_ctx(), _stored())
    assert record is not None

    jsonschema.Draft202012Validator(_load_schema("envelope.cloudevents.json")).validate(
        record.payload
    )
    jsonschema.Draft202012Validator(_load_schema("memory.item.stored.v1.json")).validate(
        record.payload["data"]
    )


def test_stored_data_omits_the_optional_content_ref() -> None:
    """The schema's ``content_ref`` is optional and ``MemoryStored`` itself
    carries no such field -- nothing to propagate."""
    record = to_outbox_record(_ctx(), _stored(memory_id="mem-3"))
    assert record is not None

    assert record.payload["data"] == {"memory_id": "mem-3", "agent_key": "rag-agent"}


def test_envelope_id_is_the_row_id_and_the_idempotency_key() -> None:
    record = to_outbox_record(_ctx(), _stored())
    assert record is not None

    assert record.payload["id"] == record.event_id


def test_envelope_names_the_producing_module_and_the_subject_memory_item() -> None:
    record = to_outbox_record(_ctx(), _stored(memory_id="mem-3"))
    assert record is not None

    assert record.payload["source"] == SOURCE == "memory"
    assert record.payload["subject"] == "mem-3"


def test_correlation_id_travels_inside_the_payload_not_only_the_column() -> None:
    record = to_outbox_record(_ctx(correlation_id="corr-abc"), _stored())
    assert record is not None

    assert record.payload["correlationid"] == "corr-abc"


def test_envelope_carries_the_events_own_workspace() -> None:
    record = to_outbox_record(_ctx(workspace_id="ws-9"), _stored(workspace_id="ws-9"))
    assert record is not None

    assert record.payload["workspaceid"] == "ws-9"


def test_envelope_time_is_the_domain_instant_not_the_mapping_instant() -> None:
    record = to_outbox_record(_ctx(), _stored())
    assert record is not None

    assert record.payload["time"] == "2026-07-19T09:30:00Z"


def test_each_mapping_mints_a_fresh_event_id() -> None:
    event = _stored()

    first = to_outbox_record(_ctx(), event)
    second = to_outbox_record(_ctx(), event)
    assert first is not None
    assert second is not None

    assert first.event_id != second.event_id
