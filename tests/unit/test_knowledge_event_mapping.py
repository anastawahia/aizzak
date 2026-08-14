"""Unit tests for the knowledge OUTBOX seam (5.1-أ): the domain-event →
outbox record mapping (``modules/knowledge/application/event_mapping.py``).

The envelopes built here are validated against the PUBLISHED schema files
themselves (``docs/design/events/schemas/``) rather than hand-copied
expectations, so a change to the contract that the code does not follow turns
these red -- the ``test_media_outbox_seam.py`` precedent. Unlike
``files``/``memory``, every ``KnowledgeEvent`` is promoted (04 §5), so there
is no ``None``-mapping branch to test here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json
from app.modules.knowledge.application.event_mapping import (
    AGGREGATE_DOCUMENT,
    SOURCE,
    STREAM,
    to_outbox_record,
)
from app.modules.knowledge.domain.events import (
    DocumentIndexed,
    DocumentIndexingFailed,
    DocumentRegistered,
    KnowledgeEvent,
)

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


def _registered(*, document_id: str = "doc-1", workspace_id: str = "ws-1") -> DocumentRegistered:
    return DocumentRegistered(
        document_id=document_id,
        workspace_id=workspace_id,
        file_id="file-1",
        occurred_at=_OCCURRED_AT,
    )


def _indexed(*, document_id: str = "doc-1", workspace_id: str = "ws-1") -> DocumentIndexed:
    return DocumentIndexed(
        document_id=document_id,
        workspace_id=workspace_id,
        file_id="file-1",
        chunk_count=7,
        collection="kb-ws-1",
        occurred_at=_OCCURRED_AT,
    )


def _indexing_failed(
    *, document_id: str = "doc-1", workspace_id: str = "ws-1"
) -> DocumentIndexingFailed:
    return DocumentIndexingFailed(
        document_id=document_id,
        workspace_id=workspace_id,
        reason="unsupported file type",
        occurred_at=_OCCURRED_AT,
    )


def test_registered_event_maps_to_the_knowledge_stream_and_aggregate() -> None:
    record = to_outbox_record(_ctx(), _registered(document_id="doc-7"))

    assert record.stream == STREAM == "stream.knowledge"
    assert record.aggregate_type == AGGREGATE_DOCUMENT == "document"
    assert record.aggregate_id == "doc-7"
    assert record.event_type == "knowledge.document.registered.v1"


@pytest.mark.parametrize(
    ("event", "expected_type"),
    [
        (_registered(), "knowledge.document.registered.v1"),
        (_indexed(), "knowledge.document.indexed.v1"),
        (_indexing_failed(), "knowledge.document.indexing_failed.v1"),
    ],
)
def test_every_knowledge_event_maps_to_its_published_event_type(
    event: KnowledgeEvent, expected_type: str
) -> None:
    """The mapping is exhaustive over ``KnowledgeEvent`` -- a variant that
    fell through would drop an event silently."""
    assert to_outbox_record(_ctx(), event).event_type == expected_type


@pytest.mark.parametrize(
    ("event", "data_schema_file", "expected_data"),
    [
        (
            _registered(document_id="doc-3"),
            "knowledge.document.registered.v1.json",
            {"document_id": "doc-3", "file_id": "file-1"},
        ),
        (
            _indexed(document_id="doc-3"),
            "knowledge.document.indexed.v1.json",
            {"document_id": "doc-3", "chunk_count": 7},
        ),
        (
            _indexing_failed(document_id="doc-3"),
            "knowledge.document.indexing_failed.v1.json",
            {"document_id": "doc-3", "reason": "unsupported file type"},
        ),
    ],
)
def test_payload_validates_and_carries_exactly_the_catalogued_fields(
    event: KnowledgeEvent, data_schema_file: str, expected_data: Json
) -> None:
    """04 §4's catalog-table listing wins over the domain event's own extra
    fields (``DocumentIndexed`` also carries ``file_id``/``collection``,
    neither catalogued for the wire event)."""
    record = to_outbox_record(_ctx(), event)

    jsonschema.Draft202012Validator(_load_schema("envelope.cloudevents.json")).validate(
        record.payload
    )
    jsonschema.Draft202012Validator(_load_schema(data_schema_file)).validate(record.payload["data"])
    assert record.payload["data"] == expected_data


def test_envelope_id_is_the_row_id_and_the_idempotency_key() -> None:
    record = to_outbox_record(_ctx(), _registered())

    assert record.payload["id"] == record.event_id


def test_envelope_names_the_producing_module_and_the_subject_document() -> None:
    record = to_outbox_record(_ctx(), _registered(document_id="doc-3"))

    assert record.payload["source"] == SOURCE == "knowledge"
    assert record.payload["subject"] == "doc-3"


def test_correlation_id_travels_inside_the_payload_not_only_the_column() -> None:
    record = to_outbox_record(_ctx(correlation_id="corr-abc"), _registered())

    assert record.payload["correlationid"] == "corr-abc"


def test_envelope_carries_the_events_own_workspace() -> None:
    record = to_outbox_record(_ctx(workspace_id="ws-9"), _registered(workspace_id="ws-9"))

    assert record.payload["workspaceid"] == "ws-9"


def test_envelope_time_is_the_domain_instant_not_the_mapping_instant() -> None:
    record = to_outbox_record(_ctx(), _registered())

    assert record.payload["time"] == "2026-07-19T09:30:00Z"


def test_each_mapping_mints_a_fresh_event_id() -> None:
    event = _registered()

    first = to_outbox_record(_ctx(), event)
    second = to_outbox_record(_ctx(), event)

    assert first.event_id != second.event_id
