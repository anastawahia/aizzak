"""Unit tests for document summaries (BE-RAG-009/010/011): the two new
aggregates, the map-reduce pipeline, and the use-cases over the shared
in-memory stack (``support_knowledge``).

What these pin, against 06 §7 INV-K6/INV-K7:

* a summary is keyed on ``(document_id, kind, lang)`` and there is **no
  fallback between keys** — asking for the Arabic overview never yields the
  English full text;
* ``purge`` destroys a document's summaries, so re-indexing cannot leave one
  describing text that no longer exists (INV-K6);
* the job **stores** its progress, unlike ``ReindexJob``, and a cancellation
  lands on the row rather than being derived from anything (INV-K7);
* **a cancelled job cannot resurrect itself**: the worker's progress write is
  guarded on ``status = 'running'``, which is the one property that makes
  cooperative cancellation safe rather than merely likely;
* ``overview`` makes exactly one provider call and ``full`` maps then
  reduces, so the cost of each kind is a property of the code and not of the
  document that happens to be passed;
* a document longer than the map ceiling is summarised as a **prefix** and
  says so through ``truncated``;
* a build that fails or is cancelled leaves any PREVIOUS summary untouched.

The LLM is a recording fake rather than a mock with expectations: what
matters is how many calls were made and what was in them, and a call list
says that where a strict mock only says "something was called".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError
from app.framework.ports.llm_provider import LlmMessage, LlmParams, LlmResult
from app.modules.knowledge.application import summarization as summarization_module
from app.modules.knowledge.application.summarization import (
    SummarizeDocument,
    SummaryBuildCancelled,
)
from app.modules.knowledge.application.use_cases import (
    SUMMARY_CANCELLED_REASON,
    BuildSummary,
    CancelSummaryJob,
    DeleteSummary,
    GetSummary,
    GetSummaryJob,
    RequestSummary,
)
from app.modules.knowledge.domain.entities import Summary, SummaryJob
from app.modules.knowledge.domain.errors import SummaryJobStateError
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
)
from app.modules.knowledge.ports.summarization import ResolvedSummarizer
from tests.unit.support_knowledge import build_knowledge, seed_document

_W1 = "ws1"
_W2 = "ws2"
_AT = datetime(2026, 8, 11, 9, 0, 0, tzinfo=UTC)


def _ctx(workspace_id: str = _W1) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


@dataclass
class RecordingLLM:
    """A structural ``LLMProvider`` that records every completion.

    ``provider``/``stream``/``supports`` exist to satisfy the Protocol
    structurally; only ``complete`` is ever reached, because summarisation
    never streams — a summary is read when it is finished, and streaming one
    would be a progress bar spelled out in tokens.
    """

    provider: str = "fake"
    reply: str = "SUMMARY"
    calls: list[tuple[list[LlmMessage], LlmParams]] = field(default_factory=list)

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        self.calls.append((list(messages), params))
        return LlmResult(
            content=f"{self.reply}-{len(self.calls)}",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    def stream(self, messages: object, params: object, api_key: str) -> object:
        raise AssertionError("a summary is never streamed")

    def supports(self, capability: str) -> bool:
        return False


@dataclass
class StubSummarizerResolver:
    """A structural ``SummarizerResolver``. ``error`` makes the "no route
    resolves a key" branch reachable, which is the failure the worker's
    ``fail`` entry point exists for."""

    llm: RecordingLLM
    model: str = "test-model"
    error: Exception | None = None

    async def resolve_summarizer(self, ctx: ExecutionContext) -> ResolvedSummarizer:
        if self.error is not None:
            raise self.error
        return ResolvedSummarizer(provider=self.llm, model=self.model, api_key="k")


def _job(
    *,
    job_id: str = "job-1",
    workspace_id: str = _W1,
    document_id: str = "doc-1",
    status: SummaryJobStatus = SummaryJobStatus.QUEUED,
    total: int = 0,
    done: int = 0,
) -> SummaryJob:
    return SummaryJob(
        id=job_id,
        workspace_id=workspace_id,
        document_id=document_id,
        kind=SummaryKind.FULL,
        lang=SummaryLanguage.AUTO,
        status=status,
        total_chunks=total,
        done_chunks=done,
        error=None,
        cancelled_at=None,
        finished_at=None,
        created_at=_AT,
    )


def _summary(*, document_id: str = "doc-1", text: str = "old text") -> Summary:
    return Summary(
        id="sum-1",
        workspace_id=_W1,
        document_id=document_id,
        kind=SummaryKind.FULL,
        lang=SummaryLanguage.AUTO,
        text=text,
        model="previous-model",
        source_chunks=3,
        truncated=False,
        built_at=_AT,
    )


# --------------------------------------------------------------------------- #
# SummaryJob — the aggregate                                                   #
# --------------------------------------------------------------------------- #


def test_percent_is_zero_while_queued_and_never_reaches_100_before_succeeding() -> None:
    """A job with no measured total is 0, not a division by zero — and a
    running job is capped at 99 even when every chunk is mapped, because the
    reduce step that turns the mapped pieces into the summary is real work
    ``total_chunks`` does not count. Showing 100 twice, seconds apart, would
    mean two different things."""
    job = _job()
    assert job.percent == 0

    job.start(4)
    assert job.percent == 0
    job.advance(4)
    assert job.percent == 99

    job.succeed(_AT)
    assert job.percent == 100


def test_start_is_re_entrant_from_running_and_re_measures_the_total() -> None:
    """At-least-once redelivery (DD-09) may hand the same job to a restarted
    worker, which re-runs the build from the first chunk. The total is
    re-measured on that pass rather than trusted from the last one, because
    the chunk rows may have changed underneath it."""
    job = _job()
    job.start(10)
    job.advance(7)

    job.start(4)

    assert job.status is SummaryJobStatus.RUNNING
    assert (job.total_chunks, job.done_chunks) == (4, 0)


def test_advance_clamps_instead_of_raising() -> None:
    """A redelivered job restarting from zero is a legitimate history;
    refusing it would turn a recoverable crash into a stuck job."""
    job = _job()
    job.start(3)
    job.advance(99)
    assert job.done_chunks == 3
    job.advance(-1)
    assert job.done_chunks == 0


def test_a_terminal_job_refuses_every_further_transition() -> None:
    job = _job()
    job.start(2)
    job.succeed(_AT)

    with pytest.raises(SummaryJobStateError):
        job.start(2)
    with pytest.raises(SummaryJobStateError):
        job.advance(1)
    with pytest.raises(SummaryJobStateError):
        job.fail("nope", _AT)
    # ...but cancelling a finished job is a QUESTION, not a violation: the
    # use-case decides whether that is a race or a mistake.
    assert job.cancel("stopped", _AT) is False


def test_cancelling_twice_keeps_the_original_instant() -> None:
    """The moment someone stopped a job is not re-datable — the
    ``ReindexJob.cancel`` rule."""
    job = _job()
    later = datetime(2026, 8, 11, 10, 0, 0, tzinfo=UTC)

    assert job.cancel("stopped", _AT) is True
    assert job.cancel("stopped", later) is False
    assert job.cancelled_at == _AT


def test_fail_is_reachable_from_queued_without_a_pointless_running_write() -> None:
    """A job can fail before it ever runs — no ``summarize`` route resolves a
    key — and forcing that through a ``running`` write would spend a
    transaction to make a state machine look tidier than the world is."""
    job = _job()
    job.fail("no route", _AT)
    assert (job.status, job.error, job.finished_at) == (SummaryJobStatus.FAILED, "no route", _AT)


# --------------------------------------------------------------------------- #
# SummarizeDocument — the pipeline                                             #
# --------------------------------------------------------------------------- #


async def _draft(
    chunks: list[str],
    *,
    kind: SummaryKind = SummaryKind.FULL,
    llm: RecordingLLM | None = None,
) -> tuple[RecordingLLM, object]:
    provider = llm or RecordingLLM()
    result = await SummarizeDocument().execute(
        _ctx(),
        chunks=chunks,
        kind=kind,
        lang=SummaryLanguage.AR,
        summarizer=ResolvedSummarizer(provider=provider, model="m", api_key="k"),
    )
    return provider, result


@pytest.mark.asyncio
async def test_an_overview_makes_exactly_one_call_and_samples_across_the_whole_document() -> None:
    """P-43 (plan §4 step 19, §3.10): an overview reads a sample spread
    ACROSS the document, not just its first ``_OVERVIEW_CHUNKS`` -- a
    document whose opening is a generic cover sheet or table of contents
    gave a worthless glance under the old "first 8" rule. Still exactly ONE
    provider call, regardless of length: sampling is a position choice, not
    an extra round trip.

    40 chunks / 8 samples span index 0 to index 39 inclusive. Both ENDS are
    pinned: a glance that stops short of the last chunk is not a glance at
    the document, and a conclusion is exactly the kind of thing an overview
    is asked for."""
    llm, draft = await _draft([f"chunk {i}" for i in range(40)], kind=SummaryKind.OVERVIEW)

    assert len(llm.calls) == 1
    assert draft.source_chunks == 8  # type: ignore[attr-defined]
    assert draft.truncated is True  # type: ignore[attr-defined]
    content = llm.calls[0][0][1].content
    # Exact picks, not substring probes: "chunk 3" is inside "chunk 33".
    assert [part for part in content.split("\n\n") if part != "[…]"] == [
        f"chunk {i}" for i in (0, 6, 11, 17, 22, 28, 33, 39)
    ]
    # Non-adjacent picks are separated by the marked gap -- the model is told
    # material was skipped, not left to guess.
    assert "[…]" in content


@pytest.mark.asyncio
async def test_an_overview_of_nine_chunks_is_not_silently_the_first_eight() -> None:
    """The narrowest case, and the one the obvious formula gets wrong.

    ``int(i * len / 8)`` over nine chunks yields ``{0..7}`` -- the exact
    "first 8" P-43 exists to replace, reintroduced silently at the one
    length where a reader would never think to check. Spacing across
    ``len - 1`` samples the ends and drops a middle chunk instead."""
    llm, _ = await _draft([f"chunk {i}" for i in range(9)], kind=SummaryKind.OVERVIEW)

    content = llm.calls[0][0][1].content
    picked = [part for part in content.split("\n\n") if part != "[…]"]
    assert picked == [f"chunk {i}" for i in (0, 1, 2, 3, 5, 6, 7, 8)]
    assert picked != [f"chunk {i}" for i in range(8)]


@pytest.mark.asyncio
async def test_an_overview_of_a_short_document_reads_everything_with_no_gap_marker() -> None:
    """At or under ``_OVERVIEW_CHUNKS`` readable chunks, "sampling" is just
    reading everything -- nothing was skipped, so no ``[…]`` marker belongs
    in the prompt (it would tell the model to imagine a gap that is not
    there)."""
    llm, draft = await _draft([f"chunk {i}" for i in range(5)], kind=SummaryKind.OVERVIEW)

    assert len(llm.calls) == 1
    assert draft.source_chunks == 5  # type: ignore[attr-defined]
    assert draft.truncated is False  # type: ignore[attr-defined]
    content = llm.calls[0][0][1].content
    assert all(f"chunk {i}" in content for i in range(5))
    assert "[…]" not in content


@pytest.mark.asyncio
async def test_a_short_full_summary_skips_the_reduce_round_trip() -> None:
    """One batch means the whole document already fits in one call, and
    map-then-reduce would spend two calls to reach what one says better:
    notes summarised from notes read worse than a summary written from the
    text."""
    llm, draft = await _draft(["a", "b", "c"])

    assert len(llm.calls) == 1
    assert draft.truncated is False  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_long_full_summary_maps_in_batches_then_reduces_once() -> None:
    llm, draft = await _draft([f"chunk {i}" for i in range(13)])

    # 13 chunks at 6 per batch = 3 map calls, plus one reduce.
    assert len(llm.calls) == 4
    assert draft.source_chunks == 13  # type: ignore[attr-defined]
    # The reduce reads the notes, not the document -- and, with only 3 of
    # them (well under `_MAP_BATCH`), `_fold` reaches its `len(grouped) == 1`
    # branch on the FIRST call, going straight to the final reduce exactly
    # like the pre-P-41 unconditional one did.
    assert "## Part 3" in llm.calls[-1][0][1].content


@pytest.mark.asyncio
async def test_a_document_with_many_map_batches_folds_notes_recursively_before_reducing() -> None:
    """P-41 (plan §4 step 17, §3.10): more than one GROUP of map notes
    (i.e. more than ``_MAP_BATCH`` of them) must never all land in the final
    reduce call raw -- that is the exact 40-batch/24k-token failure the step
    exists to fix. 43 chunks map into 8 notes (7 full batches of 6 plus one
    of 1), which is more than `_MAP_BATCH` notes, so `_fold` must group and
    fold them (2 groups of ~6/2) BEFORE the final reduce, rather than handing
    all 8 raw ``## Part N`` notes to one call the way the old unconditional
    single reduce did."""
    llm, draft = await _draft([f"chunk {i}" for i in range(43)])

    # 8 map calls + 2 first-level folds (8 notes -> 2 groups) + 1 final
    # reduce (the 2 folded notes fit in one group) = 11.
    assert len(llm.calls) == 11
    assert draft.source_chunks == 43  # type: ignore[attr-defined]
    assert draft.truncated is False  # type: ignore[attr-defined]

    # An intermediate fold call used `_FOLD_SYSTEM`, not `_REDUCE_SYSTEM`.
    fold_calls = [call for call in llm.calls if "compressing several batches" in call[0][0].content]
    assert len(fold_calls) == 2

    # The FINAL call is the reduce: it reads what the folds produced, not the
    # original per-batch notes -- proof the raw `## Part N` notes never
    # reached one call together.
    final_system, final_user = llm.calls[-1][0]
    assert "final summary" in final_system.content
    assert "## Part" not in final_user.content


@pytest.mark.asyncio
async def test_max_fold_depth_forces_a_flush_instead_of_folding_further(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The termination guarantee is EXPLICIT, not merely a side effect of
    ``_batched`` shrinking the note count: capping ``_MAX_FOLD_DEPTH`` at 1
    forces ``_fold`` to take its single-final-call branch on the very FIRST
    invocation (``depth=0 >= _MAX_FOLD_DEPTH - 1 == 0``), even though the 8
    map notes from a 43-chunk document would otherwise need one more level of
    folding (the previous test). A model that never compressed its notes
    enough to shrink the group count could not defeat this: the cap is
    checked before anything about the notes' content is."""
    monkeypatch.setattr(summarization_module, "_MAX_FOLD_DEPTH", 1)

    llm, draft = await _draft([f"chunk {i}" for i in range(43)])

    # 8 map calls + 1 flushed final reduce over all 8 raw notes -- no
    # intermediate fold call at all, unlike the depth-3 default (11 calls).
    assert len(llm.calls) == 9
    assert draft.source_chunks == 43  # type: ignore[attr-defined]
    assert "## Part 3" in llm.calls[-1][0][1].content


@pytest.mark.asyncio
async def test_a_document_past_the_map_ceiling_is_summarised_as_a_prefix_and_says_so() -> None:
    """A summary of the first 240 chunks of a 300-chunk document is a true
    summary of a part. A reader who is not told which is being misled about
    the whole, so ``truncated`` travels to the stored row and the API."""
    _, draft = await _draft([f"chunk {i}" for i in range(300)])

    assert draft.source_chunks == 240  # type: ignore[attr-defined]
    assert draft.truncated is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_language_instruction_rides_on_the_system_message() -> None:
    """The user message is document text, and an instruction embedded in it
    is an instruction a document could imitate."""
    llm, _ = await _draft(["hello"])

    system, user = llm.calls[0][0]
    assert system.role == "system"
    assert "Arabic" in system.content
    assert "Arabic" not in user.content


@pytest.mark.asyncio
async def test_a_document_with_no_readable_text_is_refused_with_a_readable_reason() -> None:
    """An ``indexed`` document with zero readable chunks is a real outcome
    (an empty PDF, a scan with no OCR text). The message is the whole
    explanation the person who pressed the button will get."""
    with pytest.raises(ValueError, match="no indexed text"):
        await _draft(["", "   "])


@pytest.mark.asyncio
async def test_cancellation_is_observed_between_batches_and_stops_the_build() -> None:
    """The only place it can be observed: a round trip in flight cannot be
    recalled, and pretending otherwise would report a stop that had not
    happened."""
    llm = RecordingLLM()
    calls: list[int] = []

    async def _cancel_after_first() -> bool:
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(SummaryBuildCancelled):
        await SummarizeDocument().execute(
            _ctx(),
            chunks=[f"chunk {i}" for i in range(13)],
            kind=SummaryKind.FULL,
            lang=SummaryLanguage.AUTO,
            summarizer=ResolvedSummarizer(provider=llm, model="m", api_key="k"),
            should_cancel=_cancel_after_first,
        )

    # Exactly one map call was paid for: the check runs BEFORE each batch, so
    # the second batch never starts.
    assert len(llm.calls) == 1


# --------------------------------------------------------------------------- #
# SummarizeDocument.translate — P-44 (plan §4 step 20, §3.10)                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_translate_reuses_the_text_with_no_call_when_the_language_already_matches() -> None:
    """Asking for the language a stored summary is already in must not spend
    a round trip proving what was already true."""
    llm = RecordingLLM()
    source = _summary(text="already in the requested language")

    draft = await SummarizeDocument().translate(
        _ctx(),
        source=source,
        lang=source.lang,
        summarizer=ResolvedSummarizer(provider=llm, model="new-model", api_key="k"),
    )

    assert llm.calls == []
    assert draft.text == source.text
    # The model that actually produced this text is still the ORIGINAL one --
    # nothing new was written, so nothing new gets credited.
    assert draft.model == source.model
    assert draft.source_chunks == source.source_chunks
    assert draft.truncated == source.truncated


@pytest.mark.asyncio
async def test_translate_calls_the_provider_once_when_the_language_differs() -> None:
    llm = RecordingLLM()
    source = _summary(text="النص الأصلي")

    draft = await SummarizeDocument().translate(
        _ctx(),
        source=source,
        lang=SummaryLanguage.EN,
        summarizer=ResolvedSummarizer(provider=llm, model="translator-model", api_key="k"),
    )

    assert len(llm.calls) == 1
    # The summary text itself is what gets translated, not chunks -- the
    # whole reason this is cheap.
    assert llm.calls[0][0][1].content == source.text
    assert draft.text == "SUMMARY-1"
    # A real translation call happened, so the model that produced THIS text
    # is the one that answered it -- not the source's original model.
    assert draft.model == "translator-model"


@pytest.mark.asyncio
async def test_translate_keeps_the_language_instruction_on_the_system_message() -> None:
    """The `_call` mechanism §3.10's ``ما لا يُمسّ`` protects is reused as-is:
    the instruction rides on the system message, never the user one, which
    here is a PREVIOUS summary's own text rather than raw document text."""
    llm = RecordingLLM()
    source = _summary(text="some text")

    await SummarizeDocument().translate(
        _ctx(),
        source=source,
        lang=SummaryLanguage.AR,
        summarizer=ResolvedSummarizer(provider=llm, model="m", api_key="k"),
    )

    system, user = llm.calls[0][0]
    assert system.role == "system"
    assert "Arabic" in system.content
    assert "Arabic" not in user.content


@pytest.mark.asyncio
async def test_translate_carries_truncated_and_source_chunks_from_the_source_unchanged() -> None:
    """A translated summary of a truncated source is still a summary of a
    truncated source -- truncation honesty survives translation."""
    llm = RecordingLLM()
    source = _summary(text="prefix only")
    source = replace(source, truncated=True, source_chunks=240)

    draft = await SummarizeDocument().translate(
        _ctx(),
        source=source,
        lang=SummaryLanguage.EN,
        summarizer=ResolvedSummarizer(provider=llm, model="m", api_key="k"),
    )

    assert draft.truncated is True
    assert draft.source_chunks == 240


# --------------------------------------------------------------------------- #
# SummarizeDocument.refine — P-45 (plan §4 step 21, §3.10, optional)           #
# --------------------------------------------------------------------------- #


async def _refine_draft(
    chunks: list[str], *, llm: RecordingLLM | None = None
) -> tuple[RecordingLLM, object]:
    provider = llm or RecordingLLM()
    result = await SummarizeDocument().refine(
        _ctx(),
        chunks=chunks,
        lang=SummaryLanguage.AR,
        summarizer=ResolvedSummarizer(provider=provider, model="m", api_key="k"),
    )
    return provider, result


@pytest.mark.asyncio
async def test_refine_makes_exactly_one_call_per_batch_with_no_separate_reduce() -> None:
    """13 chunks at 6 per batch is 3 batches. Map-reduce would spend 4 calls
    (3 map + 1 reduce, the existing test above); refine spends exactly 3 --
    one per batch and nothing more, because the last call's own output already
    IS the finished summary."""
    llm, draft = await _refine_draft([f"chunk {i}" for i in range(13)])

    assert len(llm.calls) == 3
    assert draft.source_chunks == 13  # type: ignore[attr-defined]
    assert draft.truncated is False  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refine_feeds_each_calls_own_output_into_the_next_calls_input() -> None:
    """Proof this reads in ORDER with one artefact alive throughout, not in
    isolation like a map batch: the second call's prompt must contain the
    exact text the first call answered with."""
    llm, draft = await _refine_draft([f"chunk {i}" for i in range(13)])

    first_reply = "SUMMARY-1"
    second_system, second_user = llm.calls[1][0]
    assert first_reply in second_user.content
    assert "Summary so far" in second_user.content
    assert "chunk 6" in second_user.content  # the second batch's own text
    assert "refine" in second_system.content.lower() or "running summary" in second_system.content

    # The draft is the LAST call's own reply -- there is no reduce call
    # afterwards to read instead.
    assert draft.text == f"SUMMARY-{len(llm.calls)}"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refine_summarises_a_short_document_in_one_call() -> None:
    llm, draft = await _refine_draft(["a", "b", "c"])

    assert len(llm.calls) == 1
    assert draft.truncated is False  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refine_truncates_at_the_same_ceiling_map_reduce_does_and_says_so() -> None:
    _, draft = await _refine_draft([f"chunk {i}" for i in range(300)])

    assert draft.source_chunks == 240  # type: ignore[attr-defined]
    assert draft.truncated is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refine_keeps_the_language_instruction_on_the_system_message() -> None:
    llm, _ = await _refine_draft([f"chunk {i}" for i in range(13)])

    for messages, _params in llm.calls:
        system, user = messages
        assert system.role == "system"
        assert "Arabic" in system.content
        assert "Arabic" not in user.content


@pytest.mark.asyncio
async def test_refine_is_cancellable_between_batches() -> None:
    llm = RecordingLLM()
    calls: list[int] = []

    async def _cancel_after_first() -> bool:
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(SummaryBuildCancelled):
        await SummarizeDocument().refine(
            _ctx(),
            chunks=[f"chunk {i}" for i in range(13)],
            lang=SummaryLanguage.AUTO,
            summarizer=ResolvedSummarizer(provider=llm, model="m", api_key="k"),
            should_cancel=_cancel_after_first,
        )

    # The check runs BEFORE the first batch too (there is more than one
    # batch here), so the first call is paid for and the second never starts.
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_refine_with_no_readable_text_is_refused_with_a_readable_reason() -> None:
    with pytest.raises(ValueError, match="no indexed text"):
        await _refine_draft(["", "   "])


# --------------------------------------------------------------------------- #
# RequestSummary / GetSummary / DeleteSummary / CancelSummaryJob               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_requesting_a_summary_queues_a_job_and_publishes_its_event() -> None:
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)

    job = await stack.knowledge.request_summary.start(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AR
    )

    assert job.status is SummaryJobStatus.QUEUED
    assert stack.outbox.event_types == ["knowledge.summary.requested.v1"]


@pytest.mark.asyncio
async def test_an_unindexed_document_is_a_conflict_not_an_empty_summary() -> None:
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(
        document_id="doc-1", workspace_id=_W1, status=IndexStatus.PENDING
    )

    with pytest.raises(ConflictError, match="not indexed"):
        await stack.knowledge.request_summary.start(
            _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )
    assert stack.outbox.event_types == []


@pytest.mark.asyncio
async def test_a_second_build_of_the_same_key_is_refused_before_a_token_is_spent() -> None:
    """Two impatient clicks would otherwise pay for the same document twice
    and then race to write one ``uq_summary_key`` row — so the loser pays in
    full and fails at its last statement."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    request = RequestSummary(stack.repository, stack.summary_jobs)

    await request.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
    )
    with pytest.raises(ConflictError, match="already being built"):
        await request.execute(
            _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )

    # A DIFFERENT key is not blocked: the overview and the full summary are
    # separate artefacts, and so are their builds.
    await request.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.OVERVIEW, lang=SummaryLanguage.AUTO
    )


@pytest.mark.asyncio
async def test_reading_a_summary_never_falls_back_to_another_key() -> None:
    """A fallback would answer a question the caller did not ask and label it
    as the answer to the one they did."""
    stack = build_knowledge()
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary()
    get = GetSummary(stack.summaries)

    found = await get.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
    )
    assert found.text == "old text"

    for kind, lang in (
        (SummaryKind.OVERVIEW, SummaryLanguage.AUTO),
        (SummaryKind.FULL, SummaryLanguage.AR),
    ):
        with pytest.raises(NotFoundError):
            await get.execute(_ctx(), document_id="doc-1", kind=kind, lang=lang)


@pytest.mark.asyncio
async def test_another_tenants_summary_is_indistinguishable_from_a_missing_one() -> None:
    stack = build_knowledge()
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary()

    with pytest.raises(NotFoundError):
        await GetSummary(stack.summaries).execute(
            _ctx(_W2), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )


@pytest.mark.asyncio
async def test_deleting_is_idempotent_and_says_which_happened() -> None:
    """The caller asked for a state, and after the first call that state
    holds — so the second call is a success too, and the flag is what lets
    the UI say "deleted" once and "there was nothing saved" after."""
    stack = build_knowledge()
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary()
    delete = DeleteSummary(stack.summaries)

    assert (
        await delete.execute(
            _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )
        is True
    )
    assert (
        await delete.execute(
            _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )
        is False
    )


@pytest.mark.asyncio
async def test_cancelling_stamps_the_row_and_publishes_the_failure_event() -> None:
    """The event is emitted at the cancellation rather than left for the
    worker to emit when it notices: a worker that already died will never
    notice, and the client waiting on the stream would wait forever."""
    stack = build_knowledge()
    stack.summary_jobs.rows["job-1"] = _job(status=SummaryJobStatus.RUNNING, total=4, done=2)

    job = await stack.knowledge.cancel_summary_job.cancel(_ctx(), job_id="job-1")

    assert job.status is SummaryJobStatus.CANCELLED
    assert stack.outbox.event_types == ["knowledge.summary.build_failed.v1"]


@pytest.mark.asyncio
async def test_cancelling_twice_writes_nothing_and_a_finished_job_is_a_conflict() -> None:
    stack = build_knowledge()
    stack.summary_jobs.rows["job-1"] = _job(status=SummaryJobStatus.RUNNING, total=1)
    cancel = CancelSummaryJob(stack.summary_jobs)

    await cancel.execute(_ctx(), job_id="job-1")
    _, events = await cancel.execute(_ctx(), job_id="job-1")
    assert events == ()

    stack.summary_jobs.rows["job-2"] = _job(job_id="job-2", status=SummaryJobStatus.SUCCEEDED)
    with pytest.raises(ConflictError, match="already finished"):
        await cancel.execute(_ctx(), job_id="job-2")


@pytest.mark.asyncio
async def test_an_unknown_job_is_a_404_for_both_reading_and_cancelling() -> None:
    stack = build_knowledge()
    with pytest.raises(NotFoundError):
        await GetSummaryJob(stack.summary_jobs).execute(_ctx(), job_id="nope")
    with pytest.raises(NotFoundError):
        await CancelSummaryJob(stack.summary_jobs).execute(_ctx(), job_id="nope")


# --------------------------------------------------------------------------- #
# BuildSummary — the worker-facing lifecycle                                   #
# --------------------------------------------------------------------------- #


def _builder(stack: object, resolver: StubSummarizerResolver) -> BuildSummary:
    return BuildSummary(
        stack.repository,  # type: ignore[attr-defined]
        stack.summaries,  # type: ignore[attr-defined]
        stack.summary_jobs,  # type: ignore[attr-defined]
        SummarizeDocument(),
        resolver,
    )


@pytest.mark.asyncio
async def test_a_completed_build_stores_the_summary_and_records_the_model() -> None:
    """``model`` is on the record for the reason ``host`` is on a system-stats
    reading: this is the one artefact whose content depends on which model
    produced it."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = ["alpha", "beta"]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM(), model="gpt-test"))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    attempt = await build.run(_ctx(), plan)
    job, events = await build.finalize(_ctx(), attempt)

    assert job.status is SummaryJobStatus.SUCCEEDED
    assert [type(event).__name__ for event in events] == ["SummaryBuilt"]
    stored = stack.summaries.rows[("doc-1", "full", "auto")]
    assert stored.model == "gpt-test"
    assert stored.text.startswith("SUMMARY")


@pytest.mark.asyncio
async def test_a_terminal_job_is_a_silent_no_op_so_a_queued_cancel_costs_nothing() -> None:
    """This is the whole mechanism behind "a job still queued stops for
    free": the worker's claim finds a terminal job and declines, with nothing
    in the worker needing to know cancellation exists."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = ["alpha"]
    llm = RecordingLLM()
    stack.summary_jobs.rows["job-1"] = _job(status=SummaryJobStatus.CANCELLED)

    assert await _builder(stack, StubSummarizerResolver(llm)).claim(_ctx(), job_id="job-1") is None
    assert llm.calls == []


@pytest.mark.asyncio
async def test_an_unresolvable_route_ends_the_job_instead_of_redelivering_forever() -> None:
    """Without the ``fail`` entry point this escapes the handler, is
    redelivered until the DLQ swallows it, and leaves the job ``queued``
    forever — holding ``uq_summary_job_active`` so the user cannot even ask
    again."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.summary_jobs.rows["job-1"] = _job()
    resolver = StubSummarizerResolver(RecordingLLM(), error=ConflictError("no key"))
    build = _builder(stack, resolver)

    with pytest.raises(ConflictError):
        await build.claim(_ctx(), job_id="job-1")

    attempt = await build.fail(_ctx(), job_id="job-1", reason="no key")
    assert attempt is not None
    job, events = await build.finalize(_ctx(), attempt)

    assert (job.status, job.error) == (SummaryJobStatus.FAILED, "no key")
    assert [type(event).__name__ for event in events] == ["SummaryBuildFailed"]


@pytest.mark.asyncio
async def test_an_empty_document_fails_through_the_ordinary_path_with_its_event() -> None:
    """The no-text check lives in the pipeline rather than in ``claim`` so
    the failure takes the one path every other failure takes, with its event
    minted by ``finalize`` inside the terminal transaction. A check in
    ``claim`` would have had to write the failure itself, outside that
    transaction, and the event it minted would have had nowhere to go."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = []
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    job, events = await build.finalize(_ctx(), await build.run(_ctx(), plan))

    assert job.status is SummaryJobStatus.FAILED
    assert job.error is not None and "no indexed text" in job.error
    assert [type(event).__name__ for event in events] == ["SummaryBuildFailed"]


@pytest.mark.asyncio
async def test_a_cancelled_build_cannot_resurrect_itself_through_a_progress_write() -> None:
    """**The** test for cooperative cancellation. A worker holds its job in
    memory for the whole build; the cancellation arrives as a write to the
    ROW by another process. If progress were persisted with a whole-row
    ``save``, the next map step would write that in-memory job — still
    ``running`` — straight over the cancellation, and the job would resurrect
    itself seconds after the user stopped it. ``record_progress`` is guarded
    on ``status = 'running'``, so after a cancellation it matches no row.
    """
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(13)]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    # Someone presses Stop while the first batch is in flight.
    await CancelSummaryJob(stack.summary_jobs).execute(_ctx(), job_id="job-1")

    attempt = await build.run(_ctx(), plan)

    assert attempt.cancelled is True
    # finalize writes NOTHING on the cancelled path — re-dating `cancelled_at`
    # to the moment the worker noticed would replace the moment someone asked.
    _, events = await build.finalize(_ctx(), attempt)
    assert events == ()
    assert stack.summary_jobs.rows["job-1"].status is SummaryJobStatus.CANCELLED
    assert stack.summary_jobs.rows["job-1"].error == SUMMARY_CANCELLED_REASON
    assert ("doc-1", "full", "auto") not in stack.summaries.rows


@pytest.mark.asyncio
async def test_a_cancelled_build_leaves_the_previous_summary_where_it_was() -> None:
    """A rebuild that ends by deleting what it could not replace is worse
    than no rebuild."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(13)]
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary()
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    await CancelSummaryJob(stack.summary_jobs).execute(_ctx(), job_id="job-1")
    await build.finalize(_ctx(), await build.run(_ctx(), plan))

    assert stack.summaries.rows[("doc-1", "full", "auto")].text == "old text"


@pytest.mark.asyncio
async def test_a_rebuild_replaces_the_stored_summary_under_the_same_key() -> None:
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = ["alpha"]
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary()
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    await build.finalize(_ctx(), await build.run(_ctx(), plan))

    assert len(stack.summaries.rows) == 1
    assert stack.summaries.rows[("doc-1", "full", "auto")].text != "old text"


@pytest.mark.asyncio
async def test_progress_is_written_as_the_map_advances() -> None:
    """The job stores its progress because nothing else records it
    (INV-K7) — so it has to actually be written, not merely computable."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(13)]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    assert stack.summary_jobs.rows["job-1"].total_chunks == 13

    await build.run(_ctx(), plan)
    assert stack.summary_jobs.rows["job-1"].done_chunks == 13


# --------------------------------------------------------------------------- #
# BuildSummary — P-44: translating instead of rebuilding (plan §4 step 20)     #
# --------------------------------------------------------------------------- #


def _stored(
    *,
    lang: SummaryLanguage,
    summary_id: str,
    text: str,
    built_at: datetime = _AT,
    source_chunks: int = 3,
    truncated: bool = False,
) -> Summary:
    return Summary(
        id=summary_id,
        workspace_id=_W1,
        document_id="doc-1",
        kind=SummaryKind.FULL,
        lang=lang,
        text=text,
        model="previous-model",
        source_chunks=source_chunks,
        truncated=truncated,
        built_at=built_at,
    )


def _translation_stack(
    *, stored: Sequence[Summary] = (), corpus_chunks: int = 30
) -> tuple[object, RecordingLLM]:
    """A stack whose document has a DELIBERATELY large corpus.

    ``corpus_chunks`` is well past ``_MAP_BATCH``, so a build that reads the
    corpus cannot possibly finish in one provider call — which is what makes
    "exactly one call" a real assertion about the path taken rather than a
    coincidence of a short document.
    """
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(corpus_chunks)]
    for summary in stored:
        stack.summaries.rows[("doc-1", summary.kind.value, summary.lang.value)] = summary
    llm = RecordingLLM()
    return stack, llm


@pytest.mark.asyncio
async def test_a_build_translates_a_stored_summary_instead_of_reading_the_corpus() -> None:
    """P-44's whole point (plan §4 step 20, §3.10): a language nothing is
    stored under is answered by ONE round trip over a few kilobytes of
    already-reduced Markdown, not by a second map-reduce over the corpus.

    Before this wiring ``SummarizeDocument.translate`` had no caller anywhere
    in ``src`` — the method existed and no request could ever reach it.
    """
    stack, llm = _translation_stack(
        stored=[_stored(lang=SummaryLanguage.EN, summary_id="sum-en", text="English body")]
    )
    stack.summary_jobs.rows["job-1"] = replace(_job(), lang=SummaryLanguage.AR)
    build = _builder(stack, StubSummarizerResolver(llm))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    assert plan.translate_from is not None and plan.translate_from.id == "sum-en"
    # The corpus is not even read into the plan, which is the saving itself.
    assert plan.chunks == ()

    job, events = await build.finalize(_ctx(), await build.run(_ctx(), plan))

    assert job.status is SummaryJobStatus.SUCCEEDED
    assert [type(event).__name__ for event in events] == ["SummaryBuilt"]
    assert len(llm.calls) == 1
    # The one call carries the STORED text, never a corpus chunk.
    assert llm.calls[0][0][-1].content == "English body"
    assert stack.summaries.rows[("doc-1", "full", "ar")].text.startswith("SUMMARY")


@pytest.mark.asyncio
async def test_a_translation_carries_the_sources_truth_about_what_it_covers() -> None:
    """A translated summary of a truncated source is still a summary of a
    truncated source. ``source_chunks``/``truncated`` come from the row being
    translated because a translation reads no chunk of its own — recomputing
    either would be inventing a coverage claim out of nothing."""
    stack, llm = _translation_stack(
        stored=[
            _stored(
                lang=SummaryLanguage.EN,
                summary_id="sum-en",
                text="English body",
                source_chunks=240,
                truncated=True,
            )
        ]
    )
    stack.summary_jobs.rows["job-1"] = replace(_job(), lang=SummaryLanguage.AR)
    build = _builder(stack, StubSummarizerResolver(llm))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    # The job's total is the SOURCE's coverage, not the document's 30 chunks:
    # promising a map over 30 chunks would describe work this build is not
    # doing, in either direction.
    assert stack.summary_jobs.rows["job-1"].total_chunks == 240

    await build.finalize(_ctx(), await build.run(_ctx(), plan))

    stored = stack.summaries.rows[("doc-1", "full", "ar")]
    assert (stored.source_chunks, stored.truncated) == (240, True)


@pytest.mark.asyncio
async def test_a_rebuild_of_an_occupied_key_maps_the_document_instead_of_translating() -> None:
    """The half of the rule that keeps ``POST`` honest. ``RequestSummary``
    has no ``force``: reading what is stored is ``GET``, so a POST at a key
    that already holds a summary IS a rebuild. Translating a neighbouring
    language there would make the requested language permanently
    unrebuildable for as long as any other one exists."""
    stack, llm = _translation_stack(
        stored=[
            _stored(lang=SummaryLanguage.AR, summary_id="sum-ar", text="نصّ قديم"),
            _stored(lang=SummaryLanguage.EN, summary_id="sum-en", text="English body"),
        ]
    )
    stack.summary_jobs.rows["job-1"] = replace(_job(), lang=SummaryLanguage.AR)
    build = _builder(stack, StubSummarizerResolver(llm))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    assert plan.translate_from is None
    assert len(plan.chunks) == 30

    await build.finalize(_ctx(), await build.run(_ctx(), plan))

    # A real map-reduce: several map calls plus a reduce, none of them the
    # single translate round trip.
    assert len(llm.calls) > 1
    assert stack.summaries.rows[("doc-1", "full", "ar")].text != "نصّ قديم"


@pytest.mark.asyncio
async def test_a_first_build_in_any_language_still_reads_the_corpus() -> None:
    """The regression guard on the ordinary path: with nothing stored in ANY
    language there is nothing to translate, and the build must be exactly
    what it was before P-44 was wired up."""
    stack, llm = _translation_stack()
    stack.summary_jobs.rows["job-1"] = replace(_job(), lang=SummaryLanguage.AR)
    build = _builder(stack, StubSummarizerResolver(llm))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    assert plan.translate_from is None
    assert len(plan.chunks) == 30
    assert stack.summary_jobs.rows["job-1"].total_chunks == 30


@pytest.mark.asyncio
async def test_the_translation_source_is_the_most_recently_built_of_the_others() -> None:
    """Deterministic, and deterministic in a way that means something: with
    two other languages stored, the newest is the one whose wording the
    workspace most recently accepted. A source picked by row order would let
    the same job produce a different summary on a redelivery."""
    stack, llm = _translation_stack(
        stored=[
            _stored(
                lang=SummaryLanguage.AUTO,
                summary_id="sum-auto",
                text="older body",
                built_at=datetime(2026, 8, 1, tzinfo=UTC),
            ),
            _stored(
                lang=SummaryLanguage.EN,
                summary_id="sum-en",
                text="newer body",
                built_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ]
    )
    stack.summary_jobs.rows["job-1"] = replace(_job(), lang=SummaryLanguage.AR)
    build = _builder(stack, StubSummarizerResolver(llm))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    assert plan.translate_from is not None and plan.translate_from.id == "sum-en"

    await build.run(_ctx(), plan)
    assert llm.calls[0][0][-1].content == "newer body"


@pytest.mark.asyncio
async def test_a_failing_translation_lands_the_job_in_failed_like_any_other_build() -> None:
    """A translation is not a privileged path: it runs inside the same broad
    catch, so a provider that refuses leaves the job ``failed`` with its
    reason rather than crashing the handler into an endless redelivery."""

    @dataclass
    class BrokenLLM(RecordingLLM):
        async def complete(
            self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
        ) -> LlmResult:
            raise RuntimeError("provider is down")

    stack, _ = _translation_stack(
        stored=[_stored(lang=SummaryLanguage.EN, summary_id="sum-en", text="English body")]
    )
    stack.summary_jobs.rows["job-1"] = replace(_job(), lang=SummaryLanguage.AR)
    build = _builder(stack, StubSummarizerResolver(BrokenLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    job, events = await build.finalize(_ctx(), await build.run(_ctx(), plan))

    assert (job.status, job.error) == (SummaryJobStatus.FAILED, "provider is down")
    assert [type(event).__name__ for event in events] == ["SummaryBuildFailed"]
    # And the source it could not translate is still exactly where it was.
    assert stack.summaries.rows[("doc-1", "full", "en")].text == "English body"
