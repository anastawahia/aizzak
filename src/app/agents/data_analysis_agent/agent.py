"""``DataAnalysisAgent`` — LLM analysis over a workspace data file (FR-20.2).

A thin stateless coordinator: load the target file's text (via the injected
``files`` + ``storage`` seams — never importing the files module), then stream
an analysis from the LLM. v1 is text-only LLM analysis; the heavier
code-execution form belongs to the reserved ``sandbox`` module (Requirements
§12.1), out of v1. Reaches ports ONLY through ``self.deps`` and imports NO other
agent/module/infrastructure.

Concrete ``deps`` (a resolved ``ResolvedLLM``, the real ``FilesQuery`` +
``StorageProvider``) are wired by the orchestrator at 4.7; here it is exercised
against fakes (11 §9).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.agents.data_analysis_agent.manifest import METADATA
from app.agents.data_analysis_agent.prompts import SYSTEM_PROMPT
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.file_reading import read_text_file
from app.framework.errors import AppError, ValidationError
from app.framework.ports.llm_provider import LlmMessage, LlmParams

_MAX_FILE_BYTES = 1_000_000  # bound the prompt: v1 feeds file text to the LLM
_DEFAULT_INSTRUCTION = "Analyze this data and summarize the key insights."


class DataAnalysisAgent(BaseAgent):
    """Answers questions about a workspace data file."""

    metadata = METADATA

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        file_id = self._required_str(req, "file_id")
        instruction = self._optional_str(req, "text") or _DEFAULT_INSTRUCTION
        binding = self.deps.llm
        if binding is None:
            raise AppError(
                detail="data_analysis_agent has no LLM bound",
                code="common.internal",
                status=500,
            )
        content = await read_text_file(
            self.deps.files,
            self.deps.storage,
            self.ctx,
            file_id,
            max_bytes=_MAX_FILE_BYTES,
            # Spaces plan step 10 — typed as none, not defaulted, on the
            # `rag_agent` precedent, and still none after step 12: the space
            # reached the REQUEST there, not `AgentDeps`. Reading across
            # spaces is the pre-plan behaviour; the check that ends it is
            # already in `read_text_file` and only wants an argument (plan
            # §7).
            space_id=None,
        )
        messages = [
            LlmMessage(role="system", content=SYSTEM_PROMPT),
            LlmMessage(role="user", content=f"{instruction}\n\nData:\n{content}"),
        ]
        params = LlmParams(model=binding.model)
        answer: list[str] = []
        async for chunk in binding.provider.stream(messages, params, binding.api_key):
            if chunk.delta:
                answer.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        yield AgentEvent(type="final", data={"text": "".join(answer), "file_id": file_id})

    @staticmethod
    def _required_str(req: AgentRequest, key: str) -> str:
        value = req.input.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"data_analysis_agent requires a non-empty {key!r}")
        return value

    @staticmethod
    def _optional_str(req: AgentRequest, key: str) -> str | None:
        value = req.input.get(key)
        return value if isinstance(value, str) and value.strip() else None
