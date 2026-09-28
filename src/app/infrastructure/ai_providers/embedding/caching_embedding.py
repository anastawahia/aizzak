"""The query-vector cache — capacity plan wave 4, step 4.3.

A decorator over the ``EmbeddingProvider`` port (02 §1.2), not a change to
``ExternalEmbeddingProvider``: the thing being cached is a pure function of
``(text, model, sequence ceiling)``, and expressing that as a wrapper keeps
the HTTP adapter free of any notion of a cache — the ``exa_web_search.py``
fail-open cache is the policy precedent, the wrapper shape is what keeps it
out of the transport.

**Why it pays.** Retrieval embeds the user's query string
(``knowledge/application/retrieval.py``) on the synchronous request path,
inside the 400 ms budget ``07 §2`` gives the whole RAG lookup. Queries repeat
heavily across users of one workspace and across workspaces — the plan's own
observation — so the same 384-dimensional vector is recomputed by a CPU model
that has already produced it.

**Per TEXT, never per call.** The key is a digest of one text, so a call
carrying several is answered from whatever subset is cached and asks the
service only for the misses, in their original order. Keying whole calls
would make a two-text query miss because one of its texts was new.

⚠️ **The key carries the MODEL and the SEQUENCE CEILING, not just the
text.** ``docker-compose.yml``'s own comment on ``EMB_MAX_SEQ_LEN`` says it:
changing the model or the ceiling invalidates every vector already indexed,
and a cache that survived that change would keep serving vectors from the
previous regime into a collection built under the new one — the ``4.5``
failure mode ("نتائج تبدو صحيحةً وهي عشوائيّة") reached through the cache
instead of through the collection. ``dimensions`` rides along for the same
reason and one more: a decoded entry of the wrong width is refused below, so
the two guards agree.

⚠️ **And the residual drift is named rather than papered over.** The ceiling
this key carries is the ADAPTER's half (``EmbeddingServiceSettings.
embedding_max_input_tokens``); the service's half is ``EMB_MAX_SEQ_LEN`` in
the compose file, and nothing enforces that the two are equal (that settings
class's own docstring says so). A deployment that moves one without the other
is already silently corrupting vectors before this cache exists — what the
cache adds is bounded by the TTL, which is why the TTL is minutes and not
days.

**Fail-open, both directions** (``exa_web_search.py``, verbatim policy): any
cache error on read is a miss, any cache error on write is ignored. An
embedding is an optimization surface with an authoritative source one HTTP
call away — unlike ``integrations``' OAuth ``state``, where a cache outage
disguised as a miss would be indistinguishable from an attack. Redis being
down must slow retrieval, never break it.

**``ttl_s <= 0`` means the Composition Root builds no wrapper at all**, so
the un-decorated adapter is what runs — the `م-8` switch every step in this
plan carries, and the reason this class refuses a non-positive TTL loudly
rather than treating it as "off": two representations of "off" is one too
many.

**Tokens count what was SPENT, so a hit contributes zero.** ``EmbeddingResult.
tokens`` is the work the model actually performed, and a cache hit performs
none. Estimating a plausible number for it would make the field describe
something else entirely.

**Every lookup is counted by what it found** (``aizzak_embedding_cache_total``
in ``framework/observability/metrics.py``, capacity blocker د-38). Until
2026-09-28 this cache answered from Redis and told no one, so the one number
step 4.3 is judged by -- its hit rate -- could only be read from a Redis
session. The count is per TEXT, the unit the key is built on, and a call this
wrapper delegates whole consulted nothing and counts nothing. ⚠️ A read that
FAILED is ``unavailable``, never ``miss``: fail-open makes the two behave
alike, which is exactly why they are counted apart -- folded together, a
broken Redis would read as a load whose questions never repeat.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence

from app.framework.errors import ValidationError
from app.framework.observability.metrics import embedding_cache_total
from app.framework.ports.cache_provider import CacheProvider
from app.framework.ports.embedding_provider import EmbeddingProvider, EmbeddingResult
from app.framework.settings.settings import EmbeddingServiceSettings

# The key namespace, shaped like `auth:principal:`/`search:exa:` before it.
# `v1` is the VALUE format, not the platform version: a change to how a vector
# is packed below gets a new number, and every entry written by the old code
# reads as a miss instead of as a mis-decoded vector.
_KEY_PREFIX = "embed:v1:"

# The plan's own default, and what `EMBEDDING_CACHE_TTL_S` carries when
# nothing sets it.
DEFAULT_EMBEDDING_CACHE_TTL_S = 600

# Ten minutes is the default; an hour is the ceiling. The bound exists for the
# drift case in the module docstring -- it is the longest a vector from a
# previous model/ceiling regime may keep being served after a deployment that
# changed one of them without changing the other.
MAX_EMBEDDING_CACHE_TTL_S = 3600

# Little-endian float32 -- the width the model actually produces, so packing
# is lossless (the service serialises `numpy.float32` values through JSON,
# which widens them to float64 on the wire and back to the same float32 here).
# 1536 bytes for a 384-dimensional vector, against roughly 7 KB of JSON: at
# the `§0` target of 40 queries/second this is the difference between a cache
# that fits beside everything else in Redis and one that competes with it.
_PACK_FORMAT = "<{count}f"
_BYTES_PER_FLOAT = 4


class CachingEmbeddingProvider:
    """``EmbeddingProvider`` (structural Protocol match) that answers from
    ``CacheProvider`` whatever it can and delegates the rest."""

    # A plain class attribute, not `typing.ClassVar` -- the
    # `ExternalEmbeddingProvider.provider` precedent (mypy rejects a
    # `ClassVar` against the port's instance-attribute annotation).
    provider: str

    def __init__(
        self,
        inner: EmbeddingProvider,
        cache: CacheProvider,
        settings: EmbeddingServiceSettings,
        *,
        ttl_s: int = DEFAULT_EMBEDDING_CACHE_TTL_S,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._settings = settings
        self._ttl_s = _guard_ttl(ttl_s)
        # Delegated, never re-declared: the wrapped adapter's identity is what
        # `PROVIDER_ROUTING` and the Composition Root's `{a.provider: a}` maps
        # are keyed by, and a wrapper that answered with a name of its own
        # would silently unroute the provider it wraps.
        self.provider = inner.provider

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    def dimensions(self, model: str) -> int:
        """Delegated -- no I/O either way, and the wrapper has no business
        having its own opinion about the served model's width."""
        return self._inner.dimensions(model)

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        """Cached texts from Redis, the rest from the wrapped adapter.

        A call whose texts are not ALL non-blank strings is delegated whole,
        untouched: validation is the wrapped adapter's (``_validate_texts``),
        and a wrapper that answered such a call from cache -- or raised its
        own version of the error -- would change what a caller mistake looks
        like depending on what happened to be cached.
        """
        text_list = list(texts)
        if not _is_cacheable(text_list):
            return await self._inner.embed(text_list, model, api_key)

        width = self._settings.dimensions
        hits: dict[int, list[float]] = {}
        for index, text in enumerate(text_list):
            cached = await self._get(self._key(text, model), width)
            if cached is not None:
                hits[index] = cached

        misses = [text for index, text in enumerate(text_list) if index not in hits]
        if not misses:
            return EmbeddingResult(
                vectors=[hits[index] for index in range(len(text_list))],
                model=model,
                dimensions=width,
                # Nothing was embedded -- module docstring's "tokens count
                # what was SPENT".
                tokens=0,
            )

        fetched = await self._inner.embed(misses, model, api_key)
        # `strict=True` rather than a silent truncation: "one vector per text"
        # is the port's contract (``ExternalEmbeddingProvider`` enforces it on
        # the wire with its own vector-count guard), and a shorter answer here
        # would mis-align the merge below -- handing one caller another's
        # vector, which is the one outcome a cache must never produce.
        for text, vector in zip(misses, fetched.vectors, strict=True):
            await self._put(self._key(text, model), vector, width)

        # Reassembled in the CALLER's order: a hit keeps its slot, and every
        # other slot takes the next vector the service returned -- which is
        # sound only because `misses` was built in that same order.
        pending = iter(fetched.vectors)
        merged: list[list[float]] = []
        for index in range(len(text_list)):
            cached = hits.get(index)
            merged.append(cached if cached is not None else next(pending))
        return EmbeddingResult(
            vectors=merged,
            model=model,
            dimensions=fetched.dimensions,
            tokens=fetched.tokens,
        )

    def _key(self, text: str, model: str) -> str:
        """``embed:v1:<sha256(model | ceiling | width | text)>``.

        The parts are joined by a byte that cannot occur in any of them, so no
        two different tuples can produce one digest input -- a model name
        ending in a digit beside a ceiling starting with one is exactly the
        collision a bare concatenation would allow.
        """
        material = "\x00".join(
            (
                model,
                str(self._settings.embedding_max_input_tokens),
                str(self._settings.dimensions),
                text,
            )
        )
        return f"{_KEY_PREFIX}{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    async def _get(self, key: str, width: int) -> list[float] | None:
        """Fail-open read: any error, any unreadable value, is a miss to the
        caller -- and is counted as what it was (module docstring). A value
        that does not unpack is a real ``miss``, not ``unavailable``: Redis
        answered, and the write that follows replaces the entry."""
        try:
            raw = await self._cache.get(key)
        except Exception:  # fail-open IS the policy (module docstring)
            embedding_cache_total.labels(result="unavailable").inc()
            return None
        vector = _unpack(raw, width)
        embedding_cache_total.labels(result="miss" if vector is None else "hit").inc()
        return vector

    async def _put(self, key: str, vector: list[float], width: int) -> None:
        """Fail-open write, and it refuses to store a vector of the wrong
        width rather than writing one the reader would then reject on every
        subsequent hit."""
        if len(vector) != width:
            return
        try:
            await self._cache.set(key, _pack(vector), self._ttl_s)
        except Exception:  # fail-open IS the policy (module docstring)
            return


def _is_cacheable(texts: list[str]) -> bool:
    """The wrapped adapter's ``_validate_texts`` rule, read rather than
    enforced: this decides whether to touch the cache at all, and the adapter
    still decides whether the call is legal."""
    return bool(texts) and all(isinstance(text, str) and text.strip() for text in texts)


def _pack(vector: list[float]) -> bytes:
    return struct.pack(_PACK_FORMAT.format(count=len(vector)), *vector)


def _unpack(raw: bytes | None, width: int) -> list[float] | None:
    """The inverse, and total: anything it cannot read fully is ``None``.

    The length check is not defensive tidiness. Redis is shared
    infrastructure that an older build of this process, or a different one,
    can write to; a vector of the wrong width reaching Qdrant would be
    rejected at best and, on a collection that happened to match, would score
    against coordinates that mean nothing.
    """
    if raw is None or len(raw) != width * _BYTES_PER_FLOAT:
        return None
    try:
        return list(struct.unpack(_PACK_FORMAT.format(count=width), raw))
    except struct.error:
        return None


def _guard_ttl(ttl_s: int) -> int:
    """Fail fast at CONSTRUCTION -- the ``PrincipalCache._guard_ttl``
    precedent, including its refusal of zero: "off" is expressed by not
    building this wrapper (module docstring), and a class that also accepted
    zero as "off" would give one deployment state two spellings."""
    if ttl_s <= 0:
        raise ValidationError("embedding cache ttl_s must be positive (0 means: build no cache)")
    if ttl_s > MAX_EMBEDDING_CACHE_TTL_S:
        raise ValidationError(
            f"embedding cache ttl_s must not exceed {MAX_EMBEDDING_CACHE_TTL_S}s: {ttl_s}"
        )
    return ttl_s
