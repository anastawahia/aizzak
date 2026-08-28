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
``total_ms`` (see ``_log_answer``). ``llm_ms`` lives HERE and nowhere else:
the knowledge module's own ``knowledge.retrieval`` record measures retrieval,
but the provider stream is this agent's, and the three no-LLM branches below
are the only place that can honestly report ``null`` for it. Nothing of the
answer, the question or the documents is logged — counts and durations only
(10-code-standards §10) — and none of it reaches the streaming contract,
which still carries exactly ``token`` and ``final`` (س-25 = أ, plan §7).

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

from app.agents.rag_agent.manifest import METADATA
from app.agents.rag_agent.prompts import SYSTEM_PROMPT
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.deps_ports import RetrievedChunkView, RoutedAnswerView
from app.framework.agent_runtime.source_label import format_context_block
from app.framework.errors import AppError, ValidationError
from app.framework.observability import get_logger
from app.framework.ports.llm_provider import LlmMessage, LlmParams

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
_MS_PER_SECOND = 1000


class RagAgent(BaseAgent):
    """Retrieval-augmented Q&A over the workspace knowledge base."""

    metadata = METADATA

    async def initialize(self) -> None:
        # Stateless: nothing to preload in v1 (context is fetched per-request in
        # ``run``). Conversation/memory hydration is deferred to the orchestrator.
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        # Retrieval plan §3.11 (`P-29`) — the turn's clock, started before any
        # work so `total_ms` covers routing, retrieval, the corpus listing and
        # synthesis together. A request that RAISES (no LLM bound, blank
        # input) emits no record: those are errors, and the error path already
        # has its own reporting.
        started = time.perf_counter()
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
        # and this function now needs that narrowing twice (retrieval, then
        # the corpus header below) rather than once.
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
        retrieval_attempted = knowledge is not None and space_id is not None
        routed: RoutedAnswerView | None = (
            # Retrieval plan §3.4/§4 row 11 (`P-21`, س-16 = أ) — ONE call, and
            # it is `answer`, not `retrieve`: the question is classified and
            # dispatched INSIDE the knowledge module, so this agent still
            # imports nothing and still reaches everything through
            # `self.deps` (ح-11, §6 risk 7). The arguments are the ones
            # `retrieve` took, unchanged.
            #
            # `k` is NOT passed (retrieval plan §4 row 18, `P-40`, س-24 = أ).
            # It used to be `_TOP_K = 5`, a retrieval tuning number held by an
            # agent — the one thing ح-11 says this agent does not do, and the
            # one place `Settings` could never reach because an agent reads no
            # configuration and imports nothing. Omitting it asks the module
            # for the DEPLOYMENT's configured `k`
            # (`Settings.retrieval.default_k`), which is the same 5 today and
            # is now movable without touching this file.
            #
            # ✅ Spaces plan step 8, closed by س-32: the space now arrives on
            # `AgentDeps` and is named here. It was the second of the two call
            # sites the port's docstring listed as owing one; `POST
            # /knowledge/search` was the first.
            await knowledge.answer(
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
            if knowledge is not None and space_id is not None
            else None
        )
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
            receipt = self._summary_queued_answer(query)
            self._log_answer(
                path=_PATH_SUMMARY_RECEIPT,
                retrieval_attempted=retrieval_attempted,
                context_nodes=0,
                fallback=False,
                llm_ms=None,
                started=started,
            )
            yield AgentEvent(type="token", data={"delta": receipt})
            yield AgentEvent(type="final", data={"text": receipt, "citations": []})
            return
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
            clarification = self._clarification_question(query, routed.clarification_options)
            self._log_answer(
                path=_PATH_CLARIFICATION,
                retrieval_attempted=retrieval_attempted,
                context_nodes=0,
                fallback=False,
                llm_ms=None,
                started=started,
            )
            yield AgentEvent(type="token", data={"delta": clarification})
            yield AgentEvent(type="final", data={"text": clarification, "citations": []})
            return
        chunks: Sequence[RetrievedChunkView] = () if routed is None else routed.chunks
        # Retrieval plan §3.6/§4 row 6 (`P-36`, س-23 = ج) — the corpus header
        # is fetched whenever retrieval was attempted at all, so it is ready
        # for BOTH branches below: the trust-gate fallback (prepended to the
        # user-visible text) and the normal synthesis path (prepended to the
        # system prompt via `_messages`). No knowledge seam wired at all — or,
        # since س-32, no space known — means no retrieval was attempted, and
        # there is equally no corpus to describe: `corpus_header` stays `None`
        # and `_messages` renders exactly as it did before this step.
        corpus_header: str | None = None
        if knowledge is not None and space_id is not None:
            # No `limit` named, for the reason no `k` is named on `answer`
            # above and in the same shape (plan row 18, `P-40`): the display
            # cap is the DEPLOYMENT's (`Settings.retrieval.max_corpus_names`),
            # resolved inside the module, and this agent holds no number.
            #
            # The `space_id` IS named, and the header it renders is now that
            # space's corpus rather than the workspace's (س-32). Same condition
            # as the retrieval above, deliberately: the header exists to tell a
            # user what the fallback sentence could not answer FROM, so a header
            # listing files the search was never allowed to touch would be worse
            # than none — it would name documents and then refuse to use them.
            corpus = await knowledge.list_document_names(self.ctx, space_id=space_id)
            corpus_header = self._corpus_header(query, corpus.names, corpus.total)
        if retrieval_attempted and not chunks:
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
            # The LLM provider is NEVER called on this branch: `fallback` is a
            # fixed local string picked by `_fallback_answer`, so there is
            # nothing in flight the model could improvise an answer for. Row 6
            # (`P-36`, س-23 = ج — "the header always: in the normal path AND
            # in the fallback path") prepends the corpus-awareness header to
            # this SAME `fallback` string before it is yielded — the honest
            # "I don't have enough information" sentence, followed by what the
            # workspace actually holds, so a question like «كم ملفًا لديك؟»
            # gets a genuinely useful answer even though the LLM never runs.
            fallback = self._fallback_answer(query)
            if corpus_header is not None:
                fallback = f"{fallback}\n\n{corpus_header}"
            # The one record whose `fallback` is `True` (retrieval plan
            # §3.11's own measurement): the trust gate fired, and `llm_ms` is
            # `null` because this branch never calls the model. The knowledge
            # module logged `fallback: true` from its side of the same turn
            # too, with the stage counts that say WHY the chunks were zero.
            self._log_answer(
                path=_PATH_FALLBACK,
                retrieval_attempted=retrieval_attempted,
                context_nodes=0,
                fallback=True,
                llm_ms=None,
                started=started,
            )
            yield AgentEvent(type="token", data={"delta": fallback})
            yield AgentEvent(type="final", data={"text": fallback, "citations": []})
            return
        messages = self._messages(query, chunks, corpus_header)
        params = LlmParams(model=binding.model)
        answer: list[str] = []
        # `llm_ms` (retrieval plan §3.11) — the provider stream's WALL time,
        # from the request to the last token consumed. Consumer backpressure
        # is inside it, unavoidably: the tokens are yielded onward as they
        # arrive (that is what streaming means), so no clock this agent can
        # read separates the model's time from its reader's.
        llm_started = time.perf_counter()
        async for chunk in binding.provider.stream(messages, params, binding.api_key):
            if chunk.delta:
                answer.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        self._log_answer(
            path=_PATH_SYNTHESIS,
            retrieval_attempted=retrieval_attempted,
            context_nodes=len(chunks),
            fallback=False,
            llm_ms=_elapsed_ms(llm_started),
            started=started,
        )
        yield AgentEvent(
            type="final",
            data={
                "text": "".join(answer),
                "citations": [self._citation(c) for c in chunks],
            },
        )

    @staticmethod
    def _log_answer(
        *,
        path: str,
        retrieval_attempted: bool,
        context_nodes: int,
        fallback: bool,
        llm_ms: int | None,
        started: float,
    ) -> None:
        """The turn's one structured record (retrieval plan §3.11/§4 row 17,
        ``P-29``, س-25 = أ) — emitted from each of ``run``'s four exits,
        never from a ``finally``: an async generator abandoned mid-stream
        would otherwise log a turn that never finished, at whatever moment
        the event loop happened to close it.

        ``llm_ms is None`` means the model was NEVER CALLED, which is a real
        and frequent outcome here (a queued summary, a clarification
        question, the honest fallback) — never "we forgot to measure". The
        pair ``retrieval_attempted`` + ``fallback`` separates the two ways a
        turn can carry no context: no knowledge seam wired at all (the
        optional-degrading mode — attempted ``False``), versus a real
        retrieval that came back empty (attempted ``True``, fallback
        ``True``).

        Every field is a count, a flag, a duration or a fixed path name. The
        question, the answer text, the file names and the chunk text are all
        user content and none of them is here (10-code-standards §10); the
        chunk IDS that make a retrieval traceable are logged one layer down,
        by the knowledge module, where they are read from.
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
    def _summary_queued_answer(query: str) -> str:
        """The SUMMARIZE_DOC route's receipt (retrieval plan §3.4/§4 row 11,
        ``P-21``) — one of two fixed sentences, picked by the same
        ``_ARABIC_CHAR_RE`` presence check ``_fallback_answer`` uses.

        It names no document. The agent knows a job id and nothing else — the
        module resolved the target from the caller's pinned scope — and
        echoing back a name it did not resolve is how an agent starts
        describing the wrong file with confidence (§3.5, the failure س-18
        exists to prevent).
        """
        return _SUMMARY_QUEUED_AR if _ARABIC_CHAR_RE.search(query) else _SUMMARY_QUEUED_EN

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
