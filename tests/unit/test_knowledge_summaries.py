"""Unit tests for document summaries (BE-RAG-009/010/011): the two new
aggregates, the map-reduce pipeline, and the use-cases over the shared
in-memory stack (``support_knowledge``).

What these pin, against 06 §7 INV-K6/INV-K7:

* a summary is keyed on ``(document_id, kind, lang)`` and there is **no
  fallback between keys** — asking for the Arabic overview never yields the
  English full text. The single exception is `F-6`: a read for ``ar``/``en``
  is answered by the ``auto`` row when that row's text really IS the language
  asked for, which is a narrowing of what counts as the key rather than a
  fallback to another one;
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

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError
from app.framework.ports.llm_provider import LlmMessage, LlmParams, LlmResult
from app.modules.knowledge.application import summarization as summarization_module
from app.modules.knowledge.application.routing import (
    SummaryBuildInProgress,
    SummaryTargetNotIndexed,
)
from app.modules.knowledge.application.summarization import (
    SummarizeDocument,
    SummaryBuildCancelled,
    _batched,
)
from app.modules.knowledge.application.use_cases import (
    SUMMARY_CANCELLED_REASON,
    SUMMARY_TRUNCATED_NOTICE_AR,
    SUMMARY_TRUNCATED_NOTICE_EN,
    BuildSummary,
    CancelSummaryJob,
    DeleteSummary,
    GetSummary,
    GetSummaryJob,
    ReadStoredSummary,
    RequestSummary,
    delivered_summary_text,
)
from app.modules.knowledge.domain.entities import Summary, SummaryJob
from app.modules.knowledge.domain.errors import SummaryJobStateError
from app.modules.knowledge.domain.value_objects import (
    IndexStatus,
    SummaryBlocked,
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
)
from app.modules.knowledge.ports.summarization import ResolvedSummarizer
from tests.unit.support_knowledge import (
    InMemorySummaryRepository,
    KnowledgeStack,
    build_knowledge,
    seed_document,
)

_W1 = "ws1"
_W2 = "ws2"
_AT = datetime(2026, 8, 11, 9, 0, 0, tzinfo=UTC)

# A body whose majority script is Arabic -- what an `auto` build over an
# Arabic document produces, and the only thing that makes it readable as `ar`.
_ARABIC_TEXT = "هذا المستند يشرح سياسة الاسترجاع في المنصّة بالتفصيل."


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

    # `F-9`/`F-10`: the two fields the guards in `_call` actually read.
    #
    # ``replies`` scripts the content per call — the Nth call gets the Nth
    # entry and the last entry repeats — where ``reply`` alone serves every
    # other test in this file. It exists because an empty answer at ONE step
    # of a many-step build is a different case from an empty answer
    # everywhere, and only the scripted form can tell them apart.
    #
    # ``finish_reason`` is what says an answer stopped because it ran out of
    # room rather than because it was finished. It is a field and not a
    # constant for exactly one reason: nothing else in the pipeline reads it,
    # so nothing else could ever have caught it being ignored.
    replies: Sequence[str] | None = None
    finish_reason: str = "stop"

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        self.calls.append((list(messages), params))
        return LlmResult(
            content=(
                f"{self.reply}-{len(self.calls)}"
                if self.replies is None
                else self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
            ),
            finish_reason=self.finish_reason,
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


def _summary(
    *,
    document_id: str = "doc-1",
    text: str = "old text",
    lang: SummaryLanguage = SummaryLanguage.AUTO,
    truncated: bool = False,
) -> Summary:
    return Summary(
        id="sum-1",
        workspace_id=_W1,
        document_id=document_id,
        kind=SummaryKind.FULL,
        lang=lang,
        text=text,
        model="previous-model",
        source_chunks=3,
        truncated=truncated,
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


# The batching arithmetic these tests are written against, READ from the
# module rather than copied into it. `F-2` (rag-summarization-fix-plan.md
# §3.3) moved `_MAP_BATCH` from 6 to 20, and every hard-coded "13 chunks is
# 3 batches" in this file stopped meaning what it said the moment it did --
# silently, because 13 chunks is still a perfectly good document, just one
# that now takes a different path through `execute`.
_BATCH = summarization_module._MAP_BATCH

# One map call per batch of a document read all the way to `_MAX_MAP_CHUNKS`.
_MAP_CALLS_AT_THE_CEILING = -(-summarization_module._MAX_MAP_CHUNKS // _BATCH)

# A note long enough that `_MAX_REDUCE_CHARS` -- not the count bound -- is
# what splits a fold group. Since `F-2` the COUNT bound cannot split notes at
# all: a document at the ceiling produces `_MAP_CALLS_AT_THE_CEILING` (12) of
# them, always under `_MAP_BATCH` (20). Real notes run to `_MAP_MAX_TOKENS`
# (600), ~2,400 characters; this is that order of magnitude, sized so exactly
# five fit one group and a sixth does not.
_NOTE_CHARS = summarization_module._MAX_REDUCE_CHARS // 5 - 100
_NOTES_PER_FOLD = 5


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
    chunks = _BATCH * 3 + 1

    llm, draft = await _draft([f"chunk {i}" for i in range(chunks)])

    # Four map batches (three full, one of a single chunk), plus one reduce.
    assert len(llm.calls) == 5
    assert draft.source_chunks == chunks  # type: ignore[attr-defined]
    # The reduce reads the notes, not the document -- and, with only four
    # short ones, `_fold` reaches its `len(grouped) == 1` branch on the FIRST
    # call, going straight to the final reduce exactly like the pre-P-41
    # unconditional one did.
    assert "## Part 4" in llm.calls[-1][0][1].content


@pytest.mark.asyncio
async def test_a_document_with_many_map_batches_folds_notes_recursively_before_reducing() -> None:
    """P-41 (plan §4 step 17, §3.10): more than one GROUP of map notes must
    never all land in the final reduce call raw -- that is the exact
    many-batch/over-the-window failure the step exists to fix.

    **What splits a group changed at `F-2`, and this test changed with it.**
    It used to be the COUNT bound: 43 chunks at 6 a batch made 8 notes, more
    than `_MAP_BATCH`. That can no longer happen -- a document read to
    `_MAX_MAP_CHUNKS` yields 12 notes at 20 chunks a batch, always under
    `_MAP_BATCH` -- so the split is driven by `_MAX_REDUCE_CHARS` instead,
    which is what drives it in production too: real notes are 600 tokens of
    prose, and twelve of them do not fit one 16,000-character reduce.

    A document at the ceiling with notes that size therefore folds in three
    groups of five/five/two BEFORE the final reduce, rather than handing all
    twelve raw ``## Part N`` notes to one call the way the old unconditional
    single reduce did."""
    chunks = summarization_module._MAX_MAP_CHUNKS
    llm, draft = await _draft(
        [f"chunk {i}" for i in range(chunks)], llm=RecordingLLM(reply="x" * _NOTE_CHARS)
    )

    folds = -(-_MAP_CALLS_AT_THE_CEILING // _NOTES_PER_FOLD)
    # 12 map calls + 3 first-level folds + 1 final reduce (the 3 folded notes
    # fit one group) = 16.
    assert folds == 3
    assert len(llm.calls) == _MAP_CALLS_AT_THE_CEILING + folds + 1
    assert draft.source_chunks == chunks  # type: ignore[attr-defined]
    assert draft.truncated is False  # type: ignore[attr-defined]

    # An intermediate fold call used `_FOLD_SYSTEM`, not `_REDUCE_SYSTEM`.
    fold_calls = [call for call in llm.calls if "compressing several batches" in call[0][0].content]
    assert len(fold_calls) == 3

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
    map notes from a ceiling-length document would otherwise need one more
    level of folding (the previous test). A model that never compressed its
    notes enough to shrink the group count could not defeat this: the cap is
    checked before anything about the notes' content is."""
    monkeypatch.setattr(summarization_module, "_MAX_FOLD_DEPTH", 1)

    chunks = summarization_module._MAX_MAP_CHUNKS
    llm, draft = await _draft(
        [f"chunk {i}" for i in range(chunks)], llm=RecordingLLM(reply="x" * _NOTE_CHARS)
    )

    # 12 map calls + 1 flushed final reduce over all 12 raw notes -- no
    # intermediate fold call at all, unlike the depth-3 default (16 calls).
    assert len(llm.calls) == _MAP_CALLS_AT_THE_CEILING + 1
    assert draft.source_chunks == chunks  # type: ignore[attr-defined]
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
            chunks=[f"chunk {i}" for i in range(_BATCH * 2 + 1)],
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
# _fold — `F-3` (plan §4 step 3, §3.4): the fold phase reports and listens    #
# --------------------------------------------------------------------------- #


async def _ceiling_build(
    *,
    llm: RecordingLLM,
    on_progress: object = None,
    should_cancel: object = None,
) -> object:
    """A ``full`` build of a document at ``_MAX_MAP_CHUNKS`` whose notes are
    long enough to make the fold ladder real: 12 map calls, 3 folds, 1 reduce.

    Both hooks are passed through as given so a test can watch the ORDER of
    what happens, which is the whole of what `F-3` changed."""
    hooks: dict[str, object] = {}
    if on_progress is not None:
        hooks["on_progress"] = on_progress
    if should_cancel is not None:
        hooks["should_cancel"] = should_cancel
    return await SummarizeDocument().execute(
        _ctx(),
        chunks=[f"chunk {i}" for i in range(summarization_module._MAX_MAP_CHUNKS)],
        kind=SummaryKind.FULL,
        lang=SummaryLanguage.AUTO,
        summarizer=ResolvedSummarizer(provider=llm, model="m", api_key="k"),
        **hooks,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_the_fold_phase_reports_progress_before_every_call_it_makes() -> None:
    """`F-3`: until now the fold reported nothing at all, so the last calls of
    a long build were a stretch of total silence -- the interface frozen at
    99% and, for the health checker `F-4` adds, indistinguishable from a
    wedged worker.

    The ORDER is the assertion, and reading the call count from inside the
    progress hook is what makes it visible. A map tick fires AFTER its call
    (it reports work finished, and the count it carries is real), so the nth
    map tick sees n calls. A fold tick fires BEFORE its call, so it sees one
    fewer -- which is the point: what follows a fold tick is the long wait it
    exists to announce, not a wait already over.
    """
    llm = RecordingLLM(reply="x" * _NOTE_CHARS)
    seen_calls: list[int] = []
    seen_done: list[int] = []

    async def _progress(done: int) -> None:
        seen_calls.append(len(llm.calls))
        seen_done.append(done)

    await _ceiling_build(llm=llm, on_progress=_progress)

    maps = _MAP_CALLS_AT_THE_CEILING
    folds = -(-maps // _NOTES_PER_FOLD)
    assert len(llm.calls) == maps + folds + 1

    # 1..12 from the map loop (after each call), then 12, 13, 14, 15 from the
    # fold ladder (before each of its three folds and its final reduce).
    assert seen_calls == list(range(1, maps + 1)) + [maps + i for i in range(folds + 1)]

    # And the number a fold tick carries is the total, every time: `advance`
    # clamps at `total_chunks`, which the map loop already reached. The reduce
    # is reported as a PHASE, not as a counter with nothing left to count.
    ceiling = summarization_module._MAX_MAP_CHUNKS
    assert seen_done[:maps] == [_BATCH * (i + 1) for i in range(maps)]
    assert seen_done[maps:] == [ceiling] * (folds + 1)


@pytest.mark.asyncio
async def test_stop_pressed_when_the_map_ends_is_observed_before_the_first_fold() -> None:
    """The button worked during the map and died at the fold boundary -- the
    exact window in which a long build spends its last minutes. The poll sits
    BEFORE the call, so the thirteenth round trip is never paid for."""
    llm = RecordingLLM(reply="x" * _NOTE_CHARS)

    async def _cancel_once_the_map_is_done() -> bool:
        return len(llm.calls) >= _MAP_CALLS_AT_THE_CEILING

    with pytest.raises(SummaryBuildCancelled):
        await _ceiling_build(llm=llm, should_cancel=_cancel_once_the_map_is_done)

    assert len(llm.calls) == _MAP_CALLS_AT_THE_CEILING


@pytest.mark.asyncio
async def test_stop_pressed_between_two_fold_groups_is_observed_there_too() -> None:
    """Not just at the fold's entrance: the poll is INSIDE the loop over the
    groups, which is what the comprehension this replaced had no room for. A
    build cancelled after its first fold pays for that one and no more."""
    llm = RecordingLLM(reply="x" * _NOTE_CHARS)

    async def _cancel_after_the_first_fold() -> bool:
        return len(llm.calls) > _MAP_CALLS_AT_THE_CEILING

    with pytest.raises(SummaryBuildCancelled):
        await _ceiling_build(llm=llm, should_cancel=_cancel_after_the_first_fold)

    assert len(llm.calls) == _MAP_CALLS_AT_THE_CEILING + 1


# --------------------------------------------------------------------------- #
# _batched — `F-2` (plan §4 step 2, §3.3): two live bounds, two budgets       #
# --------------------------------------------------------------------------- #


def test_batched_closes_a_batch_on_whichever_bound_is_reached_first() -> None:
    """BOTH bounds are live since `F-2`. Before it, `_MAP_BATCH = 6` closed
    every batch at ~6,600 characters, so `_MAX_BATCH_CHARS` (24,000) could not
    be reached at any chunk size the chunker emits: the character guard read
    like a guard and was unreachable code."""
    tiny = [f"chunk {i}" for i in range(_BATCH * 2)]
    assert [len(batch) for batch in _batched(tiny)] == [_BATCH, _BATCH]

    # Chunks large enough that the CHARACTER bound closes the batch first,
    # well before `_MAP_BATCH` chunks have accumulated.
    big = ["x" * (summarization_module._MAX_BATCH_CHARS // 3)] * 7
    assert [len(batch) for batch in _batched(big)] == [3, 3, 1]


def test_batched_gives_a_reduce_sized_call_the_smaller_budget() -> None:
    """The SAME chunks, grouped for two kinds of call. A map call answers in
    `_MAP_MAX_TOKENS` (600) and can afford 24,000 characters of input; a
    fold/reduce call answers in `_REDUCE_MAX_TOKENS` (2,500) and
    cannot -- 6,000 input tokens plus 2,500 output is 8,500, past an 8k
    window. That difference is the whole reason `max_chars` is a parameter
    and not a constant read inside the function."""
    chunks = ["x" * 4_000] * 6  # exactly `_MAX_BATCH_CHARS` all together

    assert len(_batched(chunks)) == 1
    assert len(_batched(chunks, max_chars=summarization_module._MAX_REDUCE_CHARS)) == 2


def test_batched_lets_a_single_oversized_chunk_through_as_its_own_batch() -> None:
    """Refusing to summarise a document because ONE of its chunks is larger
    than the whole budget would be a worse failure than the provider's own
    context error -- `_batched`'s own reasoning, unchanged by `F-2` giving it
    a second budget to be asked about."""
    huge = "x" * (summarization_module._MAX_BATCH_CHARS * 2)

    assert _batched(["small", huge, "small"]) == [["small"], [huge], ["small"]]


@pytest.mark.asyncio
async def test_a_document_that_fits_one_map_batch_but_not_one_reduce_call_still_maps() -> None:
    """`F-2`, §6 risk 2 -- the defect raising `_MAP_BATCH` would have
    introduced if the second budget had not landed in the same step.

    ``execute``'s one-call shortcut ANSWERS in ``_REDUCE_MAX_TOKENS``, so it
    has to clear the REDUCE budget, not the map one it is grouped by. This
    document is exactly one map batch -- 24,000 characters, ~6,000 tokens --
    and answering it at 2,500 tokens would ask an 8k-window model for 8,500.
    So the shortcut must decline it and pay for the map path instead: one map
    call (safe, because it answers in 600) and one reduce over its single
    note.

    At `_MAP_BATCH = 6` the two budgets could never disagree, because six
    chunks never reached either. This is the first document for which they
    can, which is why the check exists at all."""
    llm, draft = await _draft(["x" * 4_000] * 6)

    assert len(llm.calls) == 2
    map_system, map_user = llm.calls[0][0]
    assert "Part 1 of 1" in map_user.content
    assert "final summary" not in map_system.content
    assert "final summary" in llm.calls[-1][0][0].content
    assert draft.truncated is False  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# _call — the two guards every provider round trip passes (`F-9`, `F-10`)      #
# --------------------------------------------------------------------------- #


async def _run_build(
    *,
    llm: RecordingLLM,
    chunks: int = _BATCH * 2 + 1,
    stored: Sequence[Summary] = (),
    kind: SummaryKind = SummaryKind.FULL,
    lang: SummaryLanguage = SummaryLanguage.AUTO,
) -> tuple[KnowledgeStack, SummaryJob]:
    """One whole build, claim through finalize, over a corpus big enough to
    need several calls.

    The LIFECYCLE and not the pipeline, because what `F-9` is about is the
    row: a pipeline-level ``pytest.raises`` proves an exception was raised
    and says nothing about whether the job ended ``failed``, whether the
    sentence reached ``job.error``, or — the whole point — whether a blank
    summary row was written anyway.
    """
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(chunks)]
    for summary in stored:
        stack.summaries.rows[("doc-1", summary.kind.value, summary.lang.value)] = summary
    stack.summary_jobs.rows["job-1"] = replace(_job(), kind=kind, lang=lang)

    build = _builder(stack, StubSummarizerResolver(llm))
    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    job, _events = await build.finalize(_ctx(), await build.run(_ctx(), plan))
    return stack, job


@pytest.mark.asyncio
async def test_an_empty_completion_fails_the_build_instead_of_storing_a_blank_summary() -> None:
    """`F-9`: an empty model reply used to reach ``Summary(text="")`` and
    ``job.succeed()`` — a job at 100% holding ``uq_summary_key`` with nothing
    in it. The row is the reason this is worse than a failure: the next
    request reads the empty row back out of ``GET``, and a chat delivery
    hands it over in the same turn for zero calls, so the defect outlives the
    call that caused it. ``finalize``'s own "produced no text" branch could
    never fire here — its condition is ``draft is None``, and this draft
    exists with an empty ``text``."""
    stack, job = await _run_build(llm=RecordingLLM(replies=[""]))

    assert job.status is SummaryJobStatus.FAILED
    assert job.error is not None and "empty response" in job.error
    assert stack.summaries.rows == {}


@pytest.mark.asyncio
async def test_a_whitespace_only_completion_is_treated_as_empty() -> None:
    """``strip()`` runs BEFORE the check, not after: a reply of two newlines
    is a blank summary by every measure that matters to a reader."""
    _stack, job = await _run_build(llm=RecordingLLM(replies=["  \n\t "]))

    assert job.status is SummaryJobStatus.FAILED
    assert job.error is not None and "empty response" in job.error


@pytest.mark.asyncio
async def test_the_empty_completion_reason_names_the_step_it_failed_at() -> None:
    """The sentence is written for the person who will read it on the failed
    job, and "the model returned nothing" without saying WHERE leaves them
    with an unactionable fact. ``job.error`` — the field ``SummaryJobOut``
    publishes — is where it has to be legible, not a log line."""
    # First call empty: a multi-batch document's first call is a map call.
    _stack, mapped = await _run_build(llm=RecordingLLM(replies=[""]))

    # Three map calls answered, the fourth (the reduce) empty. `replies`
    # repeats its last entry, and there is no fifth call to repeat it into.
    _stack, reduced = await _run_build(
        llm=RecordingLLM(replies=["note", "note", "note", ""]),
    )

    _stack, translated = await _run_build(
        llm=RecordingLLM(replies=[""]),
        stored=[_summary(lang=SummaryLanguage.EN, text="English body")],
        lang=SummaryLanguage.AR,
    )

    assert mapped.error is not None and "at the map step" in mapped.error
    assert reduced.error is not None and "at the reduce step" in reduced.error
    assert translated.error is not None and "at the translate step" in translated.error


@pytest.mark.asyncio
async def test_an_empty_map_note_fails_the_build_rather_than_being_skipped() -> None:
    """Pinned so it is not undone in good faith later: skipping the empty
    note and folding the rest would produce a summary of a document with a
    SILENT HOLE in it — one batch nothing was read about, and no field on the
    row for the reader to learn that from. A build that failed for a stated
    reason is the honest outcome; the price is a rebuild, and that price is
    named (`F-8`)."""
    llm = RecordingLLM(replies=["note", "", "note", "reduced"])
    _stack, job = await _run_build(llm=llm)

    assert job.status is SummaryJobStatus.FAILED
    # It stopped AT the empty note. The third map call and the reduce that
    # would have papered over the hole were never paid for.
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_an_empty_translation_fails_rather_than_storing_an_empty_row() -> None:
    """The translate path reaches ``_call`` too, so one guard covers it. It
    matters here more than anywhere: a translation writes a SECOND row under
    a key of its own, so an unguarded empty answer would leave the document
    with a good English summary and a blank Arabic one, both ``succeeded``."""
    stack, job = await _run_build(
        llm=RecordingLLM(replies=[""]),
        stored=[_summary(lang=SummaryLanguage.EN, text="English body")],
        lang=SummaryLanguage.AR,
    )

    assert job.status is SummaryJobStatus.FAILED
    assert ("doc-1", "full", "ar") not in stack.summaries.rows
    # The source it was translating from is untouched, as any failed build
    # leaves any previous summary.
    assert stack.summaries.rows[("doc-1", "full", "en")].text == "English body"


@pytest.mark.asyncio
async def test_a_normal_completion_is_returned_unchanged() -> None:
    """The containing guard: neither of the two checks touches the happy
    path. A build whose model answers normally still succeeds, still stores
    its text, and still spends exactly the calls the ladder asks for."""
    llm = RecordingLLM()
    stack, job = await _run_build(llm=llm)

    assert job.status is SummaryJobStatus.SUCCEEDED
    assert stack.summaries.rows[("doc-1", "full", "auto")].text.startswith("SUMMARY")
    assert len(llm.calls) == 4  # three map batches and one reduce


@pytest.mark.asyncio
async def test_a_length_finish_reason_is_logged_with_the_step_that_hit_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`F-10`: ``finish_reason`` was received on every result and read by
    nothing, so an answer that stopped because it ran out of room was stored
    as though it had finished. Measurement only in this wave — which is why
    the build still SUCCEEDS here: the second flag on the row costs six
    layers, and nothing yet says how often this happens or at which of the
    three ceilings."""
    with caplog.at_level(logging.WARNING):
        _stack, job = await _run_build(llm=RecordingLLM(finish_reason="length"))

    truncations = [
        record for record in caplog.records if record.message == "summarization.output_truncated"
    ]
    assert job.status is SummaryJobStatus.SUCCEEDED
    assert len(truncations) == 4
    assert {record.step for record in truncations} == {"map", "reduce"}  # type: ignore[attr-defined]
    # Both ceilings appear, which is the pair the log exists to separate: the
    # map calls answer in 600 and the reduce in 2,500.
    assert {record.max_tokens for record in truncations} == {  # type: ignore[attr-defined]
        summarization_module._MAP_MAX_TOKENS,
        summarization_module._REDUCE_MAX_TOKENS,
    }


@pytest.mark.asyncio
async def test_a_stop_finish_reason_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The containing guard. A warning on every ordinary call would make the
    signal worthless the day someone looked for it."""
    with caplog.at_level(logging.WARNING):
        await _run_build(llm=RecordingLLM())

    assert [
        record for record in caplog.records if record.message == "summarization.output_truncated"
    ] == []


@pytest.mark.asyncio
async def test_the_truncation_log_carries_no_document_or_summary_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """10 §10: counts, durations and path names from a closed vocabulary —
    never a character of the document, the prompt or the reply. The obvious
    way to say "which call was this?" is the system prompt, and that is
    content."""
    secret = "CONFIDENTIAL SALARY TABLE"
    with caplog.at_level(logging.WARNING):
        await _run_build(
            llm=RecordingLLM(reply=secret, finish_reason="length"),
            chunks=1,
        )

    truncations = [
        record for record in caplog.records if record.message == "summarization.output_truncated"
    ]
    assert truncations
    for record in truncations:
        rendered = str(record.__dict__)
        assert secret not in rendered
        assert "chunk 0" not in rendered
        assert record.step in summarization_module._STEPS  # type: ignore[attr-defined]
        assert isinstance(record.max_tokens, int)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_every_call_site_passes_its_own_step_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The drift test. Every branch of the ladder is driven once and the
    ``step`` names it logged are collected: the union must be exactly
    ``_STEPS``, which fails in BOTH directions — a seventh call site added
    without a name of its own, and a name left in ``_STEPS`` after the call
    site that used it was deleted (`refine` was one, and this is what would
    have caught it)."""
    with caplog.at_level(logging.WARNING):
        # An overview: one call over a sample.
        await _run_build(llm=RecordingLLM(finish_reason="length"), kind=SummaryKind.OVERVIEW)
        # A document short enough to be answered whole, skipping map/reduce.
        await _run_build(llm=RecordingLLM(finish_reason="length"), chunks=1)
        # Long enough that the notes cannot be reduced in one call, so the
        # fold rung above the reduce is reached as well.
        await _ceiling_build(llm=RecordingLLM(reply="x" * _NOTE_CHARS, finish_reason="length"))
        # A translation off a stored row.
        await _run_build(
            llm=RecordingLLM(finish_reason="length"),
            stored=[_summary(lang=SummaryLanguage.EN, text="English body")],
            lang=SummaryLanguage.AR,
        )

    logged = {
        record.step  # type: ignore[attr-defined]
        for record in caplog.records
        if record.message == "summarization.output_truncated"
    }
    assert logged == summarization_module._STEPS


@pytest.mark.asyncio
async def test_the_published_truncated_flag_still_means_the_input_was_cut() -> None:
    """The containing guard for the meaning `F-10` must NOT borrow.
    ``truncated`` says the INPUT was cut at ``_MAX_MAP_CHUNKS`` — it is
    published in ``SummaryOut`` and spoken aloud by
    ``delivered_summary_text`` with that meaning. An output that ran out of
    room is a different fact and does not move this flag."""
    llm = RecordingLLM(finish_reason="length")
    _, short = await _draft([f"chunk {i}" for i in range(_BATCH * 2 + 1)], llm=llm)
    _, long_ = await _draft(
        [f"chunk {i}" for i in range(summarization_module._MAX_MAP_CHUNKS + 1)],
        llm=RecordingLLM(finish_reason="length"),
    )

    # Every call in both builds ran out of room; only the one whose INPUT was
    # cut says `truncated`.
    assert short.truncated is False  # type: ignore[attr-defined]
    assert long_.truncated is True  # type: ignore[attr-defined]


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
async def test_the_unindexed_refusal_says_which_refusal_it_is() -> None:
    """ب-4ب (خطة السيناريوهات §5، ف-7) — the refusal above, typed.

    The test one line up asserts it is a conflict, and that stayed true; this
    one asserts WHICH conflict, because that is the thing no caller could
    work out for itself. Both refusals share `ConflictError` and both carry
    `common.conflict`, so a caller catching the exception knew only that
    something conflicted -- and then had one sentence to write for two
    opposite facts.

    Read from the raiser rather than re-derived: a caller that asked "is this
    document indexed?" for itself would be reading the same state a second
    time, and getting a different answer whenever indexing finished in
    between.
    """
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(
        document_id="doc-1", workspace_id=_W1, status=IndexStatus.PENDING
    )

    with pytest.raises(SummaryTargetNotIndexed) as raised:
        await stack.knowledge.request_summary.start(
            _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )

    assert raised.value.reason is SummaryBlocked.NOT_INDEXED
    # And nothing about the wire changed (ق-6).
    assert isinstance(raised.value, ConflictError)
    assert raised.value.code == "common.conflict"


@pytest.mark.asyncio
async def test_the_already_building_refusal_says_which_refusal_it_is() -> None:
    """The other half, and the one whose sentence is a PROMISE: this summary
    is coming, on the conversation the first request named. Saying that about
    an unindexed document is the lie ب-4أ's neutral wording existed to
    avoid."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    request = RequestSummary(stack.repository, stack.summary_jobs)

    await request.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
    )
    with pytest.raises(SummaryBuildInProgress) as raised:
        await request.execute(
            _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )

    assert raised.value.reason is SummaryBlocked.IN_PROGRESS
    assert isinstance(raised.value, ConflictError)
    assert raised.value.code == "common.conflict"


@pytest.mark.asyncio
async def test_a_missing_document_is_still_not_a_refusal() -> None:
    """**The containing guard.** A document that does not exist is a
    `NotFoundError` and stays one -- it is not a conflict, it is not a
    refusal, and it has never been either.

    Worth pinning because the two checks it sits above are now typed: the
    temptation of a classification is to classify one case too many, and
    "there is no such document" is a different answer to a different
    question.
    """
    stack = build_knowledge()

    with pytest.raises(NotFoundError):
        await stack.knowledge.request_summary.start(
            _ctx(), document_id="doc-missing", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )


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
async def test_the_rest_post_still_always_builds() -> None:
    """**The containing guard of ب-8**, and the item's governing constraint
    written as a test (خطة السيناريوهات §6, ف-3).

    A stored summary now short-circuits the CHAT path — and it must not
    short-circuit this one. `RequestSummary`'s contract is argued and stays:
    «`POST` builds always», because REST has a second verb for reading and a
    request that sometimes built and sometimes returned yesterday's text would
    make «summarise» and «rebuild» stop being two operations. `POST` IS the
    rebuild route, and it is the only one there is until an explicit «أعِد
    التلخيص» intent exists.

    So: a summary already sitting under the exact key, and the build is queued
    regardless. The read the router does lives in the ROUTER — the one layer
    that knows its call came from a conversation — and nothing about this
    use-case moved to make room for it.
    """
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    await stack.summaries.upsert(_ctx(), _summary(text=_ENGLISH_SUMMARY))
    request = RequestSummary(stack.repository, stack.summary_jobs)

    job, _events = await request.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
    )

    assert job.status is SummaryJobStatus.QUEUED
    assert job.document_id == "doc-1"
    # And the stored row is untouched — a rebuild replaces it when it
    # finishes, not when it is asked for.
    assert (
        await stack.summaries.get(_ctx(), "doc-1", SummaryKind.FULL, SummaryLanguage.AUTO)
    ) is not None


@pytest.mark.asyncio
async def test_reading_never_crosses_a_kind_and_never_relabels_a_language() -> None:
    """A fallback would answer a question the caller did not ask and label it
    as the answer to the one they did. `F-6` narrows that rule; it does not
    lift it, and the two cases below are exactly where the narrowing stops.
    """
    stack = build_knowledge()
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary()  # "old text": English
    get = GetSummary(stack.summaries)

    found = await get.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
    )
    assert found.text == "old text"

    for kind, lang in (
        # `kind` never falls back at all: the overview and the full summary
        # are different artefacts, not two spellings of one.
        (SummaryKind.OVERVIEW, SummaryLanguage.AUTO),
        # And an `auto` row written in English is not an Arabic summary --
        # the fallback reads the TEXT, so it cannot relabel this one.
        (SummaryKind.FULL, SummaryLanguage.AR),
    ):
        with pytest.raises(NotFoundError):
            await get.execute(_ctx(), document_id="doc-1", kind=kind, lang=lang)


@pytest.mark.asyncio
async def test_an_auto_summary_answers_the_language_it_is_actually_written_in() -> None:
    """The read `F-6` exists for. The chat path builds under `auto` (nobody
    named a language) and the UI then asks for `ar` — so an exact match on
    the triple reported "no summary" for a summary that exists and is written
    in Arabic."""
    stack = build_knowledge()
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary(text=_ARABIC_TEXT)
    get = GetSummary(stack.summaries)

    found = await get.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AR
    )

    # Returned AS the `auto` row it is: nothing is relabelled, so a client can
    # always see which summary it was handed.
    assert found.lang is SummaryLanguage.AUTO
    assert found.text == _ARABIC_TEXT

    # The same row is no answer to `en` ...
    with pytest.raises(NotFoundError):
        await get.execute(
            _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.EN
        )
    # ... and the fallback is a second read through the same tenant-scoped
    # port, so another workspace still cannot see it.
    with pytest.raises(NotFoundError):
        await get.execute(
            _ctx(_W2), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AR
        )


@pytest.mark.asyncio
async def test_the_stored_key_wins_over_the_auto_row_and_auto_never_falls_back() -> None:
    """Two properties of the ORDER of the two reads: the exact key is tried
    first and short-circuits, and a request for `auto` has nothing to fall
    back to, so it stays the exact match it always was."""
    stack = build_knowledge()
    stack.summaries.rows[("doc-1", "full", "auto")] = _summary(text=_ARABIC_TEXT)
    stack.summaries.rows[("doc-1", "full", "ar")] = _summary(
        text="النسخة العربية المطلوبة", lang=SummaryLanguage.AR
    )
    get = GetSummary(stack.summaries)

    found = await get.execute(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AR
    )
    assert found.lang is SummaryLanguage.AR

    # An `ar` row is not an answer to `auto`: "summarise in whatever language
    # the document is in" is a different request, not a missing value.
    stack.summaries.rows[("doc-2", "full", "ar")] = _summary(
        document_id="doc-2", text=_ARABIC_TEXT, lang=SummaryLanguage.AR
    )
    with pytest.raises(NotFoundError):
        await get.execute(
            _ctx(), document_id="doc-2", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )


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
async def test_a_running_build_beats_once_for_every_progress_write_and_never_otherwise() -> None:
    """`F-4` (plan §4 step 4, §3.5): the heartbeat rides `_progress`, and
    ONLY `_progress`.

    That placement is the whole safety argument (§6 risk 5). A beat is a
    claim that this worker is alive and working; put anywhere a build could
    reach WITHOUT advancing, it would hide exactly the wedged handler the
    health check exists to catch. From `_progress` every beat stands behind a
    `record_progress` write that has already returned -- which is what the
    second assertion here pins: the job's stored `done_chunks` at the moment
    of each beat, never a number the beat ran ahead of.
    """
    chunks = _BATCH * 2 + 1  # three map batches, then a single-group fold
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(chunks)]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None

    stored_at_each_beat: list[int] = []

    def _beat() -> None:
        stored_at_each_beat.append(stack.summary_jobs.rows["job-1"].done_chunks)

    await build.run(_ctx(), plan, on_heartbeat=_beat)

    # Three map steps (20 / 40 / 41 chunks done) and one fold tick, which
    # reports the total again because `advance` clamps there -- `F-3`'s
    # "the reduce is a phase, not a counter" seen from the other end.
    assert stored_at_each_beat == [_BATCH, _BATCH * 2, chunks, chunks]


@pytest.mark.asyncio
async def test_a_build_given_no_heartbeat_still_records_its_progress() -> None:
    """`on_heartbeat` is optional and its absence changes nothing else: the
    progress writes an operator and the interface both read are not the
    heartbeat's to carry."""
    chunks = _BATCH * 2 + 1
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(chunks)]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None

    attempt = await build.run(_ctx(), plan)

    assert attempt.error is None
    assert stack.summary_jobs.rows["job-1"].done_chunks == chunks


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
    # More than one batch, so there IS a boundary at which cancellation can
    # be observed: `execute` skips the check entirely for a document that
    # fits one call, because there is nothing after it to cancel before.
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(_BATCH * 2 + 1)]
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
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(_BATCH * 2 + 1)]
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
    chunks = _BATCH * 2 + 1
    stack.repository.texts["doc-1"] = [f"chunk {i}" for i in range(chunks)]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    assert stack.summary_jobs.rows["job-1"].total_chunks == chunks

    await build.run(_ctx(), plan)
    assert stack.summary_jobs.rows["job-1"].done_chunks == chunks


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
    *, stored: Sequence[Summary] = (), corpus_chunks: int = _BATCH * 2 + 1
) -> tuple[object, RecordingLLM]:
    """A stack whose document has a DELIBERATELY large corpus.

    ``corpus_chunks`` is more than two full ``_MAP_BATCH`` batches, so a
    build that reads the corpus cannot possibly finish in one provider call —
    which is what makes "exactly one call" a real assertion about the path
    taken rather than a coincidence of a short document. Derived from the
    constant rather than written as a number, because `F-2` moved it.
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
    assert len(plan.chunks) == _BATCH * 2 + 1

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
    assert len(plan.chunks) == _BATCH * 2 + 1
    assert stack.summary_jobs.rows["job-1"].total_chunks == _BATCH * 2 + 1


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


# --------------------------------------------------------------------------- #
# `F-7` — the thread a build owes its answer to                               #
# --------------------------------------------------------------------------- #

_THREAD = "conv-7"


@pytest.mark.asyncio
async def test_a_build_asked_for_inside_a_thread_says_so_on_its_requested_event() -> None:
    """`F-7`: the id rides the MESSAGE, because there is no column for it and
    deliberately so — the worker reads it back off the envelope it is handed,
    which is the same thing ``SummaryRequested`` already does for the build
    key it could have loaded from the job row."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)

    await stack.knowledge.request_summary.start(
        _ctx(),
        document_id="doc-1",
        kind=SummaryKind.OVERVIEW,
        lang=SummaryLanguage.AUTO,
        conversation_id=_THREAD,
    )

    published = stack.outbox.calls[0][0].payload["data"]
    assert published["conversation_id"] == _THREAD


@pytest.mark.asyncio
async def test_a_build_asked_for_outside_a_thread_omits_the_key_rather_than_nulling_it() -> None:
    """The ``space_id`` rule in ``files``' own mapping, and the reason is the
    same: an omitted key is what an envelope published before `F-7` looks
    like, so a consumer has ONE shape to read for "no thread" instead of two.

    This is the ordinary case, not an edge one — every summary ``POST
    /documents/{id}/summary`` builds is read back through ``GET`` and has no
    thread to be delivered to."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)

    await stack.knowledge.request_summary.start(
        _ctx(), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AR
    )

    published = stack.outbox.calls[0][0].payload["data"]
    assert "conversation_id" not in published
    # The four fields that were there before `F-7` are untouched, so the
    # payload of a REST-route build is byte for byte what it always was.
    assert set(published) == {"job_id", "document_id", "kind", "lang"}


@pytest.mark.asyncio
async def test_the_thread_is_not_part_of_the_key_so_a_second_one_still_gets_the_409() -> None:
    """Two threads asking for the same overview of the same document in the
    same language are ONE build, and the second is still refused before a
    token is spent.

    This is the whole reason ``conversation_id`` is not on the job row and
    not in ``uq_summary_job_active``: had it been, the guard would have
    stopped seeing the pair as duplicates and the workspace would pay twice
    for one artefact — and then the two builds would race to write one
    ``uq_summary_key`` row."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    request = RequestSummary(stack.repository, stack.summary_jobs)

    await request.execute(
        _ctx(),
        document_id="doc-1",
        kind=SummaryKind.OVERVIEW,
        lang=SummaryLanguage.AUTO,
        conversation_id="conv-first",
    )
    with pytest.raises(ConflictError, match="already being built"):
        await request.execute(
            _ctx(),
            document_id="doc-1",
            kind=SummaryKind.OVERVIEW,
            lang=SummaryLanguage.AUTO,
            conversation_id="conv-second",
        )


@pytest.mark.asyncio
async def test_the_finished_build_stamps_the_thread_it_was_handed_on_summary_built() -> None:
    """The other half of the ride: ``finalize`` takes the id the handler read
    off the request message and puts it on ``knowledge.summary.built.v1``,
    which is what lets the delivery subscriber know where to post the text
    without a second read of anything."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = ["alpha", "beta"]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    attempt = await build.run(_ctx(), plan)
    _, events = await build.finalize(_ctx(), attempt, conversation_id=_THREAD)

    assert [type(event).__name__ for event in events] == ["SummaryBuilt"]
    assert events[0].conversation_id == _THREAD  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_finalizing_without_a_thread_publishes_none_and_not_a_missing_field() -> None:
    """``None`` on the DOMAIN event, an absent key on the WIRE — the two are
    different layers saying the same thing, and the mapping is the one place
    that translates between them."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = ["alpha"]
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    _, events = await build.finalize(_ctx(), await build.run(_ctx(), plan))

    assert events[0].conversation_id is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_failed_build_carries_no_thread_because_nothing_subscribes_to_a_failure() -> None:
    """``SummaryBuildFailed`` deliberately did not grow the field. Adding it
    would have been a promise nobody is waiting for — the ``FileRenamed``
    mistake the event's own docstring names — and the thread that asked
    already has the receipt it was given when the build was queued."""
    stack = build_knowledge()
    stack.repository.rows["doc-1"] = seed_document(document_id="doc-1", workspace_id=_W1)
    stack.repository.texts["doc-1"] = []
    stack.summary_jobs.rows["job-1"] = _job()
    build = _builder(stack, StubSummarizerResolver(RecordingLLM()))

    plan = await build.claim(_ctx(), job_id="job-1")
    assert plan is not None
    _, events = await build.finalize(_ctx(), await build.run(_ctx(), plan), conversation_id=_THREAD)

    assert [type(event).__name__ for event in events] == ["SummaryBuildFailed"]
    assert not hasattr(events[0], "conversation_id")


# --------------------------------------------------------------------------- #
# delivered_summary_text — `F-9` (plan §3.10): the cut, declared in words      #
# --------------------------------------------------------------------------- #

_ENGLISH_SUMMARY = "The retrieval policy applies to every workspace document."
_ARABIC_SUMMARY = "تنطبق سياسة الاسترجاع على كلّ مستندات مساحة العمل."


def test_a_summary_that_read_the_whole_document_is_delivered_exactly_as_built() -> None:
    """The ordinary delivery — every ``overview`` and every ``full`` build
    inside the ceiling — is byte for byte the text the model wrote. Not
    "no notice added": nothing at all is appended, trimmed or reflowed, so a
    thread shows the artefact and a ``GET`` shows the artefact."""
    summary = _summary(text=_ENGLISH_SUMMARY, truncated=False)

    assert delivered_summary_text(summary) == _ENGLISH_SUMMARY


def test_a_truncated_summary_is_delivered_with_a_sentence_saying_it_is_one() -> None:
    """`F-9`: ``truncated`` is a FIELD on the row and in ``SummaryOut``, and
    a conversation message has no fields — so this was the one surface where
    a summary of a document's opening arrived looking like a summary of the
    whole document."""
    summary = _summary(text=_ENGLISH_SUMMARY, lang=SummaryLanguage.EN, truncated=True)

    expected = f"{_ENGLISH_SUMMARY}\n\n{SUMMARY_TRUNCATED_NOTICE_EN}"
    assert delivered_summary_text(summary) == expected


@pytest.mark.parametrize(
    ("lang", "text", "expected"),
    [
        (SummaryLanguage.AR, _ENGLISH_SUMMARY, SUMMARY_TRUNCATED_NOTICE_AR),
        (SummaryLanguage.EN, _ARABIC_SUMMARY, SUMMARY_TRUNCATED_NOTICE_EN),
        (SummaryLanguage.AUTO, _ARABIC_SUMMARY, SUMMARY_TRUNCATED_NOTICE_AR),
        (SummaryLanguage.AUTO, _ENGLISH_SUMMARY, SUMMARY_TRUNCATED_NOTICE_EN),
    ],
)
def test_the_notice_speaks_the_language_the_summary_was_asked_for(
    lang: SummaryLanguage, text: str, expected: str
) -> None:
    """One language mechanism, not a second: this is ``_is_rtl``, the rule
    the export path already uses. A REQUESTED language wins over the text
    (rows one and two — a summary asked for in ``ar`` is answered in Arabic
    even where the body is not), and ``auto`` is decided by the body, because
    ``auto`` never named a language for anything to contradict."""
    assert delivered_summary_text(_summary(text=text, lang=lang, truncated=True)).endswith(expected)


def test_the_notice_follows_the_summary_and_never_stands_in_front_of_it() -> None:
    """The summary is the answer and the notice qualifies it. Above the text
    the same sentence reads as an error standing between the reader and what
    they asked for; below it, it reads as the footnote it is."""
    delivered = delivered_summary_text(_summary(text=_ENGLISH_SUMMARY, truncated=True))

    assert delivered.startswith(_ENGLISH_SUMMARY)
    assert _ENGLISH_SUMMARY not in delivered[len(_ENGLISH_SUMMARY) :]


def test_composing_a_delivery_leaves_the_stored_summary_alone() -> None:
    """The notice is composed AT DELIVERY and never written into the row: a
    ``GET`` reader already has the flag, so storing the sentence would state
    one fact twice for them and put prose the model never wrote inside the
    artefact every later reader — ``translate`` included — works from.

    ب-7ج put a SECOND composed line on this path, and it obeys the
    same rule for a sharper reason: a name is a fact about a FILE, and
    freezing it into a summary row would make the artefact disagree
    with the file the day it is renamed (INV-F4 permits exactly that)."""
    summary = _summary(text=_ENGLISH_SUMMARY, truncated=True)

    delivered_summary_text(summary, "retrieval-policy.pdf")

    assert summary.text == _ENGLISH_SUMMARY


# ------------------------------------------------------------- #
# delivered_summary_text — ب-7ج (scenarios plan §4, gap ف-2): which file #
# ------------------------------------------------------------- #


def test_a_named_delivery_says_which_file_it_summarises() -> None:
    """ب-7ج: the receipt that accepted the build can name the document
    (``RoutedAnswer.summary_target_name``), and this is the other half of
    the same sentence — a thread told «التقرير الشمالي» minutes ago and
    then handed a wall of prose was still asking its reader to assume the
    two are about one file."""
    delivered = delivered_summary_text(
        _summary(text=_ENGLISH_SUMMARY, truncated=False), "retrieval-policy.pdf"
    )

    assert delivered == f'Summary of "retrieval-policy.pdf":\n\n{_ENGLISH_SUMMARY}'


def test_the_header_stands_above_the_summary_and_the_notice_below_it() -> None:
    """The two composed lines do not compete. A title says what is
    being read and is useless after it has been read; a caveat qualifies
    the answer and belongs after it. So a truncated, named delivery
    carries both, in that order, with the body between them."""
    delivered = delivered_summary_text(
        _summary(text=_ENGLISH_SUMMARY, lang=SummaryLanguage.EN, truncated=True),
        "retrieval-policy.pdf",
    )

    assert delivered == (
        f'Summary of "retrieval-policy.pdf":\n\n{_ENGLISH_SUMMARY}\n\n{SUMMARY_TRUNCATED_NOTICE_EN}'
    )


def test_the_header_speaks_the_language_the_notice_speaks() -> None:
    """One language mechanism for both composed lines — ``_is_rtl``,
    the rule the export path already uses. They are read together, so a
    header in one script over a notice in another would be two voices
    around one summary."""
    delivered = delivered_summary_text(
        _summary(text=_ARABIC_SUMMARY, lang=SummaryLanguage.AUTO, truncated=True),
        "التقرير الشمالي.pdf",
    )

    assert delivered.startswith("ملخّص «التقرير الشمالي.pdf»:")
    assert delivered.endswith(SUMMARY_TRUNCATED_NOTICE_AR)


@pytest.mark.parametrize("name", [None, "", "   "])
def test_an_unnameable_file_is_delivered_exactly_as_it_was_before(name: str | None) -> None:
    """``None`` means the caller could not read a name — deleted,
    quarantined, or a lookup that failed — and it delivers byte for
    byte what this function delivered before the parameter existed.

    A blank is the same answer for a sharper reason: a message headed
    ``Summary of "":`` tells a reader their file is called nothing."""
    summary = _summary(text=_ENGLISH_SUMMARY, truncated=False)

    assert delivered_summary_text(summary, name) == _ENGLISH_SUMMARY


# --------------------------------------------------------------------------- #
# ReadStoredSummary — ب-8 (خطة السيناريوهات §6، الفجوة ف-3)                     #
# --------------------------------------------------------------------------- #


async def test_a_stored_summary_is_read_back_with_the_delivery_framing_a_worker_gives_it() -> None:
    """Decision 3: the text comes back through `delivered_summary_text`, the
    SAME composer the worker's own delivery uses.

    A summary read out of the store and one a worker has just finished have to
    reach a thread looking alike — the file-name header prepended, the
    truncation notice appended — or the stored copy becomes a visibly
    second-class delivery of one artefact. That is what this class is FOR: the
    router could have called the repository itself, and then there would be
    two composers of one delivery, one turn apart.
    """
    ctx = _ctx()
    summaries = InMemorySummaryRepository()
    await summaries.upsert(ctx, _summary(text=_ENGLISH_SUMMARY, truncated=True))

    text = await ReadStoredSummary(summaries).stored_text(
        ctx,
        document_id="doc-1",
        kind=SummaryKind.FULL,
        lang=SummaryLanguage.AUTO,
        file_name="quarterly.pdf",
    )

    assert text is not None
    assert _ENGLISH_SUMMARY in text
    # `F-9` — a summary of a PREFIX must not arrive looking like a summary of
    # the whole book, on this delivery any more than on the worker's.
    assert SUMMARY_TRUNCATED_NOTICE_EN in text
    # ب-7ج — the header above the body, the notice below it.
    assert text.index("quarterly.pdf") < text.index(_ENGLISH_SUMMARY)
    assert text.index(_ENGLISH_SUMMARY) < text.index(SUMMARY_TRUNCATED_NOTICE_EN)
    # And it is byte for byte what the worker would have delivered.
    assert text == delivered_summary_text(
        _summary(text=_ENGLISH_SUMMARY, truncated=True), "quarterly.pdf"
    )


async def test_an_unstored_summary_reads_as_absent_rather_than_as_an_error() -> None:
    """`ReadStoredSummary` returns `None` where `GetSummary` raises, and that
    is the first of the two reasons it is not `GetSummary`.

    A document nobody has summarised yet is the ORDINARY case on the chat
    path, and the answer to it is the build the router goes on to queue — not
    a `NotFoundError` to unwind through a route with a perfectly good next
    step.
    """
    assert (
        await ReadStoredSummary(InMemorySummaryRepository()).stored_text(
            _ctx(), document_id="doc-1", kind=SummaryKind.OVERVIEW, lang=SummaryLanguage.AUTO
        )
        is None
    )


async def test_the_stored_read_never_falls_back_across_the_key() -> None:
    """The whole triple is the key, and `SummaryRepository.get`'s no-fallback
    rule is exactly what this caller wants unchanged.

    An OVERVIEW is not a FULL summary and an Arabic one is not an English one.
    A read that widened here would answer «لخّص هذا كاملاً» with the opening
    chunks of a document and say nothing about the substitution — which is the
    depth control `F-8` built, undone one layer down.
    """
    ctx = _ctx()
    summaries = InMemorySummaryRepository()
    await summaries.upsert(ctx, _summary(text=_ENGLISH_SUMMARY))
    read = ReadStoredSummary(summaries)

    assert (
        await read.stored_text(
            ctx, document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )
        is not None
    )
    assert (
        await read.stored_text(
            ctx, document_id="doc-1", kind=SummaryKind.OVERVIEW, lang=SummaryLanguage.AUTO
        )
        is None
    )
    assert (
        await read.stored_text(
            ctx, document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AR
        )
        is None
    )


async def test_another_tenants_stored_summary_is_not_read_back_into_a_chat() -> None:
    """The tenant guard, restated on the new reader rather than assumed from
    the old one.

    `GetSummary` has this and it is load-bearing there; this class is a second
    door onto the same rows, opened for a caller — a conversation — that never
    names a workspace at all. Its scoping is the `ExecutionContext`'s, and a
    test that did not say so would leave the claim resting on a repository
    detail nobody re-checks.
    """
    summaries = InMemorySummaryRepository()
    await summaries.upsert(_ctx(_W1), _summary(text=_ENGLISH_SUMMARY))

    assert (
        await ReadStoredSummary(summaries).stored_text(
            _ctx(_W2), document_id="doc-1", kind=SummaryKind.FULL, lang=SummaryLanguage.AUTO
        )
        is None
    )
