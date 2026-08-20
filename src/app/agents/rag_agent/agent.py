"""``RagAgent`` — answers from workspace knowledge (FR-20.1, 11 §3).

A thin stateless coordinator: retrieve context from the ``knowledge`` module
(via the injected ``KnowledgeAccess`` DIP seam — never importing the module),
build the prompt, and stream the LLM answer as ``token`` events, ending with a
``final`` carrying the assembled text + citations. It reaches ports ONLY
through ``self.deps`` and imports NO other agent, module, or infrastructure —
so ``agents-independent`` / ``agents-no-api-no-infra`` hold trivially.

**Scope note (4.6):** the concrete ``deps`` (a ``ProviderResolver``-resolved
``ResolvedLLM``, the real ``KnowledgeRetrieval``) is wired by the orchestrator
at 4.7; usage enforcement/capture, conversation persistence (D-12) and SSE/WS
transport are the orchestrator's too (11 §8.1/§8.2). Here the agent is exercised
against fake ports (11 §9). Tool-calling stays out of the stream in v1 (the
recorded ``LlmChunk``-has-no-tool-field port limit); this agent retrieves
directly rather than via a tool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.agents.rag_agent.manifest import METADATA
from app.agents.rag_agent.prompts import SYSTEM_PROMPT
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.deps_ports import RetrievedChunkView
from app.framework.agent_runtime.source_label import format_labeled_chunk
from app.framework.errors import AppError, ValidationError
from app.framework.ports.llm_provider import LlmMessage, LlmParams

_TOP_K = 5


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
        messages = self._messages(query, chunks)
        params = LlmParams(model=binding.model)
        answer: list[str] = []
        async for chunk in binding.provider.stream(messages, params, binding.api_key):
            if chunk.delta:
                answer.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        yield AgentEvent(
            type="final",
            data={"text": "".join(answer), "citations": [c.chunk_id for c in chunks]},
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
