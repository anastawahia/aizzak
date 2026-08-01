"""Live-OpenAI tests for ``OpenAILLM`` (02-port-contracts §1.1, D-15,
Phase 2.8-ب-1).

Runs against the REAL OpenAI API (no Docker, no local server -- see
``tests/integration/conftest.py``'s ``live_openai``); auto-skips unless the
user has exported ``TEST_OPENAI_API_KEY`` in their OWN shell (the
``TEST_MINIO_*``/``TEST_VAULT_TOKEN`` precedent -- the secret is never
pasted into chat and never read by the assistant). As of this adapter's
authorship there was no key available anywhere in this environment (zero
``OPENAI*`` env var; Vault's KV store empty) -- this suite could not be run,
only written to run correctly the day a key appears:

    TEST_OPENAI_API_KEY=... wsl -d Ubuntu-24.04 -- bash -lc \\
        'cd /home/AIZZAK && .venv/bin/pytest tests/integration/test_openai_llm.py -v'

No text is pinned anywhere: exactly like Ollama's live suite, every
assertion here is about STRUCTURE and BEHAVIOUR -- non-empty content, the
right ``finish_reason``, positive token counts, the streaming contract's
exact shape, and the confirmed-live failure modes -- never a specific
generated string. This suite is also the ONLY place that can confirm
``tests/unit/test_openai_mapping.py``'s wire-format fixtures actually match
OpenAI's real wire shape (that module's own R-1 honest-limitation
paragraph).
"""

from __future__ import annotations

import pytest

from app.framework.errors import AppError
from app.framework.ports.llm_provider import LlmMessage, LlmParams
from app.infrastructure.ai_providers.llm.openai_llm import OpenAILLM
from tests.integration.conftest import LiveOpenAI

pytestmark = [pytest.mark.live_openai]


async def test_complete_round_trip_returns_a_well_formed_result(
    openai_llm: OpenAILLM, live_openai: LiveOpenAI
) -> None:
    """L1: closes the non-streamed body shape + ``usage`` assumption."""
    messages = [LlmMessage(role="user", content="Reply with the single word: hello")]
    params = LlmParams(model=live_openai.model, temperature=0)

    result = await openai_llm.complete(messages, params, live_openai.api_key)

    assert result.content.strip() != ""
    assert result.finish_reason == "stop"
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0


async def test_complete_with_max_tokens_one_is_cut_short_by_length(
    openai_llm: OpenAILLM, live_openai: LiveOpenAI
) -> None:
    """L2: 2.8-a's strong behavioural assertion, ported."""
    messages = [LlmMessage(role="user", content="Count slowly from one to one hundred.")]
    params = LlmParams(model=live_openai.model, temperature=0, max_tokens=1)

    result = await openai_llm.complete(messages, params, live_openai.api_key)

    assert result.finish_reason == "length"


async def test_stream_round_trip_yields_deltas_with_one_terminal_chunk(
    openai_llm: OpenAILLM, live_openai: LiveOpenAI
) -> None:
    """L3: closes the ENTIRE SSE frame-shape assumption -- the highest-value
    live case, since SSE has zero behavioural reference anywhere in alpha."""
    messages = [LlmMessage(role="user", content="Reply with the single word: hello")]
    params = LlmParams(model=live_openai.model, temperature=0)

    chunks = [c async for c in openai_llm.stream(messages, params, live_openai.api_key)]

    assert len(chunks) >= 1
    assert all(isinstance(chunk.delta, str) for chunk in chunks)
    # Only the LAST chunk may carry a finish_reason -- the port's one
    # end-of-stream signal (02 §1.1).
    assert all(chunk.finish_reason is None for chunk in chunks[:-1])
    assert chunks[-1].finish_reason == "stop"
    assert "".join(chunk.delta for chunk in chunks).strip() != ""


async def test_tool_call_round_trip_maps_to_the_neutral_shape(
    openai_llm: OpenAILLM, live_openai: LiveOpenAI
) -> None:
    """L4: closes the "arguments is a JSON string" assumption -- the one
    path 2.8-a could NOT prove live (gemma3:1b cannot do tools)."""
    messages = [LlmMessage(role="user", content="What is the weather in Cairo? Use the tool.")]
    params = LlmParams(
        model=live_openai.model,
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

    result = await openai_llm.complete(messages, params, live_openai.api_key)

    assert result.tool_calls is not None
    for call in result.tool_calls:
        assert isinstance(call["name"], str) and call["name"]
        assert isinstance(call["arguments"], dict)


async def test_unknown_model_is_agent_failed(
    openai_llm: OpenAILLM, live_openai: LiveOpenAI
) -> None:
    """L5: closes the 404 assumption."""
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="totally-unknown-model-xyz", temperature=0)

    with pytest.raises(AppError) as excinfo:
        await openai_llm.complete(messages, params, live_openai.api_key)

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_a_wrong_api_key_is_agent_failed_and_never_leaked(
    openai_llm: OpenAILLM, live_openai: LiveOpenAI
) -> None:
    """L6: closes the 401 assumption + the leak rule, live. Deliberately
    does NOT use ``live_openai.api_key`` -- a wrong key needs no real key at
    all, so this case runs even if the exported key happens to be invalid
    for some OTHER reason (it would just fail earlier, the same way)."""
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model=live_openai.model, temperature=0)
    wrong_key = "sk-definitely-not-a-real-key-00000000000000000000"

    with pytest.raises(AppError) as excinfo:
        await openai_llm.complete(messages, params, wrong_key)

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502
    assert excinfo.value.detail == "openai rejected the api key"
    assert wrong_key not in str(excinfo.value)
    assert wrong_key not in repr(excinfo.value)


async def test_a_tool_role_message_records_what_actually_happens(
    openai_llm: OpenAILLM, live_openai: LiveOpenAI
) -> None:
    """L7: port limit (d), turned from a claim into evidence
    (``openai_llm.py``'s module docstring, §8 of the design brief): asserts
    the OBSERVED outcome rather than assuming OpenAI 400s a bare ``tool``
    role message with no preceding assistant ``tool_calls`` turn -- if this
    instead succeeds, port limit (d) narrows, and the fix belongs on the
    PORT, not this adapter (a hack here would be reacting to an unverified
    belief, exactly what this step must not do)."""
    messages = [
        LlmMessage(role="user", content="hi"),
        LlmMessage(role="tool", content='{"result": "sunny"}'),
    ]
    params = LlmParams(model=live_openai.model, temperature=0)

    try:
        result = await openai_llm.complete(messages, params, live_openai.api_key)
    except AppError as exc:
        assert exc.code == "agent.failed"
        assert exc.status == 502
    else:
        assert isinstance(result.content, str)
