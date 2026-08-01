"""OpenAI adapter for the ``LLMProvider`` port (02-port-contracts §1.1,
D-15, DD-13). Phase 2.8-ب-1.

Same split as every adapter since 2.3: a factory builds the technology
client (``create_openai_http_client``, Composition Root / test harness
only), and a thin adapter class (``OpenAILLM``) implements the port over it
(structural Protocol match -- no inheritance). This module never imports
``LLMProvider`` itself, only the value types it exchanges (the
``qdrant_store.py``-imports-``VectorPoint``-never-``VectorStore``
precedent). Shares its technical primitives with ``ollama_llm.py`` via
``shared.py`` -- the extraction 2.8-a named explicitly and deferred to "the
second adapter" (this one).

Second LLM adapter, first CLOUD one (DD-13: five native adapters -- no
LangChain, no vendor SDK; ``pyproject.toml`` gains zero new dependency).
refs `llm-providers.md` §1 confirms ``kind=cloud``, ``requires_user_key=
True`` -- the first adapter that actually SENDS the ``api_key`` parameter
Ollama only ever accepted and silently ignored.

Settings: NONE. Unlike Ollama's ``OllamaSettings{base_url}``, this adapter
adds no field to ``Settings``, no env var, no ``.env.example`` line --
``_BASE_URL`` below is a literal module constant, the same shape as
``create_firebase_http_client`` (2.7). Justified on the evidence refs
`llm-providers.md` §1 records: alpha's own ``Provider.base_url_env`` is
noted there as dead code -- built, but never reachable (no provider ever
set it, and ``get_llm`` never passed a ``base_url``). A configurable
``OPENAI_BASE_URL`` would also be a key-exfiltration lever: whoever can set
that env var would redirect every prompt AND the tenant's API key to a
third party -- the exact threat this module's ``trust_env=False`` (applied
via ``shared.create_llm_http_client``) already closes through the proxy
door; an override env var would reopen it through another one. The key
itself is a secret (Vault -> ``CredentialResolver`` -> the ``api_key``
PARAMETER, DD-11), never Settings, never read from the environment by this
module. Consequence: ``from_env()`` gains no new boot requirement for this
adapter (contrast Firebase's boot-mandatory ``FIREBASE_PROJECT_ID``, 2.7).

The ``api_key`` parameter -- the first adapter that actually consumes it
(Ollama silently ignores it, refs §3 risk 3). Sent as a PER-REQUEST header
(``_auth_header``, applied in ``complete``/``stream`` at call time), NEVER
as a client-level default: the ``httpx.AsyncClient`` this adapter wraps is
process-wide, built once in the Composition Root, while the key is per-call
and per-tenant (``CredentialResolver``: user key first, then platform key).
A client-level default header would serve one tenant's key on every OTHER
tenant's request made through the same client -- the single biggest risk in
this module (corroborated by alpha's own client-cache key,
``f"{model}:...:{sha256(api_key)[:12]}"``, refs §2 -- alpha cached a
SEPARATE client per key for exactly this reason; this design sidesteps the
whole class of bug with one client and a per-request header instead). An
empty/whitespace key raises ``ValidationError``/422 before any network I/O,
in BOTH ``complete`` and ``stream``, at call time -- never ``agent.failed``
(that would blame the provider for our own wiring bug) and never a
credentials-catalog code (this adapter only knows it was not GIVEN a key;
only ``CredentialResolver`` may claim none exists at all). The key is never
interpolated into any exception message, log, or ``repr``
(10-code-standards §9/§10) -- a deliberate divergence from
``ollama_llm._guard_base_url``, which DOES echo its own bad input in its
error, because a URL is diagnostic and a key is a secret. This module
imports no logger at all (house precedent, every adapter since 2.3).
httpx's own defence helps too: ``Headers.__repr__`` renders
``authorization``/``proxy-authorization`` values as ``[secure]``
(``httpx/_models.py``) -- but ``dict(headers)``/``headers["authorization"]``
still leak the raw value in full, so this module's own discipline stays
load-bearing, never a mere backstop for httpx's.

Error policy -- every OpenAI failure becomes ``agent.failed``/502
(03-api-spec §4; 07-nfr-slo §4 pins this same code to the LLM-call timeout
budget), the SAME rule Ollama uses, sharpened by provider-specific details
never read from OpenAI's own response body (the body is NEVER read on an
error path; only the status code and OUR OWN outgoing request are
consulted -- OpenAI's 401 body is believed to echo a redacted prefix of the
submitted key, so reading it would put key material into our own error
detail, exactly what 10-code-standards §9 forbids):

* **401** is ``agent.failed``, never ``UnauthorizedError``/401: that code
  means the END USER's Firebase token is bad and would trigger a client
  re-login loop over what is actually a bad PROVIDER key.
* **429** is ``agent.failed``, never ``RateLimitedError``/
  ``common.rate_limited``: every 429 in 03-api-spec §4 is OUR OWN limit;
  mapping a provider's throttling there would tell a tenant they hit OUR
  limit (actionable: slow down) when it is really the platform's OpenAI
  account quota (not actionable by the tenant at all), and honouring the
  catalog's paired ``Retry-After`` would re-emit OpenAI's own rate-limit
  headers, leaking the platform's account tier to a tenant -- an argument
  3.79 only strengthened, since that header is now genuinely emitted for
  OUR limits, computed from OUR period boundaries. This module carried a
  "documentation-sync candidate" note proposing a ``provider.rate_limited``
  code; **3.79 resolved it the other way and the note is gone.** A catalog
  entry is a promise to emit, ``test_error_catalog.py`` enforces exactly
  that in both directions, and nothing may emit this one: the decision
  above is that a provider 429 must not reach the tenant as a rate limit at
  all. Adding the code would have shipped an unreachable row -- the very
  thing 6.2 removed ``files.not_ready``/``knowledge.not_indexed`` for. The
  cost is still recorded, in 03 §4 rather than here: collapsing 429 erases
  the one signal an orchestrator could safely retry on.
* **404** mirrors Ollama's 404 reasoning exactly: not ``NotFoundError``,
  because ``common.not_found`` means "absent within THIS TENANT's own
  data", which would misrepresent "the configured model does not exist on
  OpenAI's side" -- and D-16 means nothing branches on it anyway (no
  fallback between providers).
* **400 with a ``tool`` role message in the request** names the port-limit
  (d) failure at the point it actually happens (the ``tools_sent``
  precedent, generalized here as ``tool_role_sent`` -- derived from OUR OWN
  outgoing request, never from anything OpenAI's body said).

``content: null`` maps to ``""``, never raises -- a deliberate divergence
from ``ollama_llm._to_result``, which raises on any non-``str`` content.
OpenAI genuinely sends ``content: null`` both for a pure tool-calling
response and a content-filtered one; raising here would turn EVERY
tool-calling response into a 502 and would hide the content-filter signal
``finish_reason: "content_filter"`` already carries honestly.

A malformed individual ``tool_calls`` entry raises ``agent.failed``/502
rather than being silently dropped -- ANOTHER deliberate divergence, from
Ollama's lenient drop. ``LlmResult.tool_calls is None`` means "the model
requested no tools"; silently dropping a NAMED-but-undecodable call would
make that same result a confidently wrong, unflagged answer (refs
`llm-providers.md` §7 forbids exactly this pattern: "يجب أن يرفع/يُرجع خطأً
مطبوعاً لا محتوى مساعد ملفّقاً"). Named consequence: a ``max_tokens`` set too
low can truncate ``arguments`` mid-JSON, which now raises ``agent.failed``
instead of a clean ``finish_reason: "length"`` -- deliberate: a loud,
retryable 502 beats a silently tool-less answer with no error anywhere.

Streaming is SSE (net-new, zero behavioural reference -- refs
`llm-providers.md` §4 records that alpha never streamed ANYTHING, reaching
OpenAI exclusively through blocking LangChain calls). ``_sse_payloads``
below implements the parts of the SSE spec this adapter actually needs
(multi-line ``data:`` accumulation; ``:``-comment/``event:``/``id:``/
``retry:`` skipping) even though OpenAI is not believed to ever split a
payload across lines -- implementing 80% of a spec is exactly how a silent
truncation bug is born. ``finish_reason`` is the SOLE semantic
end-of-stream signal, mirroring Ollama's ``done``/``done_reason`` contract
exactly; the literal ``data: [DONE]`` sentinel is only SSE FRAMING, never
the semantic terminator -- a ``[DONE]`` that arrives before any chunk ever
carried a ``finish_reason`` still falls through to "stream ended before
completion", the same rule, one more adapter. ``stream_options.
include_usage`` is deliberately never requested: ``LlmChunk`` carries no
token fields at all (a port limit, below), so the extra usage frame OpenAI
would send is structurally undeliverable through this port, and this
adapter's own "never read past the terminal chunk" rule means it would
never even be reached if it were sent.

Port limits observed here (the port itself MUST NOT be modified, D-17):
(a, sharpened) a streamed call yields ZERO token data through this port,
for BOTH shipped adapters -- Ollama drops usage it actually receives on its
final NDJSON line, and OpenAI's ``stream_options.include_usage`` is not
even requested, because ``LlmChunk`` has no field to carry either
provider's usage in. (d, new) multi-turn tool use is not expressible:
``LlmResult.tool_calls`` drops OpenAI's ``id``, and ``LlmMessage`` (role +
content only) cannot replay the assistant turn that MADE the calls -- both
of which OpenAI requires to correlate a ``role: "tool"`` follow-up message.
Single-shot tool EMISSION still works (``supports("tools")`` stays
``True``, a routing hint, matching 2.8-a's own reasoning for ``supports``);
a full tool ROUND-TRIP does not. ``role="tool"`` is still accepted here and
mapped straight through rather than rejected, because the "OpenAI always
400s a bare tool message" claim has zero live proof -- hard-coding a
rejection on an unverified belief is exactly what this module must not do;
``tests/integration/test_openai_llm.py``'s L7 turns the claim into
evidence, and if confirmed live, the fix belongs on the PORT, not a hack in
this adapter.

⚠️ Honest limitation, stated plainly: the hermetic tests in this repository
prove this module's OWN logic (translation, SSE framing, token counting,
tool-call shaping) -- they cannot prove OpenAI's actual wire format. Every
wire-shape fixture in ``tests/unit/test_openai_mapping.py`` encodes this
codebase's BELIEF about that shape, not a confirmed observation: refs
`llm-providers.md` §2 shows alpha reached OpenAI exclusively through
LangChain's ``ChatOpenAI(...)``, never the raw wire, and §4 confirms
streaming did not exist in alpha at all. ``tests/integration/
test_openai_llm.py`` (marker ``live_openai``) is written to close this gap
the moment a real key exists (``TEST_OPENAI_API_KEY``, the
``TEST_MINIO_*``/``TEST_VAULT_TOKEN`` precedent) -- it could not be run as
of this adapter's authorship (verified: zero ``OPENAI*`` env var, and
Vault's KV store was empty).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace

import httpx

from app.framework.errors import AppError, ValidationError
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
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

_PROVIDER: str = "openai"

# A literal module constant, never configuration (module docstring, "Settings: NONE").
_BASE_URL: str = "https://api.openai.com/v1"
_CHAT_PATH: str = "/chat/completions"

# SSE framing sentinel -- NOT the semantic end of a stream; see _iter_chunks.
_DONE_PAYLOAD: str = "[DONE]"

# refs `llm-providers.md` §8: authored fresh, not ported (alpha has no
# per-model vision/streaming flag to harvest). `vision=False` is honesty,
# not conservatism: LlmMessage.content: str has no image channel, so a
# caller routed here for vision would find no way to send one THROUGH THIS
# PORT even though gpt-4o genuinely can see images. `tools=True` is
# well-evidenced: refs §1 records OpenAI's `is_tool_calling=True` as a
# CONSTANT, unlike Ollama's per-model flag.
_SUPPORTED_CAPABILITIES: frozenset[str] = frozenset({"streaming", "tools"})

# Named HTTP status constants (the `qdrant_store.py` `_HTTP_CONFLICT`
# precedent) -- also keeps ruff's magic-value-comparison rule quiet.
_HTTP_BAD_REQUEST: int = httpx.codes.BAD_REQUEST
_HTTP_UNAUTHORIZED: int = httpx.codes.UNAUTHORIZED
_HTTP_NOT_FOUND: int = httpx.codes.NOT_FOUND
_HTTP_TOO_MANY_REQUESTS: int = httpx.codes.TOO_MANY_REQUESTS


def create_openai_http_client(
    *,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the shared OpenAI HTTP client (Composition Root / test harness
    only). No ``settings`` parameter and no base-URL guard, unlike
    ``create_ollama_http_client``: ``_BASE_URL`` is a literal (module
    docstring), so there is nothing to validate at construction time.
    Delegates its actual client construction to
    ``shared.create_llm_http_client`` -- the ONE place ``trust_env=False``
    and the ``(timeout_s, connect=5.0)`` timeout pair are set for every LLM
    adapter."""
    return create_llm_http_client(base_url=_BASE_URL, timeout_s=timeout_s, transport=transport)


def _auth_header(api_key: str) -> dict[str, str]:
    """Build the PER-REQUEST ``Authorization`` header (module docstring's
    warning on client-level defaults) -- called at the top of both
    ``complete``/``stream``, AFTER ``_build_chat_body`` (the pinned order: a
    caller passing both an empty ``messages`` list AND an empty key
    deterministically gets the ``messages`` ``ValidationError``, never a
    race between the two guards -- see
    ``tests/unit/test_llm_provider_contract.py``). Never logs, never echoes
    the key in its own exception (10-code-standards §9/§10) -- contrast
    ``ollama_llm._guard_base_url``, which DOES echo its bad input, because a
    URL is diagnostic and a key is a secret."""
    key = api_key.strip()
    if not key:
        raise ValidationError("api_key must not be empty for the openai provider")
    return {"Authorization": f"Bearer {key}"}


def _build_chat_body(messages: Sequence[LlmMessage], params: LlmParams, *, stream: bool) -> Json:
    """Build OpenAI's ``/chat/completions`` wire body from the port's own
    ``LlmMessage``/``LlmParams`` value objects -- shared by ``complete``
    (``stream=False``) and ``stream`` (``stream=True``), the
    ``ollama_llm._build_chat_body`` precedent.

    Fails loudly, before any network call: empty ``messages``, a ``role``
    outside the port's own set (``shared.ROLES``), or an empty
    ``params.model`` all raise ``ValidationError`` -- a caller mistake,
    never folded into ``agent.failed``. ``role="tool"`` IS accepted (Ollama
    accepts all four too) -- see the module docstring's port-limit (d)
    paragraph for why this adapter does not hard-code a rejection on an
    unverified belief.

    Flat top-level fields (``max_tokens``/``top_p``/``stop``/
    ``temperature``), never Ollama's nested ``options``/``keep_alive``/
    ``think``/``num_ctx`` -- this is OpenAI's OWN wire shape (refs
    `llm-providers.md` §2), not a shared convention. ``max_tokens`` is
    omitted entirely when ``params.max_tokens`` is ``None`` -- never sent
    as a JSON ``null`` and never defaulted to an invented cap; ``stop``/
    ``tools`` are included only when truthy (omit rather than send an empty
    list, the ``ollama_llm``/``qdrant_store._build_filter`` precedent);
    ``top_p`` uses ``is not None`` rather than truthiness because, unlike an
    empty list, a scalar ``0.0`` is a real, meaningful value that must not
    be dropped. ``stream_options.include_usage`` is deliberately never
    sent -- see the module docstring's streaming paragraph.
    """
    if not messages:
        raise ValidationError("messages must not be empty")
    wire_messages: list[Json] = []
    for message in messages:
        if message.role not in ROLES:
            raise ValidationError(f"unsupported message role: {message.role!r}")
        wire: Json = {"role": message.role, "content": message.content}
        if message.tool_call_id:
            wire["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            wire["tool_calls"] = [_to_wire_tool_call(call) for call in message.tool_calls]
            # OpenAI requires `content: null` -- not `""` -- on an assistant
            # turn whose payload IS the tool calls.
            if not message.content:
                wire["content"] = None
        wire_messages.append(wire)
    if not params.model:
        raise ValidationError("params.model must not be empty")

    body: Json = {
        "model": params.model,
        "messages": wire_messages,
        "stream": stream,
        "temperature": params.temperature,
    }
    if stream:
        # 4.7-a, port limit (a): `LlmChunk` now HAS token fields, so the
        # usage frame this adapter previously declined to request is finally
        # deliverable. `_iter_chunks` is written to read past the
        # finish_reason chunk to collect it (see there).
        body["stream_options"] = {"include_usage": True}
    if params.max_tokens is not None:
        body["max_tokens"] = params.max_tokens
    if params.top_p is not None:
        body["top_p"] = params.top_p
    if params.stop:
        body["stop"] = list(params.stop)
    if params.tools:
        body["tools"] = list(params.tools)
    return body


def _has_tool_role(messages: Sequence[LlmMessage]) -> bool:
    """OUR OWN outgoing request fact (the ``tools_sent`` precedent,
    generalized): whether this call included a ``role="tool"`` message,
    consulted ONLY when a 400 arrives -- never anything read from OpenAI's
    own response body (module docstring's error-policy paragraph)."""
    return any(message.role == "tool" for message in messages)


async def _sse_payloads(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """Extract SSE ``data:`` payloads from an already-line-split async
    iterator (``response.aiter_lines()``) -- fully unit-testable against a
    fake line iterator, zero HTTP. 2.8-a's rule was "raise, never skip" for
    a malformed NDJSON line; SSE's analogue is "never skip a ``data:``
    line" -- skipping ``:``-comments/``event:``/``id:``/``retry:`` is
    skipping FRAMING, not data, so those are the only lines this function
    is allowed to discard silently.

    Multi-line ``data:`` accumulation is implemented even though OpenAI is
    not believed to ever split a payload this way: SSE is a spec, and
    implementing 80% of one is how a silent-truncation bug is born (refs
    `llm-providers.md` §7's own warning). The EOF flush (a dangling event
    with no terminating blank line) deliberately deviates from the spec,
    which says discard: a server closing without a trailing blank line
    would otherwise cost this adapter a complete, valid terminal event -- a
    spurious ``agent.failed`` -- and this leniency cannot cause silent
    truncation, only prevent a false failure.
    """
    data: list[str] = []
    async for line in lines:
        if not line:  # blank line -> dispatch the accumulated event
            if data:
                yield "\n".join(data)
                data = []
            continue
        if line.startswith(":"):  # comment / heartbeat
            continue
        name, _, value = line.partition(":")
        if name != "data":  # event: / id: / retry: / unknown field
            continue
        data.append(value[1:] if value.startswith(" ") else value)  # strip ONE leading space
    if data:  # EOF without a trailing blank line
        yield "\n".join(data)


def _to_result(payload: Json, messages: Sequence[LlmMessage]) -> LlmResult:
    """Map one parsed, non-streamed OpenAI body onto ``LlmResult``. Every
    field is read through an ``isinstance`` guard, never a bare
    ``payload["choices"][0]["message"]["content"]`` (R6, the
    ``ollama_llm._to_result`` precedent).

    ``content: null`` maps to ``""``, never raises -- see the module
    docstring's ``content: null`` paragraph (a deliberate divergence from
    ``ollama_llm._to_result``, which raises on any non-``str`` content):
    OpenAI genuinely sends a ``null`` content both for a pure tool-calling
    response and a content-filtered one, and ``finish_reason`` already
    carries that distinction honestly. Anything ELSE non-``str`` (a number,
    a list, ...) IS off-contract.
    """
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise off_contract(_PROVIDER, "response is missing 'choices'")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise off_contract(_PROVIDER, "response 'choices[0]' is not an object")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise off_contract(_PROVIDER, "response is missing a 'message' object")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise off_contract(_PROVIDER, "response 'message.content' is not a string")
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str):
        raise off_contract(_PROVIDER, "response is missing 'finish_reason'")

    usage = payload.get("usage")
    usage_obj: Json = usage if isinstance(usage, dict) else {}
    result_content = content if isinstance(content, str) else ""
    prompt_text = "\n".join(m.content for m in messages)
    return LlmResult(
        content=result_content,
        finish_reason=finish_reason,
        prompt_tokens=token_count(usage_obj, "prompt_tokens", prompt_text),
        completion_tokens=token_count(usage_obj, "completion_tokens", result_content),
        tool_calls=_to_tool_calls(message),
    )


def _to_chunk(payload: Json) -> LlmChunk:
    """Map one parsed SSE ``data:`` payload onto ``LlmChunk``. Deliberately
    lenient (the ``ollama_llm._to_chunk`` precedent): a missing/malformed
    ``choices``/``delta``/``content`` degrades to ``delta=""`` rather than
    raising -- ``parse_json_object`` has already rejected the shapes that
    must be fatal (bad JSON, a non-object body, an ``error`` key), so
    anything left here is a partial delta, not the final answer, and a soft
    default costs nothing. ``finish_reason`` is the ONLY end-of-stream
    signal in the port's contract -- populated if, and only if, this
    chunk's own ``choices[0].finish_reason`` is a non-``null`` string."""
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    delta = choice.get("delta") if isinstance(choice, dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    return LlmChunk(
        delta=content if isinstance(content, str) else "",
        finish_reason=finish_reason if isinstance(finish_reason, str) else None,
    )


def _to_tool_calls(message: Json) -> list[Json] | None:
    """Map OpenAI's ``message.tool_calls`` shape (``[{"id": ...,
    "function": {"name": ..., "arguments": "<JSON string>"}}]``) onto the
    port-neutral shape ``shared.neutral_tool_call`` owns:
    ``[{"id": str, "name": str, "arguments": Json}]``. The call ``id`` OpenAI
    emits is now CARRIED rather than dropped (4.7-a resolved port limit (d)
    by giving ``LlmMessage`` a ``tool_call_id`` to round-trip it through), so
    a missing/empty ``id`` fails the same way a missing ``name`` does -- via
    ``neutral_tool_call`` returning ``None`` and the raise below.

    Also reused by the STREAMING path: ``_assembled_tool_calls`` re-shapes
    accumulated fragments into this same vendor envelope and hands them here,
    so streamed and non-streamed tool calls pass through one decode + guard
    path rather than two that can drift.

    Diverges from ``ollama_llm._to_tool_calls``'s lenient drop (module
    docstring's ``tool_calls`` paragraph): a malformed individual entry
    RAISES ``agent.failed``/502 here rather than being silently skipped,
    because ``tool_calls=None``/``[]`` means "the model requested no
    tools" -- silently dropping a NAMED-but-undecodable call would make
    that same result a confidently wrong, unflagged answer."""
    raw = message.get("tool_calls")
    if not isinstance(raw, list) or not raw:
        return None
    calls: list[Json] = []
    for entry in raw:
        function = entry.get("function") if isinstance(entry, dict) else None
        if not isinstance(function, dict):
            raise off_contract(_PROVIDER, "returned a tool call with undecodable arguments")
        arguments = _parse_tool_arguments(function.get("arguments"))
        call = neutral_tool_call(
            entry.get("id") if isinstance(entry, dict) else None,
            function.get("name"),
            arguments,
        )
        if call is None:
            raise off_contract(_PROVIDER, "returned a tool call with undecodable arguments")
        calls.append(call)
    return calls or None


def _to_wire_tool_call(call: Json) -> Json:
    """Map ONE neutral tool call (``{"id", "name", "arguments"}``) back onto
    OpenAI's outgoing wire shape -- the inverse of ``_to_tool_calls``, needed
    to replay an assistant turn that issued calls (4.7-a, port limit (d)).

    Two vendor asymmetries this re-encodes, both of which make a naive
    round-trip silently wrong: ``arguments`` must go back out as a JSON
    *string* (it arrives as one, is decoded to an object for the neutral
    shape, and must be re-encoded here), and the entry needs its
    ``type: "function"`` discriminator, which the neutral shape has no slot
    for because it is pure OpenAI framing.

    Raises ``ValidationError``, not ``agent.failed`` (the
    ``ollama_llm._to_wire_tool_call`` precedent): everything here came from
    OUR OWN caller re-sending a previous turn's output, so a malformed entry
    is a caller mistake -- never a provider fault, and never a 502 blaming
    OpenAI for it.
    """
    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id:
        raise ValidationError("tool_calls entries must carry a non-empty 'id'")
    name = call.get("name")
    if not isinstance(name, str) or not name:
        raise ValidationError("tool_calls entries must carry a non-empty 'name'")
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        raise ValidationError("tool_calls entries must carry an 'arguments' object")
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _merge_usage(
    payload: Json, prompt: int | None, completion: int | None
) -> tuple[int | None, int | None]:
    """Fold a frame's ``usage`` object into the counters collected so far
    (4.7-a, port limit (a)).

    A frame without a usable ``usage`` object leaves both counters
    untouched -- so an already-reported count is never overwritten by a
    later frame's absence, and an absent count never becomes a zero.
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return prompt, completion
    reported_prompt = reported_token_count(usage, "prompt_tokens")
    reported_completion = reported_token_count(usage, "completion_tokens")
    return (
        reported_prompt if reported_prompt is not None else prompt,
        reported_completion if reported_completion is not None else completion,
    )


@dataclass(slots=True)
class _CallFragment:
    """One in-flight streamed tool call, keyed by its OpenAI ``index``.

    Mutable and private by design -- the one piece of per-stream state this
    otherwise-stateless adapter keeps, and it lives strictly inside a single
    ``_iter_chunks`` invocation (never on the adapter instance, which is
    shared across every request and tenant).
    """

    call_id: str | None = None
    name: str | None = None
    arguments: list[str] = field(default_factory=list)


def _accumulate_tool_call_fragments(delta: Json, buffer: dict[int, _CallFragment]) -> None:
    """Fold one streamed ``delta.tool_calls`` frame into ``buffer`` (4.7-a,
    port limit (b)).

    OpenAI does not stream a tool call whole: it sends the ``id``/``name``
    once, then the ``arguments`` as a run of partial JSON-string pieces
    across later frames, correlated ONLY by the fragment's ``index``. So the
    unit of accumulation is that index, and ``arguments`` is joined as raw
    TEXT -- never parsed per-fragment, because a fragment like ``{"a`` is not
    valid JSON and never will be until its successors arrive.

    Deliberately lenient, matching ``_to_chunk``'s stance on the streaming
    path: a fragment with no usable ``index`` is skipped rather than fatal,
    since ``parse_json_object`` has already rejected the shapes that must be
    fatal, and what an unusable ACCUMULATED call means is decided once, at
    the end, by ``_to_tool_calls``'s strict guards. ``isinstance(index,
    bool)`` is excluded explicitly: ``bool`` is an ``int`` subclass in
    Python, so ``True`` would otherwise silently become index ``1``.
    """
    raw = delta.get("tool_calls")
    if not isinstance(raw, list):
        return
    for fragment in raw:
        if not isinstance(fragment, dict):
            continue
        index = fragment.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        slot = buffer.setdefault(index, _CallFragment())
        fragment_id = fragment.get("id")
        if isinstance(fragment_id, str) and fragment_id:
            slot.call_id = fragment_id
        function = fragment.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            slot.name = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            slot.arguments.append(arguments)


def _assembled_tool_calls(buffer: dict[int, _CallFragment]) -> list[Json] | None:
    """Re-shape accumulated fragments into OpenAI's NON-streamed envelope and
    push them through ``_to_tool_calls`` -- so a streamed tool call gets the
    identical decode, id/name guards and ``agent.failed`` policy as a
    non-streamed one, rather than a parallel implementation free to drift.

    Ordered by ``index`` so the assembled list matches the model's own call
    order regardless of frame arrival order.
    """
    if not buffer:
        return None
    entries: list[Json] = [
        {
            "id": slot.call_id,
            "function": {"name": slot.name, "arguments": "".join(slot.arguments)},
        }
        for _, slot in sorted(buffer.items())
    ]
    return _to_tool_calls({"tool_calls": entries})


def _parse_tool_arguments(raw: object) -> Json:
    """OpenAI sends ``function.arguments`` as a JSON-encoded STRING, not an
    object -- the one place this adapter parses JSON found INSIDE an
    already-parsed JSON body. An absent/empty string means "a no-argument
    call" and maps to ``{}``; anything that fails to decode as a JSON
    OBJECT raises ``agent.failed``/502 (module docstring's ``tool_calls``
    paragraph) rather than being dropped or defaulted -- a
    ``max_tokens``-truncated argument string is exactly this case, and a
    loud 502 beats a silently wrong empty-args call."""
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, str):
        raise off_contract(_PROVIDER, "returned a tool call with undecodable arguments")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise off_contract(_PROVIDER, "returned a tool call with undecodable arguments") from exc
    if not isinstance(parsed, dict):
        raise off_contract(_PROVIDER, "returned a tool call with undecodable arguments")
    return parsed


def _translate_status(status: int, *, tool_role_sent: bool) -> AppError:
    """Map an OpenAI HTTP error STATUS (never its body -- module docstring's
    error-policy paragraph) onto ``agent.failed``/502. A pure function of
    ``status``/``tool_role_sent`` alone -- no I/O, nothing read from the
    response."""
    if status == _HTTP_UNAUTHORIZED:
        return off_contract(_PROVIDER, "rejected the api key")
    if status == _HTTP_TOO_MANY_REQUESTS:
        return off_contract(_PROVIDER, "call failed: rate limited")
    if status == _HTTP_NOT_FOUND:
        return off_contract(_PROVIDER, "model not available")
    if status == _HTTP_BAD_REQUEST and tool_role_sent:
        return off_contract(
            _PROVIDER, "call failed: a 'tool' role message is not expressible through this port"
        )
    return off_contract(_PROVIDER, "call failed")


def _translate(exc: Exception) -> AppError:
    """Map any transport-level ``httpx`` failure onto ``agent.failed``/502
    -- delegates to ``shared.translate_http_error``, the one place every
    LLM adapter's timeout-vs-generic distinction lives."""
    return translate_http_error(_PROVIDER, exc)


class OpenAILLM:
    """OpenAI-backed ``LLMProvider`` (02 §1.1, structural Protocol match)."""

    # A plain class attribute, NOT typing.ClassVar (the
    # ollama_llm.OllamaLLM precedent: mypy rejects ClassVar against the
    # Protocol's own INSTANCE-attribute annotation, LLMProvider.provider: str).
    provider: str = "openai"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        """Order is pinned (module docstring's ``api_key`` paragraph):
        ``_build_chat_body`` runs BEFORE ``_auth_header``, so both raise
        ``ValidationError`` before any network I/O, deterministically,
        regardless of which input was invalid."""
        body = _build_chat_body(messages, params, stream=False)
        headers = _auth_header(api_key)
        try:
            response = await self._client.post(_CHAT_PATH, json=body, headers=headers)
            if response.status_code >= _HTTP_BAD_REQUEST:
                raise _translate_status(
                    response.status_code, tool_role_sent=_has_tool_role(messages)
                )
            # Unwrapped INSIDE the try (2.5/2.6/2.8-a precedent): translated
            # by _to_result/parse_json_object's own guards, never a raw
            # exception.
            return _to_result(parse_json_object(_PROVIDER, response.text), messages)
        except httpx.HTTPError as exc:
            # AppError (raised just above, from _translate_status/
            # parse_json_object/_to_result) is not an httpx.HTTPError, so
            # it is never re-caught here -- this ordering is safe by
            # construction (the ollama_llm.complete precedent).
            raise _translate(exc) from exc

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        """A plain (non-``async``) method, matching the port's own ``def
        stream`` signature exactly (02 §1.1, the ``ollama_llm.OllamaLLM.
        stream`` precedent): it RETURNS an async generator object rather
        than awaiting one, so ``_build_chat_body``/``_auth_header`` -- and
        every ``ValidationError`` either can raise -- run HERE,
        synchronously, at call time, never deferred to the generator's
        first ``__anext__``."""
        body = _build_chat_body(messages, params, stream=True)
        headers = _auth_header(api_key)
        return self._iter_chunks(body, headers, tool_role_sent=_has_tool_role(messages))

    async def _iter_chunks(
        self, body: Json, headers: dict[str, str], *, tool_role_sent: bool
    ) -> AsyncIterator[LlmChunk]:
        """The async generator ``stream()`` hands back.

        ``async with`` guarantees the response is closed on every exit path
        (the ``ollama_llm._iter_chunks`` precedent) -- with no
        ``except Exception``/bare ``except`` anywhere here, so
        ``asyncio.CancelledError`` is never intercepted.

        Never touches ``response.text``/``.json()`` anywhere in this
        method: ``ResponseNotRead`` is an httpx ``RuntimeError``, NOT an
        ``httpx.HTTPError``, so it would escape the ``except`` below
        completely untranslated -- the "never read the error body" policy
        (module docstring) makes this trap structurally unreachable rather
        than merely avoided.

        ``finish_reason`` is the semantic terminator; the literal
        ``data: [DONE]`` frame is only SSE FRAMING (module docstring). A
        stream that ends without ever yielding a chunk carrying a
        ``finish_reason`` -- whether at EOF or right after ``[DONE]`` --
        raises rather than completing silently, the same rule as Ollama's
        missing ``done: true``.
        """
        terminal: LlmChunk | None = None
        fragments: dict[int, _CallFragment] = {}
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        try:
            async with self._client.stream(
                "POST", _CHAT_PATH, json=body, headers=headers
            ) as response:
                if response.status_code >= _HTTP_BAD_REQUEST:
                    # Status/headers are available before any body byte is
                    # read; the body is deliberately never read at all.
                    raise _translate_status(response.status_code, tool_role_sent=tool_role_sent)
                async for payload in _sse_payloads(response.aiter_lines()):
                    if payload == _DONE_PAYLOAD:
                        continue  # framing end -> falls through to the raise below

                    if terminal is not None:
                        # PAST the terminal chunk. The answer is already
                        # complete, so the "raise, never skip" rule that
                        # governs the loop above does not apply here: its
                        # purpose is preventing SILENT TRUNCATION of the
                        # answer, and nothing arriving now can truncate
                        # anything. The only thing still worth reading is the
                        # `include_usage` frame, and failing to read it is an
                        # already-contracted outcome (`None` => estimate).
                        # So a malformed trailing frame is IGNORED rather
                        # than turned into a spurious `agent.failed` on an
                        # answer we successfully received in full.
                        try:
                            trailing = parse_json_object(_PROVIDER, payload)
                        except AppError:
                            continue
                        prompt_tokens, completion_tokens = _merge_usage(
                            trailing, prompt_tokens, completion_tokens
                        )
                        continue

                    parsed = parse_json_object(_PROVIDER, payload)
                    prompt_tokens, completion_tokens = _merge_usage(
                        parsed, prompt_tokens, completion_tokens
                    )

                    choices = parsed.get("choices")
                    choice = choices[0] if isinstance(choices, list) and choices else None
                    delta = choice.get("delta") if isinstance(choice, dict) else None
                    if isinstance(delta, dict):
                        _accumulate_tool_call_fragments(delta, fragments)

                    chunk = _to_chunk(parsed)
                    if chunk.finish_reason is not None:
                        # HOLD it, do not yield yet: with `include_usage` the
                        # usage frame arrives AFTER this chunk, and the port
                        # requires counters ON the terminal chunk. Returning
                        # here (as this loop did before 4.7-a) is exactly
                        # what made a streamed turn unbillable.
                        terminal = chunk
                        continue
                    yield chunk
                if terminal is None:
                    raise off_contract(_PROVIDER, "stream ended before completion")
                yield replace(
                    terminal,
                    tool_calls=_assembled_tool_calls(fragments),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
        except httpx.HTTPError as exc:
            raise _translate(exc) from exc

    def supports(self, capability: str) -> bool:
        """Never raises on an unknown capability -- returns ``False`` (the
        port docstring's own examples). See the module docstring for why
        ``tools=True``/``vision=False``."""
        return capability in _SUPPORTED_CAPABILITIES
