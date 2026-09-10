"""Unit tests for the query-vector cache
(``infrastructure/ai_providers/embedding/caching_embedding.py``, capacity
plan wave 4 step 4.3).

Hermetic: an in-memory ``CacheProvider`` fake and a recording
``EmbeddingProvider`` fake stand in for Redis and for the HTTP adapter, so
these test the WRAPPER's decisions -- what it asks the service for, what it
stores, what it refuses to trust -- and nothing else. The transport itself is
already covered by ``test_external_embedding.py``.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

import pytest

from app.framework.errors import ValidationError
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.settings.settings import EmbeddingServiceSettings
from app.infrastructure.ai_providers.embedding.caching_embedding import (
    MAX_EMBEDDING_CACHE_TTL_S,
    CachingEmbeddingProvider,
)

_DIM = 4
_SETTINGS = EmbeddingServiceSettings(dimensions=_DIM)


class _FakeCache:
    """An in-memory ``CacheProvider``. ``fail_get``/``fail_set`` turn it into
    a broken Redis without changing anything else -- the fail-open policy is
    the point of the switches."""

    def __init__(self, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.gets = 0
        self.sets = 0

    async def get(self, key: str) -> bytes | None:
        self.gets += 1
        if self.fail_get:
            raise RuntimeError("redis is down")
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        self.sets += 1
        if self.fail_set:
            raise RuntimeError("redis is down")
        self.store[key] = value
        self.ttls[key] = ttl_s

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def incr(self, key: str, amount: int = 1) -> int:
        raise NotImplementedError

    async def expire(self, key: str, ttl_s: int) -> None:
        raise NotImplementedError


class _RecordingProvider:
    """An ``EmbeddingProvider`` that records exactly which texts it was asked
    for. Vectors are POSITION-dependent within a call and seeded per text, so
    a mis-merged result shows up as the wrong numbers rather than as
    identical-looking rows."""

    provider = "embedding-local"

    def __init__(self, *, dim: int = _DIM) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        self.calls.append(list(texts))
        return EmbeddingResult(
            vectors=[[float(len(text))] * self.dim for text in texts],
            model=model,
            dimensions=self.dim,
            tokens=7 * len(texts),
        )

    def dimensions(self, model: str) -> int:
        return self.dim


def _wrap(
    inner: _RecordingProvider, cache: _FakeCache, *, ttl_s: int = 600
) -> CachingEmbeddingProvider:
    return CachingEmbeddingProvider(inner, cache, _SETTINGS, ttl_s=ttl_s)


# --------------------------------------------------------------------------- #
# The hit -- the whole reason the step exists                                 #
# --------------------------------------------------------------------------- #
async def test_a_repeated_query_is_never_embedded_twice() -> None:
    inner, cache = _RecordingProvider(), _FakeCache()
    provider = _wrap(inner, cache)

    first = await provider.embed(["ما هي سياسة الإجازات؟"], "test-model", "")
    second = await provider.embed(["ما هي سياسة الإجازات؟"], "test-model", "")

    assert inner.calls == [["ما هي سياسة الإجازات؟"]]  # ONE call, not two
    assert second.vectors == first.vectors


async def test_a_hit_reports_zero_tokens_because_nothing_was_embedded() -> None:
    """``tokens`` is the work the model actually performed (module
    docstring). Inventing a plausible number for a hit would make the field
    describe something else -- and would hide the hit rate from the only
    place it is visible without a Redis session."""
    inner, cache = _RecordingProvider(), _FakeCache()
    provider = _wrap(inner, cache)

    miss = await provider.embed(["q"], "test-model", "")
    hit = await provider.embed(["q"], "test-model", "")

    assert miss.tokens == 7
    assert hit.tokens == 0


async def test_the_stored_value_is_packed_float32_not_json() -> None:
    """1536 bytes for a 384-wide vector against roughly 7 KB of JSON -- the
    difference between a cache that fits beside everything else in Redis and
    one that competes with it."""
    inner, cache = _RecordingProvider(), _FakeCache()

    await _wrap(inner, cache).embed(["q"], "test-model", "")

    ((_, stored),) = cache.store.items()
    assert len(stored) == _DIM * 4
    assert list(struct.unpack(f"<{_DIM}f", stored)) == [1.0] * _DIM


async def test_the_entry_carries_the_configured_ttl() -> None:
    inner, cache = _RecordingProvider(), _FakeCache()

    await _wrap(inner, cache, ttl_s=120).embed(["q"], "test-model", "")

    assert set(cache.ttls.values()) == {120}


# --------------------------------------------------------------------------- #
# Per TEXT, never per call                                                    #
# --------------------------------------------------------------------------- #
async def test_a_partial_hit_asks_the_service_only_for_the_misses() -> None:
    inner, cache = _RecordingProvider(), _FakeCache()
    provider = _wrap(inner, cache)
    await provider.embed(["alpha"], "test-model", "")
    inner.calls.clear()

    result = await provider.embed(["alpha", "bb", "alpha"], "test-model", "")

    # "alpha" is cached and appears twice; only the new text crosses the wire.
    assert inner.calls == [["bb"]]
    assert result.vectors == [[5.0] * _DIM, [2.0] * _DIM, [5.0] * _DIM]


async def test_a_partial_hit_reassembles_in_the_callers_order() -> None:
    """The correctness half: a hit keeps its slot, and every other slot takes
    the next vector the service returned -- which is only sound because the
    miss list was built in that same order."""
    inner, cache = _RecordingProvider(), _FakeCache()
    provider = _wrap(inner, cache)
    await provider.embed(["cc"], "test-model", "")
    inner.calls.clear()

    result = await provider.embed(["dddd", "cc", "e"], "test-model", "")

    assert inner.calls == [["dddd", "e"]]
    assert result.vectors == [[4.0] * _DIM, [2.0] * _DIM, [1.0] * _DIM]


# --------------------------------------------------------------------------- #
# What the key carries                                                        #
# --------------------------------------------------------------------------- #
async def test_a_different_model_never_reads_another_models_vector() -> None:
    """⚠️ ``docker-compose.yml``'s own comment: changing the model
    invalidates every vector already indexed. A cache that survived it would
    reach 4.5's failure mode -- results that look right and are random --
    through the cache instead of through the collection."""
    inner, cache = _RecordingProvider(), _FakeCache()
    provider = _wrap(inner, cache)

    await provider.embed(["q"], "model-a", "")
    await provider.embed(["q"], "model-b", "")

    assert inner.calls == [["q"], ["q"]]
    assert len(cache.store) == 2


async def test_a_different_sequence_ceiling_never_reads_the_old_vector() -> None:
    """The other half of the same ceiling: ``EMB_MAX_SEQ_LEN`` decides where
    the model stops reading a text, so a vector computed under one value is
    not the same vector under another."""
    inner, cache = _RecordingProvider(), _FakeCache()
    at_512 = CachingEmbeddingProvider(inner, cache, _SETTINGS, ttl_s=600)
    at_256 = CachingEmbeddingProvider(
        inner,
        cache,
        EmbeddingServiceSettings(dimensions=_DIM, embedding_max_input_tokens=256),
        ttl_s=600,
    )

    await at_512.embed(["q"], "test-model", "")
    await at_256.embed(["q"], "test-model", "")

    assert inner.calls == [["q"], ["q"]]
    assert len(cache.store) == 2


# --------------------------------------------------------------------------- #
# Fail-open, both directions                                                  #
# --------------------------------------------------------------------------- #
async def test_a_broken_cache_read_is_a_miss_not_an_error() -> None:
    inner, cache = _RecordingProvider(), _FakeCache(fail_get=True)

    result = await _wrap(inner, cache).embed(["q"], "test-model", "")

    assert inner.calls == [["q"]]
    assert result.vectors == [[1.0] * _DIM]


async def test_a_broken_cache_write_is_ignored_not_an_error() -> None:
    inner, cache = _RecordingProvider(), _FakeCache(fail_set=True)

    result = await _wrap(inner, cache).embed(["q"], "test-model", "")

    assert cache.sets == 1
    assert result.vectors == [[1.0] * _DIM]


async def test_a_stored_value_of_the_wrong_width_is_a_miss() -> None:
    """Redis is shared infrastructure an older build of this process can
    write to. A vector of the wrong width reaching Qdrant would be rejected
    at best, and -- on a collection that happened to match -- would score
    against coordinates that mean nothing."""
    inner, cache = _RecordingProvider(), _FakeCache()
    provider = _wrap(inner, cache)
    await provider.embed(["q"], "test-model", "")
    ((key, _),) = cache.store.items()
    cache.store[key] = struct.pack("<3f", 1.0, 2.0, 3.0)  # one float short
    inner.calls.clear()

    result = await provider.embed(["q"], "test-model", "")

    assert inner.calls == [["q"]]
    assert result.vectors == [[1.0] * _DIM]


# --------------------------------------------------------------------------- #
# Delegation -- what the wrapper must NOT change                              #
# --------------------------------------------------------------------------- #
async def test_a_caller_mistake_is_delegated_whole_and_never_answered_from_cache() -> None:
    """Validation belongs to the wrapped adapter. A wrapper that answered a
    blank text from cache -- or raised its own version of the error -- would
    make a caller mistake look different depending on what happened to be
    cached."""

    class _Validating(_RecordingProvider):
        async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
            if not texts or any(not text.strip() for text in texts):
                raise ValidationError("texts must not contain an empty entry")
            return await super().embed(texts, model, api_key)

    inner, cache = _Validating(), _FakeCache()
    provider = _wrap(inner, cache)

    with pytest.raises(ValidationError):
        await provider.embed(["  "], "test-model", "")
    with pytest.raises(ValidationError):
        await provider.embed([], "test-model", "")

    assert cache.gets == 0  # the cache was never even consulted
    assert cache.store == {}


def test_the_wrapper_answers_with_the_wrapped_adapters_identity() -> None:
    """``PROVIDER_ROUTING`` and the Composition Root's ``{a.provider: a}``
    maps are keyed by this. A wrapper with a name of its own would silently
    unroute the provider it wraps."""
    inner = _RecordingProvider()
    provider = _wrap(inner, _FakeCache())

    assert provider.provider == inner.provider == "embedding-local"
    assert provider.dimensions("test-model") == _DIM


# --------------------------------------------------------------------------- #
# The TTL guard                                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ttl_s", [0, -1])
def test_a_non_positive_ttl_is_refused_at_construction(ttl_s: int) -> None:
    """ "Off" is expressed by the Composition Root not building this wrapper.
    A class that ALSO accepted zero as "off" would give one deployment state
    two spellings."""
    with pytest.raises(ValidationError):
        _wrap(_RecordingProvider(), _FakeCache(), ttl_s=ttl_s)


def test_a_ttl_past_the_ceiling_is_refused_at_construction() -> None:
    with pytest.raises(ValidationError):
        _wrap(_RecordingProvider(), _FakeCache(), ttl_s=MAX_EMBEDDING_CACHE_TTL_S + 1)
