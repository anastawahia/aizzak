"""09-testing-strategy §5's mandated parametrized port<->adapter Liskov
suite: "منافذ ↔ محوّلات: طقم اختبار مشترك (parametrized) يُشغَّل على كل محوّل
مقابل عقد المنفذ ⇒ قابلية الاستبدال (Liskov)." The SAME assertions run
against every shipped ``LLMProvider`` adapter (Ollama, 2.8-a; OpenAI,
2.8-ب-1), each built through its OWN factory + ``httpx.MockTransport`` (the
``test_firebase_auth.py`` idiom) -- proving SUBSTITUTABILITY, not any one
adapter's wire format (that lives in ``test_ollama_mapping.py``/
``test_openai_mapping.py``).

Only satisfiable at N=2 shipped adapters -- 2.8-a explicitly deferred this
mandate to "the second adapter" (docs/implementation-status.md §3.23)
rather than write a parametrized suite of one.

Bodies are intentionally shaped so the SAME assertions hold for Ollama's
NDJSON and OpenAI's SSE without either adapter needing to change: a
non-terminal chunk always has no ``finish_reason``, and a terminal one
always does, regardless of the wire framing underneath.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pytest

from app.framework.errors import AppError, ValidationError
from app.framework.ports.llm_provider import LlmMessage, LlmParams, LLMProvider
from app.framework.settings.settings import OllamaSettings
from app.infrastructure.ai_providers.llm.ollama_llm import OllamaLLM, create_ollama_http_client
from app.infrastructure.ai_providers.llm.openai_llm import OpenAILLM, create_openai_http_client

_DUMMY_KEY = "dummy-key"  # non-empty, per §4.3 rule 4: makes the auth guard moot here.
_MESSAGES = [LlmMessage(role="user", content="hi")]
_PARAMS = LlmParams(model="test-model")

Handler = Callable[[httpx.Request], httpx.Response]


class _RecordingHandler:
    """A ``MockTransport`` handler that records every request and always
    replies with the same canned response (the ``test_ollama_mapping.py``
    precedent)."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._response


class _RaisingHandler:
    """A ``MockTransport`` handler simulating a dead connection at the
    ``httpx`` transport layer itself."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        raise self._exc


@dataclass(frozen=True)
class AdapterCase:
    """Everything the shared suite needs from ONE adapter -- its own
    factory, its own wire shapes -- so every assertion below stays
    adapter-agnostic."""

    name: str
    build: Callable[[Handler], LLMProvider]
    off_contract_body: str  # valid JSON, valid object, but missing required fields
    stream_no_terminal: str  # a non-terminal chunk, then EOF -- no finish_reason ever
    stream_with_terminal: str  # one non-terminal chunk, then one WITH a finish_reason


def _ollama_case() -> AdapterCase:
    def build(handler: Handler) -> LLMProvider:
        client = create_ollama_http_client(
            OllamaSettings(base_url="http://ollama.test"),
            timeout_s=5.0,
            transport=httpx.MockTransport(handler),
        )
        return OllamaLLM(client)

    return AdapterCase(
        name="ollama",
        build=build,
        off_contract_body="{}",
        stream_no_terminal='{"message": {"content": "a"}, "done": false}\n',
        stream_with_terminal=(
            '{"message": {"content": "a"}, "done": false}\n'
            '{"message": {"content": ""}, "done": true, "done_reason": "stop"}\n'
        ),
    )


def _openai_case() -> AdapterCase:
    def build(handler: Handler) -> LLMProvider:
        client = create_openai_http_client(timeout_s=5.0, transport=httpx.MockTransport(handler))
        return OpenAILLM(client)

    return AdapterCase(
        name="openai",
        build=build,
        off_contract_body="{}",
        stream_no_terminal=(
            'data: {"choices": [{"delta": {"content": "a"}, "finish_reason": null}]}\n\n'
        ),
        stream_with_terminal=(
            'data: {"choices": [{"delta": {"content": "a"}, "finish_reason": null}]}\n\n'
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
            "data: [DONE]\n\n"
        ),
    )


_CASES = [_ollama_case(), _openai_case()]
_CASE_IDS = [case.name for case in _CASES]


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_provider_is_a_non_empty_string(case: AdapterCase) -> None:
    llm = case.build(_RecordingHandler(httpx.Response(200)))
    assert isinstance(llm.provider, str)
    assert llm.provider != ""


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
@pytest.mark.parametrize(
    "capability", ["tools", "vision", "streaming", "", "an-unknown-capability"]
)
def test_supports_returns_bool_and_never_raises(case: AdapterCase, capability: str) -> None:
    llm = case.build(_RecordingHandler(httpx.Response(200)))
    assert isinstance(llm.supports(capability), bool)


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_stream_guard_raises_validation_error_synchronously(case: AdapterCase) -> None:
    """A plain (non-``async``) test function -- no ``await``/``async for``
    anywhere in its body -- IS ITSELF THE PROOF (the ``test_ollama_
    mapping.py`` idiom): the ``ValidationError`` from an empty ``messages``
    list is raised by the ``stream()`` CALL itself, for BOTH adapters, never
    deferred to the generator's first ``__anext__``."""
    llm = case.build(_RecordingHandler(httpx.Response(200)))

    with pytest.raises(ValidationError):
        llm.stream([], _PARAMS, _DUMMY_KEY)


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
async def test_complete_with_empty_messages_raises_validation_error_with_zero_requests(
    case: AdapterCase,
) -> None:
    handler = _RecordingHandler(httpx.Response(200, json={}))
    llm = case.build(handler)

    with pytest.raises(ValidationError):
        await llm.complete([], _PARAMS, _DUMMY_KEY)

    assert handler.calls == []


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
async def test_complete_transport_failure_is_agent_failed_never_a_bare_httpx_error(
    case: AdapterCase,
) -> None:
    llm = case.build(_RaisingHandler(httpx.ConnectError("refused")))

    with pytest.raises(AppError) as excinfo:
        await llm.complete(_MESSAGES, _PARAMS, _DUMMY_KEY)

    assert not isinstance(excinfo.value, httpx.HTTPError)
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
async def test_complete_off_contract_200_is_agent_failed_never_a_bare_exception(
    case: AdapterCase,
) -> None:
    llm = case.build(_RecordingHandler(httpx.Response(200, text=case.off_contract_body)))

    with pytest.raises(AppError) as excinfo:
        await llm.complete(_MESSAGES, _PARAMS, _DUMMY_KEY)

    assert not isinstance(excinfo.value, (KeyError, TypeError, ValueError))
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
async def test_stream_without_a_terminal_chunk_raises_agent_failed(case: AdapterCase) -> None:
    llm = case.build(_RecordingHandler(httpx.Response(200, text=case.stream_no_terminal)))

    chunks = []
    with pytest.raises(AppError) as excinfo:
        async for chunk in llm.stream(_MESSAGES, _PARAMS, _DUMMY_KEY):
            chunks.append(chunk)

    assert len(chunks) >= 1
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
async def test_stream_terminal_chunk_is_yielded_and_carries_the_only_finish_reason(
    case: AdapterCase,
) -> None:
    llm = case.build(_RecordingHandler(httpx.Response(200, text=case.stream_with_terminal)))

    chunks = [c async for c in llm.stream(_MESSAGES, _PARAMS, _DUMMY_KEY)]

    assert len(chunks) >= 2
    assert all(chunk.finish_reason is None for chunk in chunks[:-1])
    assert chunks[-1].finish_reason is not None
