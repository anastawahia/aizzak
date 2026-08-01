"""Unit tests for the media OUTBOX seam (4.7-d-2): the domain-event → outbox
record mapping and the ``MediaRequestService`` inbound port over it.

The envelopes built here are validated against the PUBLISHED schema files
themselves (``docs/design/events/schemas/``) rather than hand-copied
expectations, so a change to the contract that the code does not follow turns
these red. ``test_media_module.py`` already validates the three domain events'
``data`` payloads; what is proven HERE is everything the mapping adds on top —
routing fields, the shared event id, and the correlation id that would
otherwise be lost at the relay boundary.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest

from app.agents.image_agent.agent import ImageAgent
from app.framework.agent_runtime import AgentDependencies, AgentRequest
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import Limits
from app.framework.types import Json
from app.modules.media.application.event_mapping import (
    AGGREGATE_TYPE,
    SOURCE,
    STREAM,
    to_outbox_record,
)
from app.modules.media.application.use_cases import MediaRequestService, RequestMedia
from app.modules.media.domain.entities import MediaJob
from app.modules.media.domain.events import MediaEvent, MediaFailed, MediaGenerated, MediaRequested

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


def _requested(*, job_id: str = "job-1", workspace_id: str = "ws-1") -> MediaRequested:
    return MediaRequested(
        job_id=job_id,
        workspace_id=workspace_id,
        kind="image",
        prompt="a cat wearing sunglasses",
        agent_key="image-agent",
        params={"width": 512, "height": 512},
        occurred_at=_OCCURRED_AT,
    )


class _FakeMediaJobs:
    """Minimal ``MediaJobRepository`` — records what was added."""

    def __init__(self) -> None:
        self.added: list[MediaJob] = []

    async def add(self, ctx: ExecutionContext, job: MediaJob) -> None:
        self.added.append(job)

    async def get(self, ctx: ExecutionContext, job_id: str) -> MediaJob | None:
        return next((j for j in self.added if j.id == job_id), None)

    async def save(self, ctx: ExecutionContext, job: MediaJob) -> None:
        return None


class _FakeOutbox:
    """Minimal ``EventOutbox`` — records every append call, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[ExecutionContext, list[OutboxRecord]]] = []

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        self.calls.append((ctx, list(records)))


class _ExplodingOutbox:
    """An outbox whose append always fails (a dead database, a lost grant)."""

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        raise RuntimeError("outbox is down")


class _FakeUnitOfWork:
    """A no-op transaction boundary (5.1-أ) -- these hermetic tests only care
    that SOME ``UnitOfWork`` is injected, not that it opens a real session;
    ``test_unit_of_work.py`` covers ``TenantSessionFactory.begin`` itself."""

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        yield


def _service(
    outbox: _FakeOutbox | _ExplodingOutbox, jobs: _FakeMediaJobs | None = None
) -> MediaRequestService:
    return MediaRequestService(
        RequestMedia(jobs or _FakeMediaJobs(), Limits()),
        outbox,
        _FakeUnitOfWork(),
    )


# --------------------------------------------------------------------------- #
# to_outbox_record — routing fields                                            #
# --------------------------------------------------------------------------- #
def test_requested_event_maps_to_the_media_stream_and_aggregate() -> None:
    record = to_outbox_record(_ctx(), _requested(job_id="job-7"))

    assert record.stream == STREAM == "stream.media"
    assert record.aggregate_type == AGGREGATE_TYPE == "media_job"
    assert record.aggregate_id == "job-7"
    assert record.event_type == "media.job.requested.v1"


@pytest.mark.parametrize(
    ("event", "expected_type"),
    [
        (_requested(), "media.job.requested.v1"),
        (
            MediaGenerated(
                job_id="job-1",
                workspace_id="ws-1",
                result_file_id="file-9",
                occurred_at=_OCCURRED_AT,
            ),
            "media.job.generated.v1",
        ),
        (
            MediaFailed(
                job_id="job-1",
                workspace_id="ws-1",
                reason="provider timed out",
                occurred_at=_OCCURRED_AT,
            ),
            "media.job.failed.v1",
        ),
    ],
)
def test_every_media_event_maps_to_its_published_event_type(
    event: MediaEvent, expected_type: str
) -> None:
    """The mapping is exhaustive over ``MediaEvent`` — a variant that fell
    through would drop an event silently."""
    assert to_outbox_record(_ctx(), event).event_type == expected_type


# --------------------------------------------------------------------------- #
# to_outbox_record — the envelope, against the published schemas               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("event", "data_schema_file"),
    [
        (_requested(), "media.job.requested.v1.json"),
        (
            MediaGenerated(
                job_id="job-1",
                workspace_id="ws-1",
                result_file_id="018f0000-0000-7000-8000-000000000009",
                occurred_at=_OCCURRED_AT,
            ),
            "media.job.generated.v1.json",
        ),
        (
            MediaFailed(
                job_id="job-1",
                workspace_id="ws-1",
                reason="provider timed out",
                occurred_at=_OCCURRED_AT,
            ),
            "media.job.failed.v1.json",
        ),
    ],
)
def test_payload_validates_against_the_published_envelope_and_data_schemas(
    event: MediaEvent, data_schema_file: str
) -> None:
    record = to_outbox_record(_ctx(), event)

    jsonschema.Draft202012Validator(_load_schema("envelope.cloudevents.json")).validate(
        record.payload
    )
    jsonschema.Draft202012Validator(_load_schema(data_schema_file)).validate(record.payload["data"])


def test_envelope_id_is_the_row_id_and_the_idempotency_key() -> None:
    """DD-09: the consumer dedupes on the CloudEvents ``id``. If it differed
    from the row's primary key, a redelivery would be invisible."""
    record = to_outbox_record(_ctx(), _requested())

    assert record.payload["id"] == record.event_id


def test_envelope_names_the_producing_module_and_the_subject_job() -> None:
    record = to_outbox_record(_ctx(), _requested(job_id="job-3"))

    assert record.payload["source"] == SOURCE == "media"
    assert record.payload["subject"] == "job-3"


def test_correlation_id_travels_inside_the_payload_not_only_the_column() -> None:
    """The relay republishes ``payload`` VERBATIM, so a correlation id that
    lived only in the outbox column would never reach a consumer — the trace
    would break at exactly the async hop it exists to span."""
    record = to_outbox_record(_ctx(correlation_id="corr-abc"), _requested())

    assert record.payload["correlationid"] == "corr-abc"


def test_envelope_carries_the_events_own_workspace() -> None:
    record = to_outbox_record(_ctx(workspace_id="ws-9"), _requested(workspace_id="ws-9"))

    assert record.payload["workspaceid"] == "ws-9"


def test_envelope_time_is_the_domain_instant_not_the_mapping_instant() -> None:
    """Re-reading the clock here would publish a timestamp that drifts from the
    aggregate row the event describes."""
    record = to_outbox_record(_ctx(), _requested())

    assert record.payload["time"] == "2026-07-19T09:30:00Z"


def test_each_mapping_mints_a_fresh_event_id() -> None:
    """Two appends of the same logical event are two distinct rows; a reused id
    would make the second one a silent primary-key conflict."""
    event = _requested()

    first = to_outbox_record(_ctx(), event)
    second = to_outbox_record(_ctx(), event)

    assert first.event_id != second.event_id


# --------------------------------------------------------------------------- #
# MediaRequestService — the inbound port                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_request_queues_the_job_and_appends_its_event() -> None:
    jobs, outbox = _FakeMediaJobs(), _FakeOutbox()

    view = await _service(outbox, jobs).request(
        _ctx(),
        agent_key="image-agent",
        kind="image",
        prompt="a cat",
        params={"width": 64, "height": 64},
    )

    assert len(jobs.added) == 1
    (_, records) = outbox.calls[0]
    assert [r.event_type for r in records] == ["media.job.requested.v1"]
    assert records[0].aggregate_id == view.job_id


@pytest.mark.anyio
async def test_request_returns_a_handle_for_the_queued_job() -> None:
    """D-04: the agent gets a job to await, never a generated asset."""
    jobs, outbox = _FakeMediaJobs(), _FakeOutbox()

    view = await _service(outbox, jobs).request(
        _ctx(),
        agent_key="video-agent",
        kind="video",
        prompt="a river",
        params={"duration_seconds": 4},
    )

    assert view.job_id == jobs.added[0].id
    assert view.status == "queued"
    assert view.kind == "video"


@pytest.mark.anyio
async def test_the_append_runs_under_the_callers_context() -> None:
    """Same ``ExecutionContext`` ⇒ same RLS scope as the aggregate write."""
    ctx = _ctx(workspace_id="ws-42")
    outbox = _FakeOutbox()

    await _service(outbox).request(
        ctx,
        agent_key="image-agent",
        kind="image",
        prompt="a cat",
        params={"width": 64, "height": 64},
    )

    assert outbox.calls[0][0] is ctx


@pytest.mark.anyio
async def test_a_rejected_request_never_reaches_the_outbox() -> None:
    """Validation runs before anything is queued (INV-MJ3), so an invalid
    request must not leave an event behind."""
    outbox = _FakeOutbox()

    with pytest.raises(ValidationError):
        await _service(outbox).request(
            _ctx(), agent_key="image-agent", kind="image", prompt="   ", params={}
        )

    assert outbox.calls == []


@pytest.mark.anyio
async def test_an_outbox_failure_is_surfaced_not_swallowed() -> None:
    """Swallowing would hand the user a job id for a job nothing will ever pick
    up — a queued row with no event is invisible to the Phase-5 worker."""
    with pytest.raises(RuntimeError, match="outbox is down"):
        await _service(_ExplodingOutbox()).request(
            _ctx(),
            agent_key="image-agent",
            kind="image",
            prompt="a cat",
            params={"width": 64, "height": 64},
        )


@pytest.mark.anyio
async def test_the_append_runs_inside_the_units_of_work_transaction() -> None:
    """5.1-أ: if the append ever ran OUTSIDE ``uow.begin(ctx)``, an aggregate
    write could commit with no event and nothing else here would catch it —
    every other test in this module uses a UoW that does not know whether it
    is "active", so this one is the seam's own witness."""

    class _TrackingUnitOfWork:
        def __init__(self) -> None:
            self.active = False

        @asynccontextmanager
        async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
            self.active = True
            try:
                yield
            finally:
                self.active = False

    class _SpyOutbox:
        def __init__(self, uow: _TrackingUnitOfWork) -> None:
            self._uow = uow
            self.appended_while_active: bool | None = None

        async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
            self.appended_while_active = self._uow.active

    uow = _TrackingUnitOfWork()
    outbox = _SpyOutbox(uow)
    service = MediaRequestService(RequestMedia(_FakeMediaJobs(), Limits()), outbox, uow)

    await service.request(
        _ctx(),
        agent_key="image-agent",
        kind="image",
        prompt="a cat",
        params={"width": 64, "height": 64},
    )

    assert outbox.appended_while_active is True
    assert uow.active is False  # the block closed cleanly afterwards


# --------------------------------------------------------------------------- #
# submit — the full-aggregate face (6.1-هـ-2)                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_submit_returns_the_full_queued_aggregate() -> None:
    """The API face: 03 §2's ``MediaJobOut`` needs ``result_file_id``/``error``/
    ``created_at``, which the agent-facing ``RequestedMedia`` deliberately
    omits — ``submit`` hands back the job itself, from the SAME transaction,
    never a second read."""
    jobs, outbox = _FakeMediaJobs(), _FakeOutbox()

    job = await _service(outbox, jobs).submit(
        _ctx(),
        agent_key="image-agent",
        kind="image",
        prompt="a cat",
        params={"width": 64, "height": 64},
    )

    assert job is jobs.added[0]  # the persisted aggregate, not a projection
    assert job.status.value == "queued"
    assert job.result_file_id is None
    assert job.error is None
    assert job.created_at is not None
    (_, records) = outbox.calls[0]
    assert [r.event_type for r in records] == ["media.job.requested.v1"]
    assert records[0].aggregate_id == job.id


@pytest.mark.anyio
async def test_request_is_a_projection_of_submit() -> None:
    """One atomic core: ``request`` must carry ``submit``'s own job — a drift
    between the two faces would hand agents and API callers different jobs."""
    jobs, outbox = _FakeMediaJobs(), _FakeOutbox()

    view = await _service(outbox, jobs).request(
        _ctx(),
        agent_key="image-agent",
        kind="image",
        prompt="a cat",
        params={"width": 64, "height": 64},
    )

    job = jobs.added[0]
    assert (view.job_id, view.status, view.kind) == (job.id, job.status.value, job.kind.value)


# --------------------------------------------------------------------------- #
# The whole seam: a real agent over the real service                           #
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_the_image_agent_runs_over_the_real_service() -> None:
    """The 4.7-d-2 deliverable, end to end: ``ImageAgent`` → the framework
    ``MediaRequesting`` seam → ``MediaRequestService`` → the outbox.

    Every earlier media-agent test used a fake ``MediaRequesting``, so it proved
    the agent's half only; the agent still hit the ``common.internal``/500 guard
    in a real deployment because ``deps.media`` was ``None``. This binds the
    REAL service to that field — the same binding the Composition Root makes —
    and asserts the job the agent names is the job the outbox announces.
    """
    jobs, outbox = _FakeMediaJobs(), _FakeOutbox()
    agent = ImageAgent(ctx=_ctx(), deps=AgentDependencies(media=_service(outbox, jobs)))
    await agent.initialize()

    events = [
        event
        async for event in agent.run(
            AgentRequest(
                conversation_id=None,
                input={"prompt": "a cat", "params": {"width": 512, "height": 512}},
            )
        )
    ]

    assert [e.type for e in events] == ["final"]
    final = events[0].data
    assert final["status"] == "queued"
    assert final["kind"] == "image"
    # The handle the user is given names the job the worker will be told about.
    (_, records) = outbox.calls[0]
    assert records[0].payload["data"]["job_id"] == final["job_id"]
