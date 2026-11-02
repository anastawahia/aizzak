"""STG-01: ``InspectDocument`` (BT19; INC1-AC-7, INC1-AC-8, INC1-AC-14).

Every test drives the real use case over the in-memory stack of ``support_electrical`` with a
replayed ele-parse (``FakePdfInspection``): registration through ``DocumentService``, then
``InspectDocument.run`` for the job it created.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from typing import Any

import pytest

from app.framework.errors import NotFoundError
from app.modules.electrical.drawings.domain.characterization import Characterization
from app.modules.electrical.drawings.domain.document import (
    DocumentStatus,
    ExclusionReason,
    ProfileState,
)
from app.modules.electrical.drawings.domain.errors import InspectionUnavailable, PdfRejected
from app.modules.electrical.drawings.domain.job import JobStatus
from app.modules.electrical.drawings.domain.pages import PageKind, StageReason, StageStatus
from app.modules.electrical.drawings.domain.pipeline import Stage
from app.modules.electrical.drawings.domain.profile import DrawingProfile, ProfileSource
from app.modules.electrical.shared.kinds import Convention
from app.modules.electrical.shared.parser import IdentifierParser
from tests.unit.support_electrical import (
    CN_TOKENS,
    ELECTRICAL_SETTINGS,
    IEC_TOKENS,
    FakePdfInspection,
    Stack,
    build_stack,
    make_signals,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)

BODY = b"%PDF-1.7 a drawing"
LOW = {"schema": 1, "auto_accept_confidence": 0.5}  # accepts anything the detector can reach
HIGH = {"schema": 1, "auto_accept_confidence": 1.0}  # accepts nothing


def _pdf(pages: int = 4, tokens: tuple[str, ...] = IEC_TOKENS, **kwargs: Any) -> FakePdfInspection:
    return FakePdfInspection(
        make_summary(page_count=pages, creator="EPLAN Electric P8", producer="EPLAN"),
        [tokens_page(n, tokens) for n in range(1, pages + 1)],
        **kwargs,
    )


async def _registered(
    stack: Stack, *, profile: dict[str, Any] | None = None, file_id: str = "f1", body: bytes = BODY
) -> tuple[str, str]:
    """A project (once), a readable file and its registration; (document id, job id)."""
    project = await stack.projects.get_by_space(stack.ctx, "22222222-2222-7222-8222-222222222222")
    if project is None:
        project = await seed_project(stack, **(profile or LOW))
    await seed_file(stack, file_id, body=body)
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id=file_id, checksum=None
    )
    return registered.document.id, registered.job.id


async def _run(
    stack: Stack,
    job_id: str,
    event_id: str = "e1",
    pdf: FakePdfInspection | None = None,
    **kwargs: Any,
) -> None:
    await stack.inspector(pdf, **kwargs).run(stack.ctx, job_id=job_id, event_id=event_id)


async def _job(stack: Stack, job_id: str) -> Any:
    job = await stack.jobs.get(stack.ctx, job_id)
    assert job is not None
    return job


async def _doc(stack: Stack, document_id: str) -> Any:
    document = await stack.documents.get(stack.ctx, document_id)
    assert document is not None
    return document


@pytest.fixture
def stack() -> Stack:
    return build_stack()


# --------------------------------------------------------------------------- the whole run
async def test_an_ingestion_fingerprints_classifies_and_proposes(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    stack.outbox.records.clear()
    stack.trace.entries.clear()

    await _run(stack, job_id)

    job, document = await _job(stack, job_id), await _doc(stack, doc_id)
    assert job.status is JobStatus.SUCCEEDED and job.stage is Stage.CLASSIFY
    assert document.content_sha256 == hashlib.sha256(BODY).hexdigest()  # a body of known bytes
    assert document.page_count == 4 and document.pdf_creator == "EPLAN Electric P8"
    assert document.profile_state is ProfileState.ACCEPTED and document.is_live
    counts = await stack.pages.stage_counts(stack.ctx, doc_id, Stage.CLASSIFY)
    assert counts == {StageStatus.SUCCEEDED: 4}
    assert [c.kind for c in await stack.pages.classifications(stack.ctx, doc_id)] == [
        PageKind.VECTOR_TEXT
    ] * 4
    detected = await stack.profiles.latest(
        stack.ctx, project_id=document.project_id, document_id=doc_id
    )
    assert detected is not None and detected.source is ProfileSource.DETECTED
    assert detected.profile.identifier_convention is Convention.IEC_81346
    assert detected.confidence is not None and detected.confidence >= 0.5
    stored = await stack.characterizations.get(stack.ctx, doc_id)
    assert stored is not None and stored.producer_family == "eplan" and stored.page_count == 4
    types = stack.outbox.types()
    assert types[-2:] == ["electrical.document.profile_proposed.v1", "electrical.job.completed.v1"]
    assert types.count("electrical.job.progressed.v1") == 2  # two batches of two pages
    assert stack.outbox.data("electrical.job.completed.v1")[0]["status"] == "succeeded"
    assert (
        stack.outbox.data("electrical.document.profile_proposed.v1")[0]["needs_confirmation"]
        is False
    )
    # the staged copy is gone, and the heartbeat beat between ranges
    assert stack.stager.discarded == stack.stager.staged and len(stack.stager.staged) == 1
    assert stack.liveness.beats == 2


async def test_the_last_transaction_starts_with_the_claim_and_holds_the_effect(
    stack: Stack,
) -> None:
    _, job_id = await _registered(stack)
    stack.pdf = _pdf()
    stack.trace.entries.clear()

    await _run(stack, job_id, event_id="evt-7")

    entries = stack.trace.entries
    last_begin = len(entries) - 1 - entries[::-1].index("begin")
    final = entries[last_begin:]
    assert final[0] == "begin" and final[1] == "claim:evt-7"
    assert final[-1] == "commit" and any(e.startswith("outbox:") for e in final)
    assert [e for e in entries if e.startswith("claim:")] == ["claim:evt-7"]  # exactly one
    assert stack.ledger.claimed == ["evt-7"]


async def test_a_duplicate_delivery_changes_nothing(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await _run(stack, job_id, event_id="evt-1")
    snapshot = (dict(stack.jobs.rows), len(stack.outbox.records), len(stack.stager.staged))

    await _run(stack, job_id, event_id="evt-1")  # the same event, delivered again

    assert (dict(stack.jobs.rows), len(stack.outbox.records), len(stack.stager.staged)) == snapshot
    assert stack.ledger.claimed == ["evt-1"]  # one row after two deliveries


async def test_an_unknown_job_is_claimed_and_dropped(stack: Stack) -> None:
    stack.pdf = _pdf()
    await _run(stack, "no-such-job", event_id="evt-x")
    assert stack.ledger.claimed == ["evt-x"] and stack.stager.staged == []


# ------------------------------------------------------------- the proposal and the threshold
async def test_a_confident_proposal_succeeds_and_a_doubtful_one_awaits_confirmation(
    stack: Stack,
) -> None:
    low_doc, low_job = await _registered(stack, file_id="f1")
    stack.pdf = _pdf()
    await _run(stack, low_job)
    assert (await _job(stack, low_job)).status is JobStatus.SUCCEEDED

    strict = build_stack()
    doc_id, job_id = await _registered(strict, profile=HIGH)
    strict.pdf = _pdf()
    await _run(strict, job_id)
    job, document = await _job(strict, job_id), await _doc(strict, doc_id)
    assert job.status is JobStatus.AWAITING_CONFIRMATION and job.lease_until is None
    assert document.profile_state is ProfileState.PROPOSED
    proposed = strict.outbox.data("electrical.document.profile_proposed.v1")[0]
    assert proposed["needs_confirmation"] is True and proposed["job_id"] == job_id
    assert "electrical.job.completed.v1" not in strict.outbox.types()  # not finished yet
    assert low_doc != doc_id


@pytest.mark.parametrize(
    ("threshold", "outcome"), [(0.8, JobStatus.SUCCEEDED), (0.81, JobStatus.AWAITING_CONFIRMATION)]
)
async def test_the_threshold_is_inclusive_in_both_directions(
    threshold: float, outcome: JobStatus
) -> None:
    """A single IEC convention over 100+ identifiers gives convention confidence 1.0, so the
    proposal's confidence is the symbol standard's 1.0 x 0.8 (``SYMBOL_CONFIDENCE_FACTOR``)."""
    stack = build_stack()
    _, job_id = await _registered(stack, profile={"schema": 1, "auto_accept_confidence": threshold})
    stack.pdf = _pdf()
    await _run(stack, job_id)
    assert (await _job(stack, job_id)).status is outcome


async def test_a_proposal_is_made_from_the_full_packaged_rules_not_the_projects_selection(
    stack: Stack,
) -> None:
    """S5's note: ``rule_selection()`` narrows the conventions to the profile's own (IEC 81346 by
    default), under which ``KM1`` is not an identifier at all. A Chinese-letter-code drawing
    under the DEFAULT profile must still be recognised as one."""
    narrowed = stack.rules.identifier_rules().narrowed(DrawingProfile.default().rule_selection())
    assert IdentifierParser(narrowed).tally(CN_TOKENS).identifiers == 0  # the trap

    doc_id, job_id = await _registered(stack)  # the default convention is IEC_81346
    stack.pdf = _pdf(tokens=CN_TOKENS)
    await _run(stack, job_id)

    document = await _doc(stack, doc_id)
    detected = await stack.profiles.latest(
        stack.ctx, project_id=document.project_id, document_id=doc_id
    )
    assert detected is not None
    assert detected.profile.identifier_convention is Convention.CN_LETTER_CODES
    record = await stack.characterizations.get(stack.ctx, doc_id)
    assert record is not None and record.conventions == {Convention.CN_LETTER_CODES: 120}


async def test_mixed_standards_are_flagged_on_the_event(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = FakePdfInspection(
        make_summary(page_count=2),
        [tokens_page(1, IEC_TOKENS), tokens_page(2, CN_TOKENS)],
    )
    await _run(stack, job_id)
    assert stack.outbox.data("electrical.document.profile_proposed.v1")[0]["mixed_standards"]
    record = await stack.characterizations.get(stack.ctx, doc_id)
    assert record is not None and record.mixed_standards is True


# --------------------------------------------------------------------- the file is refused
async def test_a_size_mismatch_fails_the_job_as_source_unreadable(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.stager.bodies[f"{stack.ctx.workspace_id}/f1"] = BODY + b"!"  # not what the row says
    stack.pdf = _pdf()
    await _run(stack, job_id)
    job = await _job(stack, job_id)
    assert job.status is JobStatus.FAILED and job.failure_code == "electrical.source_unreadable"
    assert (await _doc(stack, doc_id)).is_live
    assert stack.outbox.data("electrical.job.completed.v1")[0]["failure_code"] == (
        "electrical.source_unreadable"
    )
    assert stack.pdf.calls == []  # ele-parse never saw it


async def test_a_file_that_is_gone_fails_the_job_as_source_unreadable(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    del stack.files._files[(stack.ctx.workspace_id, "f1")]
    stack.pdf = _pdf()
    await _run(stack, job_id)
    job = await _job(stack, job_id)
    assert job.status is JobStatus.FAILED and job.failure_code == "electrical.source_unreadable"


async def test_a_missing_object_fails_the_job_as_source_unreadable(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    stack.stager.bodies.clear()  # the stager raises NotFoundError for the key
    stack.pdf = _pdf()
    await _run(stack, job_id)
    assert (await _job(stack, job_id)).failure_code == "electrical.source_unreadable"


async def test_the_same_bytes_as_an_active_document_exclude_the_newcomer(stack: Stack) -> None:
    first_doc, first_job = await _registered(stack, file_id="f1")
    stack.pdf = _pdf()
    await _run(stack, first_job, event_id="e1")
    second_doc, second_job = await _registered(stack, file_id="f2")  # identical bytes
    stack.outbox.records.clear()

    await _run(stack, second_job, event_id="e2")

    second = await _doc(stack, second_doc)
    assert second.status is DocumentStatus.EXCLUDED
    assert second.exclusion_reason is ExclusionReason.DUPLICATE_CONTENT
    job = await _job(stack, second_job)
    assert job.status is JobStatus.FAILED and job.failure_code == "electrical.duplicate_document"
    assert first_doc in (job.failure_detail or "")  # names the other document
    assert (await _doc(stack, first_doc)).status is DocumentStatus.ACTIVE  # untouched
    assert stack.outbox.types() == ["electrical.job.completed.v1"]
    assert stack.ledger.claimed == ["e1", "e2"]
    assert stack.stager.discarded == stack.stager.staged


async def test_a_rejected_pdf_fails_with_ele_parses_message_and_touches_nothing_else(
    stack: Stack,
) -> None:
    other_doc, other_job = await _registered(stack, file_id="f0", body=b"%PDF-other")
    stack.pdf = _pdf()
    await _run(stack, other_job, event_id="e0")
    doc_id, job_id = await _registered(stack, file_id="f1")
    stack.pdf = FakePdfInspection(
        make_summary(), [], rejects=PdfRejected("encrypted", "the PDF is encrypted")
    )

    await _run(stack, job_id, event_id="e1")

    job = await _job(stack, job_id)
    assert job.status is JobStatus.FAILED and job.failure_code == "electrical.pdf_rejected"
    assert job.failure_detail == "the PDF is encrypted"
    assert (await _doc(stack, doc_id)).is_live and (await _doc(stack, other_doc)).is_live
    assert (await _job(stack, other_job)).status is JobStatus.SUCCEEDED
    assert stack.stager.discarded == stack.stager.staged


async def test_a_pdf_over_the_page_limit_fails_before_any_page_is_read() -> None:
    stack = build_stack(settings=ELECTRICAL_SETTINGS.model_copy(update={"max_pages": 3}))
    _, job_id = await _registered(stack)
    stack.pdf = _pdf(pages=4)
    await _run(stack, job_id)
    job = await _job(stack, job_id)
    assert job.status is JobStatus.FAILED and job.failure_code == "electrical.too_many_pages"
    assert [c for c in stack.pdf.calls if c[0] == "pages"] == []


# ------------------------------------------------------------- a page may fail, the file may not
@pytest.mark.parametrize("reason", [StageReason.MEMORY_LIMIT, StageReason.PARSE_TIMEOUT])
async def test_a_failing_page_is_recorded_and_the_rest_complete(
    stack: Stack, reason: StageReason, caplog: pytest.LogCaptureFixture
) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf(failures={2: reason})
    caplog.set_level(logging.INFO)

    await _run(stack, job_id)

    job = await _job(stack, job_id)
    assert job.status is JobStatus.SUCCEEDED  # NFR-REL-01: one bad page is not a bad file
    assert await stack.pages.stage_counts(stack.ctx, doc_id, Stage.CLASSIFY) == {
        StageStatus.SUCCEEDED: 3,
        StageStatus.FAILED: 1,
    }
    rows = (await stack.pages.list(stack.ctx, doc_id, limit=10, cursor=None)).data
    failed = next(r for r in rows if r.page_no == 2)
    assert failed.classification is not None and failed.classification.reason is reason
    assert failed.attempts == 2  # the range, then the page alone
    # the range [1,2] failed, so it was asked again page by page
    assert ("pages", 1, 2) in stack.pdf.calls and ("pages", 1, 1) in stack.pdf.calls
    assert ("pages", 2, 2) in stack.pdf.calls
    # the failed page is not evidence, the others are
    record = await stack.characterizations.get(stack.ctx, doc_id)
    assert record is not None and record.text_kinds == {PageKind.VECTOR_TEXT: 3}
    progress = stack.outbox.data("electrical.job.progressed.v1")
    assert progress[-1]["failed"] == 1 and progress[-1]["done"] == progress[-1]["total"] == 4
    # the log says which job, document and page (INC1-AC-14)
    record_log = next(r for r in caplog.records if r.message == "electrical.inspect.page_failed")
    assert (record_log.job_id, record_log.document_id, record_log.page) == (job_id, doc_id, 2)


async def test_ele_parse_unavailable_is_raised_so_the_engine_redelivers(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf(unavailable=True)
    with pytest.raises(InspectionUnavailable):
        await _run(stack, job_id)
    job = await _job(stack, job_id)
    assert job.status is JobStatus.RUNNING  # not failed: the work is only late
    assert job.lease_until is not None and stack.ledger.claimed == []
    assert stack.stager.discarded == stack.stager.staged  # the staged copy does not linger
    assert (await _doc(stack, doc_id)).is_live


# ------------------------------------------------------------------ the contract of the lease
async def test_a_live_lease_turns_a_second_run_away_without_a_claim(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    stack.pdf = _pdf(unavailable=True)
    with pytest.raises(InspectionUnavailable):
        await _run(stack, job_id, event_id="e1")
    stack.pdf = _pdf()
    staged_before = len(stack.stager.staged)

    await _run(stack, job_id, event_id="e2")  # redelivered while the lease is alive

    assert (await _job(stack, job_id)).status is JobStatus.RUNNING
    assert len(stack.stager.staged) == staged_before and stack.ledger.claimed == []


async def test_an_expired_lease_is_taken_over_and_the_attempt_counted(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    stack.pdf = _pdf(unavailable=True)
    with pytest.raises(InspectionUnavailable):
        await _run(stack, job_id, event_id="e1")
    stack.clock.advance(stack.settings.job_lease_s + 1)
    stack.pdf = _pdf()

    await _run(stack, job_id, event_id="e2")

    job = await _job(stack, job_id)
    assert job.status is JobStatus.SUCCEEDED and job.attempt == 2


async def test_a_resumed_run_asks_only_for_the_pages_that_are_left(stack: Stack) -> None:
    """Pages 1-2 are done when ele-parse dies on the second batch; the takeover requests only
    3-4, and what it ends with equals an uninterrupted run (FR-ING-04, FR-ING-05)."""
    fresh = build_stack()
    fresh_doc, fresh_job = await _registered(fresh)
    fresh.pdf = _pdf()
    await _run(fresh, fresh_job)
    expected = await fresh.characterizations.get(fresh.ctx, fresh_doc)

    doc_id, job_id = await _registered(stack)

    class DiesOnTheSecondBatch(FakePdfInspection):
        async def pages(self, source: Any, first: int, last: int) -> Any:
            if first == 3:
                self._unavailable = True
            return await super().pages(source, first, last)

    stack.pdf = DiesOnTheSecondBatch(
        make_summary(page_count=4, creator="EPLAN Electric P8", producer="EPLAN"),
        [tokens_page(n, IEC_TOKENS) for n in range(1, 5)],
    )
    with pytest.raises(InspectionUnavailable):
        await _run(stack, job_id, event_id="e1")
    assert await stack.pages.unfinished(stack.ctx, doc_id, Stage.CLASSIFY) == [3, 4]
    stack.clock.advance(stack.settings.job_lease_s + 1)
    healthy = _pdf()

    await _run(stack, job_id, event_id="e2", pdf=healthy)

    # the takeover asked for the unfinished pages ONLY
    assert [c for c in healthy.calls if c[0] == "pages"] == [("pages", 3, 4)]

    job = await _job(stack, job_id)
    assert job.status is JobStatus.SUCCEEDED
    actual = await stack.characterizations.get(stack.ctx, doc_id)
    assert actual is not None and expected is not None
    assert replace(actual, document_id="x") == replace(expected, document_id="x")


# ----------------------------------------------------------------- cancel, purge, confirmation
class _CancelAfterFirstBatch(FakePdfInspection):
    def __init__(self, stack: Stack, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stack = stack

    async def pages(self, source: Any, first: int, last: int) -> Any:
        found = await super().pages(source, first, last)
        for job in self.stack.jobs.rows.values():
            job.cancel_requested_at = self.stack.clock()  # the API's request_cancel
        return found


async def test_a_cancellation_is_honoured_at_the_batch_boundary(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _CancelAfterFirstBatch(
        stack, make_summary(page_count=4), [tokens_page(n, IEC_TOKENS) for n in range(1, 5)]
    )
    await _run(stack, job_id)

    job = await _job(stack, job_id)
    assert job.status is JobStatus.CANCELLED
    # the first batch was committed, the rest stays pending
    assert await stack.pages.unfinished(stack.ctx, doc_id, Stage.CLASSIFY) == [3, 4]
    assert stack.outbox.data("electrical.job.completed.v1")[-1]["status"] == "cancelled"
    assert stack.stager.discarded == stack.stager.staged
    assert (await _doc(stack, doc_id)).is_live


async def test_a_purge_during_the_run_stops_it_and_writes_no_pages(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)

    class PurgedWhileReading(FakePdfInspection):
        async def pages(self, source: Any, first: int, last: int) -> Any:
            await stack.services["purge_file"].execute(stack.ctx, "f1")
            return await super().pages(source, first, last)

    stack.pdf = PurgedWhileReading(
        make_summary(page_count=4), [tokens_page(n, IEC_TOKENS) for n in range(1, 5)]
    )
    await _run(stack, job_id)

    assert stack.pages.results == {}  # not one page of a purged document was written
    assert (await _doc(stack, doc_id)).status is DocumentStatus.PURGING
    job = await _job(stack, job_id)
    assert job.status is JobStatus.CANCELLED and job.failure_code == "electrical.document_not_live"
    assert stack.characterizations.rows == {}
    assert stack.stager.discarded == stack.stager.staged


async def test_a_document_that_stopped_being_live_cancels_the_job_at_the_start(
    stack: Stack,
) -> None:
    doc_id, job_id = await _registered(stack)
    document = stack.documents.rows[doc_id]
    document.exclude(ExclusionReason.FILE_UNREADABLE, stack.clock())
    stack.pdf = _pdf()

    await _run(stack, job_id)

    job = await _job(stack, job_id)
    assert job.status is JobStatus.CANCELLED and job.failure_code == "electrical.document_not_live"
    assert stack.stager.staged == [] and stack.outbox.data("electrical.job.completed.v1")


async def test_a_confirmation_finishes_the_job_without_downloading_again() -> None:
    stack = build_stack()
    doc_id, job_id = await _registered(stack, profile=HIGH)
    stack.pdf = _pdf()
    await _run(stack, job_id, event_id="e1")
    assert (await _job(stack, job_id)).status is JobStatus.AWAITING_CONFIRMATION
    downloads = len(stack.stager.staged)
    detections = len(stack.profiles.rows)

    job = await stack.document_service.confirm_profile(stack.ctx, doc_id, body=None)
    assert job.status is JobStatus.QUEUED
    await _run(stack, job_id, event_id="e2")

    final, document = await _job(stack, job_id), await _doc(stack, doc_id)
    assert final.status is JobStatus.SUCCEEDED and document.profile_state is ProfileState.CONFIRMED
    assert len(stack.stager.staged) == downloads  # no second download
    assert len(stack.profiles.rows) == detections + 1  # the confirmed version, no new detection
    assert stack.outbox.types()[-1] == "electrical.job.completed.v1"


# --------------------------------------------------------------------------- reprocessing
async def test_a_reprocessing_reclassifies_only_the_pages_asked_for(stack: Stack) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await _run(stack, job_id, event_id="e1")
    stack.pdf.calls.clear()

    job = await stack.document_service.reprocess(stack.ctx, doc_id, pages=[2])
    await _run(stack, job.id, event_id="e2")

    assert [c for c in stack.pdf.calls if c[0] == "pages"] == [("pages", 2, 2)]
    assert (await _job(stack, job.id)).status is JobStatus.SUCCEEDED
    assert await stack.pages.stage_counts(stack.ctx, doc_id, Stage.CLASSIFY) == {
        StageStatus.SUCCEEDED: 4
    }


async def test_a_reprocessing_that_finds_other_bytes_fails_as_source_unreadable(
    stack: Stack,
) -> None:
    doc_id, job_id = await _registered(stack)
    stack.pdf = _pdf()
    await _run(stack, job_id, event_id="e1")
    key = f"{stack.ctx.workspace_id}/f1"
    stack.files._files[(stack.ctx.workspace_id, "f1")] = replace(
        stack.files._files[(stack.ctx.workspace_id, "f1")], size_bytes=len(BODY) + 1
    )
    stack.stager.bodies[key] = BODY + b"!"  # same size as the row, other bytes

    job = await stack.document_service.reprocess(stack.ctx, doc_id, pages=None)
    await _run(stack, job.id, event_id="e2")

    failed = await _job(stack, job.id)
    assert (
        failed.status is JobStatus.FAILED and failed.failure_code == "electrical.source_unreadable"
    )


# ------------------------------------------------------------- progress, determinism, logging
async def test_progress_is_numbered_increasing_and_rate_limited() -> None:
    stack = build_stack(
        settings=ELECTRICAL_SETTINGS.model_copy(
            update={"inspect_batch_pages": 1, "progress_min_interval_s": 1_000.0}
        )
    )
    _, job_id = await _registered(stack)
    stack.pdf = _pdf(pages=5)
    await _run(stack, job_id, monotonic=lambda: 10.0)  # the clock never moves

    events = stack.outbox.data("electrical.job.progressed.v1")
    assert [e["seq"] for e in events] == [1, 2]  # the first batch and the last, no more
    assert [e["done"] for e in events] == [1, 5]
    assert (await _job(stack, job_id)).seq == 2


async def test_the_same_inputs_give_the_same_rows_byte_for_byte() -> None:
    """NFR-REP-02: two stacks, one file and one profile, the same rows."""

    async def ingest() -> tuple[Characterization, list[Any]]:
        stack = build_stack()
        doc_id, job_id = await _registered(stack)
        stack.pdf = _pdf(tokens=IEC_TOKENS + CN_TOKENS[:5])
        await _run(stack, job_id)
        record = await stack.characterizations.get(stack.ctx, doc_id)
        assert record is not None
        rows = [
            (key[2:], value.classification, value.signals, value.tally)
            for key, value in sorted(stack.pages.results.items(), key=lambda kv: kv[0][2])
        ]
        return replace(record, document_id="x"), rows

    assert await ingest() == await ingest()


async def test_the_staged_copy_is_discarded_even_when_the_run_raises(stack: Stack) -> None:
    _, job_id = await _registered(stack)
    stack.pdf = _pdf()
    stack.outbox.fail = True  # the first write of the pages stage blows up
    with pytest.raises(RuntimeError):
        await _run(stack, job_id)
    assert stack.stager.discarded == stack.stager.staged and stack.stager.staged


async def test_unknown_file_ids_never_reach_the_stager(stack: Stack) -> None:
    with pytest.raises(NotFoundError):
        await stack.document_service.register(stack.ctx, "nope", file_id="f9", checksum=None)
    assert stack.stager.staged == []


def test_make_signals_is_a_clean_text_page() -> None:
    assert make_signals(1).char_count == 500  # the fixture the thresholds above lean on
