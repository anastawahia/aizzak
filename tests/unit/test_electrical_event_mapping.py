"""``drawings/application/event_mapping.py`` against the PUBLISHED schemas (BT24; INC1-AC-8/12).

Each of the six event types is built and validated against the envelope schema and its own data
schema in ``docs/design/events/schemas/``. An optional field that is absent is absent, never
``null``; ``job.progressed`` carries a strictly increasing ``seq`` when the worker numbers it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import jsonschema
import pytest

from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.modules.electrical.drawings.application.event_mapping import (
    SOURCE,
    STREAM,
    to_outbox_record,
)
from app.modules.electrical.drawings.domain.events import (
    DocumentProfileProposed,
    DocumentPurged,
    DocumentRegistered,
    DrawingsEvent,
    JobCompleted,
    JobProgressed,
    JobRequested,
    JobRequestReason,
)
from app.modules.electrical.drawings.domain.job import JobStatus
from app.modules.electrical.drawings.domain.pipeline import Stage
from tests.unit.support_electrical import NOW, WS, make_ctx

SCHEMAS = Path(__file__).resolve().parents[2] / "docs" / "design" / "events" / "schemas"
U1, U2, U3, U4 = (f"0000000{n}-0000-7000-8000-000000000000" for n in range(1, 5))


def _load(name: str) -> Any:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def _events() -> list[tuple[str, DrawingsEvent]]:
    return [
        (
            "electrical.document.registered.v1",
            DocumentRegistered(U1, WS, U2, U3, U4, NOW),
        ),
        ("electrical.job.requested.v1", JobRequested(U4, WS, U1, JobRequestReason.RESUME, NOW)),
        (
            "electrical.document.profile_proposed.v1",
            DocumentProfileProposed(U1, WS, U4, 0.81, True, False, NOW),
        ),
        (
            "electrical.job.progressed.v1",
            JobProgressed(U4, WS, U1, 3, Stage.CLASSIFY, 10, 40, 1, 2, NOW),
        ),
        ("electrical.job.completed.v1", JobCompleted(U4, WS, U1, JobStatus.SUCCEEDED, None, NOW)),
        (
            "electrical.job.completed.v1",
            JobCompleted(U4, WS, U1, JobStatus.FAILED, "electrical.pdf_rejected", NOW),
        ),
        ("electrical.document.purged.v1", DocumentPurged(U1, WS, U2, U3, NOW)),
    ]


@pytest.mark.parametrize(("event_type", "event"), _events())
def test_every_event_validates_against_the_published_envelope_and_data_schemas(
    event_type: str, event: DrawingsEvent
) -> None:
    record = to_outbox_record(make_ctx(), event)

    assert record.event_type == event_type and record.stream == STREAM == "stream.electrical"
    payload = record.payload
    jsonschema.Draft202012Validator(_load("envelope.cloudevents.json")).validate(payload)
    jsonschema.Draft202012Validator(_load(f"{event_type}.json")).validate(payload["data"])
    assert payload["source"] == SOURCE == "electrical"
    assert payload["workspaceid"] == WS and payload["correlationid"] == "corr"
    assert payload["id"] == record.event_id and payload["time"].endswith("Z")
    assert payload["dataschema"] == f"https://schemas.platform/{event_type}.json"


@pytest.mark.parametrize(("event_type", "event"), _events())
def test_no_value_in_a_payload_is_null(event_type: str, event: DrawingsEvent) -> None:
    def walk(value: Any) -> None:
        assert value is not None, event_type
        if isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(to_outbox_record(make_ctx(), event).payload)


def test_failure_code_is_present_only_when_there_is_one() -> None:
    ok = to_outbox_record(make_ctx(), JobCompleted(U4, WS, U1, JobStatus.SUCCEEDED, None, NOW))
    bad = to_outbox_record(
        make_ctx(),
        JobCompleted(U4, WS, U1, JobStatus.CANCELLED, "electrical.document_not_live", NOW),
    )
    assert "failure_code" not in ok.payload["data"]
    assert bad.payload["data"]["failure_code"] == "electrical.document_not_live"


def test_the_subject_is_the_document_or_the_job_as_the_catalogue_says() -> None:
    subjects = {t: to_outbox_record(make_ctx(), e).payload["subject"] for t, e in _events()}
    assert subjects["electrical.document.registered.v1"] == U1  # document_id
    assert subjects["electrical.job.requested.v1"] == U4  # job_id
    assert subjects["electrical.document.profile_proposed.v1"] == U1
    assert subjects["electrical.job.progressed.v1"] == U4
    assert subjects["electrical.job.completed.v1"] == U4
    assert subjects["electrical.document.purged.v1"] == U1


def test_each_record_has_its_own_event_id() -> None:
    ids = [to_outbox_record(make_ctx(), e).event_id for _, e in _events()]
    assert len(set(ids)) == len(ids)


def test_the_progress_sequence_is_whatever_the_worker_numbered() -> None:
    seqs = [
        to_outbox_record(
            make_ctx(), JobProgressed(U4, WS, U1, n, Stage.CLASSIFY, n, 10, 0, 0, NOW)
        ).payload["data"]["seq"]
        for n in (1, 2, 3)
    ]
    assert seqs == [1, 2, 3]


def test_the_consumer_group_owns_exactly_the_three_types_the_worker_handles() -> None:
    (binding,) = [b for b in STATIC_CONSUMER_TOPOLOGY if b.stream == STREAM]
    assert binding.event_types == {
        "electrical.document.registered.v1",
        "electrical.job.requested.v1",
        "electrical.document.purged.v1",
    }


def test_the_mapping_is_exhaustive_over_the_event_union() -> None:
    """A seventh event type added to ``DrawingsEvent`` without a ``case`` is a mypy error
    (``assert_never``); this guards the other direction: every member is handled today."""
    handled: set[type] = {type(e) for _, e in _events()}
    assert set(get_args(DrawingsEvent)) == handled
