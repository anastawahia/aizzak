"""Ollama adapter for the ``LLMProvider`` port (02-port-contracts §1.1, D-15,
DD-13). Phase 2.8-a.

Same split as ``auth/firebase_auth.py`` (2.7), ``secrets/vault_secrets.py``
(2.6) and ``vector/qdrant_store.py`` (2.5): a factory builds the technology
client (``create_ollama_http_client``, Composition Root / test harness
only), and a thin adapter class (``OllamaLLM``) implements the port over it
(structural Protocol match -- no inheritance). This module never imports
``LLMProvider`` itself, only the value types it exchanges
(``LlmMessage``/``LlmParams``/``LlmChunk``/``LlmResult``) -- the
``qdrant_store.py``-imports-``VectorPoint``-never-``VectorStore`` precedent.

First LLM adapter (DD-13: five native adapters -- OpenAI, Gemini, Claude,
Ollama, OpenRouter -- no LangChain, no LlamaIndex). Ollama is the platform's
ONLY *local* provider (refs ``llm-providers.md`` §1: ``kind=local``,
``requires_user_key=False``) -- unlike every cloud adapter still to come,
it needs no API key at all (see "``api_key`` is silently ignored" below).

Live-probed carrying values (refs §6 -- knowledge/values reused from alpha,
never its LangChain/LlamaIndex *code*, which is deliberately NOT ported,
DD-13): ``num_ctx=8192`` with its own documented reason -- omit it and
Ollama either silently truncates the context around ~4096 tokens, or loads
the model's full native context window (some models: 100k+), which alpha's
own history records as a GPU-crashing failure mode; ``keep_alive="30m"``
sent on EVERY request (not left to the server's own default) to keep the
model resident in VRAM across calls, rather than reloading it (a ~43s cold
load, confirmed live); ``think: false``, confirmed live as a normal 200
against ``gemma3:1b`` (not a rejected field).

Deliberately DROPPED from alpha (refs §6, "يُسقَط صراحةً"): the pinned
``temperature=0.3`` and the always-unbounded ``num_predict=-1``. Both are an
anti-pattern this adapter corrects rather than carries forward --
``LlmParams`` is a PER-REQUEST value object (``temperature``, ``max_tokens``,
...) the caller already controls; this adapter has no opinion of its own on
either and never overrides them.

Documented gap, left open in v1 (refs §6): ``_NUM_CTX = 8192`` is smaller
than ``Limits.max_input_tokens = 32_000``. A prompt landing between the two
is silently truncated INSIDE Ollama -- the exact same failure family
``num_ctx`` exists to avoid in the first place, just recurring at a higher
threshold. Widening ``_NUM_CTX`` is a memory/latency trade-off for a later
phase to make deliberately (it directly trades VRAM headroom for context
size); v1 keeps the literal and the gap stays documented here rather than
silently patched.

``api_key`` is silently ignored (Decision 2, refs §3 risk 3): never read,
never sent, never named in any error detail -- Ollama has no auth of its own
to present it to. A local, no-user-key provider is exactly what
``kind=="local"`` means one layer up, at ``ProviderResolver``/
``CredentialResolver`` (refs §6); this adapter simply honours that by never
touching the parameter's value, while still accepting it, because the
``LLMProvider`` port's own shape is fixed across all five adapters (DD-13).

Error policy: EVERY failure on the Ollama side -- a dead/slow server, an
HTTP error status, an off-contract 200 body, a stream that ends before
``done:true`` -- becomes ``agent.failed``/502 (03-api-spec §4, "فشل تنفيذ
الوكيل/المزوّد"؛ 07-nfr-slo §4 pins this very code to the LLM-call timeout
budget). ``errors.py`` has no dedicated ``AgentFailedError`` subclass
(unlike ``NotFoundError``/``ValidationError``/...), so ``status=502`` is
passed explicitly to ``AppError`` here, the same way ``firebase_auth``
passes its own status values explicitly rather than relying on a subclass
default. A 404 (unknown model, confirmed live) is deliberately NOT
``NotFoundError``: ``common.not_found`` means "absent within THIS TENANT's
own data", which would misrepresent "the configured model does not exist on
the Ollama server" -- and nothing in this codebase branches on it anyway (no
Fallback between providers, D-16). The response BODY of an HTTP error is
never read (``_translate_status`` is a pure function of the status code
alone): Ollama's own confirmed-live error bodies name internal
model/registry paths (``registry.ollama.ai/library/...``), which would leak
server topology to a tenant for zero benefit. The one caller-relevant detail
this adapter does add -- whether a 400 likely means "this model does not
support tools" -- is derived from OUR OWN outgoing request
(``tools_sent = bool(params.tools)``), never from anything the server said.

Streaming is genuinely new work (refs §4: zero behavioural precedent in
alpha, which never streams anything anywhere). The discovery that shapes
this module: a non-streamed Ollama response body is confirmed-live to be
*exactly* the same shape as the LAST line of a streamed one (``done:true``,
``done_reason``, both token-count fields, all present in both) -- so one
``shared.parse_json_object`` call serves both call sites, feeding two
separate mappers (``_to_result`` for the whole body, ``_to_chunk`` per
NDJSON line).

Phase 2.8-ب-1 extracted this module's technical primitives -- the HTTP
client factory's ``trust_env=False``/timeout body, the ``agent.failed``
builder, the httpx-error translator, the response-JSON guard, the
token-count triple, and the neutral tool-call shape -- into ``shared.py``
once OpenAI (the second LLM adapter) demonstrably duplicated them; 2.8-a
named this exact list and deferred it explicitly to "the second adapter"
(docs/implementation-status.md §3.23). This module keeps everything that
stays Ollama-specific: ``OllamaSettings``/``base_url``, the NDJSON framing,
``options``/``keep_alive``/``think``/``num_ctx``, the silently-ignored
``api_key``, and the ``tools_sent`` diagnostic. Behaviour is unchanged by
the extraction -- see ``tests/integration/test_ollama_llm.py``'s live 6/6
regression net.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

import httpx

from app.framework.errors import AppError, ValidationError
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.settings.settings import OllamaSettings
from app.framework.types import Json
from app.infrastructure.ai_providers.llm.shared import (
    ROLES,
    create_llm_http_client,
    neutral_tool_call,
    off_contract,
    parse_json_object,
    reported_token_count,
    token_count,
    translate_http_error,
)

_PROVIDER: str = "ollama"

# Ollama's own chat endpoint (refs §6, §2) -- one path serves both the
# non-streamed (``stream: false``) and streamed (``stream: true``) shapes.
_CHAT_PATH: str = "/api/chat"

# The context-window carrying value (refs §6) -- see the module docstring
# for the full rationale and the documented gap against
# ``Limits.max_input_tokens``.
_NUM_CTX: int = 8192

# Sent on EVERY request (refs §6) to override the server's own default and
# keep the model resident in VRAM across calls -- confirmed live via 3
# independent readings despite one stale CODEMAP line claiming "1m".
_KEEP_ALIVE: str = "30m"

# ``supports()``'s allow-list (refs §8: authored fresh, not ported -- alpha
# has no per-model vision/streaming flag to harvest).
_SUPPORTED_CAPABILITIES: frozenset[str] = frozenset({"streaming", "tools"})

# Prefix for the call ids Ollama does not emit and this adapter synthesises
# positionally (4.7-a, port limit (d)) -- see ``_to_tool_calls``.
_SYNTHETIC_CALL_PREFIX: str = "call_"

# Named HTTP status constants (the ``qdrant_store.py`` ``_HTTP_CONFLICT``
# precedent) -- also keeps ruff's magic-value-comparison rule quiet.
_HTTP_BAD_REQUEST: int = httpx.codes.BAD_REQUEST
_HTTP_NOT_FOUND: int = httpx.codes.NOT_FOUND


def create_ollama_http_client(
    settings: OllamaSettings,
    *,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the shared Ollama HTTP client (Composition Root / test harness
    only).

    ``_guard_base_url`` runs FIRST, before the client is built: httpx itself
    happily accepts an empty/malformed ``base_url`` at construction time and
    only raises (``UnsupportedProtocol``) on the first actual request --
    which, wrapped by this adapter's own ``except httpx.HTTPError`` (that
    IS a subclass), would silently become an eternal, silent 502 on every
    call rather than a loud failure at boot.

    ``trust_env=False`` (the 2.6/2.7 DD-11 trap, sharper here): httpx's
    default (``trust_env=True``) reads ``$HTTP_PROXY``/``$HTTPS_PROXY``/
    ``$ALL_PROXY`` via ``urllib.request.getproxies()`` whenever no explicit
    ``transport`` is given -- letting whoever can set an env var on this
    process redirect every prompt (tenant content, credentials embedded in
    tool arguments, ...) to a third party. WSL environments frequently carry
    an ambient ``HTTP_PROXY`` for exactly this reason, making this trap more
    likely to fire here than in 2.6's Vault-only blast radius.

    ``timeout_s`` is typed ``float`` but the Composition Root passes
    ``Limits.llm_timeout_s`` (an ``int``, per 07-nfr-slo §4) -- Python's
    numeric tower makes this a non-issue (``float`` accepts ``int``).

    ``transport`` defaults to ``None`` (httpx's normal HTTP transport); the
    unit suite passes an ``httpx.MockTransport`` through this SAME
    parameter (the ``create_firebase_http_client`` precedent), so the
    factory's real ``timeout``/``trust_env``/``base_url`` choices are what
    actually get exercised.

    Delegates its actual client construction to
    ``shared.create_llm_http_client`` (Phase 2.8-ب-1's extraction) -- the
    ONE place ``trust_env=False`` and the ``(timeout_s, connect=5.0)``
    timeout pair are set for every LLM adapter -- while keeping ITS OWN
    signature (``settings`` + the ``_guard_base_url``-runs-first ordering
    above), because OpenAI's factory needs neither.
    """
    base_url = _guard_base_url(settings.base_url)
    return create_llm_http_client(base_url=base_url, timeout_s=timeout_s, transport=transport)


def _guard_base_url(base_url: str) -> str:
    """Reject an empty or schemeless ``base_url`` BEFORE any client is even
    built (zero network, construction-time only -- the ``_guard_project_id``/
    ``_guard_ttl`` precedent from ``firebase_auth.py``). See
    ``create_ollama_http_client`` for why this must run first."""
    stripped = base_url.strip()
    if not stripped or not stripped.startswith(("http://", "https://")):
        raise ValidationError(f"OLLAMA_BASE_URL must be an absolute http(s) URL: {base_url!r}")
    return stripped


def _build_chat_body(messages: Sequence[LlmMessage], params: LlmParams, *, stream: bool) -> Json:
    """Build Ollama's ``/api/chat`` wire body (refs §2/§6) from the port's
    own ``LlmMessage``/``LlmParams`` value objects -- shared by ``complete``
    (``stream=False``) and ``stream`` (``stream=True``).

    Fails loudly, before any network call (the ``vault_secrets.py``
    ``_guard_key_name``/``_parse_kv_path`` precedent): empty ``messages``, a
    ``role`` outside the port's own ``system|user|assistant|tool`` set (02
    §1.1's own comment), or an empty ``params.model`` all raise
    ``ValidationError`` -- a caller mistake, never folded into
    ``agent.failed``.

    ``options.num_predict`` is omitted entirely when ``params.max_tokens``
    is ``None`` -- never sent as a JSON ``null``, and never defaulted to an
    invented cap (``LlmParams`` owns that decision per-request, not this
    adapter). ``stop``/``tools`` are included only when truthy (the
    ``qdrant_store.py`` ``_build_filter`` precedent: omit rather than send an
    empty list). ``top_p`` uses ``is not None`` rather than truthiness,
    deliberately: unlike an empty list, a scalar ``0.0`` is a real,
    meaningful value that must not be dropped.
    """
    if not messages:
        raise ValidationError("messages must not be empty")
    wire_messages: list[Json] = []
    for message in messages:
        if message.role not in ROLES:
            raise ValidationError(f"unsupported message role: {message.role!r}")
        wire: Json = {"role": message.role, "content": message.content}
        if message.tool_calls:
            wire["tool_calls"] = [_to_wire_tool_call(call) for call in message.tool_calls]
        # `message.tool_call_id` is deliberately NOT sent: Ollama's /api/chat
        # has no such field (it emits no call ids in the first place, which
        # is why `_to_tool_calls` synthesises them) and correlates a tool
        # result POSITIONALLY, by message order. Dropping it here is
        # therefore lossless for this provider -- the id still did its job,
        # letting the orchestrator build the turn in the right order.
        wire_messages.append(wire)
    if not params.model:
        raise ValidationError("params.model must not be empty")

    options: Json = {"temperature": params.temperature, "num_ctx": _NUM_CTX}
    if params.max_tokens is not None:
        options["num_predict"] = params.max_tokens
    if params.top_p is not None:
        options["top_p"] = params.top_p
    if params.stop:
        options["stop"] = list(params.stop)

    body: Json = {
        "model": params.model,
        "messages": wire_messages,
        "stream": stream,
        "think": False,
        "keep_alive": _KEEP_ALIVE,
        "options": options,
    }
    if params.tools:
        body["tools"] = list(params.tools)
    return body


def _to_wire_tool_call(call: Json) -> Json:
    """Map ONE neutral tool call (``{"id", "name", "arguments"}``) back onto
    Ollama's outgoing wire shape ``{"function": {"name", "arguments"}}`` --
    the inverse of ``_to_tool_calls``, needed to replay an assistant turn
    that issued calls (4.7-a, port limit (d)).

    The neutral ``id`` is dropped: Ollama's envelope has no slot for it (see
    ``_build_chat_body``). Raises ``ValidationError``, not ``agent.failed``:
    everything here came from OUR OWN caller re-sending what a previous turn
    produced, so a malformed entry is a caller mistake like an empty
    ``messages`` list -- never a provider fault, and never folded into a 502
    that would blame Ollama for it.
    """
    name = call.get("name")
    if not isinstance(name, str) or not name:
        raise ValidationError("tool_calls entries must carry a non-empty 'name'")
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        raise ValidationError("tool_calls entries must carry an 'arguments' object")
    return {"function": {"name": name, "arguments": arguments}}


def _to_result(payload: Json, messages: Sequence[LlmMessage]) -> LlmResult:
    """Map one parsed, non-streamed Ollama body onto ``LlmResult`` (02
    §1.1). Every field is read through an ``isinstance`` guard -- never a
    bare ``payload["message"]["content"]`` -- so an off-contract 200 (no
    ``message``, non-string ``content``, missing/non-string
    ``done_reason``, ...) raises ``agent.failed`` via ``shared.off_contract``,
    never a raw ``KeyError``/``TypeError`` (R6, the 2.5/2.6 precedent)."""
    message = payload.get("message")
    if not isinstance(message, dict):
        raise off_contract(_PROVIDER, "response is missing a 'message' object")
    content = message.get("content")
    if not isinstance(content, str):
        raise off_contract(_PROVIDER, "response 'message.content' is not a string")
    finish_reason = payload.get("done_reason")
    if not isinstance(finish_reason, str):
        raise off_contract(_PROVIDER, "response is missing 'done_reason'")

    prompt_text = "\n".join(m.content for m in messages)
    return LlmResult(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=token_count(payload, "prompt_eval_count", prompt_text),
        completion_tokens=token_count(payload, "eval_count", content),
        tool_calls=_to_tool_calls(message),
    )


def _to_chunk(payload: Json) -> LlmChunk:
    """Map one parsed NDJSON line onto ``LlmChunk``. Deliberately more
    lenient than ``_to_result``: a missing/malformed ``message``/``content``
    degrades to ``delta=""`` rather than raising -- ``shared.
    parse_json_object`` has already rejected the shapes that must be fatal
    (bad JSON, a non-object body, an ``error`` key), so anything left here
    is a partial delta, not the final answer, and a soft default costs
    nothing. ``finish_reason`` is
    the ONLY end-of-stream signal in the port's contract (module docstring)
    -- it is populated if, and only if, this line's own ``done`` is
    literally ``True``.

    Token counters (4.7-a, port limit (a)) ride that SAME terminal line --
    Ollama puts ``prompt_eval_count``/``eval_count`` on it, and this adapter
    used to discard both. They are read through ``reported_token_count``, so
    an absent or ``<= 0`` counter becomes ``None`` ("unknown, estimate")
    rather than a fabricated number; and they are read ONLY when ``done``,
    so a mid-stream line can never contribute a partial count. ``tool_calls``
    is NOT set here: it is accumulated across lines by ``_iter_chunks`` and
    attached to the terminal chunk there, per ``LlmChunk``'s own contract."""
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    done = payload.get("done") is True
    finish_reason = payload.get("done_reason")
    return LlmChunk(
        delta=content if isinstance(content, str) else "",
        finish_reason=finish_reason if done and isinstance(finish_reason, str) else None,
        prompt_tokens=reported_token_count(payload, "prompt_eval_count") if done else None,
        completion_tokens=reported_token_count(payload, "eval_count") if done else None,
    )


def _to_tool_calls(message: Json) -> list[Json] | None:
    """Map Ollama's ``message.tool_calls`` shape
    (``[{"function": {"name": ..., "arguments": {...}}}]``) onto the
    port-neutral shape ``shared.neutral_tool_call`` owns:
    ``[{"id": str, "name": str, "arguments": Json}]``.

    Ollama emits NO call id of its own, so one is SYNTHESISED here from the
    entry's index within the vendor payload (``call_0``, ``call_1``, ...) --
    4.7-a, port limit (d). Positional synthesis is sound because the id's
    only job is correlating a ``role="tool"`` result back to its call within
    THIS turn, and Ollama itself correlates positionally on the way back in
    (``_build_chat_body`` drops the id again). Indices come from the RAW
    payload, not the filtered output, so a dropped malformed entry never
    renumbers the calls around it.

    An absent or empty list both mean "no tool calls" -- ``None``, never
    ``[]`` -- so a caller can test truthiness directly; a malformed
    individual entry (missing/non-dict ``function``, non-dict
    ``arguments``) is dropped rather than failing the whole response."""
    raw = message.get("tool_calls")
    if not isinstance(raw, list) or not raw:
        return None
    calls: list[Json] = []
    for index, entry in enumerate(raw):
        function = entry.get("function") if isinstance(entry, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if isinstance(arguments, dict):
            # The id Ollama does not emit, synthesised POSITIONALLY from the
            # vendor payload's own index (4.7-a) -- see the docstring.
            call = neutral_tool_call(f"{_SYNTHETIC_CALL_PREFIX}{index}", name, arguments)
            if call is not None:
                calls.append(call)
    return calls or None


def _translate_status(status: int, *, tools_sent: bool) -> AppError:
    """Map an Ollama HTTP error STATUS (never its body -- see the module
    docstring's error-policy paragraph) onto ``agent.failed``/502. A pure
    function of ``status``/``tools_sent`` alone -- no I/O, nothing read from
    the response. 404 is confirmed live for an unknown model; 400 is
    confirmed live for a tool-calling request against a model that does not
    support tools (``gemma3:1b``) -- ``tools_sent`` is OUR OWN request-shape
    fact (whether this call's ``_build_chat_body`` included a ``tools``
    key), never anything parsed from Ollama's own error body."""
    if status == _HTTP_NOT_FOUND:
        return off_contract(_PROVIDER, "model not available")
    if status == _HTTP_BAD_REQUEST and tools_sent:
        return off_contract(_PROVIDER, "call failed: this model may not support tools")
    return off_contract(_PROVIDER, "call failed")


class OllamaLLM:
    """Ollama-backed ``LLMProvider`` (02 §1.1, structural Protocol match)."""

    # A plain class attribute, NOT ``typing.ClassVar``: mypy rejects a
    # ``ClassVar`` here against the Protocol's own INSTANCE-attribute
    # annotation (``LLMProvider.provider: str``). The same shape as
    # ``AppError.code``/``AppError.status`` in ``framework/errors.py``.
    provider: str = "ollama"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        """``api_key`` is accepted (the port's fixed shape, DD-13) and never
        used (module docstring, Decision 2)."""
        # Our own guards fire before any network call.
        body = _build_chat_body(messages, params, stream=False)
        try:
            response = await self._client.post(_CHAT_PATH, json=body)
            if response.status_code >= _HTTP_BAD_REQUEST:
                raise _translate_status(response.status_code, tools_sent=bool(params.tools))
            # Unwrapped INSIDE the try (2.5/2.6 precedent): translated by
            # _to_result/parse_json_object's own guards, never a raw exception.
            return _to_result(parse_json_object(_PROVIDER, response.text), messages)
        except httpx.HTTPError as exc:
            # AppError (raised just above, from _translate_status/
            # parse_json_object/_to_result) is not an httpx.HTTPError, so it
            # is never re-caught here -- this ordering is safe by construction.
            raise translate_http_error(_PROVIDER, exc) from exc

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        """A plain (non-``async``) method, matching the port's own ``def
        stream`` signature exactly (02 §1.1): it RETURNS an async generator
        object, it does not await one. ``_build_chat_body`` -- and every
        ``ValidationError`` it can raise -- therefore runs HERE,
        synchronously, at call time, never deferred to the generator's
        first ``__anext__``: calling ``stream()`` with e.g. empty
        ``messages`` raises immediately, with zero network I/O, even
        without an ``async for`` ever touching the result."""
        body = _build_chat_body(messages, params, stream=True)
        return self._iter_chunks(body, tools_sent=bool(params.tools))

    async def _iter_chunks(self, body: Json, *, tools_sent: bool) -> AsyncIterator[LlmChunk]:
        """The async generator ``stream()`` hands back.

        ``async with`` guarantees the response is closed on every exit path
        -- a normal ``done:true`` return, an early ``break`` by the caller
        (``GeneratorExit`` thrown in at the ``yield``), or an exception --
        with no ``except Exception``/bare ``except`` anywhere here, so
        ``asyncio.CancelledError`` is never intercepted.

        The terminal chunk (``delta=""``, ``finish_reason`` set) IS
        yielded, never swallowed: ``finish_reason`` is the port's only
        end-of-stream signal (02 §1.1), so dropping that last chunk would
        erase it, and nothing is read past it. A stream that ends without
        ever emitting ``done:true`` -- a truncated connection, not a
        protocol-legal state -- raises rather than completing silently
        (refs §4's own warning: silently-truncated output is alpha's bug
        class in a new guise); a line that fails to parse raises for the
        exact same reason, rather than being skipped.
        """
        # RAW vendor entries, concatenated across lines and mapped ONCE at
        # the terminal chunk (never per-line): `_to_tool_calls` synthesises
        # ids from each entry's index, so mapping line-by-line would restart
        # that numbering on every line and emit duplicate ids within one
        # turn -- the exact correlation the ids exist to provide.
        raw_calls: list[object] = []
        try:
            async with self._client.stream("POST", _CHAT_PATH, json=body) as response:
                if response.status_code >= _HTTP_BAD_REQUEST:
                    # Status/headers are available as soon as they arrive,
                    # before any body byte is read -- no `aread()` needed to
                    # inspect them, and the body is deliberately never read
                    # at all (module docstring: no vendor-topology leak).
                    raise _translate_status(response.status_code, tools_sent=tools_sent)
                async for line in response.aiter_lines():
                    if not line:  # NDJSON keep-alive/framing blank lines
                        continue
                    payload = parse_json_object(_PROVIDER, line)
                    chunk = _to_chunk(payload)
                    # Tool calls may arrive on ANY line, not just the
                    # terminal one; the port requires them ASSEMBLED on the
                    # terminal chunk, so accumulate and attach there.
                    message = payload.get("message")
                    if isinstance(message, dict):
                        line_calls = message.get("tool_calls")
                        if isinstance(line_calls, list):
                            raw_calls.extend(line_calls)
                    if chunk.finish_reason is not None:
                        yield replace(chunk, tool_calls=_to_tool_calls({"tool_calls": raw_calls}))
                        return  # no read past the terminal line
                    yield chunk
                raise off_contract(_PROVIDER, "stream ended before completion")
        except httpx.HTTPError as exc:
            raise translate_http_error(_PROVIDER, exc) from exc

    def supports(self, capability: str) -> bool:
        """Never raises on an unknown capability -- returns ``False`` (the
        port docstring's own examples: ``'tools'``/``'vision'``/
        ``'streaming'``). Ollama's chat API supports both tool-calling
        (model-dependent -- ``gemma3:1b`` itself does not, confirmed live;
        this capability query is model-agnostic on the port, refs §8) and
        streaming; vision is out of scope for this adapter (refs §6/§8: no
        vision flag was ported -- authored fresh, deliberately
        conservative)."""
        return capability in _SUPPORTED_CAPABILITIES
