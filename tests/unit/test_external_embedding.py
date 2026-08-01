"""Unit tests for the ``ExternalEmbeddingProvider`` adapter
(``infrastructure/ai_providers/embedding/external_embedding.py``, Phase
2.10): a full hermetic proof of ``embed()``/``dimensions()`` via
``httpx.MockTransport`` wired through the adapter's OWN factory
(``create_embedding_http_client``) -- the ``test_ollama_mapping.py``/
``test_firebase_auth.py`` ``MockTransport`` idiom. No marker, no Docker, no
live embedding service.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.framework.errors import AppError, ValidationError
from app.framework.settings.settings import EmbeddingServiceSettings
from app.framework.types import Json
from app.infrastructure.ai_providers.embedding.external_embedding import (
    ExternalEmbeddingProvider,
    _guard_base_url,
    create_embedding_http_client,
)

_NO_TOKENS = object()


def _ok_response(
    *, count: int, dim: int = 4, model: str = "test-model", tokens: Any = _NO_TOKENS
) -> httpx.Response:
    """The confirmed-shape 200 body (design brief's own contract):
    ``{vectors, model, dimensions, tokens}``. ``tokens`` defaults to
    genuinely ABSENT (the sentinel), distinct from an explicit ``None``."""
    body: Json = {
        "vectors": [[float(i)] * dim for i in range(count)],
        "model": model,
        "dimensions": dim,
    }
    if tokens is not _NO_TOKENS:
        body["tokens"] = tokens
    return httpx.Response(200, json=body)


class _RecordingHandler:
    """A ``MockTransport`` handler that records every request and always
    replies with the same canned response -- the ``test_ollama_mapping.py``
    precedent."""

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


class _SequenceHandler:
    """Returns/raises one scripted outcome per call, in order; the LAST
    outcome repeats once exhausted, so a test never has to over-specify a
    tail it does not assert on."""

    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self._outcomes = outcomes
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _settings(**overrides: Any) -> EmbeddingServiceSettings:
    base: dict[str, Any] = {
        "url": "http://embedding.test",
        "model": "test-model",
        "dimensions": 4,
        "batch": 8,
        "timeout_s": 5.0,
        "max_retries": 2,
    }
    base.update(overrides)
    return EmbeddingServiceSettings(**base)


def _provider_with(
    handler: _RecordingHandler | _RaisingHandler | _SequenceHandler, **settings_overrides: Any
) -> ExternalEmbeddingProvider:
    """An ``ExternalEmbeddingProvider`` wired to ``handler`` through the
    adapter's OWN factory (the ``create_ollama_http_client`` precedent), so
    the factory's real ``trust_env``/``timeout``/``base_url`` choices are
    what actually get exercised."""
    settings = _settings(**settings_overrides)
    client = create_embedding_http_client(settings, transport=httpx.MockTransport(handler))
    return ExternalEmbeddingProvider(client, settings)


# --------------------------------------------------------------------------- #
# create_embedding_http_client / _guard_base_url                              #
# --------------------------------------------------------------------------- #
def test_create_embedding_http_client_disables_trust_env() -> None:
    client = create_embedding_http_client(_settings())
    assert client.trust_env is False


def test_create_embedding_http_client_sets_the_timeout_pair() -> None:
    client = create_embedding_http_client(_settings(timeout_s=15.0))
    assert client.timeout == httpx.Timeout(15.0, connect=5.0)


def test_create_embedding_http_client_sets_the_base_url() -> None:
    client = create_embedding_http_client(_settings(url="http://embedding.test"))
    assert str(client.base_url) == "http://embedding.test"


@pytest.mark.parametrize("base_url", ["http://x:1", "https://x", " http://x "])
def test_guard_base_url_accepts_http_and_https(base_url: str) -> None:
    assert _guard_base_url(base_url) == base_url.strip()


@pytest.mark.parametrize("base_url", ["", "   ", "ftp://x", "x.example.com"])
def test_guard_base_url_rejects_empty_or_schemeless(base_url: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _guard_base_url(base_url)
    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


def test_create_embedding_http_client_guard_runs_before_any_client_is_built() -> None:
    with pytest.raises(ValidationError):
        create_embedding_http_client(_settings(url="not-a-url"))


# --------------------------------------------------------------------------- #
# provider / dimensions -- no I/O                                             #
# --------------------------------------------------------------------------- #
def test_provider_attribute_is_embedding_local() -> None:
    provider = ExternalEmbeddingProvider(httpx.AsyncClient(), _settings())
    assert provider.provider == "embedding-local"


def test_dimensions_returns_the_pinned_settings_value_regardless_of_model() -> None:
    provider = ExternalEmbeddingProvider(httpx.AsyncClient(), EmbeddingServiceSettings())
    assert provider.dimensions("anything") == 384
    assert provider.dimensions("") == 384
    assert provider.dimensions("some-other-model-name") == 384


# --------------------------------------------------------------------------- #
# embed -- batching                                                           #
# --------------------------------------------------------------------------- #
async def test_embed_splits_into_batches_of_settings_batch_and_concatenates_in_order() -> None:
    """Mutation target: the batching boundary (8 -> 7 would split this
    9-text call into 8+1 differently, changing the call count and the
    per-call payload split asserted below)."""
    handler = _SequenceHandler([_ok_response(count=8), _ok_response(count=1)])
    provider = _provider_with(handler, batch=8)
    texts = [f"text-{i}" for i in range(9)]

    result = await provider.embed(texts, "test-model", "ignored-key")

    assert len(handler.calls) == 2
    first_body = json.loads(handler.calls[0].content)
    second_body = json.loads(handler.calls[1].content)
    assert first_body["texts"] == texts[:8]
    assert second_body["texts"] == texts[8:]
    assert len(result.vectors) == 9
    assert result.model == "test-model"
    assert result.dimensions == 4


async def test_embed_a_batch_exactly_settings_batch_long_is_a_single_call() -> None:
    handler = _SequenceHandler([_ok_response(count=8)])
    provider = _provider_with(handler, batch=8)

    result = await provider.embed([f"t{i}" for i in range(8)], "test-model", "")

    assert len(handler.calls) == 1
    assert len(result.vectors) == 8


async def test_embed_a_single_text_makes_one_call() -> None:
    handler = _RecordingHandler(_ok_response(count=1))
    provider = _provider_with(handler)

    result = await provider.embed(["hello"], "test-model", "")

    assert len(handler.calls) == 1
    assert result.vectors == [[0.0, 0.0, 0.0, 0.0]]


# --------------------------------------------------------------------------- #
# embed -- api_key is accepted and ignored                                    #
# --------------------------------------------------------------------------- #
async def test_api_key_never_changes_the_request_and_is_never_sent_as_a_header() -> None:
    handler = _RecordingHandler(_ok_response(count=1))
    provider = _provider_with(handler)

    await provider.embed(["hello"], "test-model", "key-one")
    await provider.embed(["hello"], "test-model", "key-two")

    assert len(handler.calls) == 2
    assert handler.calls[0].content == handler.calls[1].content
    assert "authorization" not in handler.calls[0].headers
    assert "authorization" not in handler.calls[1].headers


# --------------------------------------------------------------------------- #
# embed -- pre-flight validation, zero I/O                                    #
# --------------------------------------------------------------------------- #
async def test_embed_rejects_empty_texts_before_any_io() -> None:
    handler = _RecordingHandler(_ok_response(count=1))
    provider = _provider_with(handler)

    with pytest.raises(ValidationError) as excinfo:
        await provider.embed([], "test-model", "")

    assert excinfo.value.status == 422
    assert handler.calls == []


@pytest.mark.parametrize(
    "texts",
    [
        [""],
        ["   "],
        ["\n\t"],
        ["good text", ""],
        ["good text", "   "],
    ],
)
async def test_embed_rejects_any_blank_entry_before_any_io(texts: list[str]) -> None:
    handler = _RecordingHandler(_ok_response(count=1))
    provider = _provider_with(handler)

    with pytest.raises(ValidationError) as excinfo:
        await provider.embed(texts, "test-model", "")

    assert excinfo.value.status == 422
    assert handler.calls == []


# --------------------------------------------------------------------------- #
# embed -- token counting: passthrough vs the char//4 fallback                #
# --------------------------------------------------------------------------- #
async def test_embed_uses_the_services_reported_token_count_when_positive() -> None:
    handler = _RecordingHandler(_ok_response(count=2, tokens=41))
    provider = _provider_with(handler)

    result = await provider.embed(["hello world", "another text"], "test-model", "")

    assert result.tokens == 41


async def test_embed_falls_back_to_the_char_estimate_when_tokens_is_absent() -> None:
    # "hello world" -> len 11 // 4 = 2 ; "hi" -> len 2 // 4 = 0 -> max(1, 0) = 1
    handler = _RecordingHandler(_ok_response(count=2))
    provider = _provider_with(handler)

    result = await provider.embed(["hello world", "hi"], "test-model", "")

    assert result.tokens == 3


@pytest.mark.parametrize("tokens", [None, 0, -5])
async def test_embed_falls_back_to_the_char_estimate_when_tokens_is_none_or_non_positive(
    tokens: int | None,
) -> None:
    handler = _RecordingHandler(_ok_response(count=1, tokens=tokens))
    provider = _provider_with(handler)

    result = await provider.embed(["hello world"], "test-model", "")  # 11 // 4 = 2

    assert result.tokens == 2


async def test_embed_sums_tokens_across_batches() -> None:
    handler = _SequenceHandler([_ok_response(count=1, tokens=10), _ok_response(count=1, tokens=7)])
    provider = _provider_with(handler, batch=1)

    result = await provider.embed(["a", "b"], "test-model", "")

    assert result.tokens == 17


# --------------------------------------------------------------------------- #
# embed -- error mapping: everything folds into common.internal/500           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("timed out")])
async def test_embed_transport_failures_become_common_internal_after_retries(
    exc: Exception,
) -> None:
    handler = _RaisingHandler(exc)
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500
    assert len(handler.calls) == 3  # initial attempt + 2 retries


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_embed_retryable_5xx_becomes_common_internal_after_exhausting_retries(
    status: int,
) -> None:
    handler = _RecordingHandler(httpx.Response(status))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500
    assert len(handler.calls) == 3


async def test_embed_a_non_retryable_5xx_is_common_internal_after_exactly_one_call() -> None:
    """Mutation target: the error-map branch (internal -> pass-through) --
    if a 500 ever escaped untranslated this would raise something other
    than ``AppError``/``common.internal``."""
    handler = _RecordingHandler(httpx.Response(500))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"
    assert len(handler.calls) == 1  # 500 is not in the retryable set


async def test_embed_a_4xx_is_common_internal_never_agent_failed_and_not_retried() -> None:
    handler = _RecordingHandler(httpx.Response(400, json={"detail": "model mismatch"}))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500
    assert excinfo.value.code != "agent.failed"
    assert len(handler.calls) == 1


async def test_embed_an_error_status_with_a_well_formed_body_is_still_common_internal() -> None:
    """Mutation target: the status-code check in ``_embed_batch`` must fire
    on the STATUS alone, never merely fall out of a malformed-body parse.
    A coincidentally well-shaped ``{vectors, model, dimensions}`` body
    attached to a 404 would otherwise be accepted as a SUCCESSFUL embed --
    proving the status check is load-bearing, not redundant with
    ``_parse_response``'s own guards."""
    body = {"vectors": [[0.0, 0.0, 0.0, 0.0]], "model": "test-model", "dimensions": 4}
    handler = _RecordingHandler(httpx.Response(404, json=body))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"
    assert len(handler.calls) == 1


async def test_embed_a_non_json_200_is_common_internal_and_not_retried() -> None:
    handler = _RecordingHandler(httpx.Response(200, text="not json"))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"
    assert len(handler.calls) == 1


async def test_embed_a_non_object_200_is_common_internal() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=[1, 2, 3]))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError):
        await provider.embed(["hello"], "test-model", "")

    assert len(handler.calls) == 1


async def test_embed_wrong_vector_count_is_common_internal() -> None:
    handler = _RecordingHandler(_ok_response(count=1))  # 1 vector back
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["one", "two"], "test-model", "")  # 2 texts sent

    assert excinfo.value.code == "common.internal"
    assert len(handler.calls) == 1


async def test_embed_wrong_vector_dimension_is_common_internal() -> None:
    handler = _RecordingHandler(_ok_response(count=1, dim=2))  # settings dim=4
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"
    assert len(handler.calls) == 1


async def test_embed_non_numeric_vector_contents_is_common_internal() -> None:
    body = {
        "vectors": [["not", "numbers", "at", "all"]],
        "model": "test-model",
        "dimensions": 4,
    }
    handler = _RecordingHandler(httpx.Response(200, json=body))
    provider = _provider_with(handler, max_retries=2)

    with pytest.raises(AppError) as excinfo:
        await provider.embed(["hello"], "test-model", "")

    assert excinfo.value.code == "common.internal"


# --------------------------------------------------------------------------- #
# embed -- bounded retry actually helps on a transient failure                #
# --------------------------------------------------------------------------- #
async def test_embed_retries_a_503_and_succeeds_on_the_next_attempt() -> None:
    handler = _SequenceHandler([httpx.Response(503), _ok_response(count=1)])
    provider = _provider_with(handler, max_retries=2)

    result = await provider.embed(["hello"], "test-model", "")

    assert len(handler.calls) == 2
    assert result.vectors == [[0.0, 0.0, 0.0, 0.0]]


async def test_embed_with_zero_max_retries_fails_on_the_first_5xx() -> None:
    handler = _RecordingHandler(httpx.Response(503))
    provider = _provider_with(handler, max_retries=0)

    with pytest.raises(AppError):
        await provider.embed(["hello"], "test-model", "")

    assert len(handler.calls) == 1
