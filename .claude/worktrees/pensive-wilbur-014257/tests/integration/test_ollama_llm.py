"""Live-Ollama tests for ``OllamaLLM`` (02-port-contracts §1.1, D-15,
Phase 2.8-a).

Runs against a real, local Ollama instance (no Docker -- see
``tests/integration/conftest.py``'s ``live_ollama``); auto-skips when
unreachable OR when the expected model is not pulled locally.

No text is pinned anywhere: a 1B model is not a deterministic function of
its input even at ``temperature=0`` (``LlmParams`` carries no seed field to
make it one), so every assertion here is about STRUCTURE and BEHAVIOUR --
non-empty content, the right ``finish_reason``, positive token counts, the
streaming contract's exact shape, and the two confirmed-live failure modes
(an unknown model; tool-calling against a model that does not support
tools) -- never a specific generated string.
"""

from __future__ import annotations

import pytest

from app.framework.errors import AppError
from app.framework.ports.llm_provider import LlmMessage, LlmParams
from app.infrastructure.ai_providers.llm.ollama_llm import OllamaLLM
from tests.integration.conftest import LiveOllama

pytestmark = [pytest.mark.live_ollama]


async def test_complete_round_trip_returns_a_well_formed_result(
    ollama_llm: OllamaLLM, live_ollama: LiveOllama
) -> None:
    messages = [LlmMessage(role="user", content="Reply with the single word: hello")]
    params = LlmParams(model=live_ollama.model, temperature=0)

    result = await ollama_llm.complete(messages, params, "")

    assert result.content.strip() != ""
    assert result.finish_reason == "stop"
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0


async def test_complete_with_max_tokens_one_is_cut_short_by_length(
    ollama_llm: OllamaLLM, live_ollama: LiveOllama
) -> None:
    messages = [LlmMessage(role="user", content="Count slowly from one to one hundred.")]
    params = LlmParams(model=live_ollama.model, temperature=0, max_tokens=1)

    result = await ollama_llm.complete(messages, params, "")

    assert result.finish_reason == "length"
    assert result.completion_tokens <= 2


async def test_stream_round_trip_yields_text_deltas_with_one_terminal_chunk(
    ollama_llm: OllamaLLM, live_ollama: LiveOllama
) -> None:
    messages = [LlmMessage(role="user", content="Reply with the single word: hello")]
    params = LlmParams(model=live_ollama.model, temperature=0)

    chunks = [c async for c in ollama_llm.stream(messages, params, "")]

    assert len(chunks) >= 1
    assert all(isinstance(chunk.delta, str) for chunk in chunks)
    # Only the LAST chunk may carry a finish_reason -- the port's one
    # end-of-stream signal (02 §1.1).
    assert all(chunk.finish_reason is None for chunk in chunks[:-1])
    assert chunks[-1].finish_reason == "stop"
    assert "".join(chunk.delta for chunk in chunks).strip() != ""


async def test_stream_terminal_chunk_reports_real_token_counters(
    ollama_llm: OllamaLLM, live_ollama: LiveOllama
) -> None:
    """LIVE proof that 4.7-a resolved port limit (a) against a REAL provider.

    The limit as recorded in 2.8-a: "a streamed call yields ZERO token data
    through this port". Ollama put ``prompt_eval_count``/``eval_count`` on
    its terminal NDJSON line all along and this adapter discarded both,
    because ``LlmChunk`` had nowhere to carry them -- so ``UsageCapture``
    (FR-134) could only ever bill a streamed turn by estimate. This asserts
    the counters now arrive, from the real server, on the terminal chunk and
    nowhere else.

    Deliberately asserts ``> 0`` rather than an exact number: the exact
    count is the model's business and would make this test a brittle
    tautology. What must hold is that a REPORTED count arrives at all.
    """
    messages = [LlmMessage(role="user", content="Reply with the single word: hello")]
    params = LlmParams(model=live_ollama.model, temperature=0)

    chunks = [c async for c in ollama_llm.stream(messages, params, "")]

    terminal = chunks[-1]
    assert terminal.finish_reason == "stop"
    assert terminal.prompt_tokens is not None and terminal.prompt_tokens > 0
    assert terminal.completion_tokens is not None and terminal.completion_tokens > 0
    # Counters ride the terminal chunk ONLY -- a mid-stream count would be a
    # partial figure and must never be billed as the turn's usage.
    assert all(chunk.prompt_tokens is None for chunk in chunks[:-1])
    assert all(chunk.completion_tokens is None for chunk in chunks[:-1])


async def test_unknown_model_is_agent_failed(ollama_llm: OllamaLLM) -> None:
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="totally-unknown-model-xyz", temperature=0)

    with pytest.raises(AppError) as excinfo:
        await ollama_llm.complete(messages, params, "")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_tools_on_a_non_tool_calling_model_is_agent_failed(
    ollama_llm: OllamaLLM, live_ollama: LiveOllama
) -> None:
    """Negative confirmation (task brief's own live probe): the confirmed
    local model rejects any tool-calling request with HTTP 400."""
    messages = [LlmMessage(role="user", content="What's the weather in Cairo?")]
    params = LlmParams(
        model=live_ollama.model,
        temperature=0,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
    )

    with pytest.raises(AppError) as excinfo:
        await ollama_llm.complete(messages, params, "")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_a_nonempty_api_key_is_silently_ignored(
    ollama_llm: OllamaLLM, live_ollama: LiveOllama
) -> None:
    """Live proof of Decision 2: Ollama has no auth of its own to present
    an ``api_key`` to, and this adapter never reads the parameter's value
    -- a real call with a non-empty key behaves identically to one with
    none at all."""
    messages = [LlmMessage(role="user", content="Reply with the single word: hello")]
    params = LlmParams(model=live_ollama.model, temperature=0)

    without_key = await ollama_llm.complete(messages, params, "")
    with_key = await ollama_llm.complete(messages, params, "some-nonempty-api-key")

    assert without_key.finish_reason == with_key.finish_reason == "stop"
    assert without_key.content.strip() != ""
    assert with_key.content.strip() != ""
