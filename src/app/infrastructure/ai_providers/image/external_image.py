"""OpenAI Images adapter for the ``ImageProvider`` port (02-port-contracts
§1.3, D-02, DD-13). Step 19 of ``deferred-adapters-plan.md`` -- the first
adapter of ANY kind behind that port, which was a 0-byte file from Phase 2
until here.

Same split as every adapter since 2.3: a factory builds the technology
client (``create_openai_image_http_client``, Composition Root / test harness
only), and a thin adapter class (``OpenAIImage``) implements the port over it
(structural Protocol match -- no inheritance). This module never imports
``ImageProvider`` itself, only the value types it exchanges (the
``qdrant_store.py``-imports-``VectorPoint``-never-``VectorStore``
precedent), and it reuses ``llm/shared.py``'s transport/error primitives
rather than restating them -- see that module's own scope note on why an
IMAGE adapter is allowed to.

**The provider name is ``image:openai``, and that is not cosmetic.** The
name a route carries is the name ``ResolveCredential`` looks a credential up
by, and ``credentials.ProviderRef`` accepts a base LLM provider
(``openai``/``gemini``/...) or a ``<modality>:<name>`` key -- 06-domain-models
§3's own ``embedding:* | image:* | video:*`` vocabulary. A hyphenated
``openai-image`` matches NEITHER, so it would parse fine as routing, wire
fine as an adapter, and then raise ``credentials.provider_unknown``/422 on
every single generation -- a failure at request time N, for a mistake made
at wiring time. Using the modality key makes the credential a tenant
actually stores (``image:openai``) the credential this adapter asks for.
The name also keeps an image key and a chat key SEPARATE by construction:
a tenant may hold an OpenAI key entitled to one and not the other.

Settings: NONE -- ``_BASE_URL`` is a literal module constant, the same shape
and the same argument as ``openai_llm.py``: a configurable base URL would be
a key-exfiltration lever (whoever can set that env var redirects every
prompt AND the tenant's API key to a third party), which is precisely the
door ``trust_env=False`` closes on the proxy side. Timeout comes from
``Limits.media_timeout_s`` (300s), NOT ``llm_timeout_s`` (60s): image
generation is minutes-scale work, and reusing the chat budget would time
this adapter out on healthy calls.

The ``api_key`` is sent as a PER-REQUEST header (``_auth_header``, applied
at call time), NEVER as a client-level default: the ``httpx.AsyncClient``
is process-wide while the key is per-call and per-tenant, so a client-level
default would serve one tenant's key on every other tenant's request
through the same client. An empty/whitespace key raises
``ValidationError``/422 before any network I/O -- never ``agent.failed``
(that would blame the provider for our own wiring bug). The key is never
interpolated into any exception message, log, or ``repr``
(10-code-standards §9/§10); this module imports no logger at all (house
precedent, every adapter since 2.3).

Error policy -- every OpenAI failure becomes ``agent.failed``/502 via
``shared.off_contract``, and the response BODY is never read on an error
path (only the status code and OUR OWN outgoing request are consulted), the
rule ``openai_llm.py``'s error-policy paragraph states at length and this
module inherits verbatim: 401 is not ``UnauthorizedError`` (that means the
end user's Firebase token is bad and would trigger a re-login loop over a
bad PROVIDER key); 429 is not ``common.rate_limited`` (that code means OUR
limit, and honouring the provider's ``Retry-After`` would leak the
platform's account tier to a tenant); 404 is not ``NotFoundError`` (that
means "absent within this tenant's data", not "the configured model does not
exist at OpenAI").

**400 names the size we sent.** The one request field this port can produce
that OpenAI restricts to a fixed, per-model set is ``size``, so a 400 gets a
detail carrying ``WxH`` -- derived from OUR OWN outgoing request, never from
anything the response body said (the ``openai_llm._has_tool_role``
precedent, applied to the field that actually differs here). It says "or a
content-policy refusal" because the other common 400 on this endpoint is a
rejected prompt, and inventing certainty between the two would be guessing.
Deliberately NOT a hard-coded table of supported sizes: that set differs per
model and changes whenever OpenAI ships one, so a whitelist here would
reject a size that works long before anyone noticed.

**PNG is proven, not assumed.** This adapter never sends ``response_format``
or ``output_format`` -- ``gpt-image-1`` rejects the former outright, and both
would let a caller silently change the bytes' format while ``_CONTENT_TYPE``
kept claiming PNG. The default IS PNG, and the magic-byte check below turns
that from an assumption into a checked fact: a body that is not PNG fails
loudly here rather than being stored in ``files`` under a content type that
lies about it. A stored file's content type is what every later consumer
(download, OCR, the browser) trusts.

**A remote ``url`` is refused, never fetched.** Some models/parameters answer
with a hosted URL instead of inline ``b64_json``. Fetching it would be a
whole second I/O path needing its own timeout, size cap, and failure
translation -- the very argument that keeps ``VideoProvider`` out of this
plan's scope (``VideoResult.remote_url``). So the URL shape gets its own
named refusal instead of a half-built download path.

``ImageResult.model`` echoes the model we REQUESTED, not one read from the
response: this endpoint does not reliably echo it back, and defaulting a
missing echo to ``""`` would report a model nobody chose.
"""

from __future__ import annotations

import base64

import httpx

from app.framework.errors import AppError, ValidationError
from app.framework.ports.image_provider import ImageRequest, ImageResult
from app.framework.types import Json
from app.infrastructure.ai_providers.llm.shared import (
    create_llm_http_client,
    off_contract,
    parse_json_object,
    translate_http_error,
)

# 06-domain-models §3's `image:*` modality key -- see the module docstring's
# provider-name paragraph. Changing this string invalidates every stored
# credential routed at it.
_PROVIDER: str = "image:openai"

# A literal module constant, never configuration (module docstring, "Settings: NONE").
_BASE_URL: str = "https://api.openai.com/v1"
_IMAGES_PATH: str = "/images/generations"

# One image per call: the port returns ONE `ImageResult`, so asking for more
# would pay for bytes nothing can return.
_IMAGE_COUNT: int = 1

# The format this adapter guarantees, and verifies (module docstring, "PNG is
# proven, not assumed"). `_PNG_MAGIC` is the 8-byte PNG signature.
_CONTENT_TYPE: str = "image/png"
_PNG_MAGIC: bytes = b"\x89PNG\r\n\x1a\n"

# Named HTTP status constants (the `qdrant_store.py` `_HTTP_CONFLICT`
# precedent) -- also keeps ruff's magic-value-comparison rule quiet.
_HTTP_BAD_REQUEST: int = httpx.codes.BAD_REQUEST
_HTTP_UNAUTHORIZED: int = httpx.codes.UNAUTHORIZED
_HTTP_NOT_FOUND: int = httpx.codes.NOT_FOUND
_HTTP_TOO_MANY_REQUESTS: int = httpx.codes.TOO_MANY_REQUESTS


def create_openai_image_http_client(
    *,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the shared OpenAI Images HTTP client (Composition Root / test
    harness only). Delegates to ``shared.create_llm_http_client`` -- the ONE
    place ``trust_env=False`` and the ``(timeout_s, connect=5.0)`` timeout
    pair are set -- rather than constructing an ``httpx.AsyncClient`` here,
    because ``trust_env`` is a security invariant, not a per-adapter taste
    (see that function's own docstring). Callers pass
    ``Limits.media_timeout_s``, not ``llm_timeout_s`` (module docstring).

    ``transport`` defaults to ``None`` (httpx's normal HTTP transport); the
    unit suite passes an ``httpx.MockTransport`` through this SAME parameter
    (the ``create_embedding_http_client`` precedent), so this factory's real
    ``timeout``/``trust_env``/``base_url`` choices are what get exercised,
    never a stand-in.
    """
    return create_llm_http_client(base_url=_BASE_URL, timeout_s=timeout_s, transport=transport)


def _auth_header(api_key: str) -> dict[str, str]:
    """Build the PER-REQUEST ``Authorization`` header (module docstring's
    warning on client-level defaults). Never logs, never echoes the key in
    its own exception (10-code-standards §9/§10).

    A near-copy of ``openai_llm._auth_header`` and deliberately not hoisted
    into ``shared.py``: that module's scope-discipline rule admits only what
    two shipped adapters of the SAME port demonstrably duplicated, and this
    is the first (and so far only) image adapter. Two copies of six lines is
    the cheaper mistake; a premature extraction that a future local or
    signed-request image provider does not fit is the expensive one.
    """
    key = api_key.strip()
    if not key:
        raise ValidationError(f"api_key must not be empty for the {_PROVIDER} provider")
    return {"Authorization": f"Bearer {key}"}


def _size(req: ImageRequest) -> str:
    """OpenAI's ``WxH`` size string, built from the port's own two ints --
    also the ONLY request fact a 400 is allowed to name (module docstring)."""
    return f"{req.width}x{req.height}"


def _build_body(req: ImageRequest) -> Json:
    """Build the ``/images/generations`` wire body from the port's own
    ``ImageRequest``.

    Fails loudly, before any network call: a blank prompt, a blank model,
    non-positive dimensions, or a populated ``extra`` all raise
    ``ValidationError`` -- a caller mistake, never folded into
    ``agent.failed``.

    ``extra`` is REFUSED rather than merged into the body. Nothing in the
    platform populates it today (``GenParams`` has no free-form field), and
    a passthrough into a vendor body is exactly how ``response_format``/
    ``output_format`` would arrive and quietly break the PNG guarantee this
    adapter makes. Refusing keeps the port honest: a caller that starts
    setting ``extra`` learns immediately that this adapter does not carry
    it, instead of watching it vanish.
    """
    prompt = req.prompt.strip()
    if not prompt:
        raise ValidationError("prompt must not be empty")
    if not req.model.strip():
        raise ValidationError("model must not be empty")
    if req.width <= 0 or req.height <= 0:
        raise ValidationError("width and height must be positive")
    if req.extra is not None:
        raise ValidationError(f"extra is not supported by the {_PROVIDER} provider")
    return {"model": req.model, "prompt": prompt, "size": _size(req), "n": _IMAGE_COUNT}


def _to_result(payload: Json, model: str) -> ImageResult:
    """Map one 200 body onto ``ImageResult``, R6-safely: every off-contract
    shape becomes ``agent.failed``/502 with a detail naming WHICH shape, and
    never a raw ``KeyError``/``IndexError``/``binascii.Error``."""
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise off_contract(_PROVIDER, "returned no image data")
    entry = data[0]
    if not isinstance(entry, dict):
        raise off_contract(_PROVIDER, "returned a malformed image entry")
    encoded = entry.get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        # A hosted URL is a KNOWN shape, not a malformed one -- it gets its
        # own message so the operator reads "wrong model/parameters", not
        # "the provider is broken" (module docstring: never fetched).
        if isinstance(entry.get("url"), str):
            raise off_contract(_PROVIDER, "returned a remote url instead of inline image bytes")
        raise off_contract(_PROVIDER, "returned an image entry without inline bytes")
    try:
        # `binascii.Error` IS a `ValueError` subclass, so one clause covers
        # both it and any other decoding refusal.
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise off_contract(_PROVIDER, "returned undecodable base64 image bytes") from exc
    if not content.startswith(_PNG_MAGIC):
        raise off_contract(_PROVIDER, f"returned image bytes that are not {_CONTENT_TYPE}")
    return ImageResult(content=content, content_type=_CONTENT_TYPE, model=model)


def _translate_status(status: int, *, size: str) -> AppError:
    """Map an OpenAI HTTP error STATUS (never its body -- module docstring's
    error-policy paragraph) onto ``agent.failed``/502. A pure function of
    ``status``/``size`` alone -- no I/O, nothing read from the response."""
    if status == _HTTP_UNAUTHORIZED:
        return off_contract(_PROVIDER, "rejected the api key")
    if status == _HTTP_TOO_MANY_REQUESTS:
        return off_contract(_PROVIDER, "call failed: rate limited")
    if status == _HTTP_NOT_FOUND:
        return off_contract(_PROVIDER, "model not available")
    if status == _HTTP_BAD_REQUEST:
        return off_contract(
            _PROVIDER,
            f"call failed: the {size} request was rejected "
            "(unsupported size for this model, or a content-policy refusal)",
        )
    return off_contract(_PROVIDER, "call failed")


class OpenAIImage:
    """OpenAI-backed ``ImageProvider`` (02 §1.3, structural Protocol match)."""

    # A plain class attribute, NOT typing.ClassVar (the `OpenAILLM`/
    # `OllamaLLM` precedent: mypy rejects a ClassVar against the Protocol's
    # own INSTANCE-attribute annotation, `ImageProvider.provider: str`).
    provider: str = _PROVIDER

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def generate(self, req: ImageRequest, api_key: str) -> ImageResult:
        """Order is pinned, as in ``OpenAILLM.complete``: ``_build_body``
        runs BEFORE ``_auth_header``, so a caller passing both a blank
        prompt AND a blank key deterministically gets the prompt error --
        never a race between two guards, and never any network I/O."""
        body = _build_body(req)
        headers = _auth_header(api_key)
        try:
            response = await self._client.post(_IMAGES_PATH, json=body, headers=headers)
            if response.status_code >= _HTTP_BAD_REQUEST:
                raise _translate_status(response.status_code, size=_size(req))
            # Unwrapped INSIDE the try (the `OpenAILLM.complete` precedent):
            # translated by `_to_result`/`parse_json_object`'s own guards,
            # never a raw exception. `AppError` is not an `httpx.HTTPError`,
            # so the raise above is never re-caught below.
            return _to_result(parse_json_object(_PROVIDER, response.text), req.model)
        except httpx.HTTPError as exc:
            raise translate_http_error(_PROVIDER, exc) from exc
