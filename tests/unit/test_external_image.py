"""Unit tests for the ``OpenAIImage`` adapter
(``infrastructure/ai_providers/image/external_image.py``, step 19 of
``deferred-adapters-plan.md``): a hermetic proof of ``generate()`` via
``httpx.MockTransport`` wired through the adapter's OWN factory
(``create_openai_image_http_client``) -- the
``test_external_embedding.py``/``test_openai_mapping.py`` idiom. No marker,
no network, no key.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx
import pytest

from app.framework.errors import AppError, ValidationError
from app.framework.ports.image_provider import ImageRequest
from app.framework.types import Json
from app.infrastructure.ai_providers.image.external_image import (
    OpenAIImage,
    create_openai_image_http_client,
)
from app.modules.credentials.domain.value_objects import ProviderRef

# A byte string that STARTS with the real 8-byte PNG signature -- which is
# all the adapter checks, and all it should check: it verifies the format
# claim it makes, it does not decode the image.
_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-pixels"
_B64 = base64.b64encode(_PNG).decode()

_KEY = "sk-test-not-a-real-key"


class _RecordingHandler:
    """A ``MockTransport`` handler that records every request and always
    replies with the same canned response (the ``test_external_embedding.py``
    precedent)."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._response


class _RaisingHandler:
    """A ``MockTransport`` handler simulating a dead connection/timeout at the
    ``httpx`` transport layer itself."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        raise self._exc


class _NeverCalledHandler:
    """Fails the test if any request is issued at all -- how the
    "raises BEFORE network I/O" guarantees are actually proven."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request should have been sent, got {request.url}")


def _ok_body(*, encoded: str = _B64) -> Json:
    """OpenAI's confirmed ``/images/generations`` 200 shape."""
    return {"created": 1, "data": [{"b64_json": encoded}]}


_Handler = Callable[[httpx.Request], httpx.Response]


def _adapter(handler: _Handler) -> tuple[OpenAIImage, httpx.AsyncClient]:
    """Build the adapter through its OWN factory, so the real
    ``base_url``/``timeout``/``trust_env`` choices are exercised."""
    client = create_openai_image_http_client(
        timeout_s=300.0, transport=httpx.MockTransport(handler)
    )
    return OpenAIImage(client), client


def _request(
    *,
    prompt: str = "a cat wearing sunglasses",
    width: int = 1024,
    height: int = 1024,
    model: str = "gpt-image-1",
    extra: Json | None = None,
) -> ImageRequest:
    return ImageRequest(prompt=prompt, width=width, height=height, model=model, extra=extra)


# --------------------------------------------------------------------------- #
# the name                                                                     #
# --------------------------------------------------------------------------- #
def test_the_provider_name_is_a_credential_key_the_platform_can_actually_store() -> None:
    """The load-bearing test of this module, and the one that would have
    caught the plan's original ``openai-image``.

    ``ProviderRef`` is what ``ResolveCredential`` parses the routed provider
    name with, so a name it rejects wires fine, boots fine, and then fails
    EVERY generation with ``credentials.provider_unknown``/422 -- at request
    time, for a mistake made at wiring time. Asserting through the real
    domain value object rather than against the literal is the whole point:
    a future rename that drifts out of 06 §3's ``<modality>:<name>``
    vocabulary goes red here.
    """
    assert ProviderRef(OpenAIImage.provider).value == OpenAIImage.provider
    assert OpenAIImage.provider == "image:openai"


# --------------------------------------------------------------------------- #
# the happy path                                                               #
# --------------------------------------------------------------------------- #
async def test_generate_sends_the_documented_body_and_returns_decoded_png_bytes() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_body()))
    adapter, client = _adapter(handler)
    async with client:
        result = await adapter.generate(_request(width=1024, height=1536), _KEY)

    assert result.content == _PNG
    assert result.content_type == "image/png"
    # The model we ASKED for, never one read back off the response (this
    # endpoint does not reliably echo it).
    assert result.model == "gpt-image-1"

    (request,) = handler.calls
    assert str(request.url) == "https://api.openai.com/v1/images/generations"
    assert request.method == "POST"
    body = json.loads(request.content)
    assert body == {
        "model": "gpt-image-1",
        "prompt": "a cat wearing sunglasses",
        "size": "1024x1536",
        "n": 1,
    }
    # Neither format knob is ever sent -- the PNG guarantee depends on it.
    assert "response_format" not in body
    assert "output_format" not in body


async def test_the_api_key_travels_as_a_per_request_bearer_header() -> None:
    handler = _RecordingHandler(httpx.Response(200, json=_ok_body()))
    adapter, client = _adapter(handler)
    async with client:
        await adapter.generate(_request(), _KEY)

    (request,) = handler.calls
    assert request.headers["authorization"] == f"Bearer {_KEY}"
    # Never a client-level default: one process-wide client serves every
    # tenant, so a default header would send one tenant's key on another's
    # request.
    assert "authorization" not in client.headers


def test_the_factory_pins_the_base_url_and_refuses_ambient_proxy_env() -> None:
    client = create_openai_image_http_client(timeout_s=300.0)
    assert str(client.base_url) == "https://api.openai.com/v1/"
    # `trust_env=False` is the key-exfiltration defence `shared.py` centralizes.
    assert client.trust_env is False
    assert client.timeout.read == 300.0
    assert client.timeout.connect == 5.0


# --------------------------------------------------------------------------- #
# caller mistakes -- 422 BEFORE any network I/O                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("req", "message"),
    [
        pytest.param(_request(prompt="   "), "prompt must not be empty", id="blank-prompt"),
        pytest.param(_request(model=" "), "model must not be empty", id="blank-model"),
        pytest.param(_request(width=0), "width and height must be positive", id="zero-width"),
        pytest.param(
            _request(height=-1), "width and height must be positive", id="negative-height"
        ),
        pytest.param(
            _request(extra={"response_format": "url"}),
            "extra is not supported",
            id="extra-is-refused-not-merged",
        ),
    ],
)
async def test_a_malformed_request_is_a_422_and_never_reaches_the_network(
    req: ImageRequest, message: str
) -> None:
    adapter, client = _adapter(_NeverCalledHandler())
    async with client:
        with pytest.raises(ValidationError) as exc_info:
            await adapter.generate(req, _KEY)
    assert message in str(exc_info.value)


async def test_an_empty_api_key_is_a_422_not_an_agent_failure() -> None:
    """422, never ``agent.failed``/502: being handed no key is OUR wiring
    bug, and blaming the provider for it sends an operator to the wrong
    system."""
    adapter, client = _adapter(_NeverCalledHandler())
    async with client:
        with pytest.raises(ValidationError) as exc_info:
            await adapter.generate(_request(), "   ")
    assert "api_key must not be empty for the image:openai provider" in str(exc_info.value)


async def test_the_body_guard_runs_before_the_key_guard() -> None:
    """Pinned order (the ``OpenAILLM.complete`` precedent): a caller passing
    BOTH a blank prompt and a blank key gets the prompt error every time,
    never a race between two guards."""
    adapter, client = _adapter(_NeverCalledHandler())
    async with client:
        with pytest.raises(ValidationError) as exc_info:
            await adapter.generate(_request(prompt=""), "")
    assert "prompt must not be empty" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# provider failures -- every one is agent.failed/502                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        pytest.param(401, "rejected the api key", id="401-is-not-unauthorized"),
        pytest.param(429, "rate limited", id="429-is-not-our-rate-limit"),
        pytest.param(404, "model not available", id="404-is-not-not-found"),
        pytest.param(500, "call failed", id="500-is-generic"),
        pytest.param(503, "call failed", id="503-is-generic"),
    ],
)
async def test_every_error_status_becomes_agent_failed(status: int, fragment: str) -> None:
    handler = _RecordingHandler(httpx.Response(status, json={"error": {"message": "nope"}}))
    adapter, client = _adapter(handler)
    async with client:
        with pytest.raises(AppError) as exc_info:
            await adapter.generate(_request(), _KEY)

    error = exc_info.value
    assert error.code == "agent.failed"
    assert error.status == 502
    assert fragment in str(error)
    # Always prefixed with THIS adapter's own provider string, so two
    # adapters' errors are never ambiguous about their source.
    assert str(error).startswith("image:openai ")


async def test_a_400_names_the_size_we_sent_and_nothing_from_the_body() -> None:
    """The ``_has_tool_role`` precedent applied to the field that actually
    differs here: the detail is derived from OUR OWN outgoing request, never
    from the response body (which OpenAI is believed to echo key material
    into on 401, and which carries the user's prompt back on 400)."""
    handler = _RecordingHandler(
        httpx.Response(400, json={"error": {"message": "invalid size for gpt-image-1"}})
    )
    adapter, client = _adapter(handler)
    async with client:
        with pytest.raises(AppError) as exc_info:
            await adapter.generate(_request(width=333, height=777, prompt="secret prompt"), _KEY)

    detail = str(exc_info.value)
    assert "333x777" in detail
    assert "content-policy refusal" in detail
    assert "invalid size for gpt-image-1" not in detail
    assert "secret prompt" not in detail


async def test_no_error_path_ever_echoes_the_api_key() -> None:
    """10-code-standards §9/§10 -- the key is a secret, and unlike a base URL
    it is never diagnostic enough to be worth echoing."""
    handler = _RecordingHandler(httpx.Response(401, json={"error": {"message": _KEY[:8]}}))
    adapter, client = _adapter(handler)
    async with client:
        with pytest.raises(AppError) as exc_info:
            await adapter.generate(_request(), _KEY)
    assert _KEY not in str(exc_info.value)


@pytest.mark.parametrize(
    ("exc", "fragment"),
    [
        pytest.param(httpx.TimeoutException("slow"), "call timed out", id="timeout"),
        pytest.param(httpx.ConnectError("down"), "call failed", id="connect-error"),
    ],
)
async def test_a_transport_failure_becomes_agent_failed(exc: Exception, fragment: str) -> None:
    adapter, client = _adapter(_RaisingHandler(exc))
    async with client:
        with pytest.raises(AppError) as exc_info:
            await adapter.generate(_request(), _KEY)
    assert exc_info.value.code == "agent.failed"
    assert fragment in str(exc_info.value)


# --------------------------------------------------------------------------- #
# off-contract 200 bodies -- R6                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("response", "fragment"),
    [
        pytest.param(
            httpx.Response(200, content=b"<html>gateway</html>"),
            "non-JSON response body",
            id="not-json",
        ),
        pytest.param(
            httpx.Response(200, json=[1, 2]), "non-object response body", id="json-but-not-object"
        ),
        pytest.param(httpx.Response(200, json={"data": []}), "no image data", id="empty-data"),
        pytest.param(httpx.Response(200, json={}), "no image data", id="missing-data"),
        pytest.param(
            httpx.Response(200, json={"data": ["nope"]}),
            "malformed image entry",
            id="entry-not-an-object",
        ),
        pytest.param(
            httpx.Response(200, json={"data": [{"url": "https://cdn/img.png"}]}),
            "remote url instead of inline image bytes",
            id="url-is-refused-never-fetched",
        ),
        pytest.param(
            httpx.Response(200, json={"data": [{"revised_prompt": "x"}]}),
            "without inline bytes",
            id="no-bytes-at-all",
        ),
        pytest.param(
            httpx.Response(200, json=_ok_body(encoded="not base64 !!")),
            "undecodable base64",
            id="undecodable-base64",
        ),
        pytest.param(
            httpx.Response(
                200, json=_ok_body(encoded=base64.b64encode(b"\xff\xd8\xffJPEG").decode())
            ),
            "not image/png",
            id="png-is-proven-not-assumed",
        ),
    ],
)
async def test_an_off_contract_200_is_agent_failed_never_a_raw_exception(
    response: httpx.Response, fragment: str
) -> None:
    adapter, client = _adapter(_RecordingHandler(response))
    async with client:
        with pytest.raises(AppError) as exc_info:
            await adapter.generate(_request(), _KEY)
    assert exc_info.value.code == "agent.failed"
    assert fragment in str(exc_info.value)
