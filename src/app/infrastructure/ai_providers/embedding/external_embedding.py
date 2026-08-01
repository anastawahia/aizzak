"""Adapter for the ``EmbeddingProvider`` port (02-port-contracts §1.2, D-14,
DD-13). Phase 2.10.

Same split as every driven adapter since 2.3 (``qdrant_store.py``,
``ollama_llm.py``, ...): a factory builds the technology client
(``create_embedding_http_client``, Composition Root / test harness only),
and a thin adapter class (``ExternalEmbeddingProvider``) implements the port
over it (structural Protocol match -- no inheritance). This module imports
ONLY ``httpx``, ``app.framework.errors``, ``app.framework.ports.
embedding_provider`` (value types), ``app.framework.settings`` and
``app.framework.types`` (the ``Json`` alias every other adapter's own
signatures already use, ``qdrant_store.py``'s own import list) -- no torch,
no sentence-transformers, no ``app.modules``, no ``app.api``. The model
itself never runs in this process; ``services/embedding/app.py`` is a
SEPARATE deployable (its own Docker image) this adapter only ever talks to
over HTTP, exactly the way ``ollama_llm.py`` talks to a separate Ollama
server.

**Not the LLM adapters' precedent, deliberately.** ``ollama_llm.py``/
``openai_llm.py`` fold every failure into ``agent.failed``/502 because a
provider is a THIRD PARTY this platform does not operate. The central
embedding service is the opposite: it is a first-party internal dependency
this platform builds, deploys and bakes model weights into
(``services/embedding/Dockerfile``) -- structurally the same trust tier as
Postgres/Redis/Qdrant, not an external vendor. So this module follows
``qdrant_store.py``'s ``_translate`` precedent instead: EVERY transport
failure, timeout, service-side 5xx, or off-contract 200 body (wrong vector
count, wrong dimension, non-JSON) folds into ``common.internal``/500 -- never
502, and never a caller-branchable code, because there is no fallback and no
vendor to blame. ``ValidationError`` raised by this module's own pre-flight
guards (``_validate_texts``, ``_guard_base_url``) is a different, caller-
caused failure and is never folded into that translation -- the
``qdrant_store.py`` split, verbatim.

**Keyless, like Ollama.** ``api_key`` is accepted (the port's fixed shape
across every adapter, DD-13) and never read: the local model has no
authentication of its own to present it to, and the Composition Root's
``keyless_providers`` set (2.9 decision 6) names ``"embedding-local"``
alongside ``"ollama"`` so ``CredentialResolver`` is never even consulted for
it.

**Batching (refs ``llm-providers.md`` §5).** ``settings.batch`` (default 8)
caps how many texts cross the wire in one ``POST /embed`` -- the central
service loads exactly one model process-wide and this keeps any single
caller's request from monopolising it. ``embed`` splits ``texts`` into
groups of ``settings.batch``, issues one request per group, and concatenates
the vectors/sums the tokens in the ORIGINAL order -- indistinguishable to the
caller from a single unbatched call.

**Bounded retry, short and capped.** Only ``httpx.ConnectError``/
``httpx.TimeoutException`` and a 502/503/504 response are retried, up to
``settings.max_retries`` times, with a small exponential backoff capped at
``_BACKOFF_CAP_S`` -- these are exactly the "the service is transiently
unreachable/overloaded" cases. A 4xx (a caller/wiring bug -- e.g. the
adapter's configured model drifting from the baked image) and a malformed
200 (an off-contract body) are NEVER retried: retrying either would just
repeat the same failure ``max_retries`` times before reporting it.

**Token fallback.** If the service omits ``tokens`` or reports a
non-positive value, this adapter estimates ``sum(max(1, len(t)//4) for t in
texts)`` locally -- the SAME formula ``ai_providers/llm/shared.py``'s
``estimate_tokens`` already uses, duplicated rather than imported: this
module's own import list (above) deliberately excludes every sibling
``ai_providers`` module, the same "nothing speculative until a SECOND
adapter needs it" discipline ``shared.py``'s own docstring states for why it
was extracted only once OpenAI (the second LLM adapter) existed to compare
against -- there is only one embedding adapter today.

**Never logs a request/response body or any secret** -- there is no secret
here (see "Keyless" above), but the discipline is kept anyway, the same
"no logger imported at all" precedent every adapter since 2.3 follows.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import httpx

from app.framework.errors import AppError, ValidationError
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.settings.settings import EmbeddingServiceSettings
from app.framework.types import Json

_PROVIDER: str = "embedding-local"

# The service's one route (``services/embedding/app.py``).
_EMBED_PATH: str = "/embed"

# Fail fast instead of hanging a request-handling coroutine on a dead service
# (the 2.3-2.8 precedent across every driven adapter).
_CONNECT_TIMEOUT_S: float = 5.0

# Retried (module docstring): the service is transiently unreachable/
# overloaded. Everything else -- a 4xx, any other 5xx -- returns immediately.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({502, 503, 504})
_HTTP_BAD_REQUEST: int = httpx.codes.BAD_REQUEST

# Small, capped exponential backoff between retries (module docstring) --
# short enough that a genuinely transient blip costs a request little, capped
# so a caller is never made to wait longer than this for a single attempt.
_BACKOFF_BASE_S: float = 0.05
_BACKOFF_CAP_S: float = 0.5

# The token-estimate fallback's per-character rate -- ``ai_providers/llm/
# shared.py``'s ``_CHARS_PER_TOKEN``, duplicated deliberately (module
# docstring's "Token fallback" paragraph).
_CHARS_PER_TOKEN: int = 4


def create_embedding_http_client(
    settings: EmbeddingServiceSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the shared embedding-service HTTP client (Composition Root /
    test harness only) -- the ``create_ollama_http_client`` precedent.

    ``_guard_base_url`` runs FIRST, before the client is built (the same
    reason ``ollama_llm.py``'s own factory gives): httpx accepts an empty/
    malformed ``base_url`` at construction and only raises on the first
    actual request, which -- wrapped by this adapter's own retry/translate
    logic -- would otherwise become a silent, eternal 500 on every call
    rather than a loud failure at boot.

    ``trust_env=False`` closes the DD-11 proxy trap every other adapter's
    factory already closes: httpx's default reads ``$HTTP_PROXY``/
    ``$HTTPS_PROXY``/``$ALL_PROXY`` whenever no explicit ``transport`` is
    given, which would let anyone who can set an env var on this process
    redirect every embedded tenant text to a third party.

    ``transport`` defaults to ``None`` (httpx's real transport); the unit
    suite passes an ``httpx.MockTransport`` through this SAME parameter (the
    ``create_firebase_http_client``/``create_ollama_http_client`` precedent),
    so this factory's real ``timeout``/``trust_env``/``base_url`` choices are
    what actually get exercised.
    """
    base_url = _guard_base_url(settings.url)
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(settings.timeout_s, connect=_CONNECT_TIMEOUT_S),
        trust_env=False,
        transport=transport,
    )


def _guard_base_url(base_url: str) -> str:
    """Reject an empty or schemeless ``base_url`` BEFORE any client is even
    built -- the ``ollama_llm._guard_base_url`` precedent, verbatim."""
    stripped = base_url.strip()
    if not stripped or not stripped.startswith(("http://", "https://")):
        raise ValidationError(
            f"EMBEDDING_SERVICE_URL must be an absolute http(s) URL: {base_url!r}"
        )
    return stripped


def _validate_texts(texts: Sequence[str]) -> None:
    """Fail loudly, before any network call (the ``ollama_llm._build_chat_
    body``/``vault_secrets._guard_key_name`` precedent): an empty ``texts``,
    or any element that is not a non-blank string, is a CALLER mistake --
    ``ValidationError``/422, never folded into ``common.internal``."""
    if not texts:
        raise ValidationError("texts must not be empty")
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("texts must not contain an empty or whitespace-only entry")


def _internal_error(detail: str) -> AppError:
    """The ONE place this adapter constructs ``AppError(code='common.
    internal')`` -- the ``qdrant_store._translate`` precedent (module
    docstring: a first-party internal dependency, not the LLM adapters'
    502)."""
    return AppError(detail, code="common.internal")


def _estimate_tokens(texts: Sequence[str]) -> int:
    return sum(max(1, len(text) // _CHARS_PER_TOKEN) for text in texts)


def _token_count(payload: Json, texts: Sequence[str]) -> int:
    """The service's own reported count if it is a genuinely positive
    ``int``, else the local estimate (module docstring's "Token fallback")."""
    value = payload.get("tokens")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return _estimate_tokens(texts)


def _parse_response(
    raw: str, *, expected_count: int, texts: Sequence[str], dimensions: int
) -> tuple[list[list[float]], int]:
    """Map one ``POST /embed`` response body onto ``(vectors, tokens)``.

    Every field is read through an ``isinstance`` guard -- never a bare
    ``payload["vectors"]`` -- so an off-contract 200 (non-JSON, a non-object
    body, the wrong vector count, a vector of the wrong dimension or
    non-numeric contents) raises ``common.internal`` via ``_internal_error``,
    never a raw ``KeyError``/``TypeError`` (R6, the 2.5/2.6/2.8 precedent).
    """
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise _internal_error("embedding service returned a non-JSON response body") from exc
    if not isinstance(payload, dict):
        raise _internal_error("embedding service returned a non-object response body")

    raw_vectors = payload.get("vectors")
    if not isinstance(raw_vectors, list) or len(raw_vectors) != expected_count:
        raise _internal_error("embedding service returned an unexpected vector count")

    vectors: list[list[float]] = []
    for vector in raw_vectors:
        if (
            not isinstance(vector, list)
            or len(vector) != dimensions
            or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in vector)
        ):
            raise _internal_error("embedding service returned a malformed vector")
        vectors.append([float(v) for v in vector])

    return vectors, _token_count(payload, texts)


async def _call_with_retry(
    client: httpx.AsyncClient, body: Json, *, max_retries: int
) -> httpx.Response:
    """``POST /embed`` with a small, capped exponential backoff (module
    docstring) on ``ConnectError``/``TimeoutException``/502/503/504 --
    everything else (a 4xx, a healthy-looking 200) returns immediately,
    unretried."""
    attempt = 0
    while True:
        try:
            response = await client.post(_EMBED_PATH, json=body)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if attempt >= max_retries:
                raise _internal_error("embedding service call failed") from exc
        else:
            if response.status_code not in _RETRYABLE_STATUSES or attempt >= max_retries:
                return response
        attempt += 1
        await asyncio.sleep(min(_BACKOFF_BASE_S * (2 ** (attempt - 1)), _BACKOFF_CAP_S))


class ExternalEmbeddingProvider:
    """HTTP-backed ``EmbeddingProvider`` (02 §1.2, structural Protocol
    match) over the central embedding service (``services/embedding/
    app.py``)."""

    # A plain class attribute, NOT ``typing.ClassVar`` -- the ``OllamaLLM.
    # provider``/``QdrantVectorStore`` precedent: mypy rejects a ``ClassVar``
    # against the port's own INSTANCE-attribute annotation.
    provider: str = _PROVIDER

    def __init__(self, client: httpx.AsyncClient, settings: EmbeddingServiceSettings) -> None:
        self._client = client
        self._settings = settings

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        """``api_key`` is accepted (the port's fixed shape, DD-13) and never
        used (module docstring, "Keyless, like Ollama")."""
        _validate_texts(texts)
        text_list = list(texts)
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(text_list), self._settings.batch):
            batch = text_list[start : start + self._settings.batch]
            batch_vectors, batch_tokens = await self._embed_batch(batch, model)
            vectors.extend(batch_vectors)
            tokens += batch_tokens
        return EmbeddingResult(
            vectors=vectors, model=model, dimensions=self._settings.dimensions, tokens=tokens
        )

    def dimensions(self, model: str) -> int:
        """The pinned, baked-image dimension (``settings.dimensions``, 384)
        -- no I/O, and independent of ``model`` (this deployment serves
        exactly one local model, module docstring)."""
        return self._settings.dimensions

    async def _embed_batch(self, batch: Sequence[str], model: str) -> tuple[list[list[float]], int]:
        body: Json = {"texts": list(batch), "model": model}
        response = await _call_with_retry(
            self._client, body, max_retries=self._settings.max_retries
        )
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise _internal_error(f"embedding service returned HTTP {response.status_code}")
        return _parse_response(
            response.text,
            expected_count=len(batch),
            texts=batch,
            dimensions=self._settings.dimensions,
        )
