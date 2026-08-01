"""Unit tests for the OpenAI ``LLMProvider`` adapter
(``infrastructure/ai_providers/llm/openai_llm.py``, Phase 2.8-ب-1): the pure
mapping/guard helpers directly, plus a full hermetic proof of ``complete()``/
``stream()`` via ``httpx.MockTransport`` wired through the adapter's OWN
factory (``create_openai_http_client``) -- the ``test_firebase_auth.py``/
``test_ollama_mapping.py`` ``MockTransport`` idiom. No marker, no Docker, no
live OpenAI; see ``tests/integration/test_openai_llm.py`` for the live
counterpart.

⚠️ R-1, the honest gap (see ``openai_llm.py``'s own module docstring): this
suite is split into two kinds of test.

* **Security tests** (auth headers, key handling, error-message hygiene) are
  logic-only -- they prove THIS CODEBASE's own behaviour and are
  independent of whatever OpenAI's real wire format turns out to be.
* **Wire-format tests** (``_to_result``/``_to_chunk``/``_to_tool_calls`` and
  friends) are ⚠️ ASSUMPTION-BEARING: alpha reached OpenAI exclusively
  through LangChain (refs `llm-providers.md` §2), never the raw wire, and
  never streamed anything at all (§4) -- so every fixture below encodes
  this codebase's BELIEF about OpenAI's JSON shape, not a confirmed
  observation. Only ``tests/integration/test_openai_llm.py`` (``live_openai``,
  currently unrunnable -- no key available anywhere in this environment)
  can confirm it for real. Each assumption-bearing block below is marked.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.framework.errors import AppError, NotFoundError, RateLimitedError, ValidationError
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.types import Json
from app.infrastructure.ai_providers.llm.openai_llm import (
    OpenAILLM,
    _accumulate_tool_call_fragments,
    _build_chat_body,
    _CallFragment,
    _has_tool_role,
    _parse_tool_arguments,
    _sse_payloads,
    _to_chunk,
    _to_result,
    _to_tool_calls,
    _translate_status,
    create_openai_http_client,
)
from app.infrastructure.ai_providers.llm.shared import parse_json_object

_SECRET = "sk-THETESTSECRETVALUE0000000000000000000000"


# --------------------------------------------------------------------------- #
# Shared helpers                                                              #
# --------------------------------------------------------------------------- #
def _ok_payload(
    *,
    content: str | None = "hello",
    finish_reason: str = "stop",
    tool_calls: list[Json] | None = None,
    usage: Json | None = None,
) -> Json:
    """⚠️ ASSUMPTION-BEARING (module docstring): the believed non-streamed
    ``/chat/completions`` shape -- never confirmed against the real wire."""
    message: Json = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    payload: Json = {
        "id": "chatcmpl-test",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage
        if usage is not None
        else {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }
    return payload


def _sse_event(payload: Json | str) -> str:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n"


def _sse_body(*payloads: Json | str) -> str:
    return "".join(_sse_event(p) for p in payloads)


class _RecordingHandler:
    """A ``MockTransport`` handler that records every request and always
    replies with the same canned response (the ``test_ollama_mapping.py``
    ``_RecordingHandler`` precedent)."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._response


class _RaisingHandler:
    """A ``MockTransport`` handler simulating a dead connection/timeout at
    the ``httpx`` transport layer itself."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        raise self._exc


class _TrackingStream(httpx.AsyncByteStream):
    """Wraps SSE bytes as a closeable-and-observable async byte stream -- the
    one hermetic way to prove a response is actually closed (the
    ``test_ollama_mapping.py`` precedent)."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body

    async def aclose(self) -> None:
        self.closed = True


def _llm_with(handler: _RecordingHandler | _RaisingHandler) -> OpenAILLM:
    """An ``OpenAILLM`` wired to ``handler`` through the adapter's OWN
    factory (the ``create_ollama_http_client``/``create_firebase_http_client``
    precedent), so the factory's real ``trust_env``/``timeout``/``base_url``
    choices are what actually get exercised."""
    client = create_openai_http_client(timeout_s=5.0, transport=httpx.MockTransport(handler))
    return OpenAILLM(client)


async def _lines(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


async def _collect(aiter: AsyncIterator[str]) -> list[str]:
    return [item async for item in aiter]


# --------------------------------------------------------------------------- #
# 1-11 -- pure: _build_chat_body                                             #
# --------------------------------------------------------------------------- #
def test_build_chat_body_maps_messages_model_and_stream() -> None:
    messages = [LlmMessage(role="system", content="be nice"), LlmMessage(role="user", content="hi")]
    params = LlmParams(model="gpt-4o-mini")

    body = _build_chat_body(messages, params, stream=False)

    assert body["model"] == "gpt-4o-mini"
    assert body["messages"] == [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "hi"},
    ]
    assert body["stream"] is False


def test_build_chat_body_stream_flag_reflects_the_argument() -> None:
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="m")

    assert _build_chat_body(messages, params, stream=True)["stream"] is True
    assert _build_chat_body(messages, params, stream=False)["stream"] is False


def test_build_chat_body_uses_flat_fields_never_ollamas_nested_options() -> None:
    params = LlmParams(model="m", temperature=0.2, max_tokens=128, top_p=0.5, stop=["END"])

    body = _build_chat_body([LlmMessage(role="user", content="hi")], params, stream=False)

    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 128
    assert body["top_p"] == 0.5
    assert body["stop"] == ["END"]
    assert "options" not in body
    assert "keep_alive" not in body
    assert "think" not in body
    assert "num_ctx" not in body


def test_build_chat_body_requests_usage_when_streaming() -> None:
    """4.7-a (port limit (a)) INVERTS the previous rule. Until the amendment
    ``LlmChunk`` had no token fields, so this adapter deliberately never
    asked for the usage frame -- it was structurally undeliverable. Now that
    the chunk carries counters, not requesting it would be the bug: it is
    the only way OpenAI reports a streamed turn's real token usage, and
    without it every streamed turn bills an estimate."""
    body = _build_chat_body(
        [LlmMessage(role="user", content="hi")], LlmParams(model="m"), stream=True
    )
    assert body["stream_options"] == {"include_usage": True}


def test_build_chat_body_never_sends_stream_options_when_not_streaming() -> None:
    """``stream_options`` is only meaningful alongside ``stream: true``;
    OpenAI rejects it otherwise."""
    body = _build_chat_body(
        [LlmMessage(role="user", content="hi")], LlmParams(model="m"), stream=False
    )
    assert "stream_options" not in body


def test_build_chat_body_omits_optional_keys_when_none() -> None:
    params = LlmParams(model="m", max_tokens=None, top_p=None, stop=None, tools=None)

    body = _build_chat_body([LlmMessage(role="user", content="hi")], params, stream=False)

    assert "max_tokens" not in body
    assert "top_p" not in body
    assert "stop" not in body
    assert "tools" not in body


def test_build_chat_body_omits_stop_and_tools_when_empty_lists() -> None:
    params = LlmParams(model="m", stop=[], tools=[])

    body = _build_chat_body([LlmMessage(role="user", content="hi")], params, stream=False)

    assert "stop" not in body
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


@pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
def test_build_chat_body_accepts_every_port_defined_role(role: str) -> None:
    """``role="tool"`` is accepted, never rejected -- port limit (d) is
    recorded, not hacked around (module docstring)."""
    body = _build_chat_body(
        [LlmMessage(role=role, content="x")], LlmParams(model="m"), stream=False
    )
    assert body["messages"] == [{"role": role, "content": "x"}]


def test_build_chat_body_rejects_an_empty_model() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _build_chat_body([LlmMessage(role="user", content="x")], LlmParams(model=""), stream=False)
    assert excinfo.value.code == "common.validation_error"


def test_has_tool_role_true_only_when_a_tool_message_is_present() -> None:
    assert _has_tool_role([LlmMessage(role="user", content="x")]) is False
    assert _has_tool_role([LlmMessage(role="tool", content="x")]) is True


# --------------------------------------------------------------------------- #
# Security (logic-only, independent of OpenAI's real wire)                   #
# --------------------------------------------------------------------------- #
async def test_complete_sends_the_bearer_authorization_header() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)

    await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "sk-real-key")

    assert handler.calls[0].headers["authorization"] == "Bearer sk-real-key"


async def test_stream_sends_the_bearer_authorization_header() -> None:
    body = _sse_body({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    handler = _RecordingHandler(httpx.Response(200, text=body))
    llm = _llm_with(handler)

    _ = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "sk-real-key"
        )
    ]

    assert handler.calls[0].headers["authorization"] == "Bearer sk-real-key"


async def test_two_calls_with_different_keys_get_two_different_headers_on_the_same_client() -> None:
    """⚠️ Closes the cross-tenant leak (the single biggest trap this adapter
    guards against): the client is process-wide, the key is per-request.
    Killed by moving the header onto ``create_openai_http_client(headers=...)``
    as a client-level default."""
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="m")

    await llm.complete(messages, params, "key-one")
    await llm.complete(messages, params, "key-two")

    assert handler.calls[0].headers["authorization"] == "Bearer key-one"
    assert handler.calls[1].headers["authorization"] == "Bearer key-two"
    assert handler.calls[0].headers["authorization"] != handler.calls[1].headers["authorization"]


def test_create_openai_http_client_sets_no_default_authorization_header() -> None:
    client = create_openai_http_client(timeout_s=5.0)
    assert "authorization" not in client.headers


@pytest.mark.parametrize("api_key", ["", "   "])
async def test_empty_api_key_is_validation_error_before_any_network_call(api_key: str) -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)

    with pytest.raises(ValidationError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), api_key)

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422
    assert handler.calls == []


def test_empty_api_key_stream_guard_raises_before_any_network_call() -> None:
    """A plain (non-``async``) test function -- the ``test_ollama_
    mapping.py`` idiom -- is itself the proof."""
    llm = OpenAILLM(httpx.AsyncClient())

    with pytest.raises(ValidationError):
        llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "   ")


async def test_secret_never_appears_in_any_raised_error_across_the_matrix() -> None:
    """10-code-standards §9: "لا سرّ في السجلّ أو الاستجابة أو الاستثناء"."""

    async def _raise_with(handler: _RecordingHandler | _RaisingHandler) -> AppError:
        llm = _llm_with(handler)
        with pytest.raises(AppError) as excinfo:
            await llm.complete(
                [LlmMessage(role="user", content="hi")], LlmParams(model="m"), _SECRET
            )
        return excinfo.value

    errors = [
        await _raise_with(_RecordingHandler(httpx.Response(401, text="unauthorized"))),
        await _raise_with(_RecordingHandler(httpx.Response(500))),
        await _raise_with(_RecordingHandler(httpx.Response(200, text="not json"))),
        await _raise_with(_RaisingHandler(httpx.ConnectError("refused"))),
    ]

    for error in errors:
        assert _SECRET not in str(error)
        assert _SECRET not in repr(error)
        assert _SECRET not in str(error.detail)


def test_empty_api_key_guard_never_echoes_the_key_itself() -> None:
    """The empty/whitespace case cannot leak a non-empty secret by
    definition, but the guard's OWN message must not echo its (empty)
    input either -- unlike ``ollama_llm._guard_base_url``, which DOES echo
    its bad input (a URL is diagnostic; a key is a secret)."""
    with pytest.raises(ValidationError) as excinfo:
        OpenAILLM(httpx.AsyncClient()).stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "   "
        )
    assert "   " not in str(excinfo.value)


async def test_secret_is_obfuscated_in_the_request_headers_repr() -> None:
    """httpx's own defence (``Headers.__repr__`` -> ``[secure]``) -- a
    backstop, never a substitute for this module's own discipline."""
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)

    await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), _SECRET)

    assert _SECRET not in repr(handler.calls[0].headers)
    assert "[secure]" in repr(handler.calls[0].headers)


# --------------------------------------------------------------------------- #
# ⚠️ ASSUMPTION-BEARING wire-format tests (R-1): fixtures encode this        #
# codebase's BELIEF about OpenAI's wire shape -- only                        #
# tests/integration/test_openai_llm.py (live_openai) confirms it for real.   #
# refs `llm-providers.md` §2/§4: alpha reached OpenAI only via LangChain and #
# never streamed anything -- there is no harvested behavioural reference.    #
# --------------------------------------------------------------------------- #
def test_to_result_maps_the_believed_non_streamed_shape() -> None:
    result = _to_result(_ok_payload(), [LlmMessage(role="user", content="hi")])

    assert result == LlmResult(
        content="hello",
        finish_reason="stop",
        prompt_tokens=12,
        completion_tokens=5,
        tool_calls=None,
    )


def test_to_result_null_content_with_tool_calls_is_empty_string_not_a_raise() -> None:
    """🔴 The highest-value fixture (D2): if this were wrong the other way,
    EVERY tool-calling response would 502."""
    payload = _ok_payload(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q": "x"}'},
            }
        ],
    )

    result = _to_result(payload, [])

    assert result.content == ""
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [{"id": "call_1", "name": "search", "arguments": {"q": "x"}}]


def test_to_result_null_content_with_content_filter_is_empty_string() -> None:
    payload = _ok_payload(content=None, finish_reason="content_filter")

    result = _to_result(payload, [])

    assert result.content == ""
    assert result.finish_reason == "content_filter"


def test_to_result_non_string_non_null_content_is_off_contract() -> None:
    payload = _ok_payload()
    payload["choices"][0]["message"]["content"] = 12345

    with pytest.raises(AppError) as excinfo:
        _to_result(payload, [])
    assert excinfo.value.code == "agent.failed"


def test_to_result_tool_call_arguments_json_string_is_parsed_and_id_is_carried() -> None:
    payload = _ok_payload(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q": "x", "n": 3}'},
            }
        ],
    )

    result = _to_result(payload, [])

    # 4.7-a (port limit (d)): the vendor id is now CARRIED, verbatim -- it is
    # what a `role="tool"` reply sets as `tool_call_id` to correlate its
    # result. Dropping it (the pre-amendment behaviour this assertion used to
    # pin) is exactly what made a tool round-trip impossible.
    assert result.tool_calls == [
        {"id": "call_abc123", "name": "search", "arguments": {"q": "x", "n": 3}}
    ]


def test_to_result_empty_string_arguments_is_a_no_arg_call() -> None:
    payload = _ok_payload(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[
            {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": ""}}
        ],
    )

    result = _to_result(payload, [])

    assert result.tool_calls == [{"id": "call_1", "name": "search", "arguments": {}}]


def test_to_result_undecodable_arguments_is_agent_failed_never_dropped() -> None:
    """D3: diverges from Ollama's lenient drop -- a malformed tool call
    raises, it is never silently dropped."""
    payload = _ok_payload(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{not json"},
            }
        ],
    )

    with pytest.raises(AppError) as excinfo:
        _to_result(payload, [])

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502
    assert excinfo.value.detail == "openai returned a tool call with undecodable arguments"


def test_to_result_falls_back_to_estimated_tokens_when_usage_is_absent() -> None:
    payload = _ok_payload(usage={})
    del payload["usage"]

    result = _to_result(payload, [LlmMessage(role="user", content="12345678")])

    assert result.prompt_tokens == 2  # 8 chars // 4
    assert result.completion_tokens >= 1


def test_to_result_zero_usage_counters_fall_back_to_the_estimate() -> None:
    """The under-billing guard, exercised at the actual call site."""
    payload = _ok_payload(usage={"prompt_tokens": 0, "completion_tokens": 0})

    result = _to_result(payload, [LlmMessage(role="user", content="12345678")])

    assert result.prompt_tokens == 2  # estimate, NOT the reported 0
    assert result.completion_tokens >= 1


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"id": "x"}),  # no "choices" at all
        json.dumps({"choices": []}),  # empty choices
        json.dumps({"choices": ["not-a-dict"]}),  # choices[0] not an object
        json.dumps({"choices": [{"finish_reason": "stop"}]}),  # no "message"
        json.dumps(
            {"choices": [{"message": {"content": 123}, "finish_reason": "stop"}]}
        ),  # bad content type
        json.dumps({"choices": [{"message": {"content": "x"}}]}),  # no finish_reason
        json.dumps([1, 2, 3]),  # non-object body
        json.dumps({"error": {"message": "bad request"}}),  # error key present
    ],
)
def test_off_contract_200_bodies_raise_agent_failed_never_a_bare_exception(raw: str) -> None:
    with pytest.raises(AppError) as excinfo:
        _to_result(parse_json_object("openai", raw), [])

    assert not isinstance(excinfo.value, (KeyError, TypeError, ValueError))
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


def test_to_chunk_maps_delta_content() -> None:
    payload = {"choices": [{"delta": {"content": "he"}, "finish_reason": None}]}
    assert _to_chunk(payload) == LlmChunk(delta="he", finish_reason=None)


def test_to_chunk_first_chunk_role_only_delta_has_empty_string_content() -> None:
    payload = {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
    assert _to_chunk(payload) == LlmChunk(delta="", finish_reason=None)


def test_to_chunk_terminal_chunk_carries_finish_reason() -> None:
    payload = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    assert _to_chunk(payload) == LlmChunk(delta="", finish_reason="stop")


def test_to_chunk_off_shape_degrades_to_an_empty_delta_never_raises() -> None:
    """Deliberately lenient (the ``ollama_llm._to_chunk`` precedent): only
    ``parse_json_object`` guards fatally; anything past that is a partial
    delta."""
    assert _to_chunk({}) == LlmChunk(delta="", finish_reason=None)
    assert _to_chunk({"choices": []}) == LlmChunk(delta="", finish_reason=None)


@pytest.mark.parametrize(
    ("status", "tool_role_sent", "expected_detail"),
    [
        (401, False, "openai rejected the api key"),
        (401, True, "openai rejected the api key"),
        (429, False, "openai call failed: rate limited"),
        (429, True, "openai call failed: rate limited"),
        (404, False, "openai model not available"),
        (
            400,
            True,
            "openai call failed: a 'tool' role message is not expressible through this port",
        ),
        (400, False, "openai call failed"),
        (500, False, "openai call failed"),
    ],
)
def test_translate_status_maps_every_documented_case(
    status: int, tool_role_sent: bool, expected_detail: str
) -> None:
    error = _translate_status(status, tool_role_sent=tool_role_sent)

    assert error.code == "agent.failed"
    assert error.status == 502
    assert error.detail == expected_detail


def test_parse_tool_arguments_none_and_empty_string_both_mean_no_args() -> None:
    assert _parse_tool_arguments(None) == {}
    assert _parse_tool_arguments("") == {}


def test_parse_tool_arguments_non_string_is_agent_failed() -> None:
    with pytest.raises(AppError) as excinfo:
        _parse_tool_arguments(123)
    assert excinfo.value.code == "agent.failed"


def test_parse_tool_arguments_a_json_array_is_agent_failed() -> None:
    """Must decode to an OBJECT specifically, not just valid JSON."""
    with pytest.raises(AppError) as excinfo:
        _parse_tool_arguments("[1, 2, 3]")
    assert excinfo.value.code == "agent.failed"


def test_to_tool_calls_missing_function_object_raises() -> None:
    message = {"tool_calls": [{"id": "call_1", "type": "function"}]}
    with pytest.raises(AppError) as excinfo:
        _to_tool_calls(message)
    assert excinfo.value.code == "agent.failed"


@pytest.mark.parametrize("entry", ["not-a-dict", 123, None, ["nested"]])
def test_to_tool_calls_a_non_dict_entry_is_agent_failed_never_a_raw_attributeerror(
    entry: object,
) -> None:
    """R6 regression guard for THE defect class this codebase shipped three
    times (2.5, 2.6 and 2.7 each returned one at the verifier gate; 2.7's was
    literally this: a non-dict element inside a list, reached with ``.get()``,
    escaping as a raw ``AttributeError``).

    ``_to_tool_calls``'s ``isinstance(entry, dict)`` guard is load-bearing --
    without it a ``tool_calls`` list holding a non-dict raises
    ``AttributeError: 'str' object has no attribute 'get'`` straight out of
    the adapter, untranslated, on a path that handles a tenant's request.
    Deleting that guard was a mutation the suite ORIGINALLY SURVIVED (found
    by the 2.8-ب-1 verifier gate), so this test exists to make it bite.
    """
    message = {"tool_calls": [entry]}
    with pytest.raises(AppError) as excinfo:
        _to_tool_calls(message)
    assert not isinstance(excinfo.value, ValidationError)
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


def test_to_tool_calls_a_non_dict_entry_raises_even_beside_a_valid_one() -> None:
    """The mixed list: one good call, one poisoned entry. A lenient drop
    would return the good call alone and silently lose the other -- the
    "confidently wrong, unflagged answer" §7 D3 forbids."""
    message = {
        "tool_calls": [
            {"function": {"name": "search", "arguments": '{"q": "x"}'}},
            "not-a-dict",
        ]
    }
    with pytest.raises(AppError) as excinfo:
        _to_tool_calls(message)
    assert excinfo.value.code == "agent.failed"


@pytest.mark.parametrize("message", [{"content": "x"}, {"content": "x", "tool_calls": []}])
def test_to_tool_calls_absent_or_empty_is_none(message: Json) -> None:
    assert _to_tool_calls(message) is None


# --------------------------------------------------------------------------- #
# supports() / provider                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("capability", "expected"),
    [("streaming", True), ("tools", True), ("vision", False), ("unknown-capability", False)],
)
def test_supports_matches_the_capability_table(capability: str, expected: bool) -> None:
    llm = OpenAILLM(httpx.AsyncClient())
    assert llm.supports(capability) is expected


def test_provider_attribute_is_openai() -> None:
    assert OpenAILLM(httpx.AsyncClient()).provider == "openai"


# --------------------------------------------------------------------------- #
# SSE framing (logic-only, provable hermetically -- spec-derived)            #
# --------------------------------------------------------------------------- #
async def test_sse_payloads_dispatches_on_blank_line() -> None:
    payloads = await _collect(_sse_payloads(_lines('data: {"a": 1}', "")))
    assert payloads == ['{"a": 1}']


async def test_sse_payloads_skips_comment_lines() -> None:
    payloads = await _collect(_sse_payloads(_lines(": a heartbeat comment", 'data: {"a": 1}', "")))
    assert payloads == ['{"a": 1}']


async def test_sse_payloads_skips_event_id_and_retry_fields() -> None:
    payloads = await _collect(
        _sse_payloads(_lines("event: message", "id: 1", "retry: 3000", 'data: {"a": 1}', ""))
    )
    assert payloads == ['{"a": 1}']


async def test_sse_payloads_strips_exactly_one_leading_space() -> None:
    payloads = await _collect(_sse_payloads(_lines("data:  {'a': 1}", "")))
    assert payloads == [" {'a': 1}"]  # only ONE leading space is spec-stripped


async def test_sse_payloads_joins_multi_line_data_with_newline() -> None:
    payloads = await _collect(_sse_payloads(_lines("data: line1", "data: line2", "")))
    assert payloads == ["line1\nline2"]


async def test_sse_payloads_flushes_a_dangling_event_at_eof() -> None:
    """Deviates from the SSE spec (which says discard): a server closing
    without a trailing blank line must not cost a complete, valid terminal
    event."""
    payloads = await _collect(_sse_payloads(_lines('data: {"a": 1}')))
    assert payloads == ['{"a": 1}']


async def test_sse_payloads_yields_done_literally_as_a_payload() -> None:
    payloads = await _collect(_sse_payloads(_lines("data: [DONE]", "")))
    assert payloads == ["[DONE]"]


async def test_sse_payloads_ignores_lines_with_no_colon() -> None:
    payloads = await _collect(_sse_payloads(_lines("justtext", 'data: {"a": 1}', "")))
    assert payloads == ['{"a": 1}']


# --------------------------------------------------------------------------- #
# hermetic: complete()                                                       #
# --------------------------------------------------------------------------- #
async def test_complete_posts_the_built_body_with_the_authorization_header() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="gpt-4o-mini")

    await llm.complete(messages, params, "some-api-key")

    assert len(handler.calls) == 1
    request = handler.calls[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    assert json.loads(request.content) == _build_chat_body(messages, params, stream=False)
    assert request.headers["authorization"] == "Bearer some-api-key"


async def test_complete_401_is_agent_failed_with_the_key_rejected_detail() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(401, text="unauthorized")))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "bad-key")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502
    assert excinfo.value.detail == "openai rejected the api key"


async def test_complete_429_is_agent_failed_never_rate_limited() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(429, text="rate limited")))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k")

    assert not isinstance(excinfo.value, RateLimitedError)
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_complete_404_is_agent_failed_never_not_found() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(404, text="model not found")))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="x"), "k")

    assert not isinstance(excinfo.value, NotFoundError)
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_complete_400_with_a_tool_role_message_names_the_port_limit() -> None:
    handler = _RecordingHandler(httpx.Response(400, text="bad request"))
    llm = _llm_with(handler)
    messages = [LlmMessage(role="user", content="hi"), LlmMessage(role="tool", content="result")]

    with pytest.raises(AppError) as excinfo:
        await llm.complete(messages, LlmParams(model="m"), "k")

    assert (
        excinfo.value.detail
        == "openai call failed: a 'tool' role message is not expressible through this port"
    )


async def test_complete_400_without_a_tool_role_message_is_generic() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(400, text="bad request")))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k")

    assert excinfo.value.detail == "openai call failed"


async def test_complete_500_is_agent_failed() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(500)))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_complete_non_json_body_is_agent_failed() -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text="not json")))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k")

    assert excinfo.value.code == "agent.failed"


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("timed out")])
async def test_complete_transport_failures_are_agent_failed(exc: Exception) -> None:
    llm = _llm_with(_RaisingHandler(exc))

    with pytest.raises(AppError) as excinfo:
        await llm.complete([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k")

    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


async def test_complete_validation_error_is_never_swallowed_and_makes_no_call() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_payload()))
    llm = _llm_with(handler)

    with pytest.raises(ValidationError):
        await llm.complete([], LlmParams(model="m"), "k")

    assert handler.calls == []


# --------------------------------------------------------------------------- #
# hermetic: stream()                                                         #
# --------------------------------------------------------------------------- #
async def test_stream_yields_the_believed_shape_including_the_terminal_chunk() -> None:
    body = _sse_body(
        {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "4"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        "[DONE]",
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        )
    ]

    assert chunks == [
        LlmChunk(delta="", finish_reason=None),
        LlmChunk(delta="4", finish_reason=None),
        LlmChunk(delta="", finish_reason="stop"),
    ]


async def test_stream_ignores_comment_event_id_and_retry_lines() -> None:
    body = (
        ": heartbeat\n"
        + "event: message\n"
        + 'data: {"choices": [{"delta": {"content": "a"}, "finish_reason": null}]}\n\n'
        + "retry: 3000\n"
        + 'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        )
    ]

    assert [c.delta for c in chunks] == ["a", ""]


async def test_stream_tolerates_garbage_after_the_terminal_chunk() -> None:
    """4.7-a REPLACES "never read past the terminal chunk" -- collecting
    OpenAI's ``include_usage`` frame, which arrives AFTER ``finish_reason``,
    structurally requires reading past it (port limit (a)).

    The safety property that rule protected is preserved, and this test now
    pins it directly: the old rule existed to prevent SILENT TRUNCATION of
    the answer, which is impossible once the terminal chunk is in hand. So a
    malformed trailing frame must be IGNORED -- never parsed into a spurious
    ``agent.failed`` on an answer that arrived complete, and never emitted as
    an extra chunk. Contrast
    ``test_stream_a_malformed_data_line_raises_rather_than_being_skipped``,
    which proves a malformed line BEFORE the terminal chunk still raises."""
    body = (
        _sse_body({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        + "data: this-is-not-json\n\n"
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        )
    ]

    assert chunks == [LlmChunk(delta="", finish_reason="stop")]


async def test_stream_a_malformed_data_line_raises_rather_than_being_skipped() -> None:
    """The silent-truncation mutation probe: replacing this raise with a
    ``continue`` must turn this test RED (alpha's own silent-truncation bug
    class, refs §7).

    Asserting the exact ``detail`` is what makes that claim TRUE, and is not
    a stylistic nicety. The 2.8-ب-1 verifier gate found this test passing
    under its own named mutation: the malformed line is the LAST event, so a
    skipping loop just falls through to the "stream ended before completion"
    raise -- which also carries ``agent.failed``, so a code-only assertion
    cannot tell the two apart, and the probe silently proved nothing. A
    trailing well-formed terminal event is added below for the same reason:
    it removes the fall-through entirely, so the ONLY way to reach this
    raise is the malformed line itself.
    """
    body = (
        _sse_body({"choices": [{"delta": {"content": "a"}, "finish_reason": None}]})
        + "data: not-json\n\n"
        + _sse_body({"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]})
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        ):
            chunks.append(chunk)

    # Only the pre-malformed delta was yielded: the stream dies AT the bad
    # line and never reaches the (valid) terminal event behind it.
    assert [c.delta for c in chunks] == ["a"]
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.detail == "openai returned a non-JSON response body"


async def test_stream_an_error_payload_mid_stream_raises_and_is_never_yielded() -> None:
    body = (
        _sse_body({"choices": [{"delta": {"content": "a"}, "finish_reason": None}]})
        + _sse_event({"error": {"message": "server error"}})
        + _sse_body({"choices": [{"delta": {"content": "never-reached"}, "finish_reason": None}]})
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        ):
            chunks.append(chunk)

    assert [c.delta for c in chunks] == ["a"]
    assert excinfo.value.code == "agent.failed"


async def test_stream_without_a_terminal_chunk_raises() -> None:
    body = _sse_body(
        {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "b"}, "finish_reason": None}]},
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        ):
            chunks.append(chunk)

    assert [c.delta for c in chunks] == ["a", "b"]
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502
    assert excinfo.value.detail == "openai stream ended before completion"


async def test_stream_done_sentinel_without_a_prior_finish_reason_still_raises() -> None:
    """``[DONE]`` is framing, never the semantic terminator."""
    body = _sse_body(
        {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]},
        "[DONE]",
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        ):
            chunks.append(chunk)

    assert [c.delta for c in chunks] == ["a"]
    assert excinfo.value.detail == "openai stream ended before completion"


@pytest.mark.parametrize("status", [400, 401, 404, 429])
async def test_stream_4xx_before_any_chunk_is_agent_failed(status: int) -> None:
    llm = _llm_with(_RecordingHandler(httpx.Response(status, text="error body never read")))

    chunks: list[LlmChunk] = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        ):
            chunks.append(chunk)

    assert chunks == []
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


def test_stream_guard_raises_at_call_time_not_at_first_anext() -> None:
    """A plain (synchronous) test function -- no ``async def``, no
    ``await``, no ``async for`` anywhere -- is itself the proof."""
    llm = OpenAILLM(httpx.AsyncClient())  # never touched -- zero network either way

    with pytest.raises(ValidationError):
        llm.stream([], LlmParams(model="m"), "some-key")


async def test_stream_request_has_stream_true_and_the_authorization_header() -> None:
    body = _sse_body({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    handler = _RecordingHandler(httpx.Response(200, text=body))
    llm = _llm_with(handler)
    messages = [LlmMessage(role="user", content="hi")]
    params = LlmParams(model="m")

    _ = [c async for c in llm.stream(messages, params, "some-api-key")]

    assert len(handler.calls) == 1
    request = handler.calls[0]
    assert json.loads(request.content)["stream"] is True
    assert request.headers["authorization"] == "Bearer some-api-key"


async def test_stream_closing_the_generator_early_closes_the_response() -> None:
    """Proves the ``async with`` inside ``_iter_chunks`` really closes the
    OpenAI connection when the caller is done early (the
    ``test_ollama_mapping.py`` ``_TrackingStream`` idiom -- an explicit
    ``aclose()`` rather than a bare ``break``, since CPython gives no timing
    guarantee for the latter)."""
    body = _sse_body(
        {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    )
    stream = _TrackingStream(body.encode())
    llm = _llm_with(_RecordingHandler(httpx.Response(200, stream=stream)))

    generator = llm.stream([LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k")
    first = await generator.__anext__()
    await generator.aclose()

    assert first.delta == "a"
    assert stream.closed is True


# --------------------------------------------------------------------------- #
# 4.7-a -- the port amendment: tool ROUND-TRIP + streamed counters + tools    #
# --------------------------------------------------------------------------- #
def test_build_chat_body_replays_a_tool_round_trip() -> None:
    """Port limit (d): both halves of the round-trip on the wire -- the
    assistant turn that ISSUED the calls (with OpenAI's `type` discriminator
    and `arguments` re-encoded as a JSON STRING) and the tool result that
    correlates back to it via `tool_call_id`."""
    messages = [
        LlmMessage(role="user", content="weather?"),
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "call_abc", "name": "search", "arguments": {"q": "x"}}],
        ),
        LlmMessage(role="tool", content="sunny", tool_call_id="call_abc"),
    ]

    body = _build_chat_body(messages, LlmParams(model="m"), stream=False)

    assert body["messages"][1] == {
        "role": "assistant",
        # OpenAI requires null, not "", when the payload IS the tool calls.
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q": "x"}'},
            }
        ],
    }
    assert body["messages"][2] == {
        "role": "tool",
        "content": "sunny",
        "tool_call_id": "call_abc",
    }


def test_build_chat_body_re_encodes_arguments_as_a_json_string_not_an_object() -> None:
    """The asymmetry a naive round-trip gets silently wrong: `arguments`
    arrives as a JSON string, is decoded into the neutral shape, and must be
    RE-encoded on the way back out. Sending the object would 400."""
    messages = [
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "f", "arguments": {"a": 1}}],
        )
    ]

    body = _build_chat_body(messages, LlmParams(model="m"), stream=False)

    arguments = body["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(arguments, str)
    assert json.loads(arguments) == {"a": 1}


def test_build_chat_body_keeps_assistant_content_when_it_accompanies_tool_calls() -> None:
    """A model may emit prose AND tool calls in the same turn; only an EMPTY
    content becomes null."""
    messages = [
        LlmMessage(
            role="assistant",
            content="let me check",
            tool_calls=[{"id": "c1", "name": "f", "arguments": {}}],
        )
    ]

    body = _build_chat_body(messages, LlmParams(model="m"), stream=False)

    assert body["messages"][0]["content"] == "let me check"


@pytest.mark.parametrize(
    "call",
    [
        {"name": "search", "arguments": {}},  # no id
        {"id": "", "name": "search", "arguments": {}},  # empty id
        {"id": 123, "name": "search", "arguments": {}},  # non-str id
        {"id": "c1", "arguments": {}},  # no name
        {"id": "c1", "name": "", "arguments": {}},  # empty name
        {"id": "c1", "name": "search"},  # no arguments
        {"id": "c1", "name": "search", "arguments": "not-an-object"},
    ],
)
def test_build_chat_body_rejects_a_malformed_tool_call_as_a_caller_mistake(call: Json) -> None:
    """``ValidationError`` (a caller mistake), never ``agent.failed``: these
    entries came from OUR OWN caller replaying a previous turn, so a 502
    would blame OpenAI for something it never sent."""
    messages = [LlmMessage(role="assistant", content="", tool_calls=[call])]

    with pytest.raises(ValidationError):
        _build_chat_body(messages, LlmParams(model="m"), stream=False)


async def test_stream_carries_the_usage_frame_onto_the_terminal_chunk() -> None:
    """Port limit (a), the whole point of the amendment: OpenAI sends usage
    in a frame AFTER `finish_reason`, so the pre-4.7-a loop -- which returned
    at the terminal chunk -- could never have seen it, and every streamed
    turn billed an estimate."""
    body = _sse_body(
        {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 4}},
        "[DONE]",
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        )
    ]

    assert [c.delta for c in chunks] == ["hi", ""]
    assert chunks[-1].finish_reason == "stop"
    assert (chunks[-1].prompt_tokens, chunks[-1].completion_tokens) == (11, 4)


async def test_stream_reports_none_counters_when_no_usage_frame_arrives() -> None:
    """A provider/proxy that ignores `include_usage` must yield an honest
    "unknown" -- never a fabricated count."""
    body = _sse_body({"choices": [{"delta": {}, "finish_reason": "stop"}]}, "[DONE]")
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        )
    ]

    assert chunks[-1].prompt_tokens is None
    assert chunks[-1].completion_tokens is None


async def test_stream_assembles_tool_call_fragments_onto_the_terminal_chunk() -> None:
    """Port limit (b): OpenAI streams `arguments` as partial JSON-string
    pieces correlated only by `index`. The port requires them ASSEMBLED and
    decoded on the terminal chunk -- a fragment like `{"q"` is not valid
    JSON and must never reach a caller."""
    body = _sse_body(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_abc",
                                "function": {"name": "search", "arguments": ""},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"q"'}}]},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "x"}'}}]},
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        )
    ]

    assert all(c.tool_calls is None for c in chunks[:-1])
    assert chunks[-1].tool_calls == [{"id": "call_abc", "name": "search", "arguments": {"q": "x"}}]


async def test_stream_assembles_several_tool_calls_ordered_by_index() -> None:
    body = _sse_body(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 1, "id": "c2", "function": {"name": "b", "arguments": "{}"}},
                            {"index": 0, "id": "c1", "function": {"name": "a", "arguments": "{}"}},
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    chunks = [
        c
        async for c in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        )
    ]

    calls = chunks[-1].tool_calls
    assert calls is not None
    assert [c["name"] for c in calls] == ["a", "b"]


def test_accumulate_tool_call_fragments_skips_a_bool_index() -> None:
    """`bool` is an `int` subclass in Python: an unguarded `isinstance(index,
    int)` would file `True` under index 1, silently merging it with a real
    call's fragments."""
    buffer: dict[int, _CallFragment] = {}

    _accumulate_tool_call_fragments(
        {"tool_calls": [{"index": True, "id": "c1", "function": {"name": "a"}}]}, buffer
    )

    assert buffer == {}


def test_accumulate_tool_call_fragments_joins_arguments_in_arrival_order() -> None:
    buffer: dict[int, _CallFragment] = {}

    _accumulate_tool_call_fragments(
        {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{"}}]},
        buffer,
    )
    _accumulate_tool_call_fragments(
        {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}, buffer
    )

    assert buffer[0].call_id == "c1"
    assert buffer[0].name == "f"
    assert "".join(buffer[0].arguments) == "{}"


async def test_stream_undecodable_assembled_tool_call_is_agent_failed() -> None:
    """The streaming path reuses `_to_tool_calls`, so it inherits the same
    strict policy as the non-streamed one: a named-but-undecodable call is
    never silently dropped into a confidently wrong answer."""
    body = _sse_body(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "f", "arguments": "{not json"},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    )
    llm = _llm_with(_RecordingHandler(httpx.Response(200, text=body)))

    with pytest.raises(AppError) as excinfo:
        async for _ in llm.stream(
            [LlmMessage(role="user", content="hi")], LlmParams(model="m"), "k"
        ):
            pass

    assert excinfo.value.code == "agent.failed"
