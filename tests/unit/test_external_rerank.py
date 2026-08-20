"""Unit tests for the ``ExternalRerankProvider`` adapter
(``infrastructure/ai_providers/rerank/external_rerank.py``,
rag-retrieval-plan.md §4 row 21, ``P-24``, decision س-21): a full hermetic
proof of ``rerank()`` via ``httpx.MockTransport`` wired through the adapter's
OWN factory (``create_rerank_http_client``) -- the
``test_external_embedding.py``/``test_ollama_mapping.py`` ``MockTransport``
idiom. No marker, no Docker, no live rerank service.
"""

from __future__ import annotations

import ast
import inspect
import json
from typing import Any

import httpx
import pytest

from app.framework.errors import AppError, ValidationError
from app.framework.settings.settings import RerankServiceSettings
from app.infrastructure.ai_providers.rerank import external_rerank
from app.infrastructure.ai_providers.rerank.external_rerank import (
    ExternalRerankProvider,
    _guard_base_url,
    create_rerank_http_client,
)


def _ok_response(*pairs: tuple[int, float], key: str = "score") -> httpx.Response:
    """The declared 200 body: ``{"results": [{"index": i, "score": s}]}``."""
    return httpx.Response(
        200, json={"results": [{"index": index, key: score} for index, score in pairs]}
    )


class _RecordingHandler:
    """A ``MockTransport`` handler that records every request and always
    replies with the same canned response."""

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


def _settings(**overrides: Any) -> RerankServiceSettings:
    base: dict[str, Any] = {
        "url": "http://rerank.test",
        "model": "test-reranker",
        "timeout_s": 5.0,
        "max_retries": 0,
    }
    base.update(overrides)
    return RerankServiceSettings(**base)


def _provider_with(
    handler: _RecordingHandler | _RaisingHandler, **settings_overrides: Any
) -> ExternalRerankProvider:
    """An ``ExternalRerankProvider`` wired to ``handler`` through the
    adapter's OWN factory, so the factory's real ``trust_env``/``timeout``/
    ``base_url`` choices are what actually get exercised."""
    settings = _settings(**settings_overrides)
    client = create_rerank_http_client(settings, transport=httpx.MockTransport(handler))
    return ExternalRerankProvider(client, settings)


# --------------------------------------------------------------------------- #
# create_rerank_http_client / _guard_base_url                                 #
# --------------------------------------------------------------------------- #
def test_create_rerank_http_client_disables_trust_env() -> None:
    """DD-11's proxy trap, closed the way every other adapter factory closes
    it: without this, ``$HTTPS_PROXY`` could redirect retrieved tenant
    documents to a third party."""
    client = create_rerank_http_client(_settings())
    assert client.trust_env is False


def test_create_rerank_http_client_sets_a_short_timeout_pair() -> None:
    """§6 risk 6 in numbers: an OPTIONAL stage may not hold an answer
    hostage, so the connect timeout is tighter than the embedding adapter's
    and the read timeout is the configured one."""
    client = create_rerank_http_client(_settings(timeout_s=9.0))
    assert client.timeout.read == 9.0
    assert client.timeout.connect == 2.0


def test_create_rerank_http_client_guard_runs_before_any_client_is_built() -> None:
    """A deployment that turned the feature ON deserves to hear that its URL
    is unusable at BOOT -- not to have every rerank silently degrade."""
    with pytest.raises(ValidationError):
        create_rerank_http_client(_settings(url="not-a-url"))
    with pytest.raises(ValidationError):
        create_rerank_http_client(_settings(url="   "))


def test_guard_base_url_strips_and_accepts_both_schemes() -> None:
    assert _guard_base_url("  http://rerank:8080 ") == "http://rerank:8080"
    assert _guard_base_url("https://rerank.example") == "https://rerank.example"


# --------------------------------------------------------------------------- #
# rerank() -- the happy path and the request body                             #
# --------------------------------------------------------------------------- #
async def test_rerank_returns_the_service_order() -> None:
    handler = _RecordingHandler(_ok_response((2, 0.9), (0, 0.4)))
    ranked = await _provider_with(handler).rerank("q", ["a", "b", "c"])

    assert [(doc.index, doc.score) for doc in ranked] == [(2, 0.9), (0, 0.4)]


async def test_rerank_posts_query_documents_model_and_a_full_top_n() -> None:
    """``top_n = len(documents)`` is the request that gives the pipeline a
    FULL ordering to re-sort by -- asking for fewer would cost the same
    forward passes and throw away placements the stages downstream can still
    use, which is how a reranker starves ``final_top_n``."""
    handler = _RecordingHandler(_ok_response((0, 1.0)))
    await _provider_with(handler).rerank("what is the policy?", ["a", "b", "c"])

    assert len(handler.calls) == 1
    request = handler.calls[0]
    assert request.url.path == "/rerank"
    body = json.loads(request.content)
    assert body == {
        "query": "what is the policy?",
        "documents": ["a", "b", "c"],
        "model": "test-reranker",
        "top_n": 3,
    }


async def test_rerank_accepts_a_bare_list_body() -> None:
    """A Hugging Face ``text-embeddings-inference`` reranker answers with a
    bare array; the declared ``{"results": [...]}`` envelope is the other
    accepted shape. Two shapes, and no further leniency."""
    handler = _RecordingHandler(httpx.Response(200, json=[{"index": 1, "score": 0.5}]))
    ranked = await _provider_with(handler).rerank("q", ["a", "b"])

    assert [doc.index for doc in ranked] == [1]


async def test_rerank_accepts_relevance_score_as_the_score_alias() -> None:
    """The name Cohere/Jina-style APIs give the same number. One alias."""
    handler = _RecordingHandler(_ok_response((0, 0.75), key="relevance_score"))
    ranked = await _provider_with(handler).rerank("q", ["a"])

    assert ranked[0].score == 0.75


async def test_rerank_may_return_fewer_entries_than_documents() -> None:
    """Legitimate, not off-contract (the port says so): the adapter passes it
    straight through, and the PIPELINE's guard is what keeps the answer
    full-length (``test_knowledge_pipeline.py``)."""
    handler = _RecordingHandler(_ok_response((1, 0.9)))
    ranked = await _provider_with(handler).rerank("q", ["a", "b", "c"])

    assert [doc.index for doc in ranked] == [1]


async def test_rerank_provider_name_is_the_adapters_own() -> None:
    assert ExternalRerankProvider.provider == "rerank-local"


# --------------------------------------------------------------------------- #
# Caller mistakes -- ValidationError, never folded into common.internal       #
# --------------------------------------------------------------------------- #
async def test_rerank_rejects_a_blank_query_before_any_network_call() -> None:
    handler = _RecordingHandler(_ok_response((0, 1.0)))
    provider = _provider_with(handler)

    with pytest.raises(ValidationError):
        await provider.rerank("   ", ["a"])
    assert handler.calls == []


async def test_rerank_rejects_an_empty_document_list_before_any_network_call() -> None:
    handler = _RecordingHandler(_ok_response((0, 1.0)))
    provider = _provider_with(handler)

    with pytest.raises(ValidationError):
        await provider.rerank("q", [])
    assert handler.calls == []


async def test_rerank_does_not_reject_a_blank_document() -> None:
    """Unlike ``_validate_texts``: a chunk's text comes from a parser, and a
    reranker's answer for an empty document is perfectly usable -- refusing
    the whole call would turn one poor document into a failed stage."""
    handler = _RecordingHandler(_ok_response((0, 0.1), (1, 0.0)))
    ranked = await _provider_with(handler).rerank("q", ["real text", "  "])

    assert len(ranked) == 2


# --------------------------------------------------------------------------- #
# Translation -- every technical failure becomes AppError/common.internal      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        # NOT one of the two retryable classes -- the point of catching
        # ``httpx.HTTPError`` wholesale is that no raw httpx exception ever
        # escapes into the pipeline, which catches ``AppError`` alone.
        httpx.RemoteProtocolError("garbage"),
    ],
)
async def test_rerank_translates_every_transport_failure(exc: Exception) -> None:
    provider = _provider_with(_RaisingHandler(exc))

    with pytest.raises(AppError) as excinfo:
        await provider.rerank("q", ["a"])
    assert excinfo.value.code == "common.internal"


@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
async def test_rerank_translates_every_error_status(status: int) -> None:
    provider = _provider_with(_RecordingHandler(httpx.Response(status, json={"results": []})))

    with pytest.raises(AppError) as excinfo:
        await provider.rerank("q", ["a"])
    assert excinfo.value.code == "common.internal"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"no_results_key": []}),
        httpx.Response(200, json="a string"),
        httpx.Response(200, json={"results": ["not an object"]}),
        httpx.Response(200, json={"results": [{"score": 0.5}]}),
        httpx.Response(200, json={"results": [{"index": "0", "score": 0.5}]}),
        # Out of range for a two-document call, and a repeat of one.
        httpx.Response(200, json={"results": [{"index": 7, "score": 0.5}]}),
        httpx.Response(200, json={"results": [{"index": -1, "score": 0.5}]}),
        httpx.Response(200, json={"results": [{"index": 0, "score": 0.5}, {"index": 0}]}),
        httpx.Response(200, json={"results": [{"index": 0, "score": "high"}]}),
    ],
)
async def test_rerank_translates_every_off_contract_body(response: httpx.Response) -> None:
    """R6: an off-contract 200 raises ``common.internal``, never a raw
    ``KeyError``/``TypeError``/``ValueError`` out of the adapter. Bounds and
    duplicates are checked HERE so the port's promise holds for every
    caller."""
    provider = _provider_with(_RecordingHandler(response))

    with pytest.raises(AppError) as excinfo:
        await provider.rerank("q", ["a", "b"])
    assert excinfo.value.code == "common.internal"


async def test_rerank_makes_exactly_one_attempt_at_the_shipped_max_retries() -> None:
    """``max_retries = 0`` ships (``RerankServiceSettings``): a retry would
    spend a second helping of the user's latency on an OPTIONAL stage."""
    handler = _RaisingHandler(httpx.ConnectError("refused"))
    provider = _provider_with(handler)

    with pytest.raises(AppError):
        await provider.rerank("q", ["a"])
    assert len(handler.calls) == 1


async def test_rerank_retries_a_transient_failure_when_configured_to() -> None:
    """The mechanism is wired even though the shipped number disables it --
    raising it stays a configuration change, not a code one."""
    handler = _RaisingHandler(httpx.ConnectError("refused"))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError):
        await provider.rerank("q", ["a"])
    assert len(handler.calls) == 3


async def test_rerank_never_retries_a_client_error() -> None:
    """A 4xx is a caller/wiring bug (a model name the service does not
    serve); retrying just repeats it."""
    handler = _RecordingHandler(httpx.Response(400, json={"results": []}))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError):
        await provider.rerank("q", ["a"])
    assert len(handler.calls) == 1


def test_the_adapter_module_loads_no_model_and_isolates_httpx() -> None:
    """§3.10's architectural rule -- "لا أوزان داخل صورة العامل" -- read off
    the module's own AST rather than trusted (the ``domain/mmr.py``/
    ``file_resolution.py`` precedent, applied to an adapter because THIS is
    the file the rule is about).

    Exhaustive on purpose. A ``torch``/``sentence-transformers``/
    ``FlagEmbedding`` import here would bake a cross-encoder into the API and
    worker images, which is exactly the breach §3.10 names; and the only
    technical library present is ``httpx``, which is the isolation this
    adapter exists to provide (no ``app.modules``, no ``app.api``, nothing
    from a sibling ``ai_providers`` package).
    """
    tree = ast.parse(inspect.getsource(external_rerank))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert imported == {
        "__future__",
        "asyncio",
        "json",
        "collections.abc",
        "httpx",
        "app.framework.errors",
        "app.framework.ports.rerank_provider",
        "app.framework.settings.settings",
        "app.framework.types",
    }
