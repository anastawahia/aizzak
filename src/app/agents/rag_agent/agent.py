"""``RagAgent`` — answers from workspace knowledge (FR-20.1, 11 §3).

A thin stateless coordinator: retrieve context from the ``knowledge`` module
(via the injected ``KnowledgeAccess`` DIP seam — never importing the module),
build the prompt, and stream the LLM answer as ``token`` events, ending with a
``final`` carrying the assembled text + citations. It reaches ports ONLY
through ``self.deps`` and imports NO other agent, module, or infrastructure —
so ``agents-independent`` / ``agents-no-api-no-infra`` hold trivially.

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
from app.framework.agent_runtime.deps_ports import RetrievedChunkView
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
        # Retrieval plan §3.3/§4 row 5 (`P-33`) — this flag is what tells the
        # trust gate below apart from the "knowledge is optional-degrading"
        # mode just above: a caller with NO knowledge seam wired at all never
        # attempted retrieval, so there is no "retrieval result" to gate on —
        # that stays a plain LLM answer, exactly as
        # `test_without_knowledge_still_answers_with_no_citations` pins. A
        # caller that DID wire a seam and got zero chunks back is the case the
        # gate exists for.
        retrieval_attempted = self.deps.knowledge is not None
        chunks: Sequence[RetrievedChunkView] = (
            # Spaces plan step 8 — `space_id` is TYPED as none rather than
            # defaulted, and it is STILL none after step 12: that step put the
            # space on the request (`AgentInvokeIn.space_id`) but not onto
            # `AgentDeps`, so this agent has nothing to read it from. It
            # remains one of the two call sites the port's docstring names as
            # owing a space, and the plan's §7 carries the entry. Searching
            # every space is the pre-plan behaviour; the pins in `scope`
            # already cannot cross one.
            await self.deps.knowledge.retrieve(self.ctx, query, _TOP_K, scope, space_id=None)
            if self.deps.knowledge is not None
            else []
        )
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
            # in the fallback path") will later prepend a corpus-awareness
            # header to this SAME `fallback` string before it is yielded;
            # nothing about this branch's shape needs to change when it does.
            fallback = self._fallback_answer(query)
            yield AgentEvent(type="token", data={"delta": fallback})
            yield AgentEvent(type="final", data={"text": fallback, "citations": []})
            return
        messages = self._messages(query, chunks)
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
    def _messages(query: str, chunks: Sequence[RetrievedChunkView]) -> list[LlmMessage]:
        system = SYSTEM_PROMPT
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
            system = f"{SYSTEM_PROMPT}\n\nContext:\n{context}"
        return [
            LlmMessage(role="system", content=system),
            LlmMessage(role="user", content=query),
        ]
