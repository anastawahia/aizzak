"""Primitives shared by every ``LLMProvider`` adapter (02-port-contracts
§1.1, D-15, DD-13), extracted once a SECOND shipped adapter
(``openai_llm.py``, Phase 2.8-ب-1) demonstrably duplicated what
``ollama_llm.py`` (2.8-a) already had. 2.8-a named this exact list and
deferred it explicitly to "the second adapter"
(docs/implementation-status.md §3.23) rather than speculate at N=1 --
09-testing-strategy §5's "منافذ ↔ محوّلات: طقم اختبار مشترك (parametrized)"
mandate is itself only satisfiable once two adapters exist to compare (see
``tests/unit/test_llm_provider_contract.py``, unlocked by this module).

Not a base class: the house rule is verbatim in every adapter's own
docstring since 2.3 -- "structural Protocol match -- no inheritance".
Centralizing these primitives as plain functions keeps every adapter a
free-standing Protocol match while still killing real, repeated defects:
three of the last four LLM/auth-adjacent adapters (2.5, 2.6, 2.7) shipped a
genuine R6 hole before their verifier caught it, so consolidating R6's own
primitives here attacks the single most-repeated defect class in this
codebase, not a hypothetical one.

Scope discipline (read before adding a ninth item): this module holds
EXACTLY what two shipped adapters demonstrably duplicated -- nothing
speculative for Gemini/Claude/OpenRouter, none of which exist yet. In
particular, NOT here, and why:

* SSE parsing -- OpenAI is the first (and so far only) SSE adapter; Ollama
  is NDJSON. There is no duplication yet to extract, and Claude's SSE is
  believed to use ``event:`` semantically, which an OpenAI-shaped decoder
  may not fit.
* Chat-body construction / response mapping (``_build_chat_body``,
  ``_to_result``, ``_to_chunk``) -- Ollama and OpenAI resemble each other
  only because Ollama's wire format copied OpenAI's; Gemini
  (``contents``/``parts``/``generationConfig``) and Claude (top-level
  ``system`` + content blocks) are structurally different. Extracting this
  now would be the textbook "fits two, breaks the rest" trap.
* Status-code translation (``_translate_status``) -- the two adapters' maps
  share nothing but the fallthrough, which IS extracted here, as
  ``off_contract``. Ollama's vocabulary is 404/400; OpenAI's is
  401/429/404/400 -- disjoint enough that a shared map would invent
  structure neither adapter has.
* A ``finish_reason`` map -- deliberately does not exist. Both shipped
  adapters pass the provider's own value through literally; inventing a map
  would invent a semantic no provider ever sent.
* The per-adapter factory SIGNATURE -- Ollama's public factory takes an
  ``OllamaSettings`` (a genuine per-deployment ``base_url``); OpenAI's takes
  none at all (its URL is a module constant, see ``openai_llm.py``'s module
  docstring). Only the client-construction BODY is shared, as
  ``create_llm_http_client``; each adapter keeps its own public factory and
  signature.
* Named HTTP status constants (``_HTTP_NOT_FOUND`` and friends) -- these are
  ``httpx.codes`` aliases for ruff's magic-value-comparison rule (the
  ``qdrant_store.py`` ``_HTTP_CONFLICT`` precedent), not shared policy: they
  stay defined per-adapter next to the status vocabulary that actually uses
  them.

**One non-LLM consumer, and why it is not a scope breach** (step 19 of
``deferred-adapters-plan.md``): ``ai_providers/image/external_image.py``
imports ``create_llm_http_client``, ``off_contract``, ``parse_json_object``
and ``translate_http_error``. Those four are not ``LLMProvider`` policy at
all -- they are TRANSPORT and ERROR-TRANSLATION policy for "an HTTP call to a
third-party AI vendor", which an image adapter makes in exactly the same
shape (``trust_env=False`` is the same key-exfiltration defence; a vendor
failure is the same ``agent.failed``/502; a malformed 200 is the same R6
hole). The scope rule above is unchanged and still binding: it governs what
may be ADDED here, and nothing was. What did NOT travel is just as
deliberate -- ``ROLES``, ``estimate_tokens``/``token_count``/
``reported_token_count`` and ``neutral_tool_call`` are chat vocabulary an
image adapter has no use for, and ``_auth_header`` stayed COPIED in each
adapter rather than hoisted, because one image adapter is N=1 and this
module exists precisely because N=2 is the threshold.

This module imports ONLY ``httpx``, ``json``, ``app.framework.errors`` and
``app.framework.types`` -- it does not import ``llm_provider`` at all, one
notch more decoupled than either adapter (which import the port's VALUE
types only -- never the ``LLMProvider`` Protocol itself -- the
``qdrant_store.py``-imports-``VectorPoint``-never-``VectorStore``
precedent). Nothing here performs I/O beyond building an unconnected
``httpx.AsyncClient`` (construction-time only, no socket opened, the
``firebase_auth``/``ollama_llm`` precedent).
"""

from __future__ import annotations

import json

import httpx

from app.framework.errors import AppError
from app.framework.types import Json

# Fail fast on a dead/slow provider instead of hanging a request-handling
# coroutine (the 2.3-2.8-a precedent) -- distinct from the much longer
# overall call timeout each call site passes in as ``timeout_s``
# (``Limits.llm_timeout_s``), which must cover Ollama's own confirmed-live
# ~43s cold model load.
_CONNECT_TIMEOUT_S: float = 5.0

# The token-estimate fallback's per-character rate (refs
# `llm-providers.md` §3's counting triple, third rung): reused unchanged by
# every adapter that has no reported counter to trust.
_CHARS_PER_TOKEN: int = 4

# The PORT's own role vocabulary (02 §1.1's comment on ``LlmMessage.role``),
# not any one vendor's -- every adapter validates its incoming messages
# against this SAME set, because it is the port's contract they all share.
ROLES: frozenset[str] = frozenset({"system", "user", "assistant", "tool"})


def create_llm_http_client(
    *,
    base_url: str,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build one LLM adapter's ``httpx.AsyncClient`` -- the technical
    plumbing every adapter's OWN public factory (``create_ollama_http_client``,
    ``create_openai_http_client``, ...) delegates its body to, while keeping
    its own signature: some adapters take a ``Settings`` object for a
    genuinely per-deployment ``base_url`` (Ollama); others, like OpenAI,
    take none at all because their URL is a fixed module constant (see
    ``openai_llm.py``'s module docstring).

    ``trust_env=False`` is the security invariant this function exists to
    centralize (closing a verified test-coverage regression: 2.7 tested
    ``trust_env``, 2.8-a did not -- see ``test_llm_shared.py``): httpx's
    default (``trust_env=True``) reads ``$HTTP_PROXY``/``$HTTPS_PROXY``/
    ``$ALL_PROXY`` via ``urllib.request.getproxies()`` whenever no explicit
    ``transport`` is given, letting whoever can set an env var on this
    process redirect every prompt -- and, starting with OpenAI, every
    tenant's API key too -- to a third party. WSL environments frequently
    carry an ambient ``HTTP_PROXY``, making this more likely to fire here
    than in a container.

    ``transport`` defaults to ``None`` (httpx's normal HTTP transport); the
    unit suites pass an ``httpx.MockTransport`` through this SAME parameter
    (the ``create_firebase_http_client`` precedent, 2.7), so this factory's
    real ``timeout``/``trust_env``/``base_url`` choices are what actually
    get exercised, never a stand-in.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_s, connect=_CONNECT_TIMEOUT_S),
        trust_env=False,
        transport=transport,
    )


def off_contract(provider: str, detail: str) -> AppError:
    """The ONE place any LLM adapter constructs
    ``AppError(code="agent.failed")`` (03-api-spec §4, "فشل تنفيذ
    الوكيل/المزوّد"؛ 07-nfr-slo §4 pins this same code to the LLM-call
    timeout budget). ``status=502`` is passed explicitly: ``errors.py`` has
    no dedicated subclass carrying that default, unlike
    ``NotFoundError``/``ValidationError``, so every adapter sets it here the
    same way ``firebase_auth`` sets its own statuses explicitly. ``detail``
    is always prefixed with the CALLING adapter's own ``provider`` string,
    so two adapters' errors are never ambiguous about their source even
    after both funnel through this one function."""
    return AppError(f"{provider} {detail}", code="agent.failed", status=502)


def translate_http_error(provider: str, exc: Exception) -> AppError:
    """Map any transport-level ``httpx`` failure onto ``agent.failed``/502.
    A ``TimeoutException`` gets a distinguishable detail; every other
    ``httpx.HTTPError`` collapses to one generic message -- there is no
    caller-branchable case worth preserving here (D-16: nothing falls back
    between providers on an LLM failure, so no finer-grained code would
    ever be acted on)."""
    if isinstance(exc, httpx.TimeoutException):
        return off_contract(provider, "call timed out")
    return off_contract(provider, "call failed")


def parse_json_object(provider: str, raw: str) -> Json:
    """Parse one provider response body, R6-safely: never lets a malformed
    200 escape as a raw ``json.JSONDecodeError``/``AttributeError`` (the
    2.5/2.6 precedent). A body that is not valid JSON, not a JSON *object*,
    or itself carries the provider's own ``{"error": ...}`` shape (a
    mid-stream or off-contract failure signal some providers deliver on an
    otherwise-200 response) all become ``agent.failed``/502 -- never a bare
    exception, and never silently skipped (skipping a bad chunk would be a
    silently-truncated answer, alpha's own bug class in a new guise, refs
    `llm-providers.md` §4/§7)."""
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise off_contract(provider, "returned a non-JSON response body") from exc
    if not isinstance(payload, dict):
        raise off_contract(provider, "returned a non-object response body")
    if "error" in payload:
        raise off_contract(provider, "response body carried an error")
    return payload


def estimate_tokens(text: str) -> int:
    """The per-character fallback estimate (refs `llm-providers.md` §3's
    counting triple, third rung): always at least 1, so an empty completion
    never reports zero usage."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def token_count(payload: Json, key: str, fallback_text: str) -> int:
    """Read a reported token counter if it is a genuinely positive ``int``,
    else fall back to ``estimate_tokens``. ``<= 0`` is treated exactly like
    "absent": Ollama is confirmed to zero ``prompt_eval_count`` on a
    prefix-cache hit, and taking a reported ``0`` at face value would
    silently under-report usage/billing for any provider that ever does the
    same."""
    value = payload.get(key)
    if isinstance(value, int) and value > 0:
        return value
    return estimate_tokens(fallback_text)


def reported_token_count(payload: Json, key: str) -> int | None:
    """Read a token counter the provider ACTUALLY reported, or ``None`` --
    never an estimate. The streaming counterpart to ``token_count``
    (added 4.7-a for ``LlmChunk``'s new counter fields, port limit (a)).

    The ninth item in this module, and it earns its place by the same rule
    as the other eight (module docstring's scope discipline): both shipped
    adapters demonstrably need this exact ">0 else unknown" read on the
    streaming path -- Ollama's terminal NDJSON line
    (``prompt_eval_count``/``eval_count``) and OpenAI's ``include_usage``
    frame (``usage.prompt_tokens``/``completion_tokens``) differ only in
    where the object lives, which is the caller's extraction job, not this
    function's.

    Deliberately does NOT fall back to ``estimate_tokens`` the way
    ``token_count`` does: ``LlmChunk``'s contract is that ``None`` means
    "the provider reported nothing, caller must estimate". Folding an
    estimate in here would make an invented number indistinguishable from a
    measured one on the exact path (``UsageCapture``, FR-134) where that
    distinction is the whole point. ``<= 0`` is treated as absent for the
    same reason ``token_count`` does so: Ollama is confirmed to zero
    ``prompt_eval_count`` on a prefix-cache hit.
    """
    value = payload.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def neutral_tool_call(call_id: object, name: object, arguments: Json) -> Json | None:
    """The ONE shape every ``LlmResult``/``LlmChunk`` ``tool_calls`` entry
    takes: ``{"id": str, "name": str, "arguments": Json}``. Owns the SHAPE,
    not the extraction: callers pull ``id``/``name``/``arguments`` out of
    their own vendor envelope FIRST (Ollama's
    ``message.tool_calls[].function``; OpenAI's identically-named field, but
    with a JSON-*string* ``arguments`` that the caller must decode before
    ever reaching this function) and hand this function only the
    already-extracted pieces -- which is why this same function is expected
    to survive Gemini's ``name``/``args`` and Claude's ``name``/``input``
    too, unchanged.

    ``id`` (added 4.7-a, resolving port limit (d)) is what lets a caller
    correlate a ``role="tool"`` result back to the call it answers via
    ``LlmMessage.tool_call_id``. Vendors differ on whether they supply one:
    OpenAI does, Ollama emits none at all, so ``ollama_llm`` SYNTHESISES a
    positional id before calling this function. That synthesis belongs to
    the adapter, not here: this function cannot know a call's index within
    its turn, and inventing an id from nothing would silently paper over a
    vendor envelope the caller failed to read.

    Returns ``None`` for a non-``str``/empty ``id`` OR ``name`` -- the guards
    every vendor shape needs regardless of its own envelope, so every caller
    gets them for free. This function never raises: what an unusable tool
    call MEANS is each caller's own policy to enforce (Ollama drops a
    malformed entry, matching its documented "malformed individual entries
    are dropped, not fatal" mapper contract; OpenAI raises instead, because
    a tool call it cannot decode is not safely droppable -- see
    ``openai_llm.py``'s module docstring) -- this function only ever
    returns the shaped dict or ``None``.
    """
    if not isinstance(call_id, str) or not call_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    return {"id": call_id, "name": name, "arguments": arguments}
