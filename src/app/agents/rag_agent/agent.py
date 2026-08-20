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
ج):** ``_corpus_header`` builds this workspace's file-name header — up to
``_MAX_CORPUS_NAMES`` names, then a "و N ملفًا آخر"/"and N more files" tail —
and it is present on BOTH paths, never one or the other. On the NORMAL
synthesis path it is prepended to the SYSTEM message (``_messages``),
invisible to the user but read by the model, so a question like «كم ملفًا
لديك؟» ("how many files do you have?") — which, excluding ``METADATA``,
routes to plain ``CONTENT`` and usually finds no matching chunks — can still
be answered honestly instead of tripping a dry, uninformative fallback. On
the FALLBACK path above, where the LLM is never called at all, the same
header is prepended directly to the user-visible ``fallback`` text itself:
the only way that branch can reflect what the corpus holds is to say so in
the sentence the user actually reads. One shared builder, so the two paths
can never describe two different corpora.

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
from collections.abc import AsyncIterator, Sequence

from app.agents.rag_agent.manifest import METADATA
from app.agents.rag_agent.prompts import SYSTEM_PROMPT
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.deps_ports import RetrievedChunkView, RoutedAnswerView
from app.framework.agent_runtime.source_label import format_labeled_chunk
from app.framework.errors import AppError, ValidationError
from app.framework.ports.llm_provider import LlmMessage, LlmParams

_TOP_K = 5

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

# Retrieval plan §3.6/§4 row 6 (``P-36``, س-23 = ج) — the corpus-awareness
# header's display cap. A PLAIN MODULE CONSTANT, not ``Settings`` (س-24's own
# escape hatch: "a plain module constant is also acceptable for a fixed
# display cap"). The plan fixes this number itself ("سقف عرض 50 اسمًا") rather
# than leaving it a per-deployment knob, exactly like ``_TOP_K`` above — and
# unlike ``_TOP_K``, it is never read from a request either way, so there is
# nothing here for a per-request override to even attach to.
_MAX_CORPUS_NAMES = 50

# The corpus-awareness header's four fixed strings (retrieval plan §3.6),
# picked by the SAME ``_ARABIC_CHAR_RE`` query-language check the fallback
# sentences above use — one language mechanism, reused, not a second one.
_CORPUS_EMPTY_EN = "There are no files in this workspace yet."
_CORPUS_EMPTY_AR = "لا توجد ملفّات في مساحة العمل بعد."
_CORPUS_LABEL_EN = "Workspace files:"
_CORPUS_LABEL_AR = "ملفّات مساحة العمل:"


class RagAgent(BaseAgent):
    """Retrieval-augmented Q&A over the workspace knowledge base."""

    metadata = METADATA

    async def initialize(self) -> None:
        # Stateless: nothing to preload in v1 (context is fetched per-request in
        # ``run``). Conversation/memory hydration is deferred to the orchestrator.
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
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
        # Retrieval plan §3.3/§4 row 5 (`P-33`) — this flag is what tells the
        # trust gate below apart from the "knowledge is optional-degrading"
        # mode just above: a caller with NO knowledge seam wired at all never
        # attempted retrieval, so there is no "retrieval result" to gate on —
        # that stays a plain LLM answer, exactly as
        # `test_without_knowledge_still_answers_with_no_citations` pins. A
        # caller that DID wire a seam and got zero chunks back is the case the
        # gate exists for.
        retrieval_attempted = knowledge is not None
        routed: RoutedAnswerView | None = (
            # Retrieval plan §3.4/§4 row 11 (`P-21`, س-16 = أ) — ONE call, and
            # it is `answer`, not `retrieve`: the question is classified and
            # dispatched INSIDE the knowledge module, so this agent still
            # imports nothing and still reaches everything through
            # `self.deps` (ح-11, §6 risk 7). The arguments are the ones
            # `retrieve` took, unchanged.
            #
            # Spaces plan step 8 — `space_id` is TYPED as none rather than
            # defaulted, and it is STILL none after step 12: that step put the
            # space on the request (`AgentInvokeIn.space_id`) but not onto
            # `AgentDeps`, so this agent has nothing to read it from. It
            # remains one of the two call sites the port's docstring names as
            # owing a space, and the plan's §7 carries the entry. Searching
            # every space is the pre-plan behaviour; the pins in `scope`
            # already cannot cross one.
            await knowledge.answer(self.ctx, query, _TOP_K, scope, space_id=None)
            if knowledge is not None
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
            yield AgentEvent(type="token", data={"delta": clarification})
            yield AgentEvent(type="final", data={"text": clarification, "citations": []})
            return
        chunks: Sequence[RetrievedChunkView] = () if routed is None else routed.chunks
        # Retrieval plan §3.6/§4 row 6 (`P-36`, س-23 = ج) — the corpus header
        # is fetched whenever retrieval was attempted at all, so it is ready
        # for BOTH branches below: the trust-gate fallback (prepended to the
        # user-visible text) and the normal synthesis path (prepended to the
        # system prompt via `_messages`). No knowledge seam wired at all means
        # no retrieval was attempted (the optional-degrading mode above), and
        # there is equally no corpus to describe — `corpus_header` stays
        # `None` and `_messages` renders exactly as it did before this step.
        corpus_header: str | None = None
        if knowledge is not None:
            corpus = await knowledge.list_document_names(self.ctx, limit=_MAX_CORPUS_NAMES)
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
            yield AgentEvent(type="token", data={"delta": fallback})
            yield AgentEvent(type="final", data={"text": fallback, "citations": []})
            return
        messages = self._messages(query, chunks, corpus_header)
        params = LlmParams(model=binding.model)
        answer: list[str] = []
        async for chunk in binding.provider.stream(messages, params, binding.api_key):
            if chunk.delta:
                answer.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        yield AgentEvent(
            type="final",
            data={
                "text": "".join(answer),
                "citations": [self._citation(c) for c in chunks],
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
            # (`source_label.format_labeled_chunk`) is the single place that
            # shape is built, reused later by the internal `context_text`
            # capability (§3.11, P-39).
            context = "\n\n".join(
                format_labeled_chunk(
                    c.text, file_name=c.file_name, page_number=c.page_number, section=c.section
                )
                for c in chunks
            )
            system = f"{system}\n\nContext:\n{context}"
        return [
            LlmMessage(role="system", content=system),
            LlmMessage(role="user", content=query),
        ]

    @staticmethod
    def _corpus_header(query: str, names: Sequence[str], total: int) -> str:
        """The corpus-awareness header text (retrieval plan §3.6/§4 row 6,
        ``P-36``, س-23 = ج) — this workspace's file names, up to
        ``_MAX_CORPUS_NAMES`` of them, then a "و N ملفًا آخر"/"and N more
        files" tail for the rest. Reused verbatim on both the fallback path
        (prepended to the user-visible text) and the normal path (prepended
        to the system prompt) — see ``run``.

        Language follows the SAME ``_ARABIC_CHAR_RE`` presence check the
        fallback sentences use, so a query's language picks one consistent
        voice across everything this agent says about itself, on either
        path — no second i18n mechanism.

        ``total`` may exceed ``len(names)`` for two different reasons —
        ``ListDocumentNames`` capped the resolved names at the caller's
        `limit`, or it skipped a document whose file could no longer be
        read — and both collapse to the same honest tail here: "N more"
        always means ``total - len(names)``, never a distinction the header
        has no way to explain to a reader anyway.
        """
        is_arabic = bool(_ARABIC_CHAR_RE.search(query))
        if not names:
            return _CORPUS_EMPTY_AR if is_arabic else _CORPUS_EMPTY_EN
        remaining = max(0, total - len(names))
        if is_arabic:
            listed = "، ".join(names)
            tail = f"، و {remaining} ملفًا آخر." if remaining else "."
            return f"{_CORPUS_LABEL_AR} {listed}{tail}"
        listed = ", ".join(names)
        tail = f", and {remaining} more files." if remaining else "."
        return f"{_CORPUS_LABEL_EN} {listed}{tail}"
