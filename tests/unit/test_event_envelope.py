"""Unit tests for the CloudEvents envelope builder (DD-07 · 04-event-catalog §1).

The centrepiece is **validation against the published schema file itself**
(``docs/design/events/schemas/envelope.cloudevents.json``) rather than against
a hand-copied expectation: that pins the builder to the contract, so a doc
change that the code has not followed turns these tests red instead of shipping
a silently divergent wire format.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from jsonschema import Draft202012Validator

from app.framework.errors import AppError
from app.framework.events.envelope import EVENT_TYPE_PATTERN, build_envelope

_SCHEMAS = pathlib.Path(__file__).resolve().parents[2] / "docs/design/events/schemas"
_ENVELOPE_SCHEMA = json.loads((_SCHEMAS / "envelope.cloudevents.json").read_text(encoding="utf-8"))
_REQUESTED_SCHEMA = json.loads(
    (_SCHEMAS / "media.job.requested.v1.json").read_text(encoding="utf-8")
)

_WS = "018f0000-0000-7000-8000-000000000001"
_EVENT_ID = "018f0000-0000-7000-8000-000000000002"
_SUBJECT = "018f0000-0000-7000-8000-000000000003"
_CORR = "018f0000-0000-7000-8000-000000000004"
_CAUSE = "018f0000-0000-7000-8000-000000000005"

_MOMENT = datetime(2026, 7, 18, 10, 15, 0, tzinfo=UTC)


def _envelope(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "event_id": _EVENT_ID,
        "source": "media",
        "event_type": "media.job.requested.v1",
        "subject": _SUBJECT,
        "occurred_at": _MOMENT,
        "workspace_id": _WS,
        "data": {"job_id": _SUBJECT, "kind": "image", "prompt": "a cat"},
    }
    kwargs.update(overrides)
    return build_envelope(**kwargs)  # type: ignore[arg-type]


def test_envelope_validates_against_the_published_schema() -> None:
    """The whole point: what we build is what the contract describes."""
    Draft202012Validator(_ENVELOPE_SCHEMA).validate(_envelope())


def test_envelope_carries_every_field_of_catalog_section_1() -> None:
    envelope = _envelope()
    assert envelope["specversion"] == "1.0"
    assert envelope["id"] == _EVENT_ID
    assert envelope["source"] == "media"
    assert envelope["type"] == "media.job.requested.v1"
    assert envelope["subject"] == _SUBJECT
    assert envelope["time"] == "2026-07-18T10:15:00Z"
    assert envelope["datacontenttype"] == "application/json"
    assert envelope["workspaceid"] == _WS


def test_dataschema_is_derived_from_the_event_type() -> None:
    """An event can never advertise a schema that does not describe it."""
    assert _envelope()["dataschema"] == "https://schemas.platform/media.job.requested.v1.json"


def test_data_payload_validates_against_its_own_published_schema() -> None:
    data = _envelope()["data"]
    Draft202012Validator(_REQUESTED_SCHEMA).validate(data)


def test_data_is_the_last_key_so_the_stored_json_reads_envelope_then_payload() -> None:
    assert list(_envelope())[-1] == "data"


def test_time_is_converted_to_utc_not_merely_formatted() -> None:
    """A producer in a non-UTC zone must not publish a local wall clock."""
    kabul = timezone(timedelta(hours=4, minutes=30))
    envelope = _envelope(occurred_at=datetime(2026, 7, 18, 14, 45, 0, tzinfo=kabul))
    assert envelope["time"] == "2026-07-18T10:15:00Z"


def test_naive_timestamp_is_rejected_rather_than_assumed_utc() -> None:
    """DD-03. Assuming would publish a plausible-looking but wrong instant."""
    with pytest.raises(AppError) as caught:
        _envelope(occurred_at=datetime(2026, 7, 18, 10, 15, 0))
    assert caught.value.code == "common.internal"


def test_optional_extensions_are_omitted_not_null() -> None:
    """The published schema is additionalProperties:false with typed
    (non-nullable) extensions -- a null would fail validation."""
    envelope = _envelope()
    assert "correlationid" not in envelope
    assert "causationid" not in envelope
    Draft202012Validator(_ENVELOPE_SCHEMA).validate(envelope)


def test_optional_extensions_are_carried_when_supplied() -> None:
    envelope = _envelope(correlation_id=_CORR, causation_id=_CAUSE)
    assert envelope["correlationid"] == _CORR
    assert envelope["causationid"] == _CAUSE
    Draft202012Validator(_ENVELOPE_SCHEMA).validate(envelope)


@pytest.mark.parametrize(
    "bad_type",
    [
        "media.job.requested",  # no version suffix
        "media.job.requested.v",  # version with no number
        "Media.Job.Requested.v1",  # uppercase
        "media.requested.v1",  # missing an element
        "media.job.requested.v1 ",  # trailing space
        "",
    ],
)
def test_malformed_event_type_is_rejected(bad_type: str) -> None:
    """Streams and consumer groups route on `type`; a typo produces an event
    nothing will ever consume."""
    with pytest.raises(AppError) as caught:
        _envelope(event_type=bad_type)
    assert caught.value.code == "common.internal"


def test_every_published_payload_schema_name_is_a_valid_event_type() -> None:
    """Guards the naming convention `dataschema` derivation relies on."""
    pattern = re.compile(EVENT_TYPE_PATTERN)
    names = [p.stem for p in _SCHEMAS.glob("*.v*.json")]
    assert names, "no payload schemas found -- the glob is wrong"
    assert all(pattern.match(name) for name in names)


def test_event_type_pattern_has_not_drifted_from_the_published_schema() -> None:
    """The builder holds a verbatim COPY of the schema's pattern; this is what
    stops the copy from silently diverging from its source."""
    assert _ENVELOPE_SCHEMA["properties"]["type"]["pattern"] == EVENT_TYPE_PATTERN
