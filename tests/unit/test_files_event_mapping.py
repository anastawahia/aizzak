"""Unit tests for the files OUTBOX seam (5.1-أ): the domain-event → outbox
record mapping (``modules/files/application/event_mapping.py``).

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
from app.modules.files.application.event_mapping import (
    AGGREGATE_TYPE,
    SOURCE,
    STREAM,
    to_outbox_record,
)
from app.modules.files.domain.events import FileDeleted, FileUploaded

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


def _uploaded(*, file_id: str = "file-1", workspace_id: str = "ws-1") -> FileUploaded:
    return FileUploaded(
        file_id=file_id,
        workspace_id=workspace_id,
        content_type="application/pdf",
        size_bytes=2048,
        storage_key=f"{workspace_id}/{file_id}",
        checksum="a" * 64,
        occurred_at=_OCCURRED_AT,
    )


def test_uploaded_event_maps_to_the_files_stream_and_aggregate() -> None:
    record = to_outbox_record(_ctx(), _uploaded(file_id="file-7"))

    assert record is not None
    assert record.stream == STREAM == "stream.files"
    assert record.aggregate_type == AGGREGATE_TYPE == "file"
    assert record.aggregate_id == "file-7"
    assert record.event_type == "files.file.uploaded.v1"


def test_deleted_event_maps_to_none() -> None:
    """``FileDeleted`` is internal-only (04 §5: no promotion asterisk) --
    the caller must filter this out before appending to the outbox."""
    deleted = FileDeleted(file_id="file-1", workspace_id="ws-1", occurred_at=_OCCURRED_AT)

    assert to_outbox_record(_ctx(), deleted) is None


def test_uploaded_payload_validates_against_the_published_envelope_and_data_schemas() -> None:
    record = to_outbox_record(_ctx(), _uploaded())
    assert record is not None

    jsonschema.Draft202012Validator(_load_schema("envelope.cloudevents.json")).validate(
        record.payload
    )
    jsonschema.Draft202012Validator(_load_schema("files.file.uploaded.v1.json")).validate(
        record.payload["data"]
    )


def test_uploaded_data_carries_exactly_the_catalogued_fields() -> None:
    """04 §4's catalog-table listing for ``files.file.uploaded.v1`` is
    ``{file_id, content_type, size_bytes, storage_key}`` -- no ``checksum``,
    even though the domain event itself carries one."""
    record = to_outbox_record(_ctx(), _uploaded(file_id="file-3"))
    assert record is not None

    assert record.payload["data"] == {
        "file_id": "file-3",
        "content_type": "application/pdf",
        "size_bytes": 2048,
        "storage_key": "ws-1/file-3",
    }


def test_envelope_id_is_the_row_id_and_the_idempotency_key() -> None:
    record = to_outbox_record(_ctx(), _uploaded())
    assert record is not None

    assert record.payload["id"] == record.event_id


def test_envelope_names_the_producing_module_and_the_subject_file() -> None:
    record = to_outbox_record(_ctx(), _uploaded(file_id="file-3"))
    assert record is not None

    assert record.payload["source"] == SOURCE == "files"
    assert record.payload["subject"] == "file-3"


def test_correlation_id_travels_inside_the_payload_not_only_the_column() -> None:
    record = to_outbox_record(_ctx(correlation_id="corr-abc"), _uploaded())
    assert record is not None

    assert record.payload["correlationid"] == "corr-abc"


def test_envelope_carries_the_events_own_workspace() -> None:
    record = to_outbox_record(_ctx(workspace_id="ws-9"), _uploaded(workspace_id="ws-9"))
    assert record is not None

    assert record.payload["workspaceid"] == "ws-9"


def test_envelope_time_is_the_domain_instant_not_the_mapping_instant() -> None:
    record = to_outbox_record(_ctx(), _uploaded())
    assert record is not None

    assert record.payload["time"] == "2026-07-19T09:30:00Z"


def test_each_mapping_mints_a_fresh_event_id() -> None:
    event = _uploaded()

    first = to_outbox_record(_ctx(), event)
    second = to_outbox_record(_ctx(), event)
    assert first is not None
    assert second is not None

    assert first.event_id != second.event_id
