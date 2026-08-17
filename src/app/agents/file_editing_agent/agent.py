"""``FileEditingAgent`` — apply an instructed edit to a workspace file (FR-20.5).

A thin stateless coordinator: load the target file's text (via the injected
``files`` + ``storage`` seams), ask the LLM to apply the instruction, and stream
the edited content, ending with a ``final`` carrying the full edited text.

**v1 returns the edit, does not persist it.** Writing the result back (a new
file version via a files write port + ``StorageProvider.put``) is a side effect
deferred to the orchestrator/API (4.7+); this keeps the agent read-only and its
``run`` a pure produce-the-edit coordination. Imports framework only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.agents.file_editing_agent.manifest import METADATA
from app.agents.file_editing_agent.prompts import SYSTEM_PROMPT
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.agent_runtime.file_reading import read_text_file
from app.framework.errors import AppError, ValidationError
from app.framework.ports.llm_provider import LlmMessage, LlmParams

_MAX_FILE_BYTES = 1_000_000  # bound the prompt: the whole file goes to the LLM


class FileEditingAgent(BaseAgent):
    """Produces an edited version of a workspace text file per an instruction."""

    metadata = METADATA

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        file_id = self._required_str(req, "file_id")
        instruction = self._required_str(req, "instruction")
        binding = self.deps.llm
        if binding is None:
            raise AppError(
                detail="file_editing_agent has no LLM bound",
                code="common.internal",
                status=500,
            )
        content = await read_text_file(
            self.deps.files,
            self.deps.storage,
            self.ctx,
            file_id,
            max_bytes=_MAX_FILE_BYTES,
            # Spaces plan step 10 — see the twin comment in
            # `data_analysis_agent`: still no space to pass, because step 12
            # put it on the request and not on `AgentDeps` (plan §7).
            space_id=None,
        )
        messages = [
            LlmMessage(role="system", content=SYSTEM_PROMPT),
            LlmMessage(
                role="user",
                content=f"Instruction: {instruction}\n\nFile content:\n{content}",
            ),
        ]
        params = LlmParams(model=binding.model)
        edited: list[str] = []
        async for chunk in binding.provider.stream(messages, params, binding.api_key):
            if chunk.delta:
                edited.append(chunk.delta)
                yield AgentEvent(type="token", data={"delta": chunk.delta})
        yield AgentEvent(type="final", data={"text": "".join(edited), "file_id": file_id})

    @staticmethod
    def _required_str(req: AgentRequest, key: str) -> str:
        value = req.input.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"file_editing_agent requires a non-empty {key!r}")
        return value
