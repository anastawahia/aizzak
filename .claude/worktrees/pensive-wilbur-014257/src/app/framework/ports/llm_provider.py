"""LLMProvider driven port (02-port-contracts §1.1, D-15, DD-13).

Provider-neutral: the contract is the common denominator across OpenAI / Gemini
/ Claude / Ollama / OpenRouter and leaks no vendor detail. Concrete adapters
live in ``infrastructure/ai_providers/llm`` and are bound in the Composition
Root. All I/O is async; ``stream`` is an async generator.

**Amended in 4.7-a** (the recorded blocker on the orchestrator: 2.8-a/2.8-ب-1
observed four limits here but were correctly forbidden from touching the port
themselves — ``openai_llm.py``'s own docstring says "if confirmed live, the
fix belongs on the PORT, not a hack in this adapter"). All four are now
resolved, every change ADDITIVE with a ``None`` default so both shipped
adapters and every caller keep compiling:

* **(a)** ``LlmChunk`` gained ``prompt_tokens``/``completion_tokens`` — a
  streamed turn used to yield ZERO usage data for both shipped adapters, so
  ``UsageCapture`` could only ever bill an estimate.
* **(b)** ``LlmChunk`` gained ``tool_calls`` — streaming AND tools is now
  expressible in one turn, so tool use no longer forces ``complete()``.
* **(c)** ``supports()`` is documented as a hint, with ``D-16`` as the place
  of truth (see the method).
* **(d)** ``LlmMessage`` gained ``tool_call_id``/``tool_calls`` — the port
  declared a ``tool`` role that no adapter could correlate back to the call
  it answered, making a full tool round-trip impossible.

The neutral tool-call shape is ``{"id": str, "name": str, "arguments": Json}``
(``infrastructure/.../shared.py::neutral_tool_call`` owns it). ``id`` is
vendor-supplied where one exists (OpenAI) and adapter-synthesised where it
does not (Ollama emits none), so callers may always correlate a result to its
call without knowing which provider answered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class LlmMessage:
    """One conversation turn.

    ``role="tool"`` carries a tool RESULT and must set ``tool_call_id`` to
    the ``id`` of the call it answers. An ``assistant`` turn that ISSUED
    calls replays them via ``tool_calls`` (the same neutral shape
    ``LlmResult.tool_calls`` yields), with ``content=""`` when the model
    returned no prose alongside them. Both fields are what makes a full tool
    ROUND-TRIP expressible (4.7-a, port limit (d)): before them the port
    declared a ``tool`` role no adapter could actually correlate.
    """

    role: str  # system | user | assistant | tool
    content: str
    # Set on a role="tool" message: which call this result answers.
    tool_call_id: str | None = None
    # Set on a role="assistant" message: the calls that turn issued.
    tool_calls: list[Json] | None = None


@dataclass(frozen=True, slots=True)
class LlmParams:
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    tools: list[Json] | None = None  # neutral tool schemas


@dataclass(frozen=True, slots=True)
class LlmChunk:
    """One streamed increment.

    Two guarantees every adapter MUST honour, so the orchestrator can treat
    every provider identically (4.7-a, port limits (a) and (b)):

    * **Tool calls arrive ASSEMBLED, on the terminal chunk** (the one
      carrying ``finish_reason``) — never as vendor fragments. OpenAI streams
      ``arguments`` as partial JSON *string* pieces across frames; Ollama
      emits them whole. Accumulating that is each adapter's job, because a
      half-decoded argument blob is not a ``Json`` and leaking the
      reassembly rules would make the port vendor-shaped.
    * **Token counters, when known, arrive on the terminal chunk.** ``None``
      means the provider did not report them and the caller must estimate —
      an honest "unknown", never a silent zero. This is what lets
      ``UsageCapture`` (FR-134) bill a streamed turn exactly rather than
      always estimating, and it is why ``11 §8.2``'s sequence diagram (which
      draws an ``LlmResult`` after the stream loop the port never delivers)
      is reconciled to this contract instead.
    """

    delta: str
    finish_reason: str | None = None
    # Terminal chunk only — assembled, never fragments. Same neutral shape
    # as ``LlmResult.tool_calls``.
    tool_calls: list[Json] | None = None
    # Terminal chunk only. ``None`` = provider reported nothing ⇒ estimate.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LlmResult:
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    tool_calls: list[Json] | None = None


class LLMProvider(Protocol):
    provider: str  # 'openai' | 'gemini' | 'claude' | 'ollama' | 'openrouter'

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult: ...

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]: ...

    def supports(self, capability: str) -> bool:
        """A ROUTING HINT, never a guarantee (port limit (c), resolved 4.7-a).

        Capability is model-dependent for at least one shipped adapter
        (Ollama: ``gemma3:1b`` 400s a tool-calling request while the adapter
        reports ``tools=True``), and this query is model-agnostic by
        construction. The authoritative answer is the ``D-16`` routing table
        the orchestrator resolves against; a caller may use this to skip an
        obviously-wrong provider, but MUST still treat a capability refusal
        at call time as a normal translated failure rather than an
        impossible state. ``'tools' | 'vision' | 'streaming'``.
        """
        ...
