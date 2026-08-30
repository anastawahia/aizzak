"""``SummarizeDocument`` — the map-reduce summarisation pipeline (06 §7 ·
BE-RAG-009).

The ``IndexDocument`` shape one capability over, with one deliberate
difference: that pipeline holds its ``EmbeddingProvider`` for its whole
lifetime because there is one embedding service, while this one takes the LLM
adapter per call inside a ``ResolvedSummarizer`` — which adapter answers is
precisely what the ``summarize`` route decides. Either way one instance
serves every workspace and no credential is ever captured in a constructor.

**It summarises CHUNKS, not the file.** The text was already fetched,
decoded and parsed once when the document was indexed, and ``knowledge.chunks``
holds the result in reading order. Re-fetching the object from MinIO and
re-running the parser would spend the whole ingestion cost a second time to
arrive at bytes this module already has — and would quietly disagree with the
corpus the moment a parser changed, so the summary would describe text no
search could ever return.

**As of P-42 (plan §4 step 18, §3.10), one of those "chunks" may be a
*parent* chunk's text rather than a leaf's own.** ``DocumentRepository.
chunk_texts`` (the caller's source for this module's ``chunks`` argument,
``BuildSummary.claim``) resolves each leaf whose parent actually HOLDS it
(a small table's rows, P-13) to that PARENT's coarser text instead,
collapsing every row of the same table into ONE reading unit — coherent
sections read cheaper than the same content in fragments. A leaf with no
parent — or with a header-only one, which would have handed this module a
table's column names in place of its rows — is read exactly as before: its
own text, individually, with no dedup applied. This module itself stays
unaware of the distinction — it still just reads an ordered ``Sequence[str]``
— which is what keeps a document with no table an exact behavioural match
for how this pipeline worked before step 18.

**Both kinds are bounded, and the bound is reported rather than hidden.**
``overview`` reads a sample of chunks spread across the WHOLE document
(``_glance_sample``, P-43) and makes ONE call — "what is this?" is answered
better by the shape of a document than by its opening alone, which a generic
cover sheet or table of contents can make a worthless glance; a reader who
wanted the whole thing asked for ``full``. ``full`` maps over every chunk in
batches and reduces the notes into one summary, up to ``_MAX_MAP_CHUNKS``.
Past that ceiling the pipeline summarises the prefix and sets ``truncated``,
which travels all the way onto the stored row, into the API response, and —
since `F-9` — into the sentence a chat delivery appends for a reader
who has no field to read a flag in (``use_cases.delivered_summary_text``). The
alternative — an unbounded map — makes the cost of one request a function of
the largest file anyone ever uploaded, and this is already the most expensive
call the platform makes.

**Cancellation is checked between batches AND inside every call** (ب-6).
Between batches was once the only place it could be, and the reason given was
that a round trip in flight cannot be recalled — true of a round trip that
answers all at once, which is what ``complete()`` makes of every call.
``stream()`` does not: the answer arrives in pieces, so the pipeline gets the
control back thousands of times inside one call and can ask whether Stop was
pressed at any of them (``_CANCEL_POLL_INTERVAL_S`` decides how often it is
worth asking). Abandoning a stream costs the tokens already generated and
recalls nothing that was not going to be paid for anyway. The batch-boundary
polls stay exactly where they were: they are cheaper, and they stop a build
BEFORE the next call is spent, which no poll inside a started call can do.
See ``SummaryJob.cancel``.

**``translate`` (P-44, plan §4 step 20, §3.10) is a THIRD shape beside
``execute``'s two kinds, and a cheaper one.** It reads no chunk at all — its
input is an already-built ``Summary`` row, and its output is the same text in
another ``lang``. No migration was needed to carry a translation: ``lang``
and ``UNIQUE(document_id, kind, lang)`` (``knowledge.summaries``) already
key a summary on the language it was written in, so a translation is simply
one more row under the same document and kind. Asking for the language a
stored summary is already in is answered by reusing its text, with no
provider call spent proving what was already true.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic

from app.framework.context.execution_context import ExecutionContext
from app.framework.observability import get_logger
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams
from app.modules.knowledge.domain.entities import Summary
from app.modules.knowledge.domain.value_objects import SummaryKind, SummaryLanguage
from app.modules.knowledge.ports.summarization import ResolvedSummarizer

log = get_logger(__name__)

# P-43 (plan §4 step 19, §3.10): how many chunks an `overview` reads --
# spread ACROSS the document (`_glance_sample`), not just its first
# `_OVERVIEW_CHUNKS`. "First 8" gave a worthless glance for a document whose
# opening pages are a generic cover sheet or table of contents; sampling by
# position instead answers "what is this?" from the whole shape of the
# document. Still exactly ONE provider call regardless of length -- the
# chunks read are chosen by position, never by an extra round trip.
_OVERVIEW_CHUNKS = 8

# The marker `_glance_sample` inserts between two sampled chunks that were
# NOT adjacent in the document, so the model is TOLD it is reading a sample
# with real material skipped between the pieces, rather than left to
# silently infer two unrelated passages are continuous -- the same honesty
# `truncated` (module docstring) already keeps for `full`, extended here to
# `overview`'s own kind of incompleteness.
_GAP_MARKER = "[…]"

# Chunks per map call. Small enough that one provider error costs one batch
# rather than the document, large enough that a batch APPROACHES
# `_MAX_BATCH_CHARS` instead of closing long before it: at the ~1,100
# characters a chunk the chunker actually produces, 20 chunks is ~22,000
# characters, just under the character budget, so whichever of the two bounds
# is tighter for the chunks at hand is the one that binds.
#
# `F-2` (rag-summarization-fix-plan.md §3.3) raised this from 6, where the
# character budget below was DEAD: ~6,600 characters a batch cannot reach
# 24,000 at any chunk size the chunker emits, so the smaller bound won every
# time and the larger one was never read. A 240-chunk document cost 40 map
# calls and, with the folds above them, ~50 provider round trips where the
# same budgets spend ~15. Nothing about the model changed -- the batch had
# simply been sized by one of two bounds nobody had compared.
_MAP_BATCH = 20

# The ceiling on a `full` map: 240 chunks, ~110 pages. Beyond this the
# pipeline summarises the prefix and SAYS SO (`truncated`) rather than
# silently spending more -- and since `F-9` the chat delivery says so in
# words too (`use_cases.delivered_summary_text`), which is what finally makes
# the declared cut declared at the one surface that had no field to hold it.
#
# `F-2` made this a LITERAL where it had been `_MAP_BATCH * 40`. The derived
# form tied how much of a document is READ to how many chunks fit in ONE
# call, which are unrelated facts answering to unrelated pressures -- a cost
# ceiling and a context window -- and raising the batch would have moved the
# ceiling from 240 chunks to 800 as a silent side effect of a change that
# says nothing at all about how much of a document to read.
#
# `F-9`'s review (plan §3.10, §5.2) KEPT the number and REPLACED the argument
# for it, which is the part a future reader needs:
#
# * The COST argument that set 240 is spent. The build that cost ~50 provider
#   calls costs 15 since `F-2`, measured.
# * The CONTEXT argument does not bind either, also measured: re-running the
#   real ladder at raised ceilings, a 1,920-chunk build still maps at 22,422
#   characters and folds at 14,914 -- inside both budgets, with no call near
#   the 8k window. `_fold` absorbs the extra notes rather than passing them on.
# * What bounds it now is TIME, and the constant that expresses that lives in
#   another file: `Limits.summarize_job_max_duration_s` (1,800 s) derives
#   itself from `_MAX_MAP_CHUNKS / _MAP_BATCH` in its own comment. At 240
#   chunks it grants ~120 s a call; at 480 it grants ~62; at 960, ~31.
#
# So the two limits are one limit read from two ends -- and they FAIL
# DIFFERENTLY. Crossing this one yields a real summary of a prefix that says
# it is one; crossing that one yields a `failed` job and no summary at all.
# Raising this ceiling without raising that cap therefore converts declared
# truncation into outright failure, for exactly the documents the raise was
# meant to serve. The number that would justify moving both together is the
# longest single provider call in wall-clock, which plan §5 still lists as
# unmeasured because it needs a live model.
_MAX_MAP_CHUNKS = 240

# A second guard on the same thing from the other end: chunk sizes are set by
# the chunker, but a batch of unusually long ones must still not build a
# prompt no context window accepts. Characters, not tokens, because this is a
# guard rail and not an accounting -- a tokeniser here would be a dependency
# bought to make an approximation look precise.
#
# This is the budget for a call that ANSWERS in `_MAP_MAX_TOKENS`. Reachable
# since `F-2`, and the bound that binds for unusually long chunks.
_MAX_BATCH_CHARS = 24_000

# `F-2`: the same guard for a call that answers in `_REDUCE_MAX_TOKENS` --
# the fold/reduce ladder and `execute`'s one-batch shortcut. It
# MUST be smaller, and the arithmetic is the whole reason: 24,000 characters
# is ~6,000 tokens at this module's 4:1 rule of thumb, and 6,000 + 2,500 is
# 8,500 -- past an 8k window before the system prompt is counted at all. The
# map path is safe at 24,000 only because it answers in 600. 16,000 (~4,000
# tokens) plus the same 2,500 leaves room for both halves.
#
# Two budgets rather than one conservative number: lowering `_MAX_BATCH_CHARS`
# to 16,000 would shrink the map batches too, buying back the extra calls
# `F-2` exists to remove, to respect a limit the map path does not have.
_MAX_REDUCE_CHARS = 16_000

# P-41 (plan §4 step 17, §3.10): the recursion cap on `_fold`. Without it, a
# many-batch `full` build hands ONE reduce call every map note at once --
# `_MAX_MAP_CHUNKS / _MAP_BATCH` = 12 notes * `_MAP_MAX_TOKENS` = ~7k tokens
# in a SINGLE request, which is outright failure (not graceful degradation)
# against an 8k-window model once the 2,500-token answer is counted. It was
# 40 notes and ~24k tokens before `F-2` cut the batch count; the shape of the
# failure is unchanged, only its size.
# `_fold` instead groups notes the same way `_batched` groups chunks and
# recurses on the folded groups, so no single call ever exceeds one batch's
# worth of notes. The cap is an EXPLICIT termination guarantee, not an
# implicit one: `_batched` grouping already shrinks the note count on every
# level it is given (fewer groups than notes, whenever there is more than one
# group), but stating that recursion cannot exceed 3 levels regardless keeps
# the guarantee true even if a future change to `_MAP_BATCH`/`_MAX_MAP_CHUNKS`
# made that shrinkage slower, rather than resting entirely on today's
# constants happening to converge in time.
_MAX_FOLD_DEPTH = 3

_TEMPERATURE = 0.2
_MAP_MAX_TOKENS = 600
_OVERVIEW_MAX_TOKENS = 900
_REDUCE_MAX_TOKENS = 2_500

# ب-6 (summarization-scenarios-implementation-plan.md §5): how often a call
# in flight stops to ask whether Stop was pressed.
#
# NOT once per chunk, and that is the whole trade this number expresses.
# `should_cancel` is a ROW READ from the database (`use_cases.BuildSummary.
# run._should_cancel`), so polling per token would spend thousands of reads
# inside a single call on a button nobody pressed -- a price paid by every
# build to serve the rare one that is stopped. Two seconds turns the worst
# wait between a press and its effect from a whole call (`timeout_s`, 300 s
# in the deployed worker) into two, for about half a read a second while a
# build is actually running, and none at all when it is not.
_CANCEL_POLL_INTERVAL_S = 2.0

# The per-call wall-clock cap used when a caller says nothing. The real number
# is `Limits.summarize_timeout_s`, wired in `workers/bootstrap.py` where this
# pipeline is built; this default is what a direct `SummarizeDocument()` gets,
# so a caller who never heard of the setting is bounded rather than unbounded.
# The two are held equal by
# `test_the_default_call_timeout_is_the_shipped_setting`, which is what stops
# them drifting into two different answers to one question.
_DEFAULT_CALL_TIMEOUT_S = 300.0

# `F-10`/`F-9`: the closed vocabulary `_call`'s `step` is drawn from -- one
# name per place in the ladder a provider call can be made. It exists as a
# constant, rather than six literals at the call sites, for two reasons: it
# is what makes `step` safe to log at all (10 §10 admits a path name from a
# closed set, never a free string), and it is what a drift test can compare a
# real build's calls against, so a seventh call site added later without a
# name of its own fails a test rather than logging one of the other six.
#
# The three ceilings above are NOT one per step -- `overview` answers in 900,
# `map`/`fold` in 600, the rest in 2,500 -- which is exactly why the
# truncation warning carries both: which step hit its ceiling, and which
# ceiling that was.
_STEPS = frozenset({"overview", "full_single_batch", "map", "fold", "reduce", "translate"})

_LANGUAGE_INSTRUCTION = {
    SummaryLanguage.AUTO: ("Write the summary in the same language the source text is written in."),
    SummaryLanguage.AR: "Write the summary in Arabic (العربية).",
    SummaryLanguage.EN: "Write the summary in English.",
}

_MAP_SYSTEM = (
    "You are extracting notes from one part of a longer document. "
    "List the concrete facts, claims, figures, names and decisions this "
    "excerpt contains, as terse bullet points. Do not add an introduction, "
    "a conclusion, or anything the excerpt does not say. Keep every note in "
    "the language of the excerpt."
)

_REDUCE_SYSTEM = (
    "You are writing the final summary of a document from ordered notes "
    "taken across its parts. Produce well-structured Markdown with short "
    "headings and paragraphs. Cover the whole document in proportion, keep "
    "figures and names exact, and state nothing the notes do not support."
)

# P-41's intermediate step: unlike `_REDUCE_SYSTEM`, a fold call does not
# produce the final answer -- it produces MORE notes, one level coarser, that
# either get folded again or reach `_REDUCE_SYSTEM` next. Asking for Markdown
# prose here would waste an intermediate round trip polishing text that is
# about to be summarised again.
_FOLD_SYSTEM = (
    "You are compressing several batches of notes taken from different parts "
    "of a longer document into ONE shorter set of notes. Keep every concrete "
    "fact, claim, figure, name and decision the notes contain, as terse "
    "bullet points. Do not add an introduction or a conclusion, and do not "
    "state anything the notes do not already say. Keep every note in its "
    "original language."
)

_OVERVIEW_SYSTEM = (
    "You are writing a brief overview of a document from a sample of excerpts "
    "taken from across it, in reading order. A marker written exactly as "
    "'[…]' stands for a real gap between excerpts -- material was skipped "
    "there, not lost or blank. In a few short Markdown paragraphs say what "
    "the document is, what it covers, and who it appears to be for. Do not "
    "speculate about parts you were not shown, and do not mention the "
    "sampling or the marker itself."
)

_FULL_SYSTEM = (
    "You are summarising a document. Produce well-structured Markdown with "
    "short headings and paragraphs, covering the whole text in proportion, "
    "keeping figures and names exact, and stating nothing the text does not "
    "support."
)

# P-44 (plan §4 step 20, §3.10): unlike every system prompt above, this one is
# not asked to READ source material and produce notes or prose from it -- the
# "document" it is handed is an already-finished summary, and its only job is
# to carry that text into another language without changing what it says.
# Asking it to "summarise" again (`_FULL_SYSTEM`'s wording) would invite a
# second round of compression on text that was already compressed once.
_TRANSLATE_SYSTEM = (
    "You are translating an already-written document summary into another "
    "language. Preserve its Markdown structure, headings, figures and names "
    "exactly. Translate the prose faithfully and add or omit nothing the "
    "original does not already say."
)


# ب-11ب (خطة السيناريوهات §7، ف-3) — a literal until this item, and promoted
# because it is now DELIVERED: it travels as the `ValueError`'s message into
# `SummaryJob.error`, onto `SummaryBuildFailed.reason`, and from there into the
# thread that asked. `SUMMARY_DELIVERABLE_REASONS` is the closed set a thread
# may be shown verbatim, and a set cannot hold a sentence that lives as an
# inline argument.
#
# It is the ONE deliverable reason that says something the neutral sentence
# cannot: every other failure means "try again", and this one means waiting
# will not help — index the file first.
SUMMARY_NO_INDEXED_TEXT_REASON = "this document has no indexed text to summarise"


class SummaryBuildCancelled(Exception):
    """Raised out of ``SummarizeDocument.execute`` when the caller's
    ``should_cancel`` said so at a batch boundary.

    An exception rather than a ``SummaryDraft | None`` return: a cancelled
    build produced nothing, and a return type that can be "nothing" makes
    every caller destructure a success that may not be one. The worker handler
    catches this and stops — the job row was already stamped ``cancelled`` by
    the request that asked, so there is nothing left to record.
    """


@dataclass(frozen=True, slots=True)
class SummaryDraft:
    """What a completed build produced, before it becomes a ``Summary`` row.

    ``source_chunks`` is how many chunks were actually READ, and ``truncated``
    whether the document had more. Both travel to the stored row: a summary of
    the first 240 chunks of a 900-chunk document is a true summary of a part,
    and a reader who is not told which is being misled about the whole.

    ``model`` rides here rather than on the caller's attempt record because
    this is the layer that knows it was used. A failed build has no draft and
    therefore no model to report — which is correct: nothing wrote anything,
    so there is no authorship to record.
    """

    text: str
    model: str
    source_chunks: int
    truncated: bool


# The caller's two hooks. `should_cancel` is re-read from the database at each
# batch boundary rather than captured once, because the whole point is to
# observe a write another process made after this build started.
ProgressHook = Callable[[int], Awaitable[None]]
CancelHook = Callable[[], Awaitable[bool]]

# `F-3` (rag-summarization-fix-plan.md §3.4): what the FOLD phase reports.
# It carries no number, unlike `ProgressHook`, because by then there is no
# number left to carry: the map loop has already advanced the job to
# `total_chunks` and `SummaryJob.advance` clamps there, so every count a fold
# could report is the count already stored. What a tick says is that the
# build is alive and has just finished a step -- which is the whole of what
# is needed, and is the channel `F-4`'s heartbeat rides on.
PhaseHook = Callable[[], Awaitable[None]]


async def _noop_progress(_done: int) -> None:
    return None


async def _noop_phase() -> None:
    return None


async def _never_cancel() -> bool:
    return False


class SummarizeDocument:
    """Turn a document's ordered chunk text into one summary.

    **Stateless.** The adapter, model and key arrive together per call, as one
    ``ResolvedSummarizer``, rather than in the constructor the way
    ``IndexDocument`` holds its ``EmbeddingProvider``. Which adapter answers
    is exactly what the ``summarize`` route decides, so capturing one at
    composition time would ignore the configuration this feature exists to
    honour — and passing the triple as three parameters would let a caller
    pair one provider's key with another's model, which is the failure the
    resolver returns them together to prevent.

    **``timeout_s`` does not break that** (ب-6): stateless here has always
    meant no DEPENDENCY is captured — no provider, no key, no model — and a
    number is none of those. It cannot be resolved per workspace, it cannot
    be paired wrongly with anything, and it is the same for every call this
    instance ever makes. See ``__init__`` for why the number has to be held
    at all now that the pipeline streams.
    """

    def __init__(self, *, timeout_s: float = _DEFAULT_CALL_TIMEOUT_S) -> None:
        """``timeout_s`` bounds ONE provider call, end to end.

        ب-6 is what makes it necessary, and it restores a bound rather than
        adding one. While this pipeline called ``complete()`` the cap was
        already there and invisible: the summarisation adapters' httpx client
        is built with ``Limits.summarize_timeout_s`` (``workers/bootstrap.
        py``), and with ``stream: false`` a provider emits no byte until
        generation ends — so that one timeout governed the WHOLE call.
        Streaming turns the very same httpx timeout into a BETWEEN-CHUNK one,
        under which a model dribbling a token every ten seconds runs forever
        and nothing stops it short of ``Limits.summarize_job_max_duration_s``
        half an hour later. Without this parameter ب-6 would be a regression
        wearing a feature's clothes.
        """
        self._timeout_s = timeout_s

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        chunks: Sequence[str],
        kind: SummaryKind,
        lang: SummaryLanguage,
        summarizer: ResolvedSummarizer,
        on_progress: ProgressHook = _noop_progress,
        should_cancel: CancelHook = _never_cancel,
    ) -> SummaryDraft:
        """Summarise ``chunks`` (already in ``seq`` order) into one Markdown
        body.

        ``ctx`` is accepted and unused, deliberately: every other pipeline in
        this module takes it, and a signature that drops it would have to be
        widened again the first time a summary needs the workspace for a
        prompt or a quota check. It is one parameter against a signature
        change reaching every caller.
        """
        del ctx  # see the docstring

        readable = [text for text in chunks if text.strip()]
        if not readable:
            # Phrased for the person who will read it on the failed job, not
            # for a log: an `indexed` document with no readable chunk is a
            # real outcome (an empty PDF, a scan with no OCR text), and the
            # message is the whole explanation they will get. `NO_TEXT_REASON`
            # in `use_cases` is the same sentence for the same reason.
            raise ValueError(SUMMARY_NO_INDEXED_TEXT_REASON)

        if kind is SummaryKind.OVERVIEW:
            sample, sampled = _glance_sample(readable)
            text = await self._call(
                system=_OVERVIEW_SYSTEM,
                user=sample,
                lang=lang,
                summarizer=summarizer,
                max_tokens=_OVERVIEW_MAX_TOKENS,
                step="overview",
                # ب-6: an `overview` is ONE call with no batch boundary before
                # or after it, so until now Stop could not be observed on this
                # shape at all -- the hook was accepted and never read. The
                # poll inside the call is the only one it will ever have.
                should_cancel=should_cancel,
            )
            await on_progress(sampled)
            return SummaryDraft(
                text=text,
                model=summarizer.model,
                source_chunks=sampled,
                truncated=len(readable) > sampled,
            )

        window = readable[:_MAX_MAP_CHUNKS]
        truncated = len(readable) > len(window)
        batches = _batched(window)

        # One batch means the whole document already fits in one call, so the
        # map/reduce round trip would spend two calls to reach what one says
        # better: notes summarised from notes read worse than a summary
        # written from the text.
        #
        # `F-2`: "fits" is measured against `_MAX_REDUCE_CHARS`, NOT the map
        # budget `batches` above is grouped by, because this shortcut ANSWERS
        # in `_REDUCE_MAX_TOKENS` -- it is a reduce-sized call and carries the
        # reduce-sized input budget. A window that fits one MAP batch can
        # still be half again too large to be answered at 2,500 tokens. The
        # two never disagreed before only because `_MAP_BATCH = 6` kept every
        # batch far under both; raising it makes the difference reachable.
        # The smaller budget also implies `len(batches) == 1`, so this one
        # check is the whole condition.
        if len(_batched(window, max_chars=_MAX_REDUCE_CHARS)) == 1:
            text = await self._call(
                system=_FULL_SYSTEM,
                user=_join(batches[0]),
                lang=lang,
                summarizer=summarizer,
                max_tokens=_REDUCE_MAX_TOKENS,
                step="full_single_batch",
                # The same as `overview` above: one call, no boundary, and
                # the reduce-sized budget makes it the longer of the two.
                should_cancel=should_cancel,
            )
            await on_progress(len(window))
            return SummaryDraft(
                text=text, model=summarizer.model, source_chunks=len(window), truncated=truncated
            )

        notes: list[str] = []
        done = 0
        for index, batch in enumerate(batches, start=1):
            if await should_cancel():
                raise SummaryBuildCancelled
            note = await self._call(
                system=_MAP_SYSTEM,
                user=f"Part {index} of {len(batches)}.\n\n{_join(batch)}",
                lang=lang,
                summarizer=summarizer,
                max_tokens=_MAP_MAX_TOKENS,
                step="map",
                # BESIDE the boundary poll above, not instead of it: that one
                # is cheaper and stronger (one read per batch, and it stops
                # the build BEFORE the call is paid for). This one covers the
                # minutes inside a batch the boundary cannot see.
                should_cancel=should_cancel,
            )
            notes.append(f"## Part {index}\n{note}")
            done += len(batch)
            await on_progress(done)

        # `F-3`: the fold phase is the LONGEST unbroken run of provider calls
        # in the whole build, and until now it reported nothing and observed
        # nothing -- the last ~10 calls of a long document were invisible, the
        # interface sat at 99%, and Stop did nothing. Both hooks now go down
        # with it. The poll that used to stand here is gone rather than
        # duplicated: `_fold` polls before every call it makes, and its first
        # one is at exactly this point.
        async def _reduce_tick() -> None:
            """One progress write per fold step. The value is always the same
            (`advance` clamps at `total_chunks`, reached above), so what this
            moves is the row's `updated_at`, not its count -- reporting the
            reduce as a PHASE, not as a counter that has nothing left to
            count."""
            await on_progress(len(window))

        text = await self._fold(
            notes,
            lang=lang,
            summarizer=summarizer,
            on_tick=_reduce_tick,
            should_cancel=should_cancel,
        )
        return SummaryDraft(
            text=text, model=summarizer.model, source_chunks=len(window), truncated=truncated
        )

    async def translate(
        self,
        ctx: ExecutionContext,
        *,
        source: Summary,
        lang: SummaryLanguage,
        summarizer: ResolvedSummarizer,
        on_tick: PhaseHook = _noop_phase,
        should_cancel: CancelHook = _never_cancel,
    ) -> SummaryDraft:
        """P-44 (plan §4 step 20, §3.10): turn an already-built ``Summary``
        into the SAME text in another language, by reading the STORED row
        rather than the chunks it was originally built from.

        This is the reason a translation is cheap where a build is not: it
        spends at most one round trip over a few kilobytes of already-reduced
        Markdown, never a second map-reduce over the whole document's chunks.
        ``source`` is read by the caller (``BuildSummary``'s own claim step,
        the ``DocumentRepository.chunk_texts`` precedent) — this method never
        touches a repository, the same statelessness ``execute`` keeps.

        **``source.lang == lang`` is reused verbatim, with NO provider
        call.** The stored text already IS what was asked for, so spending a
        translation on a no-op would pay for a byte-identical result — the
        same reasoning ``_a_short_full_summary_skips_the_reduce_round_trip``
        applies to a redundant reduce, applied here to a redundant call.

        ``source_chunks``/``truncated`` are carried over UNCHANGED in both
        branches, never recomputed: a translation reads no chunk of its own,
        so the only honest values are the ones the summary being translated
        already carries. A translated summary of a truncated source is still
        a summary of a truncated source, in every language it is ever asked
        for.

        **``on_tick``/``should_cancel`` (ب-7).** This was the one path in the
        module that emitted nothing and listened to nothing, and the reason
        recorded for it was true: a single round trip has no step boundary to
        observe anything at. ب-6 is what retires that reason — the poll now
        lives INSIDE the call, so a translation has the points it never had.
        A ``PhaseHook`` and not a ``ProgressHook`` because there is no count
        to carry: a translation reads no chunk of its own, and its job's
        ``total_chunks`` is the SOURCE summary's coverage, fixed at ``claim``.
        What the tick says is that the build is alive — which is exactly what
        ``_reduce_tick`` says one shape over, and exactly what the worker's
        heartbeat rides on.
        """
        del ctx  # see `execute`'s own docstring for why this stays in the signature

        if source.lang is lang:
            return SummaryDraft(
                text=source.text,
                model=source.model,
                source_chunks=source.source_chunks,
                truncated=source.truncated,
            )

        # ONE tick, BEFORE the call and BELOW the shortcut above.
        #
        # Before, for `_fold`'s reason stated in full there: reporting after
        # the call would leave the only call this path makes as an unannounced
        # silence of its own full length, which is precisely the silence worth
        # announcing. Below the shortcut, because a same-language
        # "translation" spends no call and so has no silence to announce --
        # ticking there would report a phase that never happens.
        #
        # No poll of `should_cancel` stands here, unlike `_fold`'s boundaries.
        # A boundary poll earns its read by saving the call that follows it,
        # and this one could not: it would read a row the caller's own `claim`
        # wrote moments earlier, in the same handler, with nothing in between
        # that could have changed it.
        await on_tick()

        text = await self._call(
            system=_TRANSLATE_SYSTEM,
            user=source.text,
            lang=lang,
            summarizer=summarizer,
            max_tokens=_REDUCE_MAX_TOKENS,
            step="translate",
            should_cancel=should_cancel,
        )
        return SummaryDraft(
            text=text,
            model=summarizer.model,
            source_chunks=source.source_chunks,
            truncated=source.truncated,
        )

    async def _fold(
        self,
        notes: Sequence[str],
        *,
        lang: SummaryLanguage,
        summarizer: ResolvedSummarizer,
        on_tick: PhaseHook = _noop_phase,
        should_cancel: CancelHook = _never_cancel,
        depth: int = 0,
    ) -> str:
        """P-41 (plan §4 step 17, §3.10): collapse ``notes`` into ONE final
        summary without ever handing a single call more than one batch's
        worth of them.

        ``_batched`` (already used to group source chunks into map calls)
        groups these SAME-shaped strings the same way, under
        ``_MAX_REDUCE_CHARS`` rather than the map budget: what ends this
        ladder is a call answering in ``_REDUCE_MAX_TOKENS``, so it is that
        call's room the grouping has to respect (`F-2`). Since `F-2` the
        character budget is also what SPLITS a group at all --
        ``_MAX_MAP_CHUNKS / _MAP_BATCH`` is 12 notes at most, always under
        ``_MAP_BATCH``, so the count bound can no longer be the one that
        bites here. When that grouping
        already fits everything in ONE group, the whole-document reduce
        happens directly -- this is the ordinary case for anything up to
        ``_MAP_BATCH`` map notes, and the ONLY extra cost over the old
        unconditional single reduce call. Otherwise each group is folded
        (``_FOLD_SYSTEM``) into one coarser note and the SAME operation
        recurses one level up, until it fits or ``_MAX_FOLD_DEPTH`` is
        reached -- whichever comes first. At the cap, whatever notes remain
        go into ONE final call regardless of size: the provider's own
        context-window error there is a better failure than a pipeline that
        keeps folding past its own stated guarantee (``_batched``'s own
        reasoning for a single oversized chunk, carried here for notes).

        **`F-3` (plan §3.4): both hooks reach here, and the placement is the
        point.** ``should_cancel`` is polled and ``on_tick`` fired
        IMMEDIATELY BEFORE every provider call, never after -- so the longest
        stretch in which this pipeline says nothing is exactly ONE call,
        whatever the shape of the ladder above it. After-the-call reporting
        would leave the final reduce, the single longest call in the build,
        as an unannounced silence of its full duration; and a Stop pressed
        during it could not be observed at all, because the poll would come
        only once the call it was meant to prevent had already been paid for.
        """
        grouped = _batched(list(notes), max_chars=_MAX_REDUCE_CHARS)
        if len(grouped) == 1 or depth >= _MAX_FOLD_DEPTH - 1:
            if await should_cancel():
                raise SummaryBuildCancelled
            await on_tick()
            return await self._call(
                system=_REDUCE_SYSTEM,
                user="\n\n".join(notes),
                lang=lang,
                summarizer=summarizer,
                max_tokens=_REDUCE_MAX_TOKENS,
                step="reduce",
                # The single longest call in the whole build, and the last:
                # after it there is no boundary left to observe a Stop at.
                should_cancel=should_cancel,
            )
        # A loop rather than the comprehension this was: a comprehension has
        # nowhere to put the poll, and the poll between groups is the whole of
        # what makes Stop work during a long reduce.
        folded: list[str] = []
        for group in grouped:
            if await should_cancel():
                raise SummaryBuildCancelled
            await on_tick()
            folded.append(
                await self._call(
                    system=_FOLD_SYSTEM,
                    user="\n\n".join(group),
                    lang=lang,
                    summarizer=summarizer,
                    max_tokens=_MAP_MAX_TOKENS,
                    step="fold",
                    should_cancel=should_cancel,
                )
            )
        return await self._fold(
            folded,
            lang=lang,
            summarizer=summarizer,
            on_tick=on_tick,
            should_cancel=should_cancel,
            depth=depth + 1,
        )

    async def _call(
        self,
        *,
        system: str,
        user: str,
        lang: SummaryLanguage,
        summarizer: ResolvedSummarizer,
        max_tokens: int,
        step: str,
        should_cancel: CancelHook = _never_cancel,
    ) -> str:
        """One provider round trip, STREAMED (ب-6).

        The language instruction rides on the SYSTEM message, not the user
        one: the user message is document text, and an instruction embedded in
        it is an instruction a document could imitate.

        ``step`` names WHICH call in the ladder this is, from the closed
        vocabulary in ``_STEPS``. It is carried for the two guards below and
        for nothing else: both of them report a fault whose only useful
        question is "where", and neither the system prompt (content, and a
        string no dashboard groups by) nor the caller's line number answers
        it. One parameter buys both.

        **The text this returns is what ``complete()`` returned; what changed
        is when control comes back.** ``LLMProvider.stream`` was defined on
        the port and implemented by all five adapters, and this path never
        used it — so a call, once started, held the pipeline until it
        finished, and a Stop pressed a second in could not be noticed for up
        to ``self._timeout_s``. The answer arriving in pieces is what creates
        points INSIDE one round trip at which this pipeline can look up.

        ``should_cancel`` defaults to ``_never_cancel`` so the two properties
        the guards below pin stay testable in isolation, and every caller in
        this module passes the real hook down.
        """
        messages = [
            LlmMessage(role="system", content=f"{system}\n\n{_LANGUAGE_INSTRUCTION[lang]}"),
            LlmMessage(role="user", content=user),
        ]
        params = LlmParams(model=summarizer.model, temperature=_TEMPERATURE, max_tokens=max_tokens)

        parts: list[str] = []
        finish: str | None = None
        now = monotonic()
        deadline = now + self._timeout_s
        next_poll = now + _CANCEL_POLL_INTERVAL_S

        chunks = summarizer.provider.stream(messages, params, summarizer.api_key)
        try:
            while True:
                now = monotonic()
                # `finish is None` -- once the answer is COMPLETE, stop asking.
                # A stream can carry frames PAST its terminal chunk (the
                # OpenAI adapter reads a trailing usage frame there), and a
                # Stop observed in that window would throw away an answer that
                # had already fully arrived: the call paid for in full and
                # nothing stored. Cancelling is for work still ahead.
                if finish is None and now >= next_poll:
                    next_poll = now + _CANCEL_POLL_INTERVAL_S
                    if await should_cancel():
                        raise SummaryBuildCancelled
                try:
                    chunk = await _next_chunk_before(chunks, deadline)
                except StopAsyncIteration:
                    break
                parts.append(chunk.delta)
                if chunk.finish_reason is not None:
                    # The port puts it on the TERMINAL chunk, so this holds
                    # the last one that carried it -- `LlmResult.
                    # finish_reason` reached by another route, which is what
                    # leaves the `length` guard below meaning what it meant.
                    finish = chunk.finish_reason
        finally:
            await _close_quietly(chunks)

        if finish == "length":
            # `F-10`: the answer stopped because it ran out of room, not
            # because it was finished -- a summary cut mid-sentence, stored as
            # though it were whole. MEASUREMENT ONLY in this wave: how often
            # this happens, and at which of the three ceilings, is what
            # decides whether the fix is a second flag on the row or simply a
            # larger number, and neither is worth six layers of migration for
            # a rate nobody has counted yet.
            #
            # `truncated` on the draft is NOT this and must never be widened
            # to cover it: that flag says the INPUT was cut at
            # `_MAX_MAP_CHUNKS` ("I read the first part of the document"),
            # and it is published in `SummaryOut` and spoken aloud in
            # `delivered_summary_text` with exactly that meaning. This says
            # the OUTPUT was cut ("I wrote half an answer"). One sentence
            # cannot honestly carry both.
            #
            # A count and a path name from a closed vocabulary -- 10 §10: not
            # a character of the document, of the prompt, or of the reply.
            log.warning(
                "summarization.output_truncated",
                extra={"step": step, "max_tokens": max_tokens},
            )

        text = "".join(parts).strip()
        if not text:
            # `F-9`: phrased for the person who will read it on the failed
            # job, not for a log -- this sentence is all they will get, the
            # same reason `execute`'s "no indexed text to summarise" above is
            # written the way it is.
            #
            # Raised HERE rather than in the four callers: one guard covers
            # every step of every shape, where four would drift. A plain
            # `ValueError` because the whole delivery path already exists for
            # one -- `BuildSummary.run` catches it into
            # `SummaryAttempt(error=...)`, `finalize` fails the job with it,
            # and `SummaryJobOut.error` publishes it.
            #
            # An empty MAP note fails the whole build rather than being
            # skipped, deliberately: skipping it would produce a summary of a
            # document with a silent hole in it -- twenty sections nothing was
            # read about, and no way for the reader to know. A job that failed
            # for a stated reason is more honest than a summary that looks
            # complete and is not.
            raise ValueError(f"the model returned an empty response at the {step} step")
        return text


def _join(chunks: Sequence[str]) -> str:
    return "\n\n".join(chunks)


async def _next_chunk_before(chunks: AsyncIterator[LlmChunk], deadline: float) -> LlmChunk:
    """``anext(chunks)``, never past ``deadline``.

    ب-6: the timeout is re-armed PER PULL from one shared deadline, rather
    than declared once around the whole loop. Both give the same TOTAL bound
    on a call — they differ only in what gets cancelled when it expires. A
    timeout spanning the loop keeps ticking through the loop's body too, so
    an expiry landing while ``_call`` is awaiting the caller's
    ``should_cancel`` would cancel THAT: a database read, mid-query, on the
    very session the resulting failure is about to be written through. Here
    the cancellation can only ever land on the provider's own await, which is
    the one await that opted into it.

    ``agents.orchestrator._next_before`` is the same reasoning applied to an
    agent stream and states it at length; it cannot be imported from here
    (contract 3 — an application layer never imports ``app.agents``), so the
    rule is restated rather than shared.

    Raises ``TimeoutError`` when the budget is spent, including one that
    expired between pulls, and lets ``StopAsyncIteration`` fly for a stream
    that ends in time.
    """
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    async with asyncio.timeout(remaining):
        return await anext(chunks)


async def _close_quietly(chunks: AsyncIterator[LlmChunk]) -> None:
    """Best-effort ``aclose``, so the adapter's own ``async with`` — the HTTP
    response the stream is reading — unwinds NOW rather than whenever the
    generator happens to be collected.

    ب-6 is what makes this matter. Before it a call either returned or
    raised, and there was never a half-read response to leave behind; now
    ``_call`` abandons a stream mid-body BY DESIGN, on every Stop pressed
    during a call and on every per-call timeout.

    Tolerant of an iterator with no ``aclose`` — the port promises
    ``AsyncIterator``, not ``AsyncGenerator`` — and of a close that itself
    fails: cleanup must never mask the cancellation or timeout the caller is
    already on their way to seeing. ``asyncio.CancelledError`` is a
    ``BaseException`` and so passes through ``suppress(Exception)``
    untouched, which is what keeps this from swallowing a real cancellation.
    The ``orchestrator._close_quietly`` precedent.
    """
    aclose = getattr(chunks, "aclose", None)
    if aclose is None:
        return
    with suppress(Exception):
        await aclose()


def _glance_sample(readable: Sequence[str]) -> tuple[str, int]:
    """P-43 (plan §4 step 19, §3.10): up to ``_OVERVIEW_CHUNKS`` chunks
    spread evenly ACROSS ``readable``, not its first ``_OVERVIEW_CHUNKS`` --
    replacing the old "first 8", a worthless glance for a document whose
    opening pages are a generic cover sheet or table of contents. Returns
    the joined sample text (non-adjacent picks separated by
    ``_GAP_MARKER``) and how many chunks were actually included, so the
    caller can report ``source_chunks``/``truncated`` exactly as it always
    has.

    Still ONE provider call regardless of document length: this function
    only picks POSITIONS, evenly spaced by index, and never makes a round
    trip itself. A document with at most ``_OVERVIEW_CHUNKS`` readable
    chunks is short enough that "sampling" it would just be reading all of
    it, so it is -- with no gaps, since nothing was actually skipped.

    **The sample spans the document END TO END**, first chunk to last. The
    obvious formula -- ``int(i * len / _OVERVIEW_CHUNKS)`` -- does not: its
    largest index is ``int(7 * len / 8)``, which never reaches ``len - 1``
    for any length past ``_OVERVIEW_CHUNKS``, so the document's LAST chunk
    was never read at any length. Worse, at exactly nine chunks it produces
    ``{0..7}`` -- the "first 8" this whole step exists to replace -- and
    stays visibly front-loaded through the low teens. Spacing across
    ``len - 1`` instead of ``len`` pins both ends and distributes the rest
    between them, which is what "across the document" has to mean for a
    conclusion to be a glance at the whole and not at its opening.
    """
    if len(readable) <= _OVERVIEW_CHUNKS:
        return _join(readable), len(readable)

    # Integer arithmetic with explicit rounding (`+ last // 2`), not float
    # division: the positions are an index grid, and this keeps them exact
    # at every length instead of depending on how a ratio happens to round.
    # `max(..., 1)` guards the denominator only against a hypothetical
    # `_OVERVIEW_CHUNKS = 1`, which would pick index 0 and be right to.
    span = len(readable) - 1
    last = max(_OVERVIEW_CHUNKS - 1, 1)
    indices = sorted({(i * span + last // 2) // last for i in range(_OVERVIEW_CHUNKS)})

    parts: list[str] = []
    previous: int | None = None
    for index in indices:
        if previous is not None and index != previous + 1:
            parts.append(_GAP_MARKER)
        parts.append(readable[index])
        previous = index
    return "\n\n".join(parts), len(indices)


def _batched(chunks: Sequence[str], *, max_chars: int = _MAX_BATCH_CHARS) -> list[list[str]]:
    """Group chunks into batches, breaking early on ``max_chars``.

    A batch is closed by whichever bound is reached first. The character
    guard can produce a batch of one — a single chunk longer than the whole
    budget — which is correct: the alternative is refusing to summarise a
    document because one of its chunks is large, and the provider's own
    context error is a better failure than a pipeline that declines to try.

    ``max_chars`` is a PARAMETER since `F-2` (plan §3.3) because the budget
    belongs to the CALL being built, not to this grouping: a map call answers
    in ``_MAP_MAX_TOKENS`` and a fold/reduce call in
    ``_REDUCE_MAX_TOKENS``, and that difference is most of what the two
    budgets are. It defaults to the map budget because the map loop is the
    caller this function was written for and the only one that wants the
    larger number; every reduce-sized caller passes ``_MAX_REDUCE_CHARS``
    explicitly, so a call site says which kind of call it is grouping for.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for chunk in chunks:
        if current and (len(current) >= _MAP_BATCH or size + len(chunk) > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(chunk)
        size += len(chunk)
    if current:
        batches.append(current)
    return batches
