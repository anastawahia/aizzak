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

**Cancellation is checked between batches**, which is the only place it can
be: an LLM round trip in flight cannot be recalled, and pretending otherwise
would mean reporting a stop that had not happened. See ``SummaryJob.cancel``.

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

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.observability import get_logger
from app.framework.ports.llm_provider import LlmMessage, LlmParams
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
    """

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
            raise ValueError("this document has no indexed text to summarise")

        if kind is SummaryKind.OVERVIEW:
            sample, sampled = _glance_sample(readable)
            text = await self._call(
                system=_OVERVIEW_SYSTEM,
                user=sample,
                lang=lang,
                summarizer=summarizer,
                max_tokens=_OVERVIEW_MAX_TOKENS,
                step="overview",
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
        """
        del ctx  # see `execute`'s own docstring for why this stays in the signature

        if source.lang is lang:
            return SummaryDraft(
                text=source.text,
                model=source.model,
                source_chunks=source.source_chunks,
                truncated=source.truncated,
            )

        text = await self._call(
            system=_TRANSLATE_SYSTEM,
            user=source.text,
            lang=lang,
            summarizer=summarizer,
            max_tokens=_REDUCE_MAX_TOKENS,
            step="translate",
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
    ) -> str:
        """One provider round trip.

        The language instruction rides on the SYSTEM message, not the user
        one: the user message is document text, and an instruction embedded in
        it is an instruction a document could imitate.

        ``step`` names WHICH call in the ladder this is, from the closed
        vocabulary in ``_STEPS``. It is carried for the two guards below and
        for nothing else: both of them report a fault whose only useful
        question is "where", and neither the system prompt (content, and a
        string no dashboard groups by) nor the caller's line number answers
        it. One parameter buys both.
        """
        messages = [
            LlmMessage(role="system", content=f"{system}\n\n{_LANGUAGE_INSTRUCTION[lang]}"),
            LlmMessage(role="user", content=user),
        ]
        params = LlmParams(model=summarizer.model, temperature=_TEMPERATURE, max_tokens=max_tokens)
        result = await summarizer.provider.complete(messages, params, summarizer.api_key)

        if result.finish_reason == "length":
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

        text = result.content.strip()
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
