"""The guard every LLM provider sits behind -- capacity-plan step 6.1 (``ق-6``).

Three things, per provider, per process, and nothing else:

* **A concurrency ceiling that refuses instead of queueing.** When a
  provider already has ``ProviderGuardSettings.concurrency_for(provider)``
  calls in flight from this process, the next one answers **429 with
  ``Retry-After``** at once (``ق-6``, signed 2026-10-03). The alternative --
  an ``asyncio.Semaphore`` that waits -- is the silent queue ``ق-6`` turned
  down: the user waits with no news, the wait is spent inside the request's
  own timeout, and a capacity shortfall reads as a fault. A stream holds its
  permit until its last chunk, because that is how long the provider is busy.
* **A circuit that stops calling a provider that keeps failing.** After
  ``circuit_failure_threshold`` consecutive TRANSIENT failures
  (``shared.ProviderFailure.transient``: unreachable, timed out, 429, 5xx)
  every call answers **502 at once** for ``circuit_open_s``, instead of each
  one waiting out ``Limits.llm_timeout_s`` against a provider already known to
  be down. Then exactly one call is let through; its answer decides. A
  non-transient failure (a rejected key, an unknown model, a 400) proves the
  provider is answering, so it counts as alive: one tenant's bad key must not
  cut off every tenant on that provider.
* **A retry for the cheap failures only.** ``ProviderFailure.retryable`` --
  the connection never opened, or the provider answered 429/5xx straight
  away -- is retried up to ``max_retries`` times with full-jitter exponential
  backoff, or after the provider's own ``Retry-After`` when it sent one (and
  not at all when that is longer than ``_BACKOFF_CAP_S``: a provider asking
  for a minute is not a provider worth holding a request open for). Never a
  4xx, never a read timeout. A stream is retried only before its first chunk;
  after that the user has seen part of an answer, and a second one would be
  a different answer.

**Per process, not fleet-wide** (``ProviderGuardSettings`` says why), and the
same reasoning makes the circuit per process too: twelve processes each
learning in five calls that a provider is down costs sixty fast failures, not
a Redis round trip on every call.

**A decorator, not a base class** -- the ``agents/orchestrator._MeteredLLM``
shape exactly: a structural ``LLMProvider`` match that forwards ``supports``
verbatim and keeps the port's plain-``def`` ``stream``, so the adapter's own
call-time ``ValidationError`` still fires at call time, before any permit is
taken. ``ProviderGuard`` holds the state and is shared by every adapter
instance for the same provider in one process (the workers build two
``OllamaLLM``\\ s with different timeouts; both draw on one ceiling).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from app.framework.errors import RateLimitedError
from app.framework.observability.metrics import llm_circuit_open, llm_guard_total, llm_in_flight
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LLMProvider, LlmResult
from app.framework.settings.settings import ProviderGuardSettings
from app.infrastructure.ai_providers.llm.shared import (
    DEFAULT_MAX_CONNECTIONS,
    ProviderFailure,
    off_contract,
)

# Full-jitter exponential backoff (AWS Architecture Blog's "full jitter"):
# attempt n waits uniform(0, min(cap, base * 2**n)). Constants, not settings:
# a deployment tunes HOW MANY retries (`LLM_MAX_RETRIES`), and the spacing is
# what makes those retries not arrive together.
_BACKOFF_BASE_S: float = 0.5
_BACKOFF_CAP_S: float = 8.0


class ProviderGuard:
    """One provider's ceiling and circuit, in this process."""

    def __init__(
        self,
        provider: str,
        settings: ProviderGuardSettings,
        *,
        max_concurrency: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.provider = provider
        # `None` = the settings' own number; the worker roots pass `0`
        # (`ProviderGuardSettings` says why).
        self._max_concurrency = (
            settings.concurrency_for(provider) if max_concurrency is None else max_concurrency
        )
        self._retry_after_s = settings.saturation_retry_after_s
        self._threshold = settings.circuit_failure_threshold
        self._open_s = settings.circuit_open_s
        self._max_retries = settings.max_retries
        self._clock = clock
        self._rng = rng
        self._in_flight = 0
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_out = False
        llm_circuit_open.labels(provider=provider).set(0)

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def admit(self) -> bool:
        """Take a permit or refuse. Returns whether this call is the circuit's
        probe. Synchronous on purpose: nothing awaits between the check and
        the increment, so two coroutines cannot both take the last permit."""
        probe = False
        if self._opened_at is not None:
            if self._clock() - self._opened_at < self._open_s or self._probe_out:
                llm_guard_total.labels(provider=self.provider, outcome="circuit_open").inc()
                raise off_contract(self.provider, "is failing; not called (circuit open)")
            probe = True
        if self._max_concurrency and self._in_flight >= self._max_concurrency:
            llm_guard_total.labels(provider=self.provider, outcome="saturated").inc()
            raise RateLimitedError(
                f"{self.provider} is at its concurrency ceiling",
                retry_after_s=self._retry_after_s,
            )
        self._probe_out = probe
        self._in_flight += 1
        llm_in_flight.labels(provider=self.provider).inc()
        llm_guard_total.labels(provider=self.provider, outcome="admitted").inc()
        return probe

    def release(self, *, probe: bool) -> None:
        """Give the permit back. A probe that ended with no verdict (the
        caller went away mid-call) frees the probe slot, so the next call
        probes instead of the circuit staying open forever."""
        self._in_flight -= 1
        llm_in_flight.labels(provider=self.provider).dec()
        if probe:
            self._probe_out = False

    def record(self, failure: ProviderFailure | None) -> None:
        """One attempt's verdict. ``None`` is a success; a non-transient
        failure is the provider answering, which is alive too."""
        if failure is None or not failure.transient:
            self._failures = 0
            if self._opened_at is not None:
                self._opened_at = None
                llm_circuit_open.labels(provider=self.provider).set(0)
            self._probe_out = False
            return
        self._failures += 1
        if self._probe_out or self._failures >= self._threshold:
            self._opened_at = self._clock()
            self._probe_out = False
            llm_circuit_open.labels(provider=self.provider).set(1)

    def retry_delay(self, failure: ProviderFailure, attempt: int) -> float | None:
        """Seconds to wait before attempt ``attempt + 1``, or ``None`` when
        this failure goes to the caller."""
        delay: float | None = None
        if failure.retryable and attempt < self._max_retries and self._opened_at is None:
            if failure.retry_after_s is not None:
                if failure.retry_after_s <= _BACKOFF_CAP_S:
                    delay = failure.retry_after_s
            else:
                delay = self._rng() * min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * 2**attempt)
        outcome = "retried" if delay is not None else "failed"
        if delay is not None or failure.transient:
            llm_guard_total.labels(provider=self.provider, outcome=outcome).inc()
        return delay


class GuardedLLM:
    """An ``LLMProvider`` behind its provider's ``ProviderGuard``.

    Structural match, no inheritance (the house rule since 2.3)."""

    def __init__(
        self,
        inner: LLMProvider,
        guard: ProviderGuard,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if guard.provider != inner.provider:
            raise ValueError(f"guard for {guard.provider!r} cannot wrap {inner.provider!r}")
        self._inner = inner
        self._guard = guard
        self._sleep = sleep
        self.provider = inner.provider

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        probe = self._guard.admit()
        try:
            attempt = 0
            while True:
                try:
                    result = await self._inner.complete(messages, params, api_key)
                except ProviderFailure as failure:
                    self._guard.record(failure)
                    delay = self._guard.retry_delay(failure, attempt)
                    if delay is None:
                        raise
                    await self._sleep(delay)
                    attempt += 1
                    continue
                self._guard.record(None)
                return result
        finally:
            self._guard.release(probe=probe)

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        # Plain `def`, like the port: the adapter's call-time guards run HERE,
        # before any permit is taken. A retry asks the adapter for a fresh
        # stream with the same arguments.
        first = self._inner.stream(messages, params, api_key)
        return self._guarded_stream(first, lambda: self._inner.stream(messages, params, api_key))

    def supports(self, capability: str) -> bool:
        """Forwarded verbatim: the guard decides WHEN a provider is called,
        never what it can do."""
        return self._inner.supports(capability)

    async def _guarded_stream(
        self,
        first: AsyncIterator[LlmChunk],
        again: Callable[[], AsyncIterator[LlmChunk]],
    ) -> AsyncIterator[LlmChunk]:
        # The permit is taken on the first `__anext__`, not at `stream()`: a
        # stream that is created and never iterated never reached the provider
        # and must not hold its slot (the `_MeteredLLM` reasoning).
        probe = self._guard.admit()
        try:
            chunks = first
            attempt = 0
            while True:
                started = False
                try:
                    try:
                        async for chunk in chunks:
                            started = True
                            yield chunk
                    finally:
                        # The port types a stream as an `AsyncIterator`, which
                        # promises no `aclose`; every adapter returns a
                        # generator, which has one. Closing it here, not at GC,
                        # is what releases the adapter's HTTP response the
                        # moment the caller walks away mid-answer.
                        close = getattr(chunks, "aclose", None)
                        if close is not None:
                            await close()
                except ProviderFailure as failure:
                    self._guard.record(failure)
                    delay = None if started else self._guard.retry_delay(failure, attempt)
                    if delay is None:
                        if started and failure.transient:
                            llm_guard_total.labels(provider=self.provider, outcome="failed").inc()
                        raise
                    await self._sleep(delay)
                    attempt += 1
                    chunks = again()
                    continue
                self._guard.record(None)
                return
        finally:
            self._guard.release(probe=probe)


def guard_adapters(
    adapter_sets: Sequence[Sequence[LLMProvider]],
    settings: ProviderGuardSettings,
    *,
    max_concurrency: int | None = None,
) -> list[dict[str, LLMProvider]]:
    """Wrap each set of adapters, keyed by each adapter's OWN ``provider``
    (the Composition Root's rule), with ONE ``ProviderGuard`` per provider
    shared across every set -- so two clients for the same provider (the
    workers' ``F-1`` pair) draw on one ceiling and one circuit.

    ``max_concurrency=0`` is the worker roots' call (``ProviderGuardSettings``
    says why); ``None`` takes each provider's number from the settings."""
    guards: dict[str, ProviderGuard] = {}
    wrapped: list[dict[str, LLMProvider]] = []
    for adapters in adapter_sets:
        mapping: dict[str, LLMProvider] = {}
        for adapter in adapters:
            guard = guards.get(adapter.provider)
            if guard is None:
                guard = ProviderGuard(adapter.provider, settings, max_concurrency=max_concurrency)
                guards[adapter.provider] = guard
            mapping[adapter.provider] = GuardedLLM(adapter, guard)
        wrapped.append(mapping)
    return wrapped


def pool_size(settings: ProviderGuardSettings, provider: str) -> int:
    """The ``max_connections`` for a provider's HTTP client in a process whose
    guard enforces ``settings``: its concurrency ceiling, or the shared
    default when there is none (``0``)."""
    return settings.concurrency_for(provider) or DEFAULT_MAX_CONNECTIONS
