"""Unit tests for the media module: value objects, the ``MediaJob`` domain
(INV-MJ1/MJ2/MJ3), and the ``RequestMedia``/``RunMediaJob``/``GetJobStatus``
use-cases over local fakes (``MediaJobRepository``/``MediaGenerator``) --
none imported from other test modules (the ``test_knowledge_module.py``
precedent). A dedicated section validates the three domain events' wire
payloads against the published JSON Schemas under
``docs/design/events/schemas/media.job.*.v1.json`` literally, via the
``jsonschema`` package.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits
from app.framework.types import Json
from app.modules.media.application.use_cases import GetJobStatus, RequestMedia, RunMediaJob
from app.modules.media.domain.entities import MediaJob
from app.modules.media.domain.errors import InvalidMediaInput, MediaJobStateError
from app.modules.media.domain.events import MediaFailed, MediaGenerated, MediaRequested
from app.modules.media.domain.value_objects import AgentKey, GenParams, JobStatus, MediaKind

_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "docs" / "design" / "events" / "schemas"


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str = "ws1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _default_params(kind: MediaKind) -> GenParams:
    if kind is MediaKind.IMAGE:
        return GenParams.from_dict(MediaKind.IMAGE, {"width": 512, "height": 512})
    return GenParams.from_dict(MediaKind.VIDEO, {"duration_seconds": 5})


def _job(
    *,
    job_id: str = "job-1",
    workspace_id: str = "ws1",
    agent_key: str = "image-agent",
    kind: MediaKind = MediaKind.IMAGE,
    prompt: str = "a cat wearing sunglasses",
    params: GenParams | None = None,
    status: JobStatus = JobStatus.QUEUED,
    result_file_id: str | None = None,
    error: str | None = None,
) -> MediaJob:
    now = utc_now()
    return MediaJob(
        id=job_id,
        workspace_id=workspace_id,
        agent_key=AgentKey(agent_key),
        kind=kind,
        prompt=prompt,
        params=params or _default_params(kind),
        status=status,
        result_file_id=result_file_id,
        error=error,
        created_by="u1",
        created_at=now,
        updated_at=now,
        version=1,
    )


def _load_schema(filename: str) -> Json:
    return json.loads((_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Fakes (MediaJobRepository / MediaGenerator) -- local to this module.        #
# --------------------------------------------------------------------------- #
class _FakeMediaJobs:
    """In-memory ``MediaJobRepository``; records every ``save`` call's job id
    so use-case tests can assert how many persistence round-trips happened."""

    def __init__(self) -> None:
        self.rows: dict[str, MediaJob] = {}
        self.save_calls: list[str] = []

    async def get(self, ctx: ExecutionContext, job_id: str) -> MediaJob | None:
        row = self.rows.get(job_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, job: MediaJob) -> None:
        self.rows[job.id] = job

    async def save(self, ctx: ExecutionContext, job: MediaJob) -> None:
        self.rows[job.id] = job
        self.save_calls.append(job.id)


class _FakeGenerator:
    """Deterministic ``MediaGenerator`` fake; ``fail=True`` makes
    ``generate`` raise, to exercise ``RunMediaJob``'s failure path."""

    def __init__(self, *, result_file_id: str = "file-out", fail: bool = False) -> None:
        self.result_file_id = result_file_id
        self.fail = fail
        self.calls: list[tuple[MediaKind, str, GenParams]] = []

    async def generate(
        self, ctx: ExecutionContext, *, kind: MediaKind, prompt: str, params: GenParams
    ) -> str:
        self.calls.append((kind, prompt, params))
        if self.fail:
            raise RuntimeError("provider unavailable")
        return self.result_file_id


# --------------------------------------------------------------------------- #
# value_objects                                                               #
# --------------------------------------------------------------------------- #
def test_media_kind_values() -> None:
    assert [kind.value for kind in MediaKind] == ["image", "video"]


def test_job_status_values() -> None:
    assert [status.value for status in JobStatus] == ["queued", "running", "succeeded", "failed"]


def test_agent_key_normalizes_case_and_whitespace() -> None:
    assert AgentKey("  Image-Agent  ").value == "image-agent"


def test_agent_key_rejects_empty() -> None:
    with pytest.raises(InvalidMediaInput):
        AgentKey("   ")


def test_agent_key_rejects_invalid_format() -> None:
    with pytest.raises(InvalidMediaInput):
        AgentKey("Not A Slug!")


def test_gen_params_from_dict_builds_valid_image_params() -> None:
    params = GenParams.from_dict(
        MediaKind.IMAGE, {"width": 1024, "height": 768, "model": "dalle-3"}
    )
    assert (params.width, params.height, params.model) == (1024, 768, "dalle-3")
    assert params.duration_seconds is None
    assert params.as_dict() == {"width": 1024, "height": 768, "model": "dalle-3"}


def test_gen_params_from_dict_builds_valid_video_params() -> None:
    params = GenParams.from_dict(MediaKind.VIDEO, {"duration_seconds": 10})
    assert params.duration_seconds == 10
    assert (params.width, params.height, params.model) == (None, None, None)
    assert params.as_dict() == {"duration_seconds": 10}


def test_gen_params_rejects_unknown_key() -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams.from_dict(MediaKind.IMAGE, {"width": 100, "height": 100, "seed": 42})


def test_gen_params_rejects_missing_required_field_for_kind() -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams.from_dict(MediaKind.IMAGE, {"width": 100})  # height missing


def test_gen_params_rejects_video_fields_on_image_kind() -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams(kind=MediaKind.IMAGE, width=100, height=100, duration_seconds=5)


def test_gen_params_rejects_image_fields_on_video_kind() -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams(kind=MediaKind.VIDEO, duration_seconds=5, width=100)


@pytest.mark.parametrize("bad_width", [0, -1])
def test_gen_params_rejects_non_positive_dimensions(bad_width: int) -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams.from_dict(MediaKind.IMAGE, {"width": bad_width, "height": 100})


def test_gen_params_rejects_non_integer_dimension() -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams.from_dict(MediaKind.IMAGE, {"width": "100", "height": 100})


def test_gen_params_rejects_bool_as_dimension() -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams.from_dict(MediaKind.IMAGE, {"width": True, "height": 100})


def test_gen_params_rejects_blank_model() -> None:
    with pytest.raises(InvalidMediaInput):
        GenParams.from_dict(MediaKind.VIDEO, {"duration_seconds": 5, "model": "   "})


# --------------------------------------------------------------------------- #
# MediaJob transitions (INV-MJ1/MJ2)                                         #
# --------------------------------------------------------------------------- #
def test_queued_to_running_to_succeeded_round_trip() -> None:
    job = _job(status=JobStatus.QUEUED)
    job.start_running(utc_now())
    assert job.status is JobStatus.RUNNING
    job.complete("result-file-1", utc_now())
    assert job.status is JobStatus.SUCCEEDED
    assert job.result_file_id == "result-file-1"
    assert job.error is None


def test_queued_to_running_to_failed_round_trip() -> None:
    job = _job(status=JobStatus.QUEUED)
    job.start_running(utc_now())
    job.fail("provider exploded", utc_now())
    assert job.status is JobStatus.FAILED
    assert job.error == "provider exploded"


def test_start_running_is_reentrant_from_running() -> None:
    job = _job(status=JobStatus.RUNNING)
    before = job.updated_at
    later = utc_now()
    job.start_running(later)
    assert job.status is JobStatus.RUNNING
    assert job.updated_at == later
    assert job.updated_at >= before


@pytest.mark.parametrize("status", [JobStatus.SUCCEEDED, JobStatus.FAILED])
def test_start_running_from_terminal_status_raises(status: JobStatus) -> None:
    job = _job(status=status)
    with pytest.raises(MediaJobStateError):
        job.start_running(utc_now())


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.SUCCEEDED, JobStatus.FAILED])
def test_complete_from_non_running_status_raises(status: JobStatus) -> None:
    job = _job(status=status)
    with pytest.raises(MediaJobStateError):
        job.complete("result-file-1", utc_now())


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.SUCCEEDED, JobStatus.FAILED])
def test_fail_from_non_running_status_raises(status: JobStatus) -> None:
    job = _job(status=status)
    with pytest.raises(MediaJobStateError):
        job.fail("boom", utc_now())


def test_complete_clears_previous_error() -> None:
    job = _job(status=JobStatus.RUNNING, error="stale error from a previous attempt")
    job.complete("result-file-1", utc_now())
    assert job.error is None


@pytest.mark.parametrize("bad_result_file_id", [None, ""])
def test_complete_without_result_file_id_raises_invalid_media_input(
    bad_result_file_id: str | None,
) -> None:
    """INV-MJ1: ``succeeded ⇒ result_file_id NOT NULL``."""
    job = _job(status=JobStatus.RUNNING)
    with pytest.raises(InvalidMediaInput):
        job.complete(bad_result_file_id, utc_now())
    assert job.status is JobStatus.RUNNING  # rejected before any state mutation


# --------------------------------------------------------------------------- #
# RequestMedia                                                                #
# --------------------------------------------------------------------------- #
async def test_request_media_image_happy_path() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    params: Json = {"width": 1024, "height": 768, "model": "dalle-3"}

    job, events = await RequestMedia(jobs, Limits()).execute(
        ctx, agent_key="Image-Agent", kind="image", prompt="a cat", params=params
    )

    assert job.status is JobStatus.QUEUED
    assert job.agent_key.value == "image-agent"
    assert job.kind is MediaKind.IMAGE
    assert job.prompt == "a cat"
    assert job.params.as_dict() == params
    assert job.result_file_id is None
    assert job.error is None
    assert job.version == 1
    assert jobs.rows[job.id] is job

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, MediaRequested)
    assert event.job_id == job.id
    assert event.workspace_id == "ws1"
    assert event.kind == "image"
    assert event.prompt == "a cat"
    assert event.agent_key == "image-agent"
    assert event.params == params
    assert event.occurred_at == job.created_at


async def test_request_media_video_happy_path() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    params: Json = {"duration_seconds": 10}

    job, events = await RequestMedia(jobs, Limits()).execute(
        ctx, agent_key="video-agent", kind="video", prompt="a sunset timelapse", params=params
    )

    assert job.status is JobStatus.QUEUED
    assert job.kind is MediaKind.VIDEO
    assert job.params.duration_seconds == 10
    assert events[0].kind == "video"


async def test_request_media_rejects_invalid_agent_key() -> None:
    with pytest.raises(ValidationError):
        await RequestMedia(_FakeMediaJobs(), Limits()).execute(
            _ctx(), agent_key="   ", kind="image", prompt="x", params={"width": 1, "height": 1}
        )


async def test_request_media_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        await RequestMedia(_FakeMediaJobs(), Limits()).execute(
            _ctx(), agent_key="a", kind="audio", prompt="x", params={}
        )


async def test_request_media_rejects_blank_prompt() -> None:
    with pytest.raises(ValidationError):
        await RequestMedia(_FakeMediaJobs(), Limits()).execute(
            _ctx(), agent_key="a", kind="image", prompt="   ", params={"width": 1, "height": 1}
        )


async def test_request_media_rejects_malformed_params_shape() -> None:
    """INV-MJ3 shape half: ``GenParams.from_dict`` rejects a missing
    required field with the generic validation code (the ceiling check in
    ``_enforce_limits`` never even runs)."""
    with pytest.raises(ValidationError) as exc_info:
        await RequestMedia(_FakeMediaJobs(), Limits()).execute(
            _ctx(), agent_key="a", kind="image", prompt="x", params={"width": 100}
        )
    assert exc_info.value.code == "common.validation_error"


async def test_request_media_image_at_exact_dimension_ceiling_passes() -> None:
    limits = Limits()
    jobs = _FakeMediaJobs()
    job, _events = await RequestMedia(jobs, limits).execute(
        _ctx(),
        agent_key="a",
        kind="image",
        prompt="x",
        params={"width": limits.max_image_dim, "height": limits.max_image_dim},
    )
    assert job.params.width == limits.max_image_dim
    assert job.params.height == limits.max_image_dim


async def test_request_media_image_over_dimension_ceiling_rejects() -> None:
    """INV-MJ3 ceiling half, 07-nfr-slo §4: over ``max_image_dim`` rejects
    with the stable ``media.invalid_params`` code (03-api-spec §4)."""
    limits = Limits()
    with pytest.raises(ValidationError) as exc_info:
        await RequestMedia(_FakeMediaJobs(), limits).execute(
            _ctx(),
            agent_key="a",
            kind="image",
            prompt="x",
            params={"width": limits.max_image_dim + 1, "height": 100},
        )
    assert exc_info.value.code == "media.invalid_params"


async def test_request_media_video_at_exact_duration_ceiling_passes() -> None:
    limits = Limits()
    jobs = _FakeMediaJobs()
    job, _events = await RequestMedia(jobs, limits).execute(
        _ctx(),
        agent_key="a",
        kind="video",
        prompt="x",
        params={"duration_seconds": limits.max_video_seconds},
    )
    assert job.params.duration_seconds == limits.max_video_seconds


async def test_request_media_video_over_duration_ceiling_rejects() -> None:
    limits = Limits()
    with pytest.raises(ValidationError) as exc_info:
        await RequestMedia(_FakeMediaJobs(), limits).execute(
            _ctx(),
            agent_key="a",
            kind="video",
            prompt="x",
            params={"duration_seconds": limits.max_video_seconds + 1},
        )
    assert exc_info.value.code == "media.invalid_params"


# --------------------------------------------------------------------------- #
# RunMediaJob                                                                 #
# --------------------------------------------------------------------------- #
async def test_run_media_job_happy_path() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", workspace_id="ws1", status=JobStatus.QUEUED)
    jobs.rows[job.id] = job
    generator = _FakeGenerator(result_file_id="file-out-1")

    result, events = await RunMediaJob(jobs, generator).execute(ctx, job_id=job.id)

    assert result is job
    assert job.status is JobStatus.SUCCEEDED
    assert job.result_file_id == "file-out-1"
    assert jobs.save_calls == [job.id, job.id]  # start_running save + complete save
    assert len(generator.calls) == 1

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, MediaGenerated)
    assert event.job_id == job.id
    assert event.workspace_id == "ws1"
    assert event.result_file_id == "file-out-1"


async def test_run_media_job_failure_marks_job_failed_without_reraising() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", workspace_id="ws1", status=JobStatus.QUEUED)
    jobs.rows[job.id] = job
    generator = _FakeGenerator(fail=True)

    result, events = await RunMediaJob(jobs, generator).execute(ctx, job_id=job.id)

    assert result is job
    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert "unavailable" in job.error
    assert jobs.save_calls == [job.id, job.id]

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, MediaFailed)
    assert event.job_id == job.id
    assert event.workspace_id == "ws1"
    assert event.reason == job.error


async def test_run_media_job_already_succeeded_is_a_noop() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", status=JobStatus.SUCCEEDED, result_file_id="file-x")
    jobs.rows[job.id] = job
    generator = _FakeGenerator()

    result, events = await RunMediaJob(jobs, generator).execute(ctx, job_id=job.id)

    assert result is job
    assert result.status is JobStatus.SUCCEEDED
    assert events == ()
    assert jobs.save_calls == []
    assert generator.calls == []


async def test_run_media_job_already_failed_is_a_noop() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", status=JobStatus.FAILED, error="earlier failure")
    jobs.rows[job.id] = job
    generator = _FakeGenerator()

    result, events = await RunMediaJob(jobs, generator).execute(ctx, job_id=job.id)

    assert result is job
    assert result.status is JobStatus.FAILED
    assert events == ()
    assert jobs.save_calls == []
    assert generator.calls == []


async def test_run_media_job_missing_job_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await RunMediaJob(_FakeMediaJobs(), _FakeGenerator()).execute(_ctx(), job_id="missing")


async def test_run_media_job_reentrant_from_running_resumes_and_succeeds() -> None:
    """At-least-once redelivery (DD-09): a job already ``running`` (e.g.
    after a worker crashed mid-generation) is resumed, not rejected."""
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", workspace_id="ws1", status=JobStatus.RUNNING)
    jobs.rows[job.id] = job
    generator = _FakeGenerator(result_file_id="file-out-2")

    result, events = await RunMediaJob(jobs, generator).execute(ctx, job_id=job.id)

    assert result.status is JobStatus.SUCCEEDED
    assert result.result_file_id == "file-out-2"
    assert len(events) == 1
    assert isinstance(events[0], MediaGenerated)


async def test_run_persists_no_terminal_outcome_until_finalize() -> None:
    """The 5.2-أ split's load-bearing property (the closed D5 window, the
    ``knowledge`` test's twin): after ``run``, the only persisted save is the
    ``running`` claim -- the terminal status and its follow-on event wait for
    ``finalize``, which the worker handler wraps in its own single
    transaction."""
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", workspace_id="ws1", status=JobStatus.QUEUED)
    jobs.rows[job.id] = job
    use_case = RunMediaJob(jobs, _FakeGenerator(result_file_id="file-out-1"))

    attempt = await use_case.run(ctx, job_id=job.id)

    assert attempt.result_file_id == "file-out-1"
    assert attempt.error is None
    assert not attempt.is_redelivery_noop
    assert job.status is JobStatus.RUNNING  # nothing terminal persisted yet
    assert jobs.save_calls == [job.id]  # the start_running save ONLY

    result, events = await use_case.finalize(ctx, attempt)

    assert result is job
    assert job.status is JobStatus.SUCCEEDED
    assert jobs.save_calls == [job.id, job.id]
    assert [type(event).__name__ for event in events] == ["MediaGenerated"]


async def test_run_carries_a_generation_failure_as_data_for_finalize() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", workspace_id="ws1", status=JobStatus.QUEUED)
    jobs.rows[job.id] = job
    use_case = RunMediaJob(jobs, _FakeGenerator(fail=True))

    attempt = await use_case.run(ctx, job_id=job.id)

    assert attempt.result_file_id is None
    assert attempt.error is not None
    assert "unavailable" in attempt.error
    assert job.status is JobStatus.RUNNING  # not failed YET

    result, events = await use_case.finalize(ctx, attempt)

    assert result is job
    assert job.status is JobStatus.FAILED
    assert [type(event).__name__ for event in events] == ["MediaFailed"]


async def test_run_short_circuits_terminal_jobs_and_finalize_passes_through() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", status=JobStatus.SUCCEEDED, result_file_id="file-x")
    jobs.rows[job.id] = job
    generator = _FakeGenerator()
    use_case = RunMediaJob(jobs, generator)

    attempt = await use_case.run(ctx, job_id=job.id)

    assert attempt.is_redelivery_noop
    assert generator.calls == []

    result, events = await use_case.finalize(ctx, attempt)

    assert result is job
    assert events == ()
    assert jobs.save_calls == []


# --------------------------------------------------------------------------- #
# GetJobStatus                                                                #
# --------------------------------------------------------------------------- #
async def test_get_job_status_returns_the_job() -> None:
    jobs = _FakeMediaJobs()
    ctx = _ctx("ws1")
    job = _job(job_id="job-1", workspace_id="ws1")
    jobs.rows[job.id] = job

    result = await GetJobStatus(jobs).execute(ctx, job.id)

    assert result is job


async def test_get_job_status_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await GetJobStatus(_FakeMediaJobs()).execute(_ctx(), "missing")


# --------------------------------------------------------------------------- #
# Event payloads vs. the published wire JSON Schemas (literal match)          #
# --------------------------------------------------------------------------- #
async def test_media_requested_event_payload_matches_wire_schema() -> None:
    jobs = _FakeMediaJobs()
    _job_result, events = await RequestMedia(jobs, Limits()).execute(
        _ctx("ws1"),
        agent_key="image-agent",
        kind="image",
        prompt="a cat",
        params={"width": 512, "height": 512},
    )
    event = events[0]
    assert isinstance(event, MediaRequested)
    payload = {
        "job_id": event.job_id,
        "kind": event.kind,
        "prompt": event.prompt,
        "agent_key": event.agent_key,
        "params": event.params,
    }
    schema = _load_schema("media.job.requested.v1.json")
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_media_generated_event_payload_matches_wire_schema() -> None:
    event = MediaGenerated(
        job_id=new_uuid7(), workspace_id="ws1", result_file_id=new_uuid7(), occurred_at=utc_now()
    )
    payload = {"job_id": event.job_id, "result_file_id": event.result_file_id}
    schema = _load_schema("media.job.generated.v1.json")
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_media_failed_event_payload_matches_wire_schema() -> None:
    event = MediaFailed(
        job_id=new_uuid7(), workspace_id="ws1", reason="boom", occurred_at=utc_now()
    )
    payload = {"job_id": event.job_id, "reason": event.reason}
    schema = _load_schema("media.job.failed.v1.json")
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_media_requested_schema_rejects_missing_required_field() -> None:
    """Guards against a vacuously-passing schema test above: proves the
    schema really does enforce its ``required`` list."""
    schema = _load_schema("media.job.requested.v1.json")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate({"kind": "image", "prompt": "x"})


def test_media_requested_schema_rejects_additional_properties() -> None:
    """Guards against a vacuously-passing schema test above: proves
    ``additionalProperties: false`` really is enforced."""
    schema = _load_schema("media.job.requested.v1.json")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(
            {"job_id": "x", "kind": "image", "prompt": "p", "extra": "nope"}
        )
