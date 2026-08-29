"""``RagAgent`` — answers from workspace knowledge (FR-20.1, 11 §3).

A thin stateless coordinator: ask the ``knowledge`` module to answer a
question (via the injected ``KnowledgeAccess`` DIP seam — never importing the
module), build the prompt, and stream the LLM answer as ``token`` events,
ending with a ``final`` carrying the assembled text + citations. It reaches
ports ONLY through ``self.deps`` and imports NO other agent, module, or
infrastructure — so ``agents-independent`` / ``agents-no-api-no-infra`` hold
trivially.

**Intent routing (retrieval plan §3.4/§4 row 11, ``P-21``, س-16 = أ):** the
one call this agent makes is ``self.deps.knowledge.answer(...)``, not
``retrieve``. Classifying the question and dispatching it — SUMMARIZE_DOC to
the module's ``RequestSummary``, CONTENT to its ``RetrieveContext`` — happens
INSIDE the knowledge module, where both routes already live. That is what
keeps this agent's declared convention intact (ح-11, §6 risk 7): classifying
here would mean importing the classifier and then holding a second seam for
whichever route it picked. When the summarisation route ran, ``run`` yields a
short receipt and never calls the LLM (see ``_summary_queued_answer``);
otherwise the routed chunks feed the normal synthesis path below, exactly as
``retrieve``'s did.

**The clarification question (retrieval plan §3.5/§4 row 14, ``P-04``, س-18 =
أ):** when the module could not decide WHICH file a summarisation question
named, it hands back the candidate names, and this agent asks the user —
«أيّ ملفّ تقصد؟» followed by the names — as ORDINARY ANSWER TEXT on the
existing ``token``/``final`` pair (see ``_clarification_question``). No new
event type and no change to the streaming contract: the structured
``clarification`` event is out of scope by س-18 (plan §7), and a client that
knows nothing about file resolution renders this correctly by doing nothing.
The LLM is not called here either — every word of it comes from the module's
list plus one fixed opening line.

**Citations (retrieval plan §3.2/§4 row 3, ``P-32``):** the ``final`` event's
``citations`` is a list of ``{document_id, file_name, page, chunk_id}``
objects — enough for a client to render and act on a source without a second
round trip — not the bare ``chunk_id`` UUID string it used to be.
``file_name``/``page`` are carried straight through as ``null`` when the
retrieved chunk itself has none (see ``_citation``).

**Trust gate + honest fallback (retrieval plan §3.3/§4 row 5, ``P-33``):**
when a knowledge seam IS wired and retrieval genuinely comes back with zero
chunks, ``run`` never calls the LLM at all — it yields a fixed, honest
"I don't have enough information" answer instead. Before this, the "most
dangerous gap in the whole file" (§3.3, quoted verbatim) was that the path
fell through to bare ``SYSTEM_PROMPT`` with no context, and the model would
answer from its OWN parametric knowledge as though it were sourced from the
user's documents — silent hallucination presented as a sourced fact. س-22 = أ
closes only this EXPLICIT zero-chunks case, with NO numeric confidence
threshold: ``RetrievalResult.best_dense_score``/``best_bm25_score`` (step 4,
``P-28``) are not consumed here, and "weak chunks" stays an accepted open
risk until an evaluation set exists (§6 risk 2). See ``_fallback_answer`` /
``run`` for the branch itself.

**Corpus awareness (retrieval plan §3.6/§4 row 6, ``P-36``, decision س-23 =
ج):** ``_corpus_header`` builds this workspace's file-name header — the names
the module hands over (however many the deployment shows), then a
number-agreeing "N more files" tail — and it is present on BOTH paths, never
one or the other. On the NORMAL synthesis path it is prepended to the SYSTEM
message (``_messages``), invisible to the user but read by the model, so a
question like «كم ملفًا لديك؟» ("how many files do you have?") — which,
excluding ``METADATA``,
routes to plain ``CONTENT`` and usually finds no matching chunks — can still
be answered honestly instead of tripping a dry, uninformative fallback. On
the FALLBACK path above, where the LLM is never called at all, the same
header is prepended directly to the user-visible ``fallback`` text itself:
the only way that branch can reflect what the corpus holds is to say so in
the sentence the user actually reads. One shared builder, so the two paths
can never describe two different corpora.

**Answer measurements (retrieval plan §3.11/§4 row 17, ``P-29``, س-25 = أ):**
``run`` emits ONE ``rag_agent.answer`` record per turn — ``path`` ·
``retrieval_attempted`` · ``context_nodes`` · ``fallback`` · ``llm_ms`` ·
``total_ms`` · ``error_type`` (see ``_log_answer``). ``llm_ms`` lives HERE and
nowhere else: the knowledge module's own ``knowledge.retrieval`` record
measures retrieval, but the provider stream is this agent's, and the no-LLM
branches below are the only place that can honestly report ``null`` for it.
Nothing of the answer, the question or the documents is logged — counts,
durations, fixed path names and one exception CLASS name only
(10-code-standards §10) — and none of it reaches the streaming contract,
which still carries exactly ``token`` and ``final`` (س-25 = أ, plan §7).

**The الموجة 1 guards (``docs/rag-agent-scenarios-implementation-plan.md``
§3):** six gaps the scenario review found, all of them closed in this file and
its test alone — no seam widened, no contract touched. ب-1 refuses to emit an
empty ``final``, which the orchestrator would otherwise serialise into the
user's thread as raw JSON. ب-2 wraps the corpus listing so a cosmetic header
can no longer sink an answer that was already retrieved and paid for. ب-3 adds
the fifth branch: a summarisation whose target could not be identified asks
WHICH FILE — with the space's listing beneath it — instead of falling silently
into the content route and apologising. ب-4أ turns a refused summary build
from a technical error event into a neutral sentence. ب-5 makes a failing turn
emit a record instead of a silence. ب-6 warns once when a turn runs with no
space, the safe-but-silent degraded path (ق-7). Each carries its own ``path``
name so none of them can hide inside a successful one.

**Scope note (4.6):** the concrete ``deps`` (a ``ProviderResolver``-resolved
``ResolvedLLM``, the real ``KnowledgeRetrieval``) is wired by the orchestrator
at 4.7; usage enforcement/capture, conversation persistence (D-12) and SSE/WS
transport are the orchestrator's too (11 §8.1/§8.2). Here the agent is exercised
against fake ports (11 §9). Tool-calling stays out of the stream in v1 (the
recorded ``LlmChunk``-has-no-tool-field port limit); this agent retrieves
directly rather than via a tool.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from app.agents.rag_agent.manifest import METADATA
from app.agents.rag_agent.prompts import SYSTEM_PROMPT
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.deps_ports import (
    KnowledgeAccess,
    ResolvedLLM,
    RetrievedChunkView,
    RoutedAnswerView,
)
from app.framework.agent_runtime.source_label import format_context_block
from app.framework.errors import AppError, ConflictError, ValidationError
from app.framework.observability import get_logger
from app.framework.ports.llm_provider import LlmMessage, LlmParams
from app.framework.types import Uuid

_logger = get_logger(__name__)

# Retrieval plan §3.3/§4 row 5 (``P-33``) — the two fixed fallback sentences,
# picked by whether the query itself contains any Arabic-script character.
# This is NOT a new i18n mechanism (no catalog, no locale files): it is the
# same "match the question's language" convention ``SYSTEM_PROMPT`` already
# states for the LLM ("Answer in the same language as the question"), applied
# here as a plain presence check because this branch never calls the LLM to
# do that matching itself. It deliberately does not reuse
# ``knowledge.domain.tokenization.detect_language`` (the ratio-based
# Arabic/English split the knowledge module already implements): that would
# cross the import line ``rag_agent`` declares above (and ح-11, §6 risk 7) —
# "imports nothing beyond ``self.deps`` plus this ``framework`` kernel" — and
# a binary pick between two fixed sentences does not need that sophistication.
_ARABIC_CHAR_RE = re.compile("[؀-ۿ]")
_FALLBACK_ANSWER_EN = "I don't have enough information in the workspace documents to answer that."
_FALLBACK_ANSWER_AR = "لا أملك معلومات كافية في مستندات مساحة العمل للإجابة عن هذا السؤال."

# Retrieval plan §3.4/§4 row 11 (`P-21`) — what the SUMMARIZE_DOC route says
# back. Two fixed sentences picked by the SAME `_ARABIC_CHAR_RE` presence
# check the fallback pair above uses; one language mechanism in this agent,
# never a second one. It reports that the build was ACCEPTED and does not
# promise the summary in this turn, because the build is a worker's job
# (`SummaryRequested` → `BuildSummary`) and nothing here waits for it.
_SUMMARY_QUEUED_EN = "A summary of that document is being prepared — it will be available shortly."
_SUMMARY_QUEUED_AR = "جارٍ إعداد ملخّص المستند المطلوب، وسيكون متاحًا بعد قليل."

# ب-7أ (scenarios plan §4, gap ف-2) — the same receipt for the turn where
# the module RESOLVED the build's target and said which file it is.
# One pair, differing from the one above in the name and in nothing
# else, so the two cannot drift into two different promises about
# when the summary arrives.
#
# The agent still resolves nothing: the name arrives across the seam
# (`RoutedAnswerView.summary_target_name`) already chosen by the
# resolver, so uttering it repeats the module's decision rather than
# asserting one. Without a name the pair above is used unchanged —
# never this one with a blank where the file should be.
#
# The name is INTERPOLATED into a template, never concatenated onto a
# prefix, and it is QUOTED: an Arabic sentence carrying a Latin file
# name is bidirectional text, and the marks are what keep a name like
# `budget-2025.xlsx` from running into the punctuation around it.
_SUMMARY_QUEUED_NAMED_EN = 'A summary of "{name}" is being prepared — it will be available shortly.'
_SUMMARY_QUEUED_NAMED_AR = "جارٍ إعداد ملخّص «{name}»، وسيكون متاحًا بعد قليل."

# ب-3 (خطة الفجوات §3، ف-5) — the fifth branch's question: a summarisation
# was asked and its TARGET is unknown. Two fixed sentences picked by the same
# `_ARABIC_CHAR_RE` presence check every other sentence in this agent uses —
# still one language mechanism, never a second one.
#
# It is a QUESTION, and that is the item: this case used to fall through to
# the content route, retrieve nothing and end in the fallback apology, so the
# most natural phrasing a user can reach for («لخّص لي هذا») produced the
# least useful answer the agent has. An apology is not a form of this reply —
# the agent knows exactly what was asked and is missing exactly one name.
#
# Unlike the receipt and the clarification, this one CARRIES the corpus
# header (see `run`): those two already name files, this one names none, and
# the space's listing is the menu that makes the question answerable.
_SUMMARY_TARGET_EN = "Which file would you like me to summarise?"
_SUMMARY_TARGET_AR = "أيّ ملفّ تريد تلخيصه؟"

# ب-4أ (خطة الفجوات §3، ف-7) — what a refused summary build says. The module
# raises `common.conflict` from TWO places under ONE code: a build already
# running for this key, and a document that was never indexed and holds no
# text to summarise at all. This agent cannot tell them apart, so the sentence
# is written to be TRUE OF BOTH: «تعذّر البدء الآن» is honest either way,
# where «ما زال قيد الإعداد» would be a flat lie on the second — nothing is
# being prepared for an unindexed document, and nothing will be.
#
# It is deliberately temporary. ب-4ب (الموجة 3) classifies the reason inside
# the module, where the distinction lives, and replaces this single neutral
# sentence with two exact ones. Until then a neutral truth beats a precise
# falsehood.
_SUMMARY_BLOCKED_EN = (
    "I couldn't start a summary of that file just now. If one is already being "
    "prepared, it will reach you in this conversation when it is ready."
)
_SUMMARY_BLOCKED_AR = (
    "تعذّر بدءُ تلخيص هذا الملفّ الآن. إن كان تلخيصٌ له قيد الإعداد فسيصلك في هذه المحادثة عند اكتماله."
)

# ب-3 — the module's `Intent.SUMMARIZE_DOC` value, as a LITERAL.
#
# Importing the enum would cross the line this agent's docstring and the
# `agents-independent` contract both draw (ق-1), and this is precisely the
# widening `RoutedAnswerView` was documented for: "`intent` is `str` here and
# a `StrEnum` there… an agent comparing against a string literal is reading
# the same value the module wrote". The copy is guarded by a drift test
# (`test_the_agents_intent_literal_matches_the_modules_enum`) rather than by
# hope: the test layer may import both sides, and that is where the two are
# compared.
_INTENT_SUMMARIZE_DOC = "summarize_doc"

# Retrieval plan §3.5/§4 row 14 (`P-04`, س-18 = أ) — the clarification
# question's opening line, again picked by the SAME `_ARABIC_CHAR_RE` check;
# the Arabic form is the plan's own wording («أيّ ملفّ تقصد؟»).
#
# It is ORDINARY ANSWER TEXT, and that is the decision, not an implementation
# detail: س-18 = أ chose text over a structured `clarification` event
# precisely because the event would change the streaming contract and need a
# UI that already understood it (plan §7). So this leaves on the same
# `token` + `final` pair every other answer uses, and a client that has never
# heard of file resolution renders it correctly by doing nothing.
#
# The candidates are listed one per LINE rather than inline as the plan's
# illustrative «أ / ب / ج»: a file name may itself contain a comma or a
# slash, and an inline separator would let two names read as three files —
# in the one sentence whose entire job is to keep the user from picking the
# wrong file.
_CLARIFY_FILE_EN = "Which file do you mean?"
_CLARIFY_FILE_AR = "أيّ ملفّ تقصد؟"
_CLARIFY_BULLET = "- "

# Retrieval plan §3.6/§4 row 6 (`P-36`, س-23 = ج) — the corpus-awareness
# header's display cap is NOT here, and this comment stands where it used to
# be (`_MAX_CORPUS_NAMES = 50`) because its absence is the point.
#
# It was kept as a plain module constant on the grounds that س-24's sweep
# names the RETRIEVAL knobs (`_W_DENSE` `_W_BM25` `_RRF_K` `_SEARCH_OVERFETCH`
# `_TOP_K`) and a DISPLAY cap is none of them. True about the number, beside
# the point about the address: whatever it tuned, it was a tuning number held
# by an AGENT — the one thing ح-11 says this agent does not do, and the one
# place `Settings` cannot reach, since an agent reads no configuration and
# imports nothing. Shortening a header was a code edit in the agents layer.
#
# So it moved to the module side in `_TOP_K`'s exact shape (plan row 18,
# `P-40`): `list_document_names` takes an OPTIONAL `limit`, this agent names
# none, and `Settings.retrieval.max_corpus_names` is resolved inside
# `ListDocumentNames`. §3.6 still fixes the shipped value at 50, with its
# ~500-token-per-request price named and accepted right there — nothing about
# the header changed, only who owns the number.

# The corpus-awareness header's four fixed strings (retrieval plan §3.6),
# picked by the SAME ``_ARABIC_CHAR_RE`` query-language check the fallback
# sentences above use — one language mechanism, reused, not a second one.
# ⚠️ **They name the SPACE, not the workspace, since س-32** (owner decision
# 2026-08-26). The header is rendered from `list_document_names(space_id=...)`,
# which walks one space's corpus — so «ملفّات مساحة العمل» would have been a
# sentence that counted one space's files and called them the workspace's. The
# user-visible half of a data-isolation change is still a data-isolation
# change: a label that overstates its scope teaches a user to expect files the
# search can never reach.
_CORPUS_EMPTY_EN = "There are no files in this space yet."
_CORPUS_EMPTY_AR = "لا توجد ملفّات في هذا الفضاء بعد."
_CORPUS_LABEL_EN = "Files in this space:"
_CORPUS_LABEL_AR = "ملفّات هذا الفضاء:"

# The Arabic overflow tail, in the FOUR forms Arabic number agreement
# (تمييز العدد) actually has rather than the one this header used to render
# for every count: «و 5 ملفًّا آخر» is correct from 11 to 99 and from nowhere
# else. One remaining file names no numeral at all («وملفّ آخر»); two name
# the dual, and no numeral either («وملفّان آخران»); 3-10 take the broken
# plural in the genitive («و 5 ملفّات أخرى»); 11 and up take the singular in
# the accusative («و 50 ملفًّا آخر»). User-visible text on an Arabic-first
# product, and on the FALLBACK path especially — where the answer is already
# an apology. Assembled by `_more_files_ar` below.
#
# The first two are WHOLE tails, conjunction included, because nothing comes
# between «و» and the noun; the last two are the noun ALONE, because the
# numeral does. And all four spell the word «ملفّ» with its shadda, the
# convention every other user-facing string here already keeps
# (`_CORPUS_LABEL_AR` «ملفّات هذا الفضاء», `_CLARIFY_FILE_AR` «أيّ ملفّ
# تقصد؟») — the old tail's «ملفًا» was the one exception, and one sentence
# that spells the same word two ways is exactly the sloppiness this row is
# about.
_CORPUS_MORE_AR_ONE = "وملفّ آخر"
_CORPUS_MORE_AR_TWO = "وملفّان آخران"
_CORPUS_MORE_AR_FEW = "ملفّات أخرى"
_CORPUS_MORE_AR_MANY = "ملفًّا آخر"
# The two boundaries of that agreement, named because they are GRAMMAR and
# not tuning: the dual is exactly two, and the genitive plural runs to ten.
_AR_DUAL = 2
_AR_PLURAL_MAX = 10

# The `path` field of the `rag_agent.answer` record (retrieval plan §3.11/§4
# row 17, `P-29`) — which of `run`'s four exits this turn took. It is what
# makes a `llm_ms` of `null` readable: three of the four never call the model
# at all, and without the name they would be indistinguishable from a
# provider that answered instantaneously.
_PATH_SYNTHESIS = "synthesis"
_PATH_FALLBACK = "fallback"
_PATH_SUMMARY_RECEIPT = "summary_receipt"
_PATH_CLARIFICATION = "clarification"
# The four the الموجة 1 guards added. Each is its own name for the reason the
# first four are: a path that folded into an existing one would stop being
# countable, and every one of these exists because a case was invisible in the
# measurements before it.
#
# `empty_completion` (ب-1) is the odd one and deliberately so — it is the ONLY
# no-answer path that reports a real `llm_ms`, because the model was called and
# did spend that time before returning nothing.
# `summary_target_unknown` (ب-3) and `summary_conflict` (ب-4أ) never call it.
# `error` (ب-5) is the exit that used to log nothing at all, which is why the
# turn failure rate had to be inferred from missing lines rather than read.
_PATH_EMPTY_COMPLETION = "empty_completion"
_PATH_SUMMARY_TARGET_UNKNOWN = "summary_target_unknown"
_PATH_SUMMARY_CONFLICT = "summary_conflict"
_PATH_ERROR = "error"
_MS_PER_SECOND = 1000


@dataclass(frozen=True, slots=True)
class _FixedReply:
    """A turn answered WITHOUT the model: one fixed sentence and the ``path``
    that names which of the five such exits produced it.

    The five (retrieval plan §3.4/§3.5/§3.3, plus ب-3 and ب-4أ of the gap
    plan): a queued summary's receipt · the clarification question · a
    summarisation whose target is unknown · a refused build · the trust-gate
    fallback. They are one TYPE and not five branches-with-their-own-emitter
    because they owe the identical two events on the identical contract (ق-4),
    and the way to make that identical rather than merely intended is to give
    them one emit site in ``_answer``.

    ``citations`` is not a field: a sentence this agent wrote itself cites
    nothing, on all five. ``fallback`` is ``True`` on exactly one — the trust
    gate — because that flag is §3.11's measurement of the gate firing, not a
    synonym for "no model was called".
    """

    text: str
    path: str
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class _Synthesis:
    """A turn that goes to the model: the prompt, and what the answer will be
    described by.

    ``chunks`` is carried for the citations and the ``context_nodes`` count;
    ``query`` and ``corpus_header`` for the one case where a synthesis turn
    still ends in a fixed sentence — ب-1's empty completion, which needs the
    same apology and the same header the trust gate would have used.
    """

    binding: ResolvedLLM
    query: str
    messages: list[LlmMessage]
    chunks: Sequence[RetrievedChunkView]
    corpus_header: str | None


@dataclass(slots=True)
class _TurnRecord:
    """The two facts ب-5's error record needs, and that only the turn's BODY
    can learn: when the turn started, and whether retrieval was ever attempted.

    ``run`` is a frame around ``_answer`` (see ``run``), so the ``except`` that
    logs a failed turn sits one call OUTSIDE the function that discovers those
    two things. A mutable carrier is how the frame reads what the body got as
    far as learning — the alternative was indenting the whole body under a
    ``try`` inside ``run``, which buys the same two values and costs the
    function's readability.

    ``retrieval_attempted`` starts ``False`` and MEANS it: a turn that failed
    before it could ask (a blank query, an unbound LLM) genuinely attempted no
    retrieval, so the default is the truth rather than a placeholder.

    Not frozen, and it is the only mutable value this agent holds — for one
    turn, on one stack, never shared. Everything else here stays the frozen
    carrier the kernel's convention asks for.
    """

    started: float
    retrieval_attempted: bool = False


class RagAgent(BaseAgent):
    """Retrieval-augmented Q&A over the workspace knowledge base."""

    metadata = METADATA

    async def initialize(self) -> None:
        # Stateless: nothing to preload in v1 (context is fetched per-request in
        # ``run``). Conversation/memory hydration is deferred to the orchestrator.
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        """One turn, wrapped in the record that ب-5 (خطة الفجوات §3، ف-14) owed
        the measurements.

        The body is ``_answer``; this is only the frame around it. A turn that
        RAISED used to emit no ``rag_agent.answer`` line at all, so the failure
        rate of this agent had to be inferred from the ABSENCE of records
        rather than read from their presence — and the reason a turn failed was
        nowhere at all. This logs one record and RE-RAISES: the item is
        measurement, not handling, and swallowing the exception would turn a
        fault into a silence, which is the very thing it is fixing.

        ``except Exception``, never ``BaseException``, and that IS the item's
        substance. An abandoned generator ends in ``GeneratorExit`` and a
        cancelled turn in ``CancelledError`` — both ``BaseException`` — so
        neither is caught here, and the guarantee ``_log_answer`` documents
        ("emitted from each of ``run``'s exits, never from a ``finally``")
        survives verbatim: a stream the reader walked away from still logs
        nothing. A ``finally`` would have broken it, which is why this is not
        one.
        """
        turn = _TurnRecord(started=time.perf_counter())
        try:
            async for event in self._answer(req, turn):
                yield event
        except Exception as exc:
            # Only the exception's CLASS NAME (ق-5): a message can carry a
            # document id or a fragment of the question — `ConflictError`'s
            # does — while a type name is a name and nothing else.
            self._log_answer(
                path=_PATH_ERROR,
                retrieval_attempted=turn.retrieval_attempted,
                context_nodes=0,
                fallback=False,
                llm_ms=None,
                started=turn.started,
                error_type=type(exc).__name__,
            )
            raise

    async def _answer(self, req: AgentRequest, turn: _TurnRecord) -> AsyncIterator[AgentEvent]:
        """The turn itself: decide what this reply IS (``_plan``), then emit it.

        The split is not tidying. Five of this agent's exits answer WITHOUT
        calling the model — a queued summary's receipt, a clarification
        question, a summarisation with no known target (ب-3), a refused build
        (ب-4أ) and the trust-gate fallback — and they all owe the identical
        pair of events on the identical contract (ق-4: ``token`` then
        ``final``, never a third type). Deciding in a plain function and
        emitting in ONE place is what makes that identical rather than merely
        intended: a new fixed reply is a new ``_FixedReply``, and there is no
        second emit site for it to diverge from.

        Only the synthesis path streams, so only it stays here.
        """
        plan = await self._plan(req, turn)
        if isinstance(plan, _FixedReply):
            # `llm_ms` is `None` and `context_nodes` is `0` for every one of
            # these: nothing was synthesised and nothing was given to a model.
            # `fallback` is `True` on exactly one of them — the trust gate —
            # and the `path` is what tells the other four apart in the logs.
            self._log_answer(
                path=plan.path,
                retrieval_attempted=turn.retrieval_attempted,
                context_nodes=0,
                fallback=plan.fallback,
                llm_ms=None,
                started=turn.started,
            )
            yield AgentEvent(type="token", data={"delta": plan.text})
            yield AgentEvent(type="final", data={"text": plan.text, "citations": []})
            return
        params = LlmParams(model=plan.binding.model)
        answer: list[str] = []
        # `llm_ms` (retrieval plan §3.11) — the provider stream's WALL time,
        # from the request to the last token consumed. Consumer backpressure
        # is inside it, unavoidably: the tokens are yielded onward as they
        # arrive (that is what streaming means), so no clock this agent can
        # read separates the model's time from its reader's.
        llm_started = time.perf_counter()
        stream = plan.binding.provider.stream(plan.messages, params, plan.binding.api_key)
        async for chunk in stream:
            if chunk.delta:
                answer.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        llm_ms = _elapsed_ms(llm_started)
        text = "".join(answer)
        citations: list[dict[str, str | int | None]] = [self._citation(c) for c in plan.chunks]
        path = _PATH_SYNTHESIS
        # `strip()`, not `if not text`: a reply of whitespace alone is as empty
        # as no reply at all, and reaches the orchestrator's JSON fallback by
        # exactly the same route.
        if not text.strip():
            # ب-1 (خطة الفجوات §3، ف-4) — the model streamed nothing. An empty
            # reply is not a different SHAPE of answer, it is the ABSENCE of
            # one, and letting it through wrote a literal
            # `{"citations": [...], "text": ""}` into the user's own thread:
            # `_turn_content` (the orchestrator) finds neither text nor
            # attachment on the `final` and serialises the payload as the
            # message body. That JSON fallback is correct and deliberate for
            # the media agents, whose `final` is structured and carries no text
            # at all — this agent's is textual, and an empty text on it is a
            # fault. The one who knows the difference is the agent, so the
            # guard lives here and not in the orchestrator.
            #
            # It is treated exactly as "no chunks": the same trust-gate
            # sentence and the same corpus header that branch would have
            # carried, so what the user reads is one thing whether it was
            # retrieval that fell silent or the model.
            text = self._with_corpus(self._fallback_answer(plan.query), plan.corpus_header)
            # An answer that was never written rests on nothing. Showing five
            # sources beneath an apology tells the user the apology is sourced.
            citations = []
            # Its own path, never `synthesis` — and `llm_ms` stays MEASURED,
            # because the model genuinely was called and genuinely spent that
            # time. That pairing (a real duration on a no-answer path) is what
            # makes this case countable instead of hiding inside a successful
            # one. `fallback` stays `False`: that flag means the trust gate
            # fired on zero chunks, and this turn HAD chunks — the path name is
            # what separates the two.
            path = _PATH_EMPTY_COMPLETION
            yield AgentEvent(type="token", data={"delta": text})
        self._log_answer(
            path=path,
            retrieval_attempted=turn.retrieval_attempted,
            context_nodes=len(plan.chunks),
            fallback=False,
            llm_ms=llm_ms,
            started=turn.started,
        )
        yield AgentEvent(type="final", data={"text": text, "citations": citations})

    async def _plan(self, req: AgentRequest, turn: _TurnRecord) -> _FixedReply | _Synthesis:
        """Route the question and decide what this turn answers WITH — a fixed
        sentence, or a prompt for the model.

        Everything that can end a turn without the LLM returns a
        ``_FixedReply`` from here; the one path that streams returns a
        ``_Synthesis``. No event is emitted in this function, which is what
        lets the five fixed replies share one emit site in ``_answer``.
        """
        query = self._query(req)
        binding = self.deps.llm
        if binding is None:
            # A wiring bug, not a user error: RAG cannot run without an LLM.
            # Surfaced as the executor's safe error shape (the web_search-tool
            # precedent: registered-but-unwired ⇒ 500, never a silent empty answer).
            raise AppError(detail="rag_agent has no LLM bound", code="common.internal", status=500)
        # Knowledge is optional-degrading: with no retrieval seam the agent still
        # answers from the model alone (and cites nothing) rather than failing.
        # BE-RAG-005 — the thread's pinned retrieval scope, passed straight
        # through. `()` on the bundle means UNSCOPED, and it is translated to
        # `None` here rather than forwarded as an empty sequence: one layer
        # down, an empty list means "a scope that resolved to nothing" and
        # legitimately retrieves nothing, which is the opposite of what an
        # un-pinned thread wants.
        scope = self.deps.knowledge_scope or None
        # A LOCAL binding, not repeated `self.deps.knowledge` reads: mypy
        # narrows `is not None` reliably only against a plain local variable,
        # and this function needs that narrowing twice (retrieval, then the
        # corpus header below) rather than once.
        knowledge = self.deps.knowledge
        # س-32 (owner decision 2026-08-26) — the space this turn lives in, put
        # on the bundle by the orchestrator from the turn's own thread. A LOCAL
        # binding for `knowledge`'s reason: mypy narrows `is not None` against a
        # plain local, and the module's seam takes a non-nullable `space_id`.
        #
        # ⚠️ **No space ⇒ no retrieval.** `None` here is not "search
        # everything", which is exactly what this line used to do by passing
        # `space_id=None` down: it is "this turn's space is unknown", and the
        # only honest answer to an unknown boundary is to stay inside it. The
        # turn degrades to the SAME shape as a deployment with no knowledge
        # seam wired at all — a plain LLM answer with no citations and no
        # corpus header — rather than to an answer drawn from every space in
        # the workspace. It is reachable only on the orchestrator's degraded
        # path (no conversations seam) or for a thread that has since gone, and
        # both of those are wiring facts, not something a caller can ask for.
        space_id = self.deps.space_id
        self._warn_if_unscoped(req, knowledge, space_id)
        # Retrieval plan §3.3/§4 row 5 (`P-33`) — this flag is what tells the
        # trust gate below apart from the "knowledge is optional-degrading"
        # mode just above: a caller with NO knowledge seam wired at all never
        # attempted retrieval, so there is no "retrieval result" to gate on —
        # that stays a plain LLM answer, exactly as
        # `test_without_knowledge_still_answers_with_no_citations` pins. A
        # caller that DID wire a seam and got zero chunks back is the case the
        # gate exists for.
        #
        # A seam wired but no space known is "never attempted" too, and for the
        # same reason it is for an absent seam: nothing was searched, so there
        # is no zero-chunk result for the gate to speak about.
        #
        # It is written onto `turn` rather than kept as a local because ب-5's
        # error record is emitted one frame up, in `run`, out of whatever this
        # turn had managed to learn before it failed.
        turn.retrieval_attempted = knowledge is not None and space_id is not None
        routed: RoutedAnswerView | None = None
        if knowledge is not None and space_id is not None:
            try:
                # Retrieval plan §3.4/§4 row 11 (`P-21`, س-16 = أ) — ONE call,
                # and it is `answer`, not `retrieve`: the question is classified
                # and dispatched INSIDE the knowledge module, so this agent
                # still imports nothing and still reaches everything through
                # `self.deps` (ح-11, §6 risk 7). The arguments are the ones
                # `retrieve` took, unchanged.
                #
                # `k` is NOT passed (retrieval plan §4 row 18, `P-40`, س-24 =
                # أ). It used to be `_TOP_K = 5`, a retrieval tuning number
                # held by an agent — the one thing ح-11 says this agent does
                # not do, and the one place `Settings` could never reach
                # because an agent reads no configuration and imports nothing.
                # Omitting it asks the module for the DEPLOYMENT's configured
                # `k` (`Settings.retrieval.default_k`), which is the same 5
                # today and is now movable without touching this file.
                #
                # ✅ Spaces plan step 8, closed by س-32: the space now arrives
                # on `AgentDeps` and is named here. It was the second of the
                # two call sites the port's docstring listed as owing one;
                # `POST /knowledge/search` was the first.
                routed = await knowledge.answer(
                    self.ctx,
                    query,
                    file_ids=scope,
                    space_id=space_id,
                    # `F-7` — the thread this turn belongs to, passed through and
                    # never read here. A SUMMARIZE_DOC route queues a build that
                    # finishes long after this generator has returned, and this is
                    # what lets the finished text reach the thread instead of
                    # waiting for someone to poll the summary route. `None` on a
                    # turn that opens no thread is a real value, and the module
                    # reads it as "nowhere to deliver".
                    conversation_id=req.conversation_id,
                )
            except ConflictError:
                # ب-4أ (خطة الفجوات §3، ف-7) — `RequestSummary` refuses a second
                # build for a key one is already running, and that refusal used
                # to travel up as an UNTRANSLATED `AppError`: the executor
                # rendered it as a technical error event, so a user who asked
                # for a summary twice was shown a fault instead of an answer.
                # The original decision (wording belongs to whoever displays)
                # is right — and the thing displaying here IS this agent.
                #
                # `ConflictError` ALONE, never the general `AppError`: a broken
                # store is not a message for a user, and a `NotFoundError` on a
                # deleted document is a different state deserving a different
                # sentence. The catch wraps THIS CALL only, never the body —
                # see `_fetch_corpus_header` for the same rule stated for the
                # one other call that is allowed to fail quietly.
                return _FixedReply(self._summary_blocked_answer(query), _PATH_SUMMARY_CONFLICT)
        if routed is not None and routed.summary_job_id is not None:
            # The SUMMARIZE_DOC route ran and queued a build (retrieval plan
            # §3.4). There is nothing to synthesise and nothing to cite: the
            # summary is produced by a worker and read back through the
            # summary routes, so the honest thing this turn can say is that
            # the build was accepted. The LLM is never called, exactly as on
            # the fallback branch below.
            #
            # The corpus-awareness header is deliberately NOT prepended here,
            # and it is not fetched either. س-23 = ج puts the header on the
            # two ANSWERING paths — the one that answers from chunks and the
            # one that admits it has none — because the header is what keeps
            # "I don't know" from being uninformative. This branch is a
            # receipt for an action on a document the caller ALREADY named;
            # listing the workspace's files back at them would answer a
            # question nobody asked.
            #
            # ب-7أ/ب-7ب (ف-2) — and the receipt NAMES the document
            # now, when the module sent a name. It is the module's
            # resolution being read back, not a name lifted off the
            # query: see `_summary_queued_answer`. Without one the
            # sentence is exactly what it was.
            return _FixedReply(
                self._summary_queued_answer(query, routed.summary_target_name),
                _PATH_SUMMARY_RECEIPT,
            )
        if routed is not None and routed.clarification_options:
            # Retrieval plan §3.5/§4 row 14 (`P-04`, س-18 = أ) — the module
            # resolved the question's file name to SEVERAL documents (or to
            # one it was not confident about) and refused to choose. The
            # honest answer this turn is a question back, and it travels as
            # ORDINARY ANSWER TEXT on the same two events every other answer
            # uses: no new event type, no change to the streaming contract.
            #
            # The LLM is never called, exactly as on the receipt branch above
            # and the fallback branch below: the sentence is built from the
            # names the module handed over, so there is nothing for a model
            # to improvise — and asking one to phrase the question could let
            # it drop, merge or invent a candidate, which is the failure
            # (§3.5) this whole path exists to prevent.
            #
            # No corpus-awareness header either, for the receipt branch's
            # reason: this answer already names files, and appending the
            # whole workspace listing under a question about three specific
            # candidates would bury the choice it is asking the user to make.
            question = self._clarification_question(query, routed.clarification_options)
            return _FixedReply(question, _PATH_CLARIFICATION)
        # Retrieval plan §3.6/§4 row 6 (`P-36`, س-23 = ج) — the corpus header
        # is fetched whenever retrieval was attempted at all, so it is ready
        # for BOTH remaining branches: the trust-gate fallback (prepended to
        # the user-visible text) and the normal synthesis path (prepended to
        # the system prompt via `_messages`).
        #
        # ب-3 moved this line ABOVE the summarisation-without-a-target branch
        # that follows it. There the listing is not decoration but the ANSWER —
        # it is the menu the user picks a file from — so the fetch has to have
        # happened by the time that branch runs. The two branches that must NOT
        # see a header (the receipt and the clarification) return above this
        # line, so the move changed nothing for them: they still never fetch one.
        corpus_header = await self._fetch_corpus_header(query, knowledge, space_id)
        if self._is_targetless_summary(routed):
            # ب-3 (خطة الفجوات §3، ف-5) — the fifth branch. «لخّص لي هذا»
            # ("summarise this for me") is classified as a summarisation
            # correctly, matches no file name, and then falls SILENTLY through
            # to the content route, where it retrieves nothing and ends in «لا
            # أملك معلومات كافية». The most natural phrasing a user can reach
            # for produced the least useful answer the agent has.
            #
            # A summarisation was asked and its target is unknown: the honest
            # reply is the QUESTION, and an apology is not one of its forms —
            # the agent knows exactly what was requested and is missing exactly
            # one name, a name the user can supply in a word.
            #
            # The header IS attached here, unlike on the two branches above,
            # and their reason for withholding it does not apply: they already
            # name files, this one names none, and the space's listing is the
            # only material that turns the question into something answerable.
            target = self._with_corpus(self._summary_target_question(query), corpus_header)
            return _FixedReply(target, _PATH_SUMMARY_TARGET_UNKNOWN)
        chunks: Sequence[RetrievedChunkView] = () if routed is None else routed.chunks
        if turn.retrieval_attempted and not chunks:
            # The trust gate + honest fallback (retrieval plan §3.3, the "most
            # dangerous gap in the whole file": before this branch existed the
            # path fell through to bare `SYSTEM_PROMPT` with no context, and
            # the model would answer from its OWN parametric knowledge as
            # though it were sourced from the user's documents). س-22 = أ
            # closes only this EXPLICIT zero-chunks case, with NO numeric
            # confidence threshold — `RetrievalResult.best_dense_score` /
            # `best_bm25_score` (step 4, `P-28`) are not read here, and "weak
            # chunks" stays an accepted open risk until an evaluation set
            # exists (§6 risk 2).
            #
            # The LLM provider is NEVER called on this branch: the text is a
            # fixed local string picked by `_fallback_answer`, so there is
            # nothing in flight the model could improvise an answer for. Row 6
            # (`P-36`, س-23 = ج — "the header always: in the normal path AND
            # in the fallback path") puts the corpus-awareness header beneath
            # that SAME string — the honest "I don't have enough information"
            # sentence, followed by what the space actually holds, so a
            # question like «كم ملفًا لديك؟» gets a genuinely useful answer
            # even though the LLM never runs.
            #
            # This is the one `_FixedReply` whose `fallback` is `True`
            # (retrieval plan §3.11's own measurement): the trust gate fired.
            # The knowledge module logged `fallback: true` from its side of the
            # same turn too, with the stage counts that say WHY chunks were zero.
            apology = self._with_corpus(self._fallback_answer(query), corpus_header)
            return _FixedReply(apology, _PATH_FALLBACK, fallback=True)
        return _Synthesis(
            binding=binding,
            query=query,
            messages=self._messages(query, chunks, corpus_header),
            chunks=chunks,
            corpus_header=corpus_header,
        )

    @staticmethod
    def _is_targetless_summary(routed: RoutedAnswerView | None) -> bool:
        """ب-3 (خطة الفجوات §3، ف-5) — was this a summarisation whose TARGET
        the module could not identify?

        Three conditions, and each one excludes a route that already has its
        own answer: the intent was a summarisation, no build was queued (that
        is the receipt), and no candidates came back (that is the clarification
        question). What is left is the case that used to fall silently through
        to the content route.

        The intent is matched as a STRING LITERAL rather than by importing
        ``Intent`` (ق-1) — precisely the widening ``RoutedAnswerView``
        documents: "``intent`` is ``str`` here and a ``StrEnum`` there… an
        agent comparing against a string literal is reading the same value the
        module wrote". The copy is held to the enum by a drift test
        (``test_the_agents_intent_literal_matches_the_modules_enum``), which
        lives in the test layer because that is the only layer allowed to
        import both sides.
        """
        return (
            routed is not None
            and routed.intent == _INTENT_SUMMARIZE_DOC
            and routed.summary_job_id is None
            and not routed.clarification_options
        )

    @staticmethod
    def _warn_if_unscoped(
        req: AgentRequest, knowledge: KnowledgeAccess | None, space_id: Uuid | None
    ) -> None:
        """ب-6 (خطة الفجوات §3، ف-9) — one warning for a turn that runs with a
        knowledge seam wired and NO space known.

        That path is safe by construction (ق-7: an unknown boundary is stayed
        inside, never widened) and it was also completely SILENT: the model
        answers alone, with no retrieval, no citations and no corpus header —
        the exact shape of answer the trust gate exists to prevent, arriving
        through a different door. ``retrieval_attempted=False`` cannot tell it
        apart from "no seam wired at all", and those are two different
        diagnoses.

        A warning and not a refusal. ق-أ (§9 of the plan) records why: refusing
        the turn is a VISIBLE behaviour change on a path whose frequency nobody
        has measured yet, so the order is illuminate → measure → decide, with a
        written re-open criterion instead of a guess.

        The ``conversation_id`` is an IDENTIFIER, not content (ق-5): it is what
        makes one warning traceable to one thread without putting a single word
        of the question into a log.
        """
        if knowledge is not None and space_id is None:
            _logger.warning(
                "rag_agent.unscoped_turn",
                extra={"conversation_id": req.conversation_id},
            )

    async def _fetch_corpus_header(
        self, query: str, knowledge: KnowledgeAccess | None, space_id: Uuid | None
    ) -> str | None:
        """This space's corpus-awareness header (retrieval plan §3.6/§4 row 6,
        ``P-36``, س-23 = ج), or ``None`` when there is none to build — and
        since ب-2, also when building it FAILED.

        No ``limit`` is named, for the reason no ``k`` is named on ``answer``
        and in the same shape (plan row 18, ``P-40``): the display cap is the
        DEPLOYMENT's (``Settings.retrieval.max_corpus_names``), resolved inside
        the module, and this agent holds no number. The ``space_id`` IS named
        (س-32): the header exists to tell a user what the fallback sentence
        could not answer FROM, so one listing files the search was never
        allowed to touch would be worse than none — it would name documents and
        then refuse to use them.

        **ب-2 (خطة الفجوات §3، ف-10) — the guard.** This call had none, and it
        runs BEFORE synthesis, so a transient fault in the file seam sank the
        whole turn: a perfectly good retrieval, already paid for, thrown away
        for a header whose only job is to phrase the answer better. ``None`` is
        a state both callers already know and handle, so the failure degrades
        to exactly what a turn looked like before the header existed — not to a
        new branch.

        ⚠️ The wrap is on THIS call and nowhere else. ``answer`` is deliberately
        left bare (س-28): with no context there is no answer, and catching a
        retrieval failure would produce a confident reply from the model's own
        parametric knowledge with no citations — precisely what the trust gate
        was built to prevent.

        ``Exception`` rather than ``AppError``: what is expected here is a
        network fault or a driver timeout, and most of those are not
        ``AppError``s. It does not catch ``CancelledError`` (a
        ``BaseException``), so cancellation still propagates untouched. And the
        record is a WARNING, not an info line — a dependency that failed belongs
        on a dashboard even when the user never notices it.
        """
        if knowledge is None or space_id is None:
            return None
        try:
            corpus = await knowledge.list_document_names(self.ctx, space_id=space_id)
        except Exception as exc:
            _logger.warning("rag_agent.corpus_header_unavailable", exc_info=exc)
            return None
        return self._corpus_header(query, corpus.names, corpus.total)

    @staticmethod
    def _with_corpus(text: str, corpus_header: str | None) -> str:
        """``text`` with the corpus-awareness header beneath it, or ``text``
        alone when there is no header (retrieval plan §3.6/§4 row 6).

        One spelling of the join for the three user-visible paths that carry it
        — the trust-gate fallback, the empty completion (ب-1) and the
        targetless summarisation (ب-3) — for the reason ``_corpus_header``
        itself is one builder: three call sites spelling their own separator is
        three sentences waiting to drift apart. The normal synthesis path joins
        it into the SYSTEM prompt instead, and that spelling lives in
        ``_messages``, where the reader is a model rather than a person.
        """
        return text if corpus_header is None else f"{text}\n\n{corpus_header}"

    @staticmethod
    def _log_answer(
        *,
        path: str,
        retrieval_attempted: bool,
        context_nodes: int,
        fallback: bool,
        llm_ms: int | None,
        started: float,
        error_type: str | None = None,
    ) -> None:
        """The turn's one structured record (retrieval plan §3.11/§4 row 17,
        ``P-29``, س-25 = أ) — emitted from each of ``run``'s exits, never
        from a ``finally``: an async generator abandoned mid-stream would
        otherwise log a turn that never finished, at whatever moment the
        event loop happened to close it. ب-5 added the eighth exit (the
        error path) and kept that guarantee by catching ``Exception``, which
        ``GeneratorExit`` and ``CancelledError`` are not.

        ``llm_ms is None`` means the model was NEVER CALLED, which is a real
        and frequent outcome here (a queued summary, a clarification
        question, a summarisation with no known target, a blocked build, the
        honest fallback) — never "we forgot to measure". The one place a
        ``path`` reports no answer WITH a real ``llm_ms`` is
        ``empty_completion`` (ب-1): the model ran, spent that time and
        returned nothing.

        The pair ``retrieval_attempted`` + ``fallback`` separates the two
        ways a turn can carry no context: no knowledge seam wired at all (the
        optional-degrading mode — attempted ``False``), versus a real
        retrieval that came back empty (attempted ``True``, fallback
        ``True``).

        ``error_type`` (ب-5, ف-14) is the exception's CLASS NAME on the error
        path and ``None`` everywhere else — present on every record for
        ``llm_ms``'s reason: a field that is always there and sometimes null
        is readable, one that appears only sometimes is not. The class name
        and never the message: ``ConflictError``'s message carries a document
        id, and ق-5 keeps ids and user content out of this record entirely.

        Every field is a count, a flag, a duration, a fixed path name or a
        type name. The question, the answer text, the file names and the
        chunk text are all user content and none of them is here
        (10-code-standards §10); the chunk IDS that make a retrieval
        traceable are logged one layer down, by the knowledge module, where
        they are read from.
        """
        _logger.info(
            "rag_agent.answer",
            extra={
                "path": path,
                "retrieval_attempted": retrieval_attempted,
                "context_nodes": context_nodes,
                "fallback": fallback,
                "llm_ms": llm_ms,
                "total_ms": _elapsed_ms(started),
                "error_type": error_type,
            },
        )

    @staticmethod
    def _query(req: AgentRequest) -> str:
        # R6 at the request boundary: a missing/blank/non-str ``text`` is a
        # caller error (422), not a raw KeyError from a boot/request path.
        value = req.input.get("text")
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("rag_agent requires a non-empty 'text' input")
        return value

    @staticmethod
    def _fallback_answer(query: str) -> str:
        """The zero-chunks trust-gate fallback (retrieval plan §3.3/§4 row 5,
        ``P-33``) — one of two fixed, honest sentences, picked by whether
        ``query`` contains any Arabic-script character. See the module-level
        comment above ``_ARABIC_CHAR_RE`` for why this is a presence check
        rather than a reuse of the knowledge module's ratio-based
        ``detect_language``.
        """
        return _FALLBACK_ANSWER_AR if _ARABIC_CHAR_RE.search(query) else _FALLBACK_ANSWER_EN

    @staticmethod
    def _summary_queued_answer(query: str, name: str | None = None) -> str:
        """The SUMMARIZE_DOC route's receipt (retrieval plan §3.4/§4 row 11,
        ``P-21``) — one of two fixed sentences, picked by the same
        ``_ARABIC_CHAR_RE`` presence check ``_fallback_answer`` uses,
        naming the document when the module sent a name (ب-7أ, ف-2).

        **It still names nothing the agent resolved**, which is the rule
        this method kept when it named nothing at all: an agent that
        echoed a filename out of the QUERY would be asserting a
        resolution nobody performed. ``name`` is not from the query. It
        crossed the seam from the module that ran ``resolve_file``,
        chose one document and refused every alternative, so saying it
        repeats that choice back rather than inventing one.

        **And that is what makes a wrong choice visible.** A FUZZY match
        at 0.78 over a 0.75 threshold used to be invisible until a
        summary of the wrong document arrived minutes later — or, when a
        thread was pinned to another file (س-21), never. Named here, it
        is contradictable in the same breath as the acceptance.

        ``None`` — no build to name, or a target this corpus cannot name
        — falls back to the unnamed wording verbatim. A blank is never
        interpolated: a receipt reading «ملخّص «»» is worse than one that
        names no file, because it looks like a name that came out empty.
        """
        if name is not None and name.strip():
            template = (
                _SUMMARY_QUEUED_NAMED_AR
                if _ARABIC_CHAR_RE.search(query)
                else _SUMMARY_QUEUED_NAMED_EN
            )
            return template.format(name=name.strip())
        return _SUMMARY_QUEUED_AR if _ARABIC_CHAR_RE.search(query) else _SUMMARY_QUEUED_EN

    @staticmethod
    def _summary_target_question(query: str) -> str:
        """The targetless-summarisation question (ب-3, ف-5) — one of two fixed
        sentences, picked by the same ``_ARABIC_CHAR_RE`` presence check
        ``_fallback_answer`` uses.

        It names no file, because none was resolved — the same rule
        ``_summary_queued_answer`` keeps. The difference from
        ``_clarification_question`` is that there the module handed over a
        SHORTLIST it refused to choose from, and here it matched nothing at
        all: the caller ``run`` answers that by attaching the space's corpus
        header beneath this line, so the user has the menu without this
        sentence ever asserting a name.
        """
        return _SUMMARY_TARGET_AR if _ARABIC_CHAR_RE.search(query) else _SUMMARY_TARGET_EN

    @staticmethod
    def _summary_blocked_answer(query: str) -> str:
        """The refused-build sentence (ب-4أ, ف-7) — one of two fixed
        sentences, picked by the same ``_ARABIC_CHAR_RE`` presence check.

        Neutral by construction: see the ``_SUMMARY_BLOCKED_*`` strings for
        why one error code covering two different states forces a sentence
        true of both, and which item replaces it with two exact ones.
        """
        return _SUMMARY_BLOCKED_AR if _ARABIC_CHAR_RE.search(query) else _SUMMARY_BLOCKED_EN

    @staticmethod
    def _clarification_question(query: str, options: Sequence[str]) -> str:
        """The tie-break question (retrieval plan §3.5/§4 row 14, ``P-04``,
        س-18 = أ): one fixed opening line — picked by the same
        ``_ARABIC_CHAR_RE`` presence check every other sentence this agent
        writes uses — followed by the candidate file names, one per line.

        These names are the ONLY document names this agent ever utters, and
        the difference from ``_summary_queued_answer`` (which pointedly names
        nothing) is the whole point: there it would be asserting which file
        it acted on, here it is asking. It echoes the module's list verbatim
        and neither trims, re-orders nor de-duplicates it — the resolver
        already capped it at five, and an agent that dropped a candidate
        would be narrowing a choice it is not the one making.
        """
        head = _CLARIFY_FILE_AR if _ARABIC_CHAR_RE.search(query) else _CLARIFY_FILE_EN
        listed = "\n".join(f"{_CLARIFY_BULLET}{name}" for name in options)
        return f"{head}\n{listed}"

    @staticmethod
    def _citation(chunk: RetrievedChunkView) -> dict[str, str | int | None]:
        """One `{document_id, file_name, page, chunk_id}` citation (retrieval
        plan §3.2/§4 row 3, ``P-32``) — a citation a human can act on,
        replacing the bare ``chunk_id`` UUID the ``final`` event used to emit.

        ``file_name``/``page`` are REUSED verbatim from the already-retrieved
        chunk (step 1's fields — ``page`` is ``chunk.page_number`` under the
        shorter wire name the plan names for this shape), never re-derived
        from anywhere else. Both stay explicitly ``None`` — a real, always-
        present JSON key holding ``null``, never an omitted key — exactly
        when the chunk itself carries no value there (an older Qdrant point,
        or a parser that never emitted one): the same missing-value story
        ``RetrievedChunkOut``/``format_labeled_chunk`` already tell, so a
        client sees ONE degradation rule across the whole citation surface.
        """
        return {
            "document_id": chunk.document_id,
            "file_name": chunk.file_name,
            "page": chunk.page_number,
            "chunk_id": chunk.chunk_id,
        }

    @staticmethod
    def _messages(
        query: str, chunks: Sequence[RetrievedChunkView], corpus_header: str | None
    ) -> list[LlmMessage]:
        system = SYSTEM_PROMPT
        # Retrieval plan §3.6/§4 row 6 (`P-36`, س-23 = ج) — the corpus header
        # is ALWAYS in the system prompt when retrieval was attempted at all
        # (`corpus_header is not None`), independently of whether THIS
        # request's chunks came back empty or full: the model should know
        # what the workspace holds on every normal-path answer, not only the
        # ones that happen to retrieve nothing.
        if corpus_header is not None:
            system = f"{system}\n\n{corpus_header}"
        if chunks:
            # Retrieval plan §3.2/P-31 — the source label is added HERE, at
            # display time, above each chunk's own text; the shared unit
            # (`source_label.format_context_block`) is the single place that
            # shape is built, and since plan row 19 the knowledge module's
            # internal `context_text` capability (§3.11, P-39) renders the
            # SAME block through the SAME call. The join moved into that unit
            # with row 19 for the reason §3.2 gives ("لا صيغتان تنحرفان"): a
            # separator spelt at this call site would have been a second
            # format waiting to drift from the module's.
            #
            # `chunks` arrives descending and already truncated (§3.7), and
            # nothing here re-orders it — the most relevant chunk stays
            # `[#1]`, at the TOP of the context. `LongContextReorder` is a
            # rejected design, not an omission (§3.7, §7).
            context = format_context_block(chunks)
            system = f"{system}\n\nContext:\n{context}"
        return [
            LlmMessage(role="system", content=system),
            LlmMessage(role="user", content=query),
        ]

    @staticmethod
    def _corpus_header(query: str, names: Sequence[str], total: int) -> str:
        """The corpus-awareness header text (retrieval plan §3.6/§4 row 6,
        ``P-36``, س-23 = ج) — this workspace's file names, however many the
        module handed over, then an "and N more files" tail for the rest (in
        Arabic, whichever of ``_more_files_ar``'s four number-agreeing forms N
        calls for). Reused verbatim on both the fallback path
        (prepended to the user-visible text) and the normal path (prepended
        to the system prompt) — see ``run``.

        Language follows the SAME ``_ARABIC_CHAR_RE`` presence check the
        fallback sentences use, so a query's language picks one consistent
        voice across everything this agent says about itself, on either
        path — no second i18n mechanism.

        ``total`` may exceed ``len(names)`` for two different reasons —
        ``ListDocumentNames`` capped the resolved names at the deployment's
        configured display cap, or it skipped a document whose file could no
        longer be read — and both collapse to the same honest tail here:
        "N more" always means ``total - len(names)``, never a distinction the
        header has no way to explain to a reader anyway.
        """
        is_arabic = bool(_ARABIC_CHAR_RE.search(query))
        if not names:
            return _CORPUS_EMPTY_AR if is_arabic else _CORPUS_EMPTY_EN
        remaining = max(0, total - len(names))
        if is_arabic:
            listed = "، ".join(names)
            tail = f"، {_more_files_ar(remaining)}." if remaining else "."
            return f"{_CORPUS_LABEL_AR} {listed}{tail}"
        listed = ", ".join(names)
        tail = f", and {remaining} more files." if remaining else "."
        return f"{_CORPUS_LABEL_EN} {listed}{tail}"


def _more_files_ar(remaining: int) -> str:
    """The Arabic "and N more files" tail for ``remaining`` unlisted files, in
    whichever of the four forms Arabic number agreement (تمييز العدد) calls
    for — see the ``_CORPUS_MORE_AR_*`` strings for the rule itself.

    A module function beside ``_corpus_header``, the one caller, for
    ``_elapsed_ms``'s reason: this agent imports nothing beyond ``self.deps``
    and the framework kernel it already names, and a shared i18n helper is a
    wider change than the four strings it would carry. It stays next to the
    header because the header's own language choice (``_ARABIC_CHAR_RE``) is
    what decides whether it is called at all — there is one voice per query,
    and no second i18n mechanism.

    ``1`` and ``2`` name NO numeral: the noun's own form carries the count in
    Arabic, and «و 1 ملفّ آخر» would be as odd to read as "and 1 more
    files" is in English. From 11 up the singular accusative is the honest
    form for every count this header can reach — the exact hundreds and
    thousands take a genitive of their own in careful prose, a refinement
    this deliberately does not attempt: it would need the number spelled out
    in words, which a file COUNT is not.

    ``remaining`` is ``total - len(names)`` and therefore never negative (see
    ``_corpus_header``'s ``max(0, ...)``) and never zero here — a zero tail is
    the caller's plain full stop, not a form of this sentence.
    """
    if remaining == 1:
        return _CORPUS_MORE_AR_ONE
    if remaining == _AR_DUAL:
        return _CORPUS_MORE_AR_TWO
    noun = _CORPUS_MORE_AR_FEW if remaining <= _AR_PLURAL_MAX else _CORPUS_MORE_AR_MANY
    return f"و {remaining} {noun}"


def _elapsed_ms(started: float) -> int:
    """Whole milliseconds since ``started`` on the MONOTONIC clock — never the
    wall clock, which an NTP step could run backwards mid-answer and turn a
    duration into a negative number.

    A module function rather than a shared ``observability`` helper: this
    agent imports nothing beyond ``self.deps`` and the framework kernel it
    already names, and adding an undesigned public helper to that kernel to
    save one line is a wider change than the duplication it removes (recorded
    in the plan's §7, alongside the knowledge module's identical copy).
    """
    return round((time.perf_counter() - started) * _MS_PER_SECOND)
