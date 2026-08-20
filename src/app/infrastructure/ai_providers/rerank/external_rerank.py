"""Adapter for the ``RerankProvider`` port (rag-retrieval-plan.md §3.10,
``P-24``, decision س-21).

The same split as every driven adapter since 2.3 (``qdrant_store.py``,
``ollama_llm.py``, ``external_embedding.py``): a factory builds the
technology client (``create_rerank_http_client``, Composition Root / test
harness only) and a thin adapter class (``ExternalRerankProvider``)
implements the port over it (structural Protocol match -- no inheritance).
``external_embedding.py`` is the precedent this file follows line for line,
because the two adapters describe the same kind of dependency: a first-party
internal model service behind a small HTTP API.

**No model weights, and that is the point of the row** (§3.10: "لا أوزان
داخل صورة العامل" -- an existing architectural rule whose breach bloats the
image). This module imports ONLY ``httpx``, ``app.framework.errors``,
``app.framework.ports.rerank_provider`` (value types),
``app.framework.settings`` and ``app.framework.types`` -- no torch, no
sentence-transformers, no ``FlagEmbedding``, no ``app.modules``, no
``app.api``. The cross-encoder runs in a SEPARATE deployable this adapter
only ever talks to over HTTP, the way ``ollama_llm.py`` talks to an Ollama
server, so nothing this file adds can put a model into the API or worker
image.

**Failure is deliberate, and it is never fatal.** ``ExternalRerankProvider``
translates like ``external_embedding.py`` does -- every transport failure,
timeout, service-side error status or off-contract 200 body folds into
``AppError``/``common.internal``, never a raw ``httpx``/``ValueError``
escaping the infrastructure layer -- and then ``RetrieveContext._rerank``
CATCHES that and carries on with the order it already had. The division is
deliberate: an adapter's job is to report a failure honestly in the
platform's own vocabulary, and whether an OPTIONAL pipeline stage is worth
failing an answer over is the pipeline's decision, not the transport's. The
result is that a slow, erroring or unreachable rerank service costs an
answer its improvement and nothing else -- see ``RetrieveContext._rerank``
for the guarantee stated from the other side. ``ValidationError`` from this
module's own pre-flight guards (``_validate_inputs``, ``_guard_base_url``)
is a different, caller-caused failure -- the ``qdrant_store.py``/
``external_embedding.py`` split, verbatim.

**Latency is the whole cost of this feature** (§6 risk ٦), so the timeouts
are tight and ``max_retries`` ships at ``0``
(``RerankServiceSettings``' own docstring has the argument). One request per
``rerank`` call -- no batching, unlike ``external_embedding.py``: a
cross-encoder scores the whole candidate set against ONE query in one
forward pass, and the set is ~10-20 documents by §3.10's scope, so splitting
it would multiply round trips for nothing.

**Keyless, like Ollama and like the embedding service.** The port carries no
``api_key`` at all (see ``rerank_provider.py`` for why the resolver-driven
``model``/``api_key`` pair is absent), and ``CredentialResolver`` is never
consulted: an internal service on the platform's own network has no
credential of its own to present.

**Never logs a request/response body** -- documents are tenant content
(10-code-standards §10). No logger is imported at all, the precedent every
adapter since 2.3 follows.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import httpx

from app.framework.errors import AppError, ValidationError
from app.framework.ports.rerank_provider import RerankedDocument
from app.framework.settings.settings import RerankServiceSettings
from app.framework.types import Json

_PROVIDER: str = "rerank-local"

# The service's one route.
_RERANK_PATH: str = "/rerank"

# Fail fast instead of hanging a request-handling coroutine on a dead service
# (the 2.3-2.8 precedent). Tighter than `external_embedding.py`'s 5s on
# purpose: this stage is OPTIONAL (§6 risk 6), so time spent discovering the
# service is gone is time added to an answer that needs none of it.
_CONNECT_TIMEOUT_S: float = 2.0

# Retried when `settings.max_retries` allows it (it ships at 0): the service
# is transiently unreachable/overloaded. Everything else -- a 4xx, any other
# 5xx -- returns immediately.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({502, 503, 504})
_HTTP_BAD_REQUEST: int = httpx.codes.BAD_REQUEST

# Small, capped exponential backoff between retries -- inert at the shipped
# `max_retries = 0`, and kept so raising that number stays a configuration
# change rather than a code one (`external_embedding.py`'s shape).
_BACKOFF_BASE_S: float = 0.05
_BACKOFF_CAP_S: float = 0.5


def create_rerank_http_client(
    settings: RerankServiceSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the shared rerank-service HTTP client (Composition Root / test
    harness only) -- the ``create_embedding_http_client`` precedent.

    ``_guard_base_url`` runs FIRST, before the client is built: httpx accepts
    an empty/malformed ``base_url`` at construction and only raises on the
    first actual request, which -- wrapped by this adapter's own translate
    logic and then swallowed by the pipeline's degrade-and-carry-on branch --
    would otherwise become a reranker that is silently never applied. A
    deployment that turned the feature ON deserves to hear that its URL is
    unusable at BOOT.

    ``trust_env=False`` closes the DD-11 proxy trap every other adapter's
    factory closes: httpx's default reads ``$HTTP_PROXY``/``$HTTPS_PROXY``/
    ``$ALL_PROXY`` whenever no explicit ``transport`` is given, which would
    let anyone who can set an env var on this process redirect every
    retrieved tenant document to a third party.

    ``transport`` defaults to ``None`` (httpx's real transport); the unit
    suite passes an ``httpx.MockTransport`` through this SAME parameter, so
    this factory's real ``timeout``/``trust_env``/``base_url`` choices are
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
    built -- the ``external_embedding._guard_base_url`` precedent, verbatim."""
    stripped = base_url.strip()
    if not stripped or not stripped.startswith(("http://", "https://")):
        raise ValidationError(f"RERANK_SERVICE_URL must be an absolute http(s) URL: {base_url!r}")
    return stripped


def _validate_inputs(query: str, documents: Sequence[str]) -> None:
    """Fail loudly, before any network call (the ``external_embedding.
    _validate_texts`` precedent): a blank query or an empty ``documents`` is
    a CALLER mistake -- ``ValidationError``, never folded into
    ``common.internal``.

    Individual documents are NOT rejected for being blank, unlike
    ``_validate_texts``: a chunk's text arrives from a parser and a
    reranker's answer for an empty document (a low score) is perfectly
    usable, so refusing the whole call would turn a poor document into a
    failed stage.
    """
    if not query.strip():
        raise ValidationError("rerank query must not be empty")
    if not documents:
        raise ValidationError("rerank documents must not be empty")


def _internal_error(detail: str) -> AppError:
    """The ONE place this adapter constructs ``AppError(code='common.
    internal')`` -- the ``qdrant_store._translate``/``external_embedding``
    precedent. The pipeline catches ``AppError``, so this is also the shape
    that makes a rerank outage degrade instead of failing an answer."""
    return AppError(detail, code="common.internal")


def _entries(payload: object) -> list[object]:
    """The result entries out of either accepted body shape.

    ``{"results": [...]}`` is this adapter's declared contract (the Cohere/
    Jina rerank shape), and a BARE top-level list is accepted too because
    that is what a Hugging Face ``text-embeddings-inference`` reranker
    returns. Two shapes is the whole of the leniency here -- anything else
    is off-contract and raises.
    """
    if isinstance(payload, dict):
        results = payload.get("results")
        if not isinstance(results, list):
            raise _internal_error("rerank service returned no results array")
        return list(results)
    if isinstance(payload, list):
        return list(payload)
    raise _internal_error("rerank service returned an unexpected response body")


def _score(entry: dict[str, object]) -> float:
    """``score``, or ``relevance_score`` -- the name Cohere/Jina-style APIs
    use for the same number. One alias, no other key guessing."""
    for key in ("score", "relevance_score"):
        value = entry.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    raise _internal_error("rerank service returned a result without a score")


def _parse_response(raw: str, *, document_count: int) -> list[RerankedDocument]:
    """Map one ``POST /rerank`` response body onto the port's value objects.

    Every field is read through an ``isinstance`` guard -- never a bare
    ``entry["index"]`` -- so an off-contract 200 (non-JSON, an unexpected
    body, a missing/non-integer ``index``, an out-of-range ``index``, a
    repeated one, a missing score) raises ``common.internal`` via
    ``_internal_error`` rather than a raw ``KeyError``/``TypeError`` (R6, the
    2.5/2.6/2.8 precedent).

    Bounds and duplicates are checked HERE, at the boundary, so the port's
    documented promise ("carries no entry for a document it did not rank, and
    never repeats an index") is true for every caller rather than something
    each caller re-checks. The pipeline's own guard
    (``RetrieveContext._rerank``) still tolerates a short list -- that one is
    about a service answering with FEWER entries, which is legitimate, not
    off-contract.
    """
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise _internal_error("rerank service returned a non-JSON response body") from exc

    ranked: list[RerankedDocument] = []
    seen: set[int] = set()
    for entry in _entries(payload):
        if not isinstance(entry, dict):
            raise _internal_error("rerank service returned a malformed result entry")
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < document_count:
            raise _internal_error("rerank service returned an out-of-range document index")
        if index in seen:
            raise _internal_error("rerank service returned a repeated document index")
        seen.add(index)
        ranked.append(RerankedDocument(index=index, score=_score(entry)))
    return ranked


async def _call_with_retry(
    client: httpx.AsyncClient, body: Json, *, max_retries: int
) -> httpx.Response:
    """``POST /rerank``, with a small capped backoff on
    ``ConnectError``/``TimeoutException``/502/503/504 while ``max_retries``
    allows it (it ships at ``0`` -- one attempt).

    Every OTHER ``httpx.HTTPError`` (a protocol error, a broken read, a
    redirect loop) is translated on the spot and never retried. Catching the
    library's whole base class rather than the two transient subclasses is
    what keeps a raw ``httpx`` exception from escaping the infrastructure
    layer into the pipeline -- which would bypass the ``AppError`` the
    degrade-and-carry-on branch is written against and fail the answer this
    stage exists to improve.
    """
    attempt = 0
    while True:
        try:
            response = await client.post(_RERANK_PATH, json=body)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if attempt >= max_retries:
                raise _internal_error("rerank service call failed") from exc
        except httpx.HTTPError as exc:
            raise _internal_error("rerank service call failed") from exc
        else:
            if response.status_code not in _RETRYABLE_STATUSES or attempt >= max_retries:
                return response
        attempt += 1
        await asyncio.sleep(min(_BACKOFF_BASE_S * (2 ** (attempt - 1)), _BACKOFF_CAP_S))


class ExternalRerankProvider:
    """HTTP-backed ``RerankProvider`` (rag-retrieval-plan.md §3.10,
    structural Protocol match) over an external cross-encoder rerank
    service."""

    # A plain class attribute, NOT ``typing.ClassVar`` -- the
    # ``ExternalEmbeddingProvider.provider``/``OllamaLLM.provider``
    # precedent: mypy rejects a ``ClassVar`` against the port's own
    # INSTANCE-attribute annotation.
    provider: str = _PROVIDER

    def __init__(self, client: httpx.AsyncClient, settings: RerankServiceSettings) -> None:
        self._client = client
        self._settings = settings

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankedDocument]:
        """One ``POST /rerank`` for the whole candidate set (module
        docstring: no batching).

        ``top_n`` is sent as ``len(documents)`` -- i.e. "rank all of these",
        the request that gives the pipeline a full ordering to re-sort by.
        Sending a smaller ``top_n`` would cost the same forward passes (a
        cross-encoder scores every pair regardless) and merely throw away
        placements the parent-dedup and context-budget stages downstream can
        still consume, which is precisely how a reranker ends up starving
        ``final_top_n``.
        """
        _validate_inputs(query, documents)
        document_list = list(documents)
        body: Json = {
            "query": query,
            "documents": document_list,
            "model": self._settings.model,
            "top_n": len(document_list),
        }
        response = await _call_with_retry(
            self._client, body, max_retries=self._settings.max_retries
        )
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise _internal_error(f"rerank service returned HTTP {response.status_code}")
        return _parse_response(response.text, document_count=len(document_list))
