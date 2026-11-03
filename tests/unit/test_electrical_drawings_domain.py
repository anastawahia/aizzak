"""Drawings domain aggregates and values (BT15, INC1-AC-5): profile validation and fingerprint,
document / job / project state machines, the events, and the in-memory fakes S7 builds on."""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from typing import Any

import pytest

from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.modules.electrical.drawings.domain.classification import classify_page
from app.modules.electrical.drawings.domain.document import (
    DocumentStatus,
    ExclusionReason,
    ProfileState,
)
from app.modules.electrical.drawings.domain.errors import (
    DocumentStateError,
    InvalidDrawingInput,
    InvalidProfile,
    JobStateError,
    PageRangeFailed,
    PdfRejected,
)
from app.modules.electrical.drawings.domain.events import (
    DocumentPurged,
    JobCompleted,
    JobRequested,
    JobRequestReason,
)
from app.modules.electrical.drawings.domain.job import JobKind, JobStatus
from app.modules.electrical.drawings.domain.pages import (
    ClassificationThresholds,
    PageResult,
    StageReason,
)
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION, Stage
from app.modules.electrical.drawings.domain.profile import (
    CrossReferenceFormat,
    DrawingProfile,
    ProfileSource,
    ProfileVersion,
    SymbolStandard,
)
from app.modules.electrical.drawings.domain.project import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
)
from app.modules.electrical.shared.kinds import Convention, PlcFamily
from app.modules.electrical.shared.rules import IdentifierRules
from tests.eval.electrical.m04_identifiers import load_rules
from tests.unit.support_electrical import (
    NOW,
    WS,
    FakeDrawingFile,
    FakeDrawingFiles,
    FakePdfInspection,
    FakeStager,
    InMemoryDocumentRepository,
    InMemoryJobRepository,
    InMemoryPageRepository,
    InMemoryProfileRepository,
    InMemoryProjectRepository,
    later,
    make_ctx,
    make_document,
    make_job,
    make_project,
    make_signals,
    make_summary,
)

KNOWN = frozenset({"sequential", "page_column", "voltage_based"})


def body(**changes: Any) -> dict[str, Any]:
    return {"schema": 1, **changes}


def profile(**changes: Any) -> DrawingProfile:
    return DrawingProfile.from_body(body(**changes), known_patterns=KNOWN)


# --------------------------------------------------------------------------------------
# the profile (FR-ING-01, NFR-CFG-02)
# --------------------------------------------------------------------------------------
def test_the_default_profile_round_trips_through_its_canonical_body() -> None:
    default = DrawingProfile.default()
    again = DrawingProfile.from_body(default.to_body(), known_patterns=KNOWN)
    assert again == default
    assert again.fingerprint() == default.fingerprint()
    assert default.to_body()["schema"] == 1


def test_an_empty_body_with_a_schema_is_the_default() -> None:
    assert profile() == DrawingProfile.default()


@pytest.mark.parametrize(
    ("raw", "path"),
    [
        ({}, "schema"),
        ({"schema": 2}, "schema"),
        ({"schema": True}, "schema"),
        (body(surprise=1), "body"),  # S-8: an unknown name is not echoed as the path
        (body(symbol_standard="made_up"), "symbol_standard"),
        (body(identifier_convention=3), "identifier_convention"),
        (body(cross_reference_format="nope"), "cross_reference_format"),
        (body(junction_convention=None), "junction_convention"),
        (body(plc_vendor="mitsubishi"), "plc_vendor"),
        (body(plc_families=[]), "plc_families"),
        (body(plc_families=["s7_en", "z80"]), "plc_families[1]"),
        (body(plc_families="s7_en"), "plc_families"),
        (body(languages=[]), "languages"),
        (body(languages=["en", "fr"]), "languages[1]"),
        (body(wire_number_patterns=["sequential", "(a+)+"]), "wire_number_patterns[1]"),
        (body(wire_number_patterns=[1]), "wire_number_patterns[0]"),
        (body(extra_aspects=["x"]), "extra_aspects[0]"),
        (body(extra_aspects=["=", "+", "-", "&", "#", "==", "++", "&&", "##"]), "extra_aspects"),
        (body(voltage_labels=["x" * 17]), "voltage_labels[0]"),
        (body(voltage_labels=[" "]), "voltage_labels[0]"),
        (body(voltage_labels=["a"] * 65), "voltage_labels"),
        (body(auto_accept_confidence=0.49), "auto_accept_confidence"),
        (body(auto_accept_confidence=1.01), "auto_accept_confidence"),
        (body(auto_accept_confidence="high"), "auto_accept_confidence"),
        (body(auto_accept_confidence=True), "auto_accept_confidence"),
        (body(vendor_library="acme"), "vendor_library"),
        (body(symbol_standard="vendor", vendor_library=""), "vendor_library"),
        (body(classification=[]), "classification"),
        (body(classification={"min_text": 3}), "classification"),
        (body(classification={"min_text_chars": 2.5}), "classification.min_text_chars"),
        (body(classification={"min_text_chars": -1}), "classification.min_text_chars"),
        (body(classification={"garbled_ratio": 1.5}), "classification.garbled_ratio"),
        (body(classification={"ambiguity_margin": 0.9}), "classification.ambiguity_margin"),
        (body(classification={"outlined_curves_min": 0}), "classification.outlined_curves_min"),
    ],
)
def test_from_body_rejects_naming_the_field_path(raw: dict[str, Any], path: str) -> None:
    with pytest.raises(InvalidProfile) as caught:
        DrawingProfile.from_body(raw, known_patterns=KNOWN)
    assert caught.value.path == path
    assert path in str(caught.value)


_HOSTILE = "evil\u202e\u200bname<script>" + "x" * 300


@pytest.mark.parametrize(
    "raw",
    [
        body(**{_HOSTILE: 1}),
        body(classification={_HOSTILE: 1}),
        body(wire_number_patterns=[_HOSTILE]),
    ],
)
def test_an_invalid_profile_never_echoes_what_the_client_sent(raw: dict[str, Any]) -> None:
    """S-8 (PR-B security review): the 422 names the field and lists what the SERVER knows; the
    unknown name or value the client supplied (any length, any script, bidi controls) is not
    reflected in the path, the message or the string form."""
    with pytest.raises(InvalidProfile) as caught:
        DrawingProfile.from_body(raw, known_patterns=KNOWN)
    for text in (caught.value.path, caught.value.message, str(caught.value)):
        assert "evil" not in text
        assert "\u202e" not in text
        assert "<script>" not in text
        assert len(text) < 300


def test_from_body_rejects_a_body_that_is_not_an_object() -> None:
    not_an_object: Any = []
    with pytest.raises(InvalidProfile) as caught:
        DrawingProfile.from_body(not_an_object, known_patterns=KNOWN)
    assert caught.value.path == "body"


def test_a_vendor_library_is_allowed_only_with_the_vendor_standard() -> None:
    found = profile(symbol_standard="vendor", vendor_library="  Acme Symbols ")
    assert found.symbol_standard is SymbolStandard.VENDOR
    assert found.vendor_library == "Acme Symbols"


def test_set_like_lists_are_canonical_whatever_their_order() -> None:
    a = profile(
        languages=["zh", "en"],
        plc_families=["tia", "s7_en"],
        wire_number_patterns=["page_column", "sequential"],
        extra_aspects=["++", "=="],
        voltage_labels=["-48V", "+12V", "-48V"],
    )
    b = profile(
        languages=["en", "zh"],
        plc_families=["s7_en", "tia"],
        wire_number_patterns=["sequential", "page_column"],
        extra_aspects=["==", "++"],
        voltage_labels=["+12V", "-48V"],
    )
    assert a == b and a.fingerprint() == b.fingerprint()


def test_the_fingerprint_ignores_json_key_order_and_follows_every_value() -> None:
    default = DrawingProfile.default()
    shuffled = dict(reversed(list(default.to_body().items())))
    assert DrawingProfile.from_body(shuffled, known_patterns=KNOWN).fingerprint() == (
        default.fingerprint()
    )
    assert len(default.fingerprint()) == 64
    changed = profile(classification={"garbled_ratio": 0.25})
    assert changed.fingerprint() != default.fingerprint()
    assert profile(auto_accept_confidence=0.95).fingerprint() != default.fingerprint()
    # the canonical body is plain JSON
    assert json.loads(json.dumps(default.to_body())) == default.to_body()


def test_the_rule_selection_narrows_the_packaged_rules_by_name_only() -> None:
    chosen = profile(
        identifier_convention="cn_letter_codes",
        cross_reference_format="page_dot_column",
        wire_number_patterns=["sequential"],
        plc_families=["s7_en"],
        voltage_labels=["-48V"],
    )
    selection = chosen.rule_selection()
    assert selection.conventions == frozenset({Convention.CN_LETTER_CODES})
    assert selection.cross_reference_format == "page_dot_column"
    assert selection.plc_families == frozenset({PlcFamily.S7_EN})
    narrowed: IdentifierRules = load_rules().narrowed(selection)
    assert set(narrowed.wire_number_patterns) == {"sequential"}
    assert set(narrowed.cross_reference_formats) == {"page_dot_column"}
    assert "-48V" in narrowed.voltage_labels
    assert DrawingProfile.default().rule_selection().cross_reference_format is None


def test_the_known_pattern_names_are_the_packaged_ones() -> None:
    assert frozenset(load_rules().wire_number_patterns) == KNOWN


def test_every_cross_reference_choice_names_a_packaged_format() -> None:
    names = set(load_rules().cross_reference_formats)
    assert {f.value for f in CrossReferenceFormat} - {"auto"} == names


async def test_profile_versions_are_consecutive_per_owner() -> None:
    repo = InMemoryProfileRepository()
    ctx = make_ctx()

    def version(document_id: str | None) -> ProfileVersion:
        default = DrawingProfile.default()
        return ProfileVersion(
            id="v",
            workspace_id=WS,
            project_id="p1",
            document_id=document_id,
            version=0,
            source=ProfileSource.DEFAULT,
            profile=default,
            fingerprint=default.fingerprint(),
            confidence=None,
            mixed_standards=False,
            created_by="u1",
            created_at=NOW,
        )

    assert (await repo.append(ctx, version(None))).version == 1
    assert (await repo.append(ctx, version(None))).version == 2
    assert (await repo.append(ctx, version("d1"))).version == 1  # a document owns its own line
    latest = await repo.latest(ctx, project_id="p1", document_id=None)
    assert latest is not None and latest.version == 2
    assert await repo.latest(make_ctx("other"), project_id="p1", document_id=None) is None


# --------------------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------------------
SHA = "a" * 64


def test_a_new_document_is_live_and_excluding_it_keeps_the_row_out_of_every_read() -> None:
    doc = make_document()
    assert doc.is_live
    doc.exclude(ExclusionReason.DUPLICATE_CONTENT, later(1))
    assert doc.status is DocumentStatus.EXCLUDED and not doc.is_live
    assert doc.exclusion_reason is ExclusionReason.DUPLICATE_CONTENT
    with pytest.raises(DocumentStateError):
        doc.exclude(ExclusionReason.FILE_UNREADABLE, later(2))


def test_purging_is_reachable_from_any_status_and_idempotent() -> None:
    for status in DocumentStatus:
        doc = make_document(status=status)
        changed = doc.mark_purging(later(5))
        assert changed is (status is not DocumentStatus.PURGING)
        assert doc.status is DocumentStatus.PURGING
    doc = make_document()
    assert doc.mark_purging(later(5)) is True and doc.purging_at == later(5)
    assert doc.mark_purging(later(9)) is False
    assert doc.purging_at == later(5) and doc.updated_at == later(5)  # nothing written


def test_purging_an_excluded_document_clears_its_exclusion_reason() -> None:
    """``ck_drawings_documents_exclusion``: a reason exists only on an EXCLUDED document, so
    the transition to PURGING takes it away (S6 found the old code kept it)."""
    doc = make_document()
    doc.exclude(ExclusionReason.FILE_UNREADABLE, later(1))
    assert doc.exclusion_reason is ExclusionReason.FILE_UNREADABLE
    assert doc.mark_purging(later(2)) is True
    assert doc.status is DocumentStatus.PURGING and doc.exclusion_reason is None
    # the pairing holds for every status the aggregate can be in after any transition
    for status in DocumentStatus:
        other = make_document()
        if status is DocumentStatus.EXCLUDED:
            other.exclude(ExclusionReason.DUPLICATE_CONTENT, later(1))
        elif status is DocumentStatus.PURGING:
            other.mark_purging(later(1))
        assert (other.status is DocumentStatus.EXCLUDED) == (other.exclusion_reason is not None)


def test_an_inspection_is_recorded_on_a_live_document_only() -> None:
    doc = make_document()
    doc.record_inspection(
        sha256=SHA,
        page_count=12,
        creator="EPLAN",
        producer="Cairo",
        pipeline_version=PIPELINE_VERSION,
        now=later(3),
    )
    assert (doc.content_sha256, doc.page_count, doc.pdf_creator) == (SHA, 12, "EPLAN")
    assert doc.pipeline_version == PIPELINE_VERSION and doc.updated_at == later(3)
    for status in (DocumentStatus.EXCLUDED, DocumentStatus.PURGING):
        with pytest.raises(DocumentStateError):
            make_document(status=status).record_inspection(
                sha256=SHA,
                page_count=1,
                creator=None,
                producer=None,
                pipeline_version="1",
                now=NOW,
            )


@pytest.mark.parametrize(("sha", "pages"), [("xyz", 1), ("A" * 64, 1), (SHA, -1)])
def test_an_inspection_with_a_bad_hash_or_count_is_refused(sha: str, pages: int) -> None:
    with pytest.raises(InvalidDrawingInput):
        make_document().record_inspection(
            sha256=sha, page_count=pages, creator=None, producer=None, pipeline_version="1", now=NOW
        )


def test_the_profile_state_moves_only_on_a_live_document_and_a_repeat_writes_nothing() -> None:
    doc = make_document()
    doc.set_profile_state(ProfileState.PROPOSED, later(1))
    assert doc.profile_state is ProfileState.PROPOSED and doc.updated_at == later(1)
    doc.set_profile_state(ProfileState.PROPOSED, later(9))
    assert doc.updated_at == later(1)
    with pytest.raises(DocumentStateError):
        make_document(status=DocumentStatus.PURGING).set_profile_state(ProfileState.ACCEPTED, NOW)


# --------------------------------------------------------------------------------------
# the project
# --------------------------------------------------------------------------------------
def test_a_project_renames_and_describes_without_churn() -> None:
    project = make_project()
    project.rename("  Plant 7  ", later(1))
    assert project.name == "Plant 7" and project.updated_at == NOW  # no-op: same name
    project.rename("Plant 8", later(2))
    assert project.name == "Plant 8" and project.updated_at == later(2)
    project.describe("  north hall ", later(3))
    assert project.description == "north hall"
    project.describe("   ", later(4))
    assert project.description is None and project.updated_at == later(4)
    project.describe(None, later(5))
    assert project.updated_at == later(4)


@pytest.mark.parametrize("name", ["", "   ", "x" * (MAX_NAME_CHARS + 1), "bad\x00name", "a\u200bb"])
def test_a_bad_project_name_is_refused(name: str) -> None:
    with pytest.raises(InvalidDrawingInput):
        make_project().rename(name, NOW)


def test_the_limits_of_a_project_are_inclusive() -> None:
    project = make_project()
    project.rename("n" * MAX_NAME_CHARS, later(1))
    project.describe("d" * MAX_DESCRIPTION_CHARS, later(1))
    with pytest.raises(InvalidDrawingInput):
        project.describe("d" * (MAX_DESCRIPTION_CHARS + 1), later(2))


# --------------------------------------------------------------------------------------
# the job (FR-ING-04/05)
# --------------------------------------------------------------------------------------
def test_a_job_runs_to_success() -> None:
    job = make_job()
    assert job.is_open and not job.is_stale(NOW)
    assert job.take_lease(later(1), 600) is True
    assert job.status is JobStatus.RUNNING and job.started_at == later(1)
    assert job.lease_until == later(601)
    job.extend_lease(later(100), 600)
    assert job.lease_until == later(700)
    assert [job.next_seq(), job.next_seq()] == [1, 2]
    job.succeed(later(200))
    assert job.status is JobStatus.SUCCEEDED and not job.is_open
    assert job.finished_at == later(200) and job.lease_until is None


def test_a_lease_is_taken_once_and_only_taken_over_when_stale() -> None:
    job = make_job()
    assert job.take_lease(later(0), 60) is True
    assert job.take_lease(later(30), 60) is False  # held
    assert job.is_stale(later(61)) and not job.is_stale(later(59))
    assert job.take_lease(later(61), 60) is True  # the worker died: taken over
    assert job.attempt == 2 and job.started_at == later(0)
    for status in (JobStatus.SUCCEEDED, JobStatus.AWAITING_CONFIRMATION, JobStatus.CANCELLED):
        assert make_job(status=status).take_lease(NOW, 60) is False


def test_a_running_job_that_needs_confirmation_waits_and_is_requeued_by_the_person() -> None:
    job = make_job()
    job.take_lease(later(0), 60)
    job.await_confirmation(later(5))
    assert job.status is JobStatus.AWAITING_CONFIRMATION and job.lease_until is None
    assert job.is_open
    job.requeue(later(9))
    assert job.status is JobStatus.QUEUED and job.attempt == 2


def test_a_failed_job_keeps_its_code_and_a_bounded_detail_until_resumed() -> None:
    job = make_job()
    job.take_lease(later(0), 60)
    job.fail("electrical.pdf_rejected", "x" * 5000, later(3))
    assert job.status is JobStatus.FAILED and job.failure_code == "electrical.pdf_rejected"
    assert job.failure_detail is not None and len(job.failure_detail) == 2000
    assert not job.is_open
    job.requeue(later(8))
    assert job.status is JobStatus.QUEUED and job.attempt == 2
    assert job.failure_code is None and job.failure_detail is None and job.finished_at is None


def test_a_cancel_is_immediate_when_queued_and_cooperative_when_running() -> None:
    queued = make_job()
    queued.cancel(later(1))
    assert queued.status is JobStatus.CANCELLED and queued.finished_at == later(1)

    running = make_job()
    running.take_lease(later(0), 60)
    running.request_cancel(later(2))
    running.request_cancel(later(3))  # repeating keeps the first moment
    assert running.status is JobStatus.RUNNING and running.cancel_requested_at == later(2)
    running.cancel(later(4))
    assert running.status is JobStatus.CANCELLED

    purged = make_job()
    purged.cancel(later(1), code="electrical.document_not_live")
    assert purged.failure_code == "electrical.document_not_live"


def test_a_cancelled_job_can_be_resumed_and_a_stale_running_one_too() -> None:
    cancelled = make_job()
    cancelled.cancel(later(1))
    cancelled.requeue(later(2))
    assert cancelled.status is JobStatus.QUEUED and cancelled.cancel_requested_at is None

    stale = make_job()
    stale.take_lease(later(0), 60)
    stale.request_cancel(later(10))
    stale.requeue(later(100))  # lease expired at 60
    assert stale.status is JobStatus.QUEUED and stale.attempt == 2
    assert stale.cancel_requested_at is None


@pytest.mark.parametrize(
    "act",
    [
        lambda j: j.extend_lease(NOW, 60),
        lambda j: j.succeed(NOW),
        lambda j: j.fail("c", "d", NOW),
        lambda j: j.await_confirmation(NOW),
        lambda j: j.request_cancel(NOW),
    ],
)
def test_actions_that_need_a_running_job_refuse_a_queued_one(act: Any) -> None:
    with pytest.raises(JobStateError):
        act(make_job())


def test_requeue_and_cancel_refuse_what_the_table_does_not_allow() -> None:
    for status in (JobStatus.QUEUED, JobStatus.SUCCEEDED):
        with pytest.raises(JobStateError):
            make_job(status=status).requeue(NOW)
    running = make_job(status=JobStatus.RUNNING, lease_until=NOW + timedelta(seconds=60))
    with pytest.raises(JobStateError):
        running.requeue(NOW)  # not stale
    for status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
        with pytest.raises(JobStateError):
            make_job(status=status).cancel(NOW)


def test_job_kinds_and_stage_are_the_declared_ones() -> None:
    assert [k.value for k in JobKind] == ["ingest", "reprocess"]
    assert [s.value for s in Stage] == ["classify"]


# --------------------------------------------------------------------------------------
# events and errors
# --------------------------------------------------------------------------------------
def test_events_are_frozen_references() -> None:
    purged = DocumentPurged("d1", WS, "p1", "f1", NOW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        purged.__setattr__("file_id", "other")
    assert JobRequested("j1", WS, "d1", JobRequestReason.RESUME, NOW).reason.value == "resume"
    done = JobCompleted("j1", WS, "d1", JobStatus.FAILED, "electrical.pdf_rejected", NOW)
    assert done.status is JobStatus.FAILED


def test_the_inspection_errors_carry_their_cause() -> None:
    rejected = PdfRejected("encrypted", "password protected")
    assert (rejected.code, rejected.detail) == ("encrypted", "password protected")
    assert PageRangeFailed(StageReason.MEMORY_LIMIT).reason is StageReason.MEMORY_LIMIT


# --------------------------------------------------------------------------------------
# the in-memory fakes S7 stands on (BT14)
# --------------------------------------------------------------------------------------
async def test_the_fake_repositories_scope_by_workspace_and_raise_the_unique_index_codes() -> None:
    ctx, other = make_ctx(), make_ctx("99999999-9999-7999-8999-999999999999")
    projects = InMemoryProjectRepository()
    await projects.add(ctx, make_project())
    assert await projects.get(other, "p1") is None
    with pytest.raises(ConflictError) as dup:
        await projects.add(ctx, make_project(id="p2"))
    assert dup.value.code == "electrical.project_exists"
    stale = make_project()
    fresh = (await projects.get(ctx, "p1")) or stale
    fresh.rename("Other", later(1))
    await projects.save(ctx, fresh)
    assert fresh.version == 2
    with pytest.raises(ConflictError):
        await projects.save(ctx, stale)  # version 1 against a stored version 2

    documents = InMemoryDocumentRepository()
    await documents.add(ctx, make_document())
    with pytest.raises(ConflictError) as same_file:
        await documents.add(ctx, make_document(id="d2"))
    assert same_file.value.code == "electrical.document_exists"
    await documents.add(ctx, make_document(id="d2", file_id="f2", content_sha256=SHA))
    twin = make_document(id="d3", file_id="f3")
    await documents.add(ctx, twin)
    twin.content_sha256 = SHA
    with pytest.raises(ConflictError) as same_bytes:
        await documents.save(ctx, twin)
    assert same_bytes.value.code == "electrical.duplicate_document"
    assert await documents.lock_live(ctx, "d1") is not None
    assert await documents.lock_live(other, "d1") is None
    assert await documents.active_files(ctx, "p1") == {"d1": "f1", "d2": "f2", "d3": "f3"}

    jobs = InMemoryJobRepository()
    await jobs.add(ctx, make_job())
    with pytest.raises(ConflictError) as second_open:
        await jobs.add(ctx, make_job(id="j2"))
    assert second_open.value.code == "electrical.job_conflict"
    assert await jobs.open_count(ctx) == 1 and await jobs.open_count(other) == 0
    first = await jobs.get(ctx, "j1")
    assert first is not None and await jobs.queue_position(ctx, first) == 1


async def test_the_fake_page_repository_resumes_from_the_unfinished_pages() -> None:
    ctx, pages = make_ctx(), InMemoryPageRepository()
    await pages.seed(ctx, "d1", [1, 2, 3], Stage.CLASSIFY, PIPELINE_VERSION)
    assert list(await pages.unfinished(ctx, "d1", Stage.CLASSIFY)) == [1, 2, 3]
    signals = make_signals(2)
    result = PageResult(classify_page(signals, ClassificationThresholds()), signals)
    await pages.record(ctx, "d1", [result])
    await pages.seed(ctx, "d1", [1, 2, 3], Stage.CLASSIFY, PIPELINE_VERSION)  # harmless re-seed
    assert list(await pages.unfinished(ctx, "d1", Stage.CLASSIFY)) == [1, 3]
    counts = await pages.stage_counts(ctx, "d1", Stage.CLASSIFY)
    assert counts == {"pending": 2, "succeeded": 1}
    assert [
        r.page_no
        for r in (await pages.list(ctx, "d1", limit=9, cursor=None)).data
        if r.classification
    ] == [2]
    listing = await pages.list(ctx, "d1", limit=2, cursor=None)
    assert [r.page_no for r in listing.data] == [1, 2] and listing.next_cursor is not None
    rest = await pages.list(ctx, "d1", limit=2, cursor=listing.next_cursor)
    assert [r.page_no for r in rest.data] == [3] and rest.next_cursor is None


INSPECT = json.dumps(
    {
        "schema": 1,
        "summary": {
            "page_count": 2,
            "pdf_version": "1.6",
            "encrypted": False,
            "creator": "EPLAN",
            "producer": None,
            "outline_count": 0,
        },
        "pages": [
            {**dataclasses.asdict(make_signals(1)), "tokens": ["-K5"]},
            {
                **dataclasses.asdict(make_signals(2)),
                "fonts": [
                    {
                        "name": "F1",
                        "font_type": "Type0",
                        "cid": True,
                        "embedded": True,
                        "has_tounicode": False,
                    }
                ],
            },
        ],
    }
)


async def test_the_fake_inspection_replays_an_inspect_file() -> None:
    fake = FakePdfInspection.from_json(INSPECT, failures={2: StageReason.PARSE_TIMEOUT})
    source = await FakeStager({"k": b"%PDF"}).stage(
        make_ctx(), document_id="d1", storage_key="k", expected_size=4
    )
    summary = await fake.summary(source)
    assert summary.creator == "EPLAN" and summary.page_count == 2
    first = await fake.pages(source, 1, 1)
    assert first[0].tokens == ("-K5",)
    with pytest.raises(PageRangeFailed) as range_failed:
        await fake.pages(source, 1, 2)
    assert range_failed.value.reason is StageReason.PARSE_TIMEOUT
    with pytest.raises(PageRangeFailed):
        await fake.pages(source, 2, 2)
    clean = FakePdfInspection.from_json(INSPECT)
    second = (await clean.pages(source, 2, 2))[0]
    assert second.fonts[0].cid and not second.fonts[0].has_tounicode
    assert make_summary(creator="x").creator == "x"


async def test_the_fake_stager_and_files_answer_like_their_ports() -> None:
    stager = FakeStager({"k": b"abc"})
    ctx = make_ctx()
    source = await stager.stage(ctx, document_id="d1", storage_key="k", expected_size=3)
    assert source.name == f"{WS}/d1.pdf" and source.size_bytes == 3
    assert source.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    with pytest.raises(ValidationError) as unreadable:
        await stager.stage(ctx, document_id="d1", storage_key="k", expected_size=4)
    assert unreadable.value.code == "electrical.source_unreadable"
    with pytest.raises(NotFoundError):
        await stager.stage(ctx, document_id="d1", storage_key="gone", expected_size=0)
    await stager.discard(source)
    assert stager.discarded == [source]

    files = FakeDrawingFiles()
    files.add(FakeDrawingFile(file_id="f1"))
    assert (await files.get_readable(ctx, "f1")) is not None
    assert await files.get_readable(make_ctx("other"), "f1") is None
    assert await files.names_for_files(ctx, ["f1", "nope"]) == {"f1": "plant.pdf"}
