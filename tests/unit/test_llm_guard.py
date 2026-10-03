"""capacity-plan 6.1 -- the per-provider guard (``ai_providers/llm/guard.py``)
and the failure classification it reads (``shared.ProviderFailure``).

The guard is driven through a scripted fake ``LLMProvider`` with a fake clock
and a recording ``sleep``, so every assertion is about the guard's own
decisions -- when it refuses, when it calls, when it waits -- and nothing
here waits for real time. The classification half runs the REAL adapters
over ``httpx.MockTransport`` (the ``test_llm_provider_contract.py`` idiom):
the guard is only as good as what the adapters tell it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence

import httpx
import pytest

from app.framework.errors import RateLimitedError, ValidationError
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.settings.settings import OllamaSettings, ProviderGuardSettings
from app.infrastructure.ai_providers.llm.guard import (
    GuardedLLM,
    ProviderGuard,
    guard_adapters,
    pool_size,
)
from app.infrastructure.ai_providers.llm.ollama_llm import OllamaLLM, create_ollama_http_client
from app.infrastructure.ai_providers.llm.openai_llm import OpenAILLM, create_openai_http_client
from app.infrastructure.ai_providers.llm.shared import (
    ProviderFailure,
    create_llm_http_client,
    parse_retry_after,
)

_MESSAGES = [LlmMessage(role="user", content="hi")]
_PARAMS = LlmParams(model="m")
_RESULT = LlmResult(content="ok", finish_reason="stop", prompt_tokens=1, completion_tokens=1)

_DOWN = ProviderFailure("fake call failed", retryable=True)  # refused connection
_READ_TIMEOUT = ProviderFailure("fake call timed out", transient=True)
_BAD_KEY = ProviderFailure("fake rejected the api key")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _FakeLLM:
    """Answers each call from a script: an exception to raise, or a result.
    For ``stream`` a script entry is a list of chunks, optionally ending in
    an exception raised after them."""

    provider = "fake"

    def __init__(self, script: Sequence[object] = ()) -> None:
        self.script = list(script)
        self.calls = 0
        self.closed = 0
        self.gate: asyncio.Event | None = None

    def _next(self) -> object:
        self.calls += 1
        return self.script.pop(0) if self.script else _RESULT

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        item = self._next()
        if self.gate is not None:
            await self.gate.wait()
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, LlmResult)
        return item

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        if not messages:
            raise ValidationError("messages must not be empty")
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[LlmChunk]:
        item = self._next()
        steps = item if isinstance(item, list) else [item]
        try:
            for step in steps:
                if isinstance(step, BaseException):
                    raise step
                if isinstance(step, LlmChunk):
                    yield step
                    continue
                yield LlmChunk(delta="a")
                yield LlmChunk(delta="", finish_reason="stop")
        finally:
            self.closed += 1

    def supports(self, capability: str) -> bool:
        return capability == "tools"


def _settings(**overrides: object) -> ProviderGuardSettings:
    base: dict[str, object] = {
        "max_concurrency": 1,
        "circuit_failure_threshold": 3,
        "circuit_open_s": 30.0,
        "max_retries": 2,
    }
    base.update(overrides)
    return ProviderGuardSettings.model_validate(base)


def _guarded(
    inner: _FakeLLM, *, clock: Callable[[], float] | None = None, **overrides: object
) -> tuple[GuardedLLM, ProviderGuard, _Sleeps]:
    guard = ProviderGuard("fake", _settings(**overrides), clock=clock or _Clock(), rng=lambda: 1.0)
    sleeps = _Sleeps()
    return GuardedLLM(inner, guard, sleep=sleeps), guard, sleeps


async def _drain(stream: AsyncIterator[LlmChunk]) -> list[LlmChunk]:
    return [chunk async for chunk in stream]


# -- the ceiling ------------------------------------------------------------ #


async def test_a_full_ceiling_answers_429_with_retry_after_instead_of_waiting() -> None:
    inner = _FakeLLM()
    inner.gate = asyncio.Event()
    llm, guard, _ = _guarded(inner)

    holder = asyncio.create_task(llm.complete(_MESSAGES, _PARAMS, "k"))
    await asyncio.sleep(0)
    assert guard.in_flight == 1

    with pytest.raises(RateLimitedError) as refused:
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert refused.value.status == 429
    assert refused.value.retry_after_s == 5
    assert inner.calls == 1  # the refused call never reached the provider

    inner.gate.set()
    assert await holder == _RESULT
    assert guard.in_flight == 0
    assert await llm.complete(_MESSAGES, _PARAMS, "k") == _RESULT


async def test_a_stream_holds_its_permit_until_its_last_chunk() -> None:
    inner = _FakeLLM()
    llm, guard, _ = _guarded(inner)

    stream = llm.stream(_MESSAGES, _PARAMS, "k")
    assert guard.in_flight == 0  # created, not iterated: no slot taken

    first = await anext(stream)
    assert first.delta == "a"
    assert guard.in_flight == 1
    with pytest.raises(RateLimitedError):
        await llm.complete(_MESSAGES, _PARAMS, "k")

    assert [c.finish_reason async for c in stream] == ["stop"]
    assert guard.in_flight == 0


async def test_a_stream_abandoned_mid_answer_frees_its_permit_and_closes_the_adapter() -> None:
    inner = _FakeLLM()
    llm, guard, _ = _guarded(inner)

    stream = llm.stream(_MESSAGES, _PARAMS, "k")
    await anext(stream)
    await stream.aclose()  # type: ignore[attr-defined]

    assert guard.in_flight == 0
    assert inner.closed == 1


def test_call_time_validation_still_fires_at_call_time() -> None:
    llm, guard, _ = _guarded(_FakeLLM())
    with pytest.raises(ValidationError):
        llm.stream([], _PARAMS, "k")
    assert guard.in_flight == 0


async def test_zero_lifts_the_ceiling() -> None:
    inner = _FakeLLM()
    inner.gate = asyncio.Event()
    llm, guard, _ = _guarded(inner, max_concurrency=0)
    tasks = [asyncio.create_task(llm.complete(_MESSAGES, _PARAMS, "k")) for _ in range(20)]
    await asyncio.sleep(0)
    assert guard.in_flight == 20
    inner.gate.set()
    await asyncio.gather(*tasks)
    assert guard.in_flight == 0


# -- the circuit ------------------------------------------------------------ #


async def test_consecutive_transient_failures_open_the_circuit_and_it_answers_502_at_once() -> None:
    clock = _Clock()
    inner = _FakeLLM([_READ_TIMEOUT] * 3)
    llm, guard, _ = _guarded(inner, clock=clock)

    for _ in range(3):
        with pytest.raises(ProviderFailure):
            await llm.complete(_MESSAGES, _PARAMS, "k")
    assert guard.is_open

    with pytest.raises(ProviderFailure) as refused:
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert refused.value.status == 502
    assert refused.value.code == "agent.failed"
    assert "circuit open" in refused.value.detail
    assert inner.calls == 3  # not called while open


async def test_after_the_open_window_one_probe_decides() -> None:
    clock = _Clock()
    inner = _FakeLLM([_READ_TIMEOUT] * 3)
    llm, guard, _ = _guarded(inner, clock=clock)
    for _ in range(3):
        with pytest.raises(ProviderFailure):
            await llm.complete(_MESSAGES, _PARAMS, "k")

    clock.now += 30.0
    inner.gate = asyncio.Event()
    probe = asyncio.create_task(llm.complete(_MESSAGES, _PARAMS, "k"))
    await asyncio.sleep(0)
    # Only the probe may be out; a second call during it is refused as open.
    with pytest.raises(ProviderFailure, match="circuit open"):
        await llm.complete(_MESSAGES, _PARAMS, "k")
    inner.gate.set()
    assert await probe == _RESULT
    assert not guard.is_open
    assert await llm.complete(_MESSAGES, _PARAMS, "k") == _RESULT


async def test_a_failed_probe_reopens_the_circuit_for_another_window() -> None:
    clock = _Clock()
    inner = _FakeLLM([_READ_TIMEOUT] * 4)
    llm, guard, _ = _guarded(inner, clock=clock)
    for _ in range(3):
        with pytest.raises(ProviderFailure):
            await llm.complete(_MESSAGES, _PARAMS, "k")

    clock.now += 30.0
    with pytest.raises(ProviderFailure, match="timed out"):
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert guard.is_open
    clock.now += 29.0
    with pytest.raises(ProviderFailure, match="circuit open"):
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert inner.calls == 4


async def test_a_probe_whose_caller_left_does_not_wedge_the_circuit() -> None:
    clock = _Clock()
    inner = _FakeLLM([_READ_TIMEOUT] * 3)
    llm, guard, _ = _guarded(inner, clock=clock)
    for _ in range(3):
        with pytest.raises(ProviderFailure):
            await llm.complete(_MESSAGES, _PARAMS, "k")

    clock.now += 30.0
    inner.gate = asyncio.Event()
    probe = asyncio.create_task(llm.complete(_MESSAGES, _PARAMS, "k"))
    await asyncio.sleep(0)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    inner.gate.set()
    assert await llm.complete(_MESSAGES, _PARAMS, "k") == _RESULT
    assert not guard.is_open


async def test_a_rejected_key_never_opens_the_circuit() -> None:
    inner = _FakeLLM([_BAD_KEY] * 10)
    llm, guard, sleeps = _guarded(inner)
    for _ in range(10):
        with pytest.raises(ProviderFailure, match="api key"):
            await llm.complete(_MESSAGES, _PARAMS, "k")
    assert not guard.is_open
    assert sleeps.calls == []  # and it is never retried


async def test_a_provider_that_answers_resets_the_failure_count() -> None:
    inner = _FakeLLM([_READ_TIMEOUT, _READ_TIMEOUT, _RESULT, _READ_TIMEOUT, _READ_TIMEOUT])
    llm, guard, _ = _guarded(inner)
    for expected in (ProviderFailure, ProviderFailure, None, ProviderFailure, ProviderFailure):
        if expected is None:
            await llm.complete(_MESSAGES, _PARAMS, "k")
        else:
            with pytest.raises(expected):
                await llm.complete(_MESSAGES, _PARAMS, "k")
    assert not guard.is_open


# -- the retry -------------------------------------------------------------- #


async def test_a_retryable_failure_is_retried_with_backoff_then_succeeds() -> None:
    inner = _FakeLLM([_DOWN, _DOWN, _RESULT])
    llm, _, sleeps = _guarded(inner, circuit_failure_threshold=5)
    assert await llm.complete(_MESSAGES, _PARAMS, "k") == _RESULT
    assert inner.calls == 3
    assert sleeps.calls == [0.5, 1.0]  # rng=1.0: the full-jitter ceiling, doubling


async def test_retries_stop_at_max_retries() -> None:
    inner = _FakeLLM([_DOWN] * 5)
    llm, _, sleeps = _guarded(inner, circuit_failure_threshold=10)
    with pytest.raises(ProviderFailure):
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert inner.calls == 3
    assert len(sleeps.calls) == 2


async def test_a_read_timeout_is_never_retried() -> None:
    inner = _FakeLLM([_READ_TIMEOUT, _RESULT])
    llm, _, sleeps = _guarded(inner)
    with pytest.raises(ProviderFailure, match="timed out"):
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert inner.calls == 1
    assert sleeps.calls == []


async def test_the_providers_retry_after_paces_the_retry() -> None:
    throttled = ProviderFailure("fake rate limited", retryable=True, retry_after_s=2.0)
    inner = _FakeLLM([throttled, _RESULT])
    llm, _, sleeps = _guarded(inner)
    assert await llm.complete(_MESSAGES, _PARAMS, "k") == _RESULT
    assert sleeps.calls == [2.0]


async def test_a_retry_after_longer_than_the_cap_is_not_waited_for() -> None:
    throttled = ProviderFailure("fake rate limited", retryable=True, retry_after_s=60.0)
    inner = _FakeLLM([throttled, _RESULT])
    llm, _, sleeps = _guarded(inner)
    with pytest.raises(ProviderFailure, match="rate limited"):
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert sleeps.calls == []


async def test_retries_stop_once_the_circuit_opens() -> None:
    inner = _FakeLLM([_DOWN] * 5)
    llm, guard, sleeps = _guarded(inner, circuit_failure_threshold=2, max_retries=5)
    with pytest.raises(ProviderFailure):
        await llm.complete(_MESSAGES, _PARAMS, "k")
    assert guard.is_open
    assert inner.calls == 2
    assert len(sleeps.calls) == 1


async def test_a_stream_is_retried_before_its_first_chunk() -> None:
    inner = _FakeLLM([[_DOWN], ["ok"]])
    llm, _, sleeps = _guarded(inner)
    chunks = await _drain(llm.stream(_MESSAGES, _PARAMS, "k"))
    assert [c.finish_reason for c in chunks] == [None, "stop"]
    assert inner.calls == 2
    assert sleeps.calls == [0.5]


async def test_a_stream_that_failed_mid_answer_is_not_retried() -> None:
    inner = _FakeLLM([[LlmChunk(delta="half"), _DOWN], ["ok"]])
    llm, guard, sleeps = _guarded(inner)
    seen: list[str] = []
    with pytest.raises(ProviderFailure):
        async for chunk in llm.stream(_MESSAGES, _PARAMS, "k"):
            seen.append(chunk.delta)
    assert seen == ["half"]
    assert inner.calls == 1
    assert sleeps.calls == []
    assert guard.in_flight == 0


# -- wiring ----------------------------------------------------------------- #


def test_every_adapter_for_one_provider_shares_one_guard() -> None:
    settings = ProviderGuardSettings()
    first, second = guard_adapters([[_FakeLLM()], [_FakeLLM()]], settings)
    assert isinstance(first["fake"], GuardedLLM)
    assert first["fake"]._guard is second["fake"]._guard  # type: ignore[attr-defined]
    assert first["fake"].supports("tools")


def test_the_worker_call_lifts_the_ceiling_and_keeps_the_circuit() -> None:
    (mapping,) = guard_adapters([[_FakeLLM()]], ProviderGuardSettings(), max_concurrency=0)
    guard = mapping["fake"]._guard  # type: ignore[attr-defined]
    assert guard.max_concurrency == 0


def test_a_guard_refuses_to_wrap_another_providers_adapter() -> None:
    guard = ProviderGuard("ollama", ProviderGuardSettings())
    with pytest.raises(ValueError, match="cannot wrap"):
        GuardedLLM(_FakeLLM(), guard)


def test_ollama_gets_its_own_smaller_ceiling_and_pools_follow_the_ceiling() -> None:
    settings = ProviderGuardSettings()
    assert settings.concurrency_for("ollama") == 2
    assert settings.concurrency_for("openai") == 5
    assert pool_size(settings, "openai") == 5
    assert pool_size(ProviderGuardSettings(max_concurrency=0), "openai") == 16


def test_the_llm_client_pool_is_the_size_it_was_given() -> None:
    client = create_llm_http_client(base_url="http://x", timeout_s=1.0, max_connections=3)
    pool = client._transport._pool  # type: ignore[attr-defined]
    assert pool._max_connections == 3
    assert pool._max_keepalive_connections == 3


# -- what the adapters tell the guard ---------------------------------------- #


def _openai(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAILLM:
    return OpenAILLM(
        create_openai_http_client(timeout_s=5.0, transport=httpx.MockTransport(handler))
    )


def _ollama(handler: Callable[[httpx.Request], httpx.Response]) -> OllamaLLM:
    return OllamaLLM(
        create_ollama_http_client(
            OllamaSettings(), timeout_s=5.0, transport=httpx.MockTransport(handler)
        )
    )


async def _failure(llm: OpenAILLM | OllamaLLM, *, stream: bool = False) -> ProviderFailure:
    with pytest.raises(ProviderFailure) as caught:
        if stream:
            await _drain(llm.stream(_MESSAGES, _PARAMS, "k"))
        else:
            await llm.complete(_MESSAGES, _PARAMS, "k")
    return caught.value


@pytest.mark.parametrize("stream", [False, True])
async def test_openai_429_is_retryable_and_carries_its_retry_after(stream: bool) -> None:
    failure = await _failure(
        _openai(lambda _: httpx.Response(429, headers={"retry-after": "3"})), stream=stream
    )
    assert (failure.transient, failure.retryable, failure.retry_after_s) == (True, True, 3.0)
    assert (failure.code, failure.status) == ("agent.failed", 502)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_5xx_is_retryable(status: int) -> None:
    for llm in (
        _openai(lambda _: httpx.Response(status)),
        _ollama(lambda _: httpx.Response(status)),
    ):
        failure = await _failure(llm)
        assert failure.retryable


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_a_4xx_is_neither_retryable_nor_transient(status: int) -> None:
    failure = await _failure(_openai(lambda _: httpx.Response(status)))
    assert (failure.transient, failure.retryable) == (False, False)


def _raise(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


async def test_a_refused_connection_is_retryable_but_a_read_timeout_only_transient() -> None:
    refused = await _failure(_ollama(_raise(httpx.ConnectError("refused"))))
    assert (refused.transient, refused.retryable) == (True, True)
    slow = await _failure(_ollama(_raise(httpx.ReadTimeout("slow"))))
    assert (slow.transient, slow.retryable) == (True, False)


async def test_an_off_contract_body_is_not_transient() -> None:
    failure = await _failure(_openai(lambda _: httpx.Response(200, json={"nope": 1})))
    assert not failure.transient


def test_a_retry_after_date_is_ignored_rather_than_guessed() -> None:
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after("-1") is None
    assert parse_retry_after(" 7 ") == 7.0
    assert parse_retry_after(None) is None


# -- the 429 reaches the client with its Retry-After ------------------------ #


def test_a_saturation_429_keeps_its_retry_after_through_the_agent_run() -> None:
    from app.agents.orchestrator import _in_band_error  # noqa: PLC0415
    from app.framework.agent_runtime.executor import _error_event  # noqa: PLC0415

    event = _error_event(RateLimitedError("ollama is at its concurrency ceiling", retry_after_s=5))
    assert event.data == {
        "code": "common.rate_limited",
        "status": 429,
        "detail": "ollama is at its concurrency ceiling",
        "retry_after_s": 5,
    }
    rebuilt = _in_band_error(event)
    assert isinstance(rebuilt, RateLimitedError)
    assert (rebuilt.status, rebuilt.retry_after_s) == (429, 5)


def test_any_other_failure_event_is_unchanged() -> None:
    from app.framework.agent_runtime.executor import _error_event  # noqa: PLC0415

    assert _error_event(_BAD_KEY).data == {
        "code": "agent.failed",
        "status": 502,
        "detail": "fake rejected the api key",
    }
