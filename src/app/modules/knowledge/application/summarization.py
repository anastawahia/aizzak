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
which travels all the way onto the stored row and into the API response. The
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

**``refine`` (P-45, plan §4 step 21, §3.10, optional) is a FOURTH shape: an
alternative to ``execute``'s map-reduce for ``full`` summaries, not a
replacement for it.** Map-reduce reads every batch in isolation and only
combines the notes afterwards; refine reads batches in order and keeps one
summary alive throughout, rewriting it as each new batch arrives, so a later
part can change how an earlier one reads the way a person reading in order
would notice that -- at the cost of a call sequence that cannot run in
parallel the way map-reduce's independent batches can. Both are exposed
side by side; nothing here prefers one over the other.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.llm_provider import LlmMessage, LlmParams
from app.modules.knowledge.domain.entities import Summary
from app.modules.knowledge.domain.value_objects import SummaryKind, SummaryLanguage
from app.modules.knowledge.ports.summarization import ResolvedSummarizer

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
# rather than the document, large enough that a 200-chunk file is ~34 calls
# and not 200.
_MAP_BATCH = 6

# The ceiling on a `full` map. 40 map calls plus one reduce is already the
# most expensive request in the API; beyond this the pipeline summarises the
# prefix and SAYS SO (`truncated`) rather than silently spending more.
_MAX_MAP_CHUNKS = _MAP_BATCH * 40

# A second guard on the same thing from the other end: chunk sizes are set by
# the chunker, but a batch of unusually long ones must still not build a
# prompt no context window accepts. Characters, not tokens, because this is a
# guard rail and not an accounting -- a tokeniser here would be a dependency
# bought to make an approximation look precise.
_MAX_BATCH_CHARS = 24_000

# P-41 (plan §4 step 17, §3.10): the recursion cap on `_fold`. Without it, a
# 40-batch `full` build hands ONE reduce call all 40 map notes at once --
# roughly 40 * `_MAP_MAX_TOKENS` = 24k tokens in a SINGLE request, which is
# outright failure (not graceful degradation) against an 8k-window model.
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

# P-45 (plan §4 step 21, §3.10, optional): `refine`'s first call has no prior
# summary to work from -- its first batch is written exactly the way
# `execute`'s own single-batch shortcut writes one, from `_FULL_SYSTEM`
# directly. Naming it separately (rather than reusing `_FULL_SYSTEM` inline)
# keeps `refine`'s call sequence self-describing at the call site.
_REFINE_FIRST_SYSTEM = _FULL_SYSTEM

# `refine`'s every call AFTER the first: unlike `_MAP_SYSTEM` (which reads one
# part in isolation and writes terse NOTES about it) this reads the RUNNING
# summary together with the next part and rewrites the summary whole, so the
# result at every step is already a complete, readable summary of everything
# seen so far -- never notes awaiting a separate reduce.
_REFINE_SYSTEM = (
    "You are maintaining a running summary of a document as more of it "
    "arrives, one part at a time. You will be given the summary written so "
    "far and the next part of the document. Rewrite the summary so it "
    "folds in the new part, in well-structured Markdown with short headings "
    "and paragraphs, covering everything read so far in proportion, keeping "
    "figures and names exact, and stating nothing the summary-so-far or the "
    "new part does not support."
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


async def _noop_progress(_done: int) -> None:
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
        if len(batches) == 1:
            text = await self._call(
                system=_FULL_SYSTEM,
                user=_join(batches[0]),
                lang=lang,
                summarizer=summarizer,
                max_tokens=_REDUCE_MAX_TOKENS,
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
            )
            notes.append(f"## Part {index}\n{note}")
            done += len(batch)
            await on_progress(done)

        if await should_cancel():
            raise SummaryBuildCancelled

        text = await self._fold(notes, lang=lang, summarizer=summarizer)
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
        )
        return SummaryDraft(
            text=text,
            model=summarizer.model,
            source_chunks=source.source_chunks,
            truncated=source.truncated,
        )

    async def refine(
        self,
        ctx: ExecutionContext,
        *,
        chunks: Sequence[str],
        lang: SummaryLanguage,
        summarizer: ResolvedSummarizer,
        on_progress: ProgressHook = _noop_progress,
        should_cancel: CancelHook = _never_cancel,
    ) -> SummaryDraft:
        """P-45 (plan §4 step 21, §3.10, **optional**): the ``refine``
        alternative to ``execute``'s map-reduce, for ``full`` summaries only
        -- ``overview`` has no map/reduce/refine choice to make, it is
        already one call over a sample (``_glance_sample``), so this method
        takes no ``kind``.

        **Genuinely a different algorithm, not a reshaped map-reduce.**
        Map-reduce reads every batch in ISOLATION (``_MAP_SYSTEM`` never sees
        another batch's notes) and only combines them afterwards
        (``_fold``/``_REDUCE_SYSTEM``). Refine reads batches in ORDER and
        keeps exactly ONE artefact alive the whole time -- a complete summary
        that already covers everything read so far -- rewriting it with each
        new batch (``_REFINE_SYSTEM``) instead of writing notes about the
        batch alone. There is no reduce step because there is nothing left to
        combine: the last call's output already IS the finished summary.

        **The trade this buys and the one it spends are both real.** A
        document whose ending qualifies or reverses something its opening
        said is read the way a person reads it -- in order, each part able to
        change how the last part is understood -- where map-reduce's isolated
        notes cannot express that relationship until the reduce step, if it
        notices at all. What it spends is concurrency: map-reduce's batches
        are independent and could run in parallel; refine's calls cannot,
        because each one needs the last one's output. Neither is
        strictly better, which is the whole reason this is a CHOICE
        (P-45's own framing) and not a replacement for ``execute``.

        Bounded the same way ``execute`` is: ``_MAX_MAP_CHUNKS`` caps how much
        is read, and ``truncated`` says so on the returned draft exactly as it
        does there.
        """
        readable = [text for text in chunks if text.strip()]
        if not readable:
            raise ValueError("this document has no indexed text to summarise")

        window = readable[:_MAX_MAP_CHUNKS]
        truncated = len(readable) > len(window)
        batches = _batched(window)

        # Cancellation is checked before EVERY batch when there is more than
        # one, the `execute` map loop's own placement -- including the
        # first, unlike `translate`'s single call. A document that fits in
        # one batch skips the check entirely, the same single-batch shortcut
        # `execute` takes for the same reason: there is nothing after it to
        # cancel before.
        if len(batches) > 1 and await should_cancel():
            raise SummaryBuildCancelled

        running = await self._call(
            system=_REFINE_FIRST_SYSTEM,
            user=_join(batches[0]),
            lang=lang,
            summarizer=summarizer,
            max_tokens=_REDUCE_MAX_TOKENS,
        )
        done = len(batches[0])
        await on_progress(done)

        for index, batch in enumerate(batches[1:], start=2):
            if await should_cancel():
                raise SummaryBuildCancelled
            running = await self._call(
                system=_REFINE_SYSTEM,
                user=(
                    f"Summary so far:\n{running}\n\nPart {index} of {len(batches)}:\n{_join(batch)}"
                ),
                lang=lang,
                summarizer=summarizer,
                max_tokens=_REDUCE_MAX_TOKENS,
            )
            done += len(batch)
            await on_progress(done)

        return SummaryDraft(
            text=running, model=summarizer.model, source_chunks=len(window), truncated=truncated
        )

    async def _fold(
        self,
        notes: Sequence[str],
        *,
        lang: SummaryLanguage,
        summarizer: ResolvedSummarizer,
        depth: int = 0,
    ) -> str:
        """P-41 (plan §4 step 17, §3.10): collapse ``notes`` into ONE final
        summary without ever handing a single call more than one batch's
        worth of them.

        ``_batched`` (already used to group source chunks into map calls)
        groups these SAME-shaped strings the same way. When that grouping
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
        """
        grouped = _batched(list(notes))
        if len(grouped) == 1 or depth >= _MAX_FOLD_DEPTH - 1:
            return await self._call(
                system=_REDUCE_SYSTEM,
                user="\n\n".join(notes),
                lang=lang,
                summarizer=summarizer,
                max_tokens=_REDUCE_MAX_TOKENS,
            )
        folded = [
            await self._call(
                system=_FOLD_SYSTEM,
                user="\n\n".join(group),
                lang=lang,
                summarizer=summarizer,
                max_tokens=_MAP_MAX_TOKENS,
            )
            for group in grouped
        ]
        return await self._fold(folded, lang=lang, summarizer=summarizer, depth=depth + 1)

    async def _call(
        self,
        *,
        system: str,
        user: str,
        lang: SummaryLanguage,
        summarizer: ResolvedSummarizer,
        max_tokens: int,
    ) -> str:
        """One provider round trip.

        The language instruction rides on the SYSTEM message, not the user
        one: the user message is document text, and an instruction embedded in
        it is an instruction a document could imitate.
        """
        messages = [
            LlmMessage(role="system", content=f"{system}\n\n{_LANGUAGE_INSTRUCTION[lang]}"),
            LlmMessage(role="user", content=user),
        ]
        params = LlmParams(model=summarizer.model, temperature=_TEMPERATURE, max_tokens=max_tokens)
        result = await summarizer.provider.complete(messages, params, summarizer.api_key)
        return result.content.strip()


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
    """
    if len(readable) <= _OVERVIEW_CHUNKS:
        return _join(readable), len(readable)

    step = len(readable) / _OVERVIEW_CHUNKS
    indices = sorted({int(i * step) for i in range(_OVERVIEW_CHUNKS)})

    parts: list[str] = []
    previous: int | None = None
    for index in indices:
        if previous is not None and index != previous + 1:
            parts.append(_GAP_MARKER)
        parts.append(readable[index])
        previous = index
    return "\n\n".join(parts), len(indices)


def _batched(chunks: Sequence[str]) -> list[list[str]]:
    """Group chunks into map batches, breaking early on ``_MAX_BATCH_CHARS``.

    A batch is closed by whichever bound is reached first. The character
    guard can produce a batch of one — a single chunk longer than the whole
    budget — which is correct: the alternative is refusing to summarise a
    document because one of its chunks is large, and the provider's own
    context error is a better failure than a pipeline that declines to try.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for chunk in chunks:
        if current and (len(current) >= _MAP_BATCH or size + len(chunk) > _MAX_BATCH_CHARS):
            batches.append(current)
            current, size = [], 0
        current.append(chunk)
        size += len(chunk)
    if current:
        batches.append(current)
    return batches
