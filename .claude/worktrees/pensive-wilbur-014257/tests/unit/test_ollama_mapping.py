"""Unit tests for the Ollama ``LLMProvider`` adapter
(``infrastructure/ai_providers/llm/ollama_llm.py``, Phase 2.8-a): the pure
mapping/guard helpers directly, plus a full hermetic proof of ``complete()``/
``stream()`` via ``httpx.MockTransport`` wired through the adapter's OWN
factory (``create_ollama_http_client``) -- the ``test_firebase_auth.py``
``MockTransport`` idiom. No marker, no Docker, no live Ollama; see
``tests/integration/test_ollama_llm.py`` for the live counterpart.

Phase 2.8-ب-1 extracted this adapter's technical primitives
(``_estimate_tokens``, ``_off_contract``, ``_parse_payload``, ``_token_count``,
``_translate``) into ``shared.py``; their own tests moved with them to
``test_llm_shared.py`` (they test the SHARED primitive directly, not
anything Ollama-specific). What stays HERE is everything that still is
Ollama-specific: ``_build_chat_body``'s NDJSON/``options`` shape,
``_to_result``/``_to_tool_calls``'s Ollama wire shape, ``_translate_status``'s
own 404/400 vocabulary, and the full ``complete()``/``stream()`` hermetic
proof -- unchanged in behaviour by the extraction (see
``tests/integration/test_ollama_llm.py``'s live 6/6 regression net).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.framework.errors import AppError, NotFoundError, ValidationError
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.settings.settings import OllamaSettings
from app.framework.types import Json
from app.infrastructure.ai_providers.llm.ollama_llm import (
    OllamaLLM,
    _build_chat_body,
    _guard_base_url,
    _to_result,
    _to_tool_calls,
    _translate_status,
    create_ollama_http_client,
)
from app.infrastructure.ai_providers.llm.shared import parse_json_object


# --------------------------------------------------------------------------- #
# Shared helpers                                                              #
# --------------------------------------------------------------------------- #
def _ok_payload(**overrides: Any) -> Json:
    """The confirmed-live non-streamed shape (task brief's own probe)."""
    payload: Json = {
        "model": "gemma3:1b",
        "message": {"role": "assistant", "content": "hello"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 12,
        "eval_count": 5,
    }
    payload.update(overrides)
    return payload


def _ndjson(*lines: Json) -> str:
    return "".join(json.dumps(line) + "\n" for line in lines)


class _RecordingHandler:
    """A ``MockTransport`` handler that records every request and always
    replies with the same canned response -- the ``_JwksHandler``
    precedent (``test_firebase_auth.py``), simplified: nothing here needs
    per-call mutable behaviour."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._response


class _RaisingHandler:
    """A ``MockTransport`` handler simulating a dead connection/timeout at
    the ``httpx`` transport layer itself -- raises instead of ever
    returning a response."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        raise self._exc


class _TrackingStream(httpx.AsyncByteStream):
    """Wraps NDJSON bytes as a closeable-and-observable async byte stream --
    the one hermetic way to prove a response is actually closed: a plain
    ``text=``/``json=`` ``httpx.Response`` builds an ordinary in-memory
    stream with no observable close signal."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body

    async def aclose(self) -> None:
        self.closed = True


def _llm_with(handler: _RecordingHandler | _RaisingHandler) -> OllamaLLM:
    """An ``OllamaLLM`` wired to ``handler`` through the adapter's OWN
    factory (the ``create_firebase_http_client``/``create_vault_client``
    precedent), so the factory's real ``trust_env``/``timeout``/``base_url``
    choices are what actually get exercised."""
    client = create_ollama_http_client(
        OllamaSettings(base_url="http://ollama.test"),
        timeout_s=5.0,
        transport=httpx.MockTransport(handler),
    )
    return OllamaLLM(client)


# --------------------------------------------------------------------------- #
# 1-9 -- pure: _build_chat_body                                              #
# --------------------------------------------------------------------------- #
def test_build_chat_body_maps_messages_model_and_stream() -> None:
    messages = [LlmMessage(role="system", content="be nice"), LlmMessage(role="user", content="hi")]
    params = LlmParams(model="gemma3:1b")

    body = _build_chat_body(messages, params, stream=False)

    assert body["model"] == "gemma3:1b"
    assert body["messages"] == [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "hi"},
    ]
    assert body["stream"] is False


def test_build_chat_body_stream_flag_reflects_the_argument() -> None:
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="gemma3:1b")

    assert _build_chat_body(messages, params, stream=True)["stream"] is True
    assert _build_chat_body(messages, params, stream=False)["stream"] is False


@pytest.mark.parametrize("stream", [False, True])
def test_build_chat_body_always_carries_the_pinned_values(stream: bool) -> None:
    """think:false, keep_alive:"30m" and num_ctx:8192 (refs §6) are on
    EVERY request, streamed or not."""
    body = _build_chat_body(
        [LlmMessage(role="user", content="hi")], LlmParams(model="m"), stream=stream
    )

    assert body["think"] is False
    assert body["keep_alive"] == "30m"
    assert body["options"]["num_ctx"] == 8192


def test_build_chat_body_maps_llm_params_onto_options() -> None:
    params = LlmParams(model="m", temperature=0.2, max_tokens=128, top_p=0.5, stop=["END"])

    options = _build_chat_body([LlmMessage(role="user", content="hi")], params, stream=False)[
        "options"
    ]

    assert options["temperature"] == 0.2
    assert options["num_predict"] == 128
    assert options["top_p"] == 0.5
    assert options["stop"] == ["END"]


def test_build_chat_body_omits_optional_keys_when_none() -> None:
    params = LlmParams(model="m", max_tokens=None, top_p=None, stop=None, tools=None)

    body = _build_chat_body([LlmMessage(role="user", content="hi")], params, stream=False)

    assert "num_predict" not in body["options"]
    assert "top_p" not in body["options"]
    assert "stop" not in body["options"]
    assert "tools" not in body


def test_build_chat_body_omits_stop_and_tools_when_empty_lists() -> None:
    params = LlmParams(model="m", stop=[], tools=[])

    body = _build_chat_body([LlmMessage(role="user", content="hi")], params, stream=False)

    assert "stop" not in body["options"]
    assert "tools" not in body


def test_build_chat_body_includes_tools_only_when_present() -> None:
    tools = [{"type": "function", "function": {"name": "search"}}]
    params = LlmParams(model="m", tools=tools)

    body = _build_chat_body([LlmMessage(role="user", content="hi")], params, stream=False)

    assert body["tools"] == tools


def test_build_chat_body_rejects_empty_messages() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _build_chat_body([], LlmParams(model="m"), stream=False)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


def test_build_chat_body_rejects_an_unknown_role() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _build_chat_body(
            [LlmMessage(role="admin", content="x")], LlmParams(model="m"), stream=False
        )
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


@pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
def test_build_chat_body_accepts_every_port_defined_role(role: str) -> None:
    body = _build_chat_body(
        [LlmMessage(role=role, content="x")], LlmParams(model="m"), stream=False
    )
    assert body["messages"] == [{"role": role, "content": "x"}]


def test_build_chat_body_rejects_an_empty_model() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _build_chat_body([LlmMessage(role="user", content="x")], LlmParams(model=""), stream=False)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


async def test_api_key_never_changes_the_request_body_by_a_single_byte() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="m")

    await llm.complete(messages, params, "key-one")
    await llm.complete(messages, params, "key-two")

    assert len(handler.calls) == 2
    assert handler.calls[0].content == handler.calls[1].content


# --------------------------------------------------------------------------- #
# 10-15 -- pure: _to_result / _to_tool_calls / _token_count                   #
# --------------------------------------------------------------------------- #
def test_to_result_maps_the_confirmed_live_shape() -> None:
    result = _to_result(_ok_payload(), [LlmMessage(role="user", content="hi")])

    assert result == LlmResult(
        content="hello",
        finish_reason="stop",
        prompt_tokens=12,
        completion_tokens=5,
        tool_calls=None,
    )


def test_to_result_passes_done_reason_through_literally() -> None:
    payload = _ok_payload(done_reason="length")
    assert _to_result(payload, []).finish_reason == "length"


def test_to_result_maps_tool_calls_to_the_neutral_shape() -> None:
    payload = _ok_payload(
        message={
            "content": "",
            "tool_calls": [{"function": {"name": "search", "arguments": {"q": "x"}}}],
        },
        done_reason="tool_calls",
    )

    result = _to_result(payload, [])

    # 4.7-a: Ollama emits no id, so one is SYNTHESISED positionally.
    assert result.tool_calls == [{"id": "call_0", "name": "search", "arguments": {"q": "x"}}]


@pytest.mark.parametrize("message", [{"content": "x"}, {"content": "x", "tool_calls": []}])
def test_to_tool_calls_absent_or_empty_is_none(message: Json) -> None:
    assert _to_tool_calls(message) is None


def test_to_tool_calls_drops_malformed_entries_but_keeps_well_formed_ones() -> None:
    message = {
        "tool_calls": [
            {"function": {"name": "search", "arguments": {"q": "x"}}},
            {"function": {"name": "broken"}},  # missing "arguments"
            "not-even-a-dict",
        ]
    }
    assert _to_tool_calls(message) == [{"id": "call_0", "name": "search", "arguments": {"q": "x"}}]


def test_to_tool_calls_synthesises_ids_from_the_raw_index_not_the_kept_position() -> None:
    """4.7-a: a dropped malformed entry must NOT renumber the calls after it
    -- ids come from the RAW payload's index. If this regressed to
    enumerating the kept list, the surviving second call would claim
    ``call_0``, the id already implicitly associated with the dropped one."""
    message = {
        "tool_calls": [
            {"function": {"name": "broken"}},  # dropped: no "arguments"
            {"function": {"name": "search", "arguments": {"q": "x"}}},
        ]
    }
    assert _to_tool_calls(message) == [{"id": "call_1", "name": "search", "arguments": {"q": "x"}}]


def test_to_tool_calls_ids_are_unique_across_several_calls() -> None:
    message = {
        "tool_calls": [
            {"function": {"name": "a", "arguments": {}}},
            {"function": {"name": "b", "arguments": {}}},
        ]
    }
    calls = _to_tool_calls(message)
    assert calls is not None
    assert [c["id"] for c in calls] == ["call_0", "call_1"]


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"done": True, "done_reason": "stop"}),  # no "message" at all
        json.dumps({"message": "not-a-dict", "done": True, "done_reason": "stop"}),
        json.dumps(
            {"message": {"content": 123}, "done": True, "done_reason": "stop"}
        ),  # bad content
        json.dumps({"message": {"content": "x"}, "done": True}),  # no done_reason
        json.dumps([1, 2, 3]),  # non-object body
        json.dumps({"error": "model crashed"}),  # error key present
    ],
)
def test_off_contract_200_bodies_raise_agent_failed_never_a_bare_exception(raw: str) -> None:
    with pytest.raises(AppError) as excinfo:
        _to_result(parse_json_object("ollama", raw), [])

    assert not isinstance(excinfo.value, KeyError)
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


# --------------------------------------------------------------------------- #
# 16-19 -- pure: supports / provider / _guard_base_url / _parse_payload      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("capability", "expected"),
    [("streaming", True), ("tools", True), ("vision", False), ("unknown-capability", False)],
)
def test_supports_matches_the_capability_table(capability: str, expected: bool) -> None:
    llm = OllamaLLM(httpx.AsyncClient())
    assert llm.supports(capability) is expected


def test_provider_attribute_is_ollama() -> None:
    assert OllamaLLM(httpx.AsyncClient()).provider == "ollama"


@pytest.mark.parametrize("base_url", ["http://x:1", "https://x", " http://x "])
def test_guard_base_url_accepts_http_and_https(base_url: str) -> None:
    assert _guard_base_url(base_url) == base_url.strip()


@pytest.mark.parametrize("base_url", ["", "   ", "ftp://x", "x.example.com"])
def test_guard_base_url_rejects_empty_or_schemeless(base_url: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _guard_base_url(base_url)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# pure: _translate_status -- Ollama's OWN status vocabulary. NOT extracted: it #
# shares nothing with OpenAI's but the fallthrough, which IS extracted as      #
# shared.off_contract. The generic primitives this file used to cover          #
# (_parse_payload / _off_contract / _translate / _token_count /                #
# _estimate_tokens) moved to shared.py in 2.8-b-1 and their tests moved WITH   #
# them to tests/unit/test_llm_shared.py -- deliberately not duplicated here.   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "tools_sent", "expected_detail"),
    [
        (404, False, "ollama model not available"),
        (404, True, "ollama model not available"),
        (400, True, "ollama call failed: this model may not support tools"),
        (400, False, "ollama call failed"),
        (500, False, "ollama call failed"),
    ],
)
def test_translate_status_maps_every_confirmed_live_case(
    status: int, tools_sent: bool, expected_detail: str
) -> None:
    error = _translate_status(status, tools_sent=tools_sent)

    assert error.code == "agent.failed"
    assert error.status == 502
    assert error.detail == expected_detail


# --------------------------------------------------------------------------- #
# 20-26 -- hermetic: complete()                                              #
# --------------------------------------------------------------------------- #
async def test_complete_posts_the_built_body_with_no_authorization_header() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="gemma3:1b")

    await llm.complete(messages, params, "some-api-key")

    assert len(handler.calls) == 1
    request = handler.calls[0]
    assert request.method == "POST"
    assert request.url.path == "/api/chat"
    assert json.loads(request.content) == _build_chat_body(messages, params, stream=False)
    assert "authorization" not in request.headers


async def test_complete_404_is_agent_failed_never_not_found() -> None:
    handler = _RecordingHandler(httpx.Response(404, json={"error": "model 'x' not found"}))
    llm = _llm_with(handler)

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="x"), "")

    assert not isinstance(excinfo.value, NotFoundError)
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_complete_400_with_tools_sent_names_the_tools_detail() -> None:
    body = httpx.Response(
        400, json={"error": "registry.ollama.ai/library/gemma3:1b does not support tools"}
    )
    llm = _llm_with(_RecordingHandler(body))
    params = LlmParams(model="gemma3:1b", tools=[{"type": "function", "function": {"name": "x"}}])

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], params, "")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.detail == "ollama call failed: this model may not support tools"


async def test_complete_500_is_agent_failed() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(500)))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_complete_non_json_body_is_agent_failed() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text="not json")))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")

    assert excinfo.value.code == "agent.failed"


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("timed out")])
async def test_complete_transport_failures_are_agent_failed(exc: Exception) -> None:
    llm = _llm_with(_RaisingHandler(exc))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_complete_validation_error_is_never_swallowed_and_makes_no_call() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)

    with pytest.raises(ValidationError):
        await llm.complete([], LlmParams(model="m"), "")

    assert handler.calls == []


# --------------------------------------------------------------------------- #
# 27-36 -- hermetic: stream()                                                #
# --------------------------------------------------------------------------- #
async def test_stream_yields_the_confirmed_live_shape_including_the_terminal_chunk() -> None:
    body = _ndjson(
        {"message": {"content": "4"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 18,
            "eval_count": 3,
        },
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    ]

    # 4.7-a (port limit (a)): the terminal chunk now CARRIES the counters
    # Ollama puts on its final NDJSON line. Before the amendment both were
    # discarded and a streamed turn was unbillable.
    assert chunks == [
        LlmChunk(delta="4", finish_reason=None),
        LlmChunk(delta="", finish_reason="stop", prompt_tokens=18, completion_tokens=3),
    ]


async def test_stream_ignores_blank_lines() -> None:
    body = (
        "\n"
        + _ndjson({"message": {"content": "a"}, "done": False})
        + "\n\n"
        + _ndjson({"message": {"content": ""}, "done": True, "done_reason": "stop"})
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    ]

    assert [c.delta for c in chunks] == ["a", ""]


async def test_stream_never_reads_past_the_terminal_line() -> None:
    body = (
        _ndjson({"message": {"content": ""}, "done": True, "done_reason": "stop"})
        + "this line would blow up if it were ever parsed\n"
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    ]

    assert chunks == [LlmChunk(delta="", finish_reason="stop")]


async def test_stream_a_malformed_line_raises_rather_than_being_skipped() -> None:
    body = _ndjson({"message": {"content": "a"}, "done": False}) + "not-json\n"
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), ""
        ):
            chunks.append(chunk)

    assert [c.delta for c in chunks] == ["a"]
    assert excinfo.value.code == "agent.failed"


async def test_stream_an_error_line_mid_stream_raises_and_is_never_yielded() -> None:
    body = (
        _ndjson({"message": {"content": "a"}, "done": False})
        + json.dumps({"error": "model crashed"})
        + "\n"
        + _ndjson({"message": {"content": "never-reached"}, "done": False})
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), ""
        ):
            chunks.append(chunk)

    assert [c.delta for c in chunks] == ["a"]
    assert excinfo.value.code == "agent.failed"


async def test_stream_without_a_terminal_line_raises() -> None:
    body = _ndjson(
        {"message": {"content": "a"}, "done": False},
        {"message": {"content": "b"}, "done": False},
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), ""
        ):
            chunks.append(chunk)

    assert [c.delta for c in chunks] == ["a", "b"]
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


@pytest.mark.parametrize("status", [400, 404])
async def test_stream_4xx_before_any_chunk_is_agent_failed(status: int) -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(status, json={"error": "boom"})))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), ""
        ):
            chunks.append(chunk)

    assert chunks == []
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


def test_stream_guard_raises_at_call_time_not_at_first_anext() -> None:
    """A plain (synchronous) test function -- no ``async def``, no
    ``await``, no ``async for`` anywhere -- is itself the proof: calling
    ``stream()`` with invalid arguments raises immediately, as an ordinary
    function call, never deferred to the async generator's first
    ``__anext__``."""
    llm = OllamaLLM(httpx.AsyncClient())  # never touched -- zero network either way

    with pytest.raises(ValidationError):
        llm.stream([], LlmParams(model="m"), "")


async def test_stream_request_has_stream_true_and_no_authorization_header() -> None:
    body = _ndjson({"message": {"content": ""}, "done": True, "done_reason": "stop"})
    handler = _RecordingHandler(httpx.Response(200, text=body))
    llm = _llm_with(handler)
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="m")

    _ = [c async for c in llm.stream(messages, params, "some-api-key")]

    assert len(handler.calls) == 1
    request = handler.calls[0]
    assert json.loads(request.content)["stream"] is True
    assert "authorization" not in request.headers


async def test_stream_closing_the_generator_early_closes_the_response() -> None:
    """Proves the ``async with`` inside ``_iter_chunks`` really closes the
    Ollama connection when the caller is done early. Exercised via an
    EXPLICIT ``aclose()`` on the generator (the ``contextlib.aclosing``
    idiom) rather than a bare ``break``: CPython gives no timing guarantee
    that abandoning an async generator via ``break`` alone runs its
    finalizer synchronously (cleanup is scheduled through the loop's
    asyncgen hooks instead, verified experimentally against this very
    adapter) -- only an explicit close is deterministic to assert on.
    """
    stream = _TrackingStream(
        _ndjson(
            {"message": {"content": "a"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop"},
        ).encode()
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, stream=stream)))

    generator = llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    first = await generator.__anext__()
    await generator.aclose()

    assert first.delta == "a"
    assert stream.closed is True


# --------------------------------------------------------------------------- #
# 4.7-a -- the port amendment: tool ROUND-TRIP + streamed token counters      #
# --------------------------------------------------------------------------- #
def test_build_chat_body_replays_an_assistant_turns_tool_calls() -> None:
    """Port limit (d): replaying the assistant turn that ISSUED the calls is
    half of what makes a tool round-trip expressible. The neutral id is
    dropped because Ollama's envelope has no slot for it."""
    messages = [
        LlmMessage(role="user", content="weather?"),
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "call_0", "name": "search", "arguments": {"q": "x"}}],
        ),
        LlmMessage(role="tool", content="sunny", tool_call_id="call_0"),
    ]

    body = _build_chat_body(messages, LlmParams(model="m"), stream=False)

    assert body["messages"][1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "search", "arguments": {"q": "x"}}}],
    }
    # Ollama correlates POSITIONALLY and has no  field.
    assert body["messages"][2] == {"role": "tool", "content": "sunny"}


@pytest.mark.parametrize(
    "call",
    [
        {"id": "call_0", "arguments": {}},  # no name
        {"id": "call_0", "name": "", "arguments": {}},  # empty name
        {"id": "call_0", "name": 123, "arguments": {}},  # non-str name
        {"id": "call_0", "name": "search"},  # no arguments
        {"id": "call_0", "name": "search", "arguments": "not-an-object"},
    ],
)
def test_build_chat_body_rejects_a_malformed_tool_call_as_a_caller_mistake(call: Json) -> None:
    """ValidationError (a caller mistake), never agent.failed: these
    entries came from OUR OWN caller replaying a previous turn, so blaming
    Ollama with a 502 would point every operator at the wrong system."""
    messages = [LlmMessage(role="assistant", content="", tool_calls=[call])]

    with pytest.raises(ValidationError):
        _build_chat_body(messages, LlmParams(model="m"), stream=False)


async def test_stream_assembles_tool_calls_onto_the_terminal_chunk() -> None:
    """Port limit (b): streaming AND tools in one turn. The port requires
    the calls ASSEMBLED on the terminal chunk, never as per-line fragments."""
    body = _ndjson(
        {"message": {"content": "thinking"}, "done": False},
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "search", "arguments": {"q": "x"}}}],
            },
            "done": False,
        },
        {"message": {"content": ""}, "done": True, "done_reason": "tool_calls"},
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    ]

    assert [c.tool_calls for c in chunks[:-1]] == [None, None]
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls == [{"id": "call_0", "name": "search", "arguments": {"q": "x"}}]


async def test_stream_tool_call_ids_stay_unique_across_lines() -> None:
    """The collision this adapter must not have: mapping line-by-line would
    restart id synthesis on every line and emit two call_0s in one turn
    -- destroying the very correlation the ids exist to provide."""
    body = _ndjson(
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "a", "arguments": {}}}],
            },
            "done": False,
        },
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "b", "arguments": {}}}],
            },
            "done": False,
        },
        {"message": {"content": ""}, "done": True, "done_reason": "tool_calls"},
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    ]

    calls = chunks[-1].tool_calls
    assert calls is not None
    assert [c["name"] for c in calls] == ["a", "b"]
    assert len({c["id"] for c in calls}) == 2


async def test_stream_reports_none_counters_when_ollama_reports_none() -> None:
    """None means "estimate", and must never be a fabricated number --
    the distinction UsageCapture bills on (FR-134)."""
    body = _ndjson(
        {"message": {"content": "hi"}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop"},
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    ]

    assert chunks[-1].prompt_tokens is None
    assert chunks[-1].completion_tokens is None


async def test_stream_never_takes_counters_from_a_mid_stream_line() -> None:
    """Counters ride the terminal line only: a mid-stream line carrying them
    is a partial count and must not be reported as the turn's usage."""
    body = _ndjson(
        {"message": {"content": "a"}, "done": False, "prompt_eval_count": 99, "eval_count": 99},
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 7,
            "eval_count": 2,
        },
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "")
    ]

    assert chunks[0].prompt_tokens is None
    assert (chunks[-1].prompt_tokens, chunks[-1].completion_tokens) == (7, 2)
