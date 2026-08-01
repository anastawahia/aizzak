"""Exa adapter for the ``WebSearchProvider`` port (Phase 4.4; ``refs/tools.md``
§1/§3). One concrete web-search provider; the port stays vendor-neutral.

Same split as every adapter since 2.3: a factory builds the technology client
(``create_exa_http_client`` — Composition Root / test harness only) and a thin
class (``ExaWebSearchAdapter``) implements the port over it (structural
Protocol match, no inheritance). It imports the port's VALUE type
(``WebSearchHit``) only, never the ``WebSearchProvider`` Protocol itself (the
``qdrant_store``-imports-``VectorPoint``-never-``VectorStore`` precedent);
structural conformance is proven in the test, at the wiring seam.

**The key is the adapter's, injected once.** Unlike an LLM key (per-tenant,
per-call, from ``CredentialResolver``), the Exa key is a single PLATFORM
secret read from ``SecretsProvider`` in the Composition Root and passed to
this constructor (``refs/tools.md`` §1/§4.1). An empty/whitespace key is a
wiring bug and raises ``ValidationError`` at construction — never a silent
misconfiguration that only surfaces on the first request. The key is sent as
a PER-REQUEST ``x-api-key`` header and never interpolated into any log,
exception, or ``repr`` (10-code-standards §9; the ``openai_llm`` precedent);
``trust_env=False`` on the client closes the proxy-env exfiltration door the
same way. When no key is configured the Composition Root simply does not wire
web search at all (``deps.web_search`` stays ``None`` and the tool degrades) —
so this adapter is only ever built WITH a key.

**Best-effort, never fatal (a deliberate divergence from the LLM adapters).**
Web search is an AUXILIARY capability: a transport failure, timeout, or
non-2xx from Exa must NOT ``agent.failed``/502 the whole turn (as an LLM
failure does), so ``search`` returns an EMPTY tuple on any ``httpx`` failure
or error status — the graceful "no results" alpha itself returned
(``refs/tools.md`` §1 step 2). Only ``httpx.HTTPError`` and error statuses are
absorbed; a programming bug (a non-HTTP exception) still propagates. The
response body is parsed R6-safely: every field is read through an
``isinstance`` guard (the ``openai_llm._to_result`` precedent), so a
malformed Exa 200 degrades to fewer/zero hits, never a raw
``AttributeError``/``TypeError``.

**Cache: fail-open (``refs/tools.md`` §1/§4.6).** Results are cached by
``search:exa:<sha256(query)>`` for public web queries (tenant-neutral — the
port carries no ``ctx``). Any ``CacheProvider`` error on GET is a miss (fall
through to Exa); any error on SET is ignored — the cache is an optimization,
never a correctness dependency. ``cache_ttl_s=0`` disables caching entirely.

**Hermetic limitation, stated plainly (the ``openai_llm`` stance):** the unit
tests drive this module through an ``httpx.MockTransport`` and prove its OWN
logic (request shaping, R6-safe response mapping, dedup, snippet fallback,
cache fail-open) — they cannot prove Exa's real wire format. The request/
response shapes encode this codebase's belief (``refs/tools.md`` §1, which
notes §4.4 that the exact REST contract lives inside ``exa_py`` and should be
confirmed against Exa's docs). ``tests/integration/test_exa_web_search.py``
(marker ``live_web_search``) closes that gap the moment ``TEST_EXA_API_KEY``
exists.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import httpx

from app.framework.errors import ValidationError
from app.framework.ports.cache_provider import CacheProvider
from app.framework.ports.web_search_provider import WebSearchHit
from app.framework.types import Json

# A literal module constant, never configuration (the ``openai_llm`` _BASE_URL
# precedent: a configurable base URL would be a key-exfiltration lever).
_BASE_URL: str = "https://api.exa.ai"
_SEARCH_PATH: str = "/search"

# Fail fast on a dead/slow Exa instead of hanging the request coroutine (the
# shared LLM-client precedent), distinct from the overall ``timeout_s`` budget.
_CONNECT_TIMEOUT_S: float = 5.0

# Snippet shaping (``refs/tools.md`` §1 step 3), carried over verbatim.
_SNIPPET_MIN_CHARS: int = 30  # below this, a result is too thin to keep
_SNIPPET_MAX_CHARS: int = 500  # hard cap on the emitted snippet
_TEXT_FALLBACK_CHARS: int = 350  # text[:350] when highlights are absent/thin
_MAX_HIGHLIGHTS: int = 2  # join at most the top-2 highlights
_HIGHLIGHT_JOIN: str = " … "

_HTTP_OK_CEILING: int = httpx.codes.MULTIPLE_CHOICES  # first non-2xx status (300)


def create_exa_http_client(
    *,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the shared Exa HTTP client (Composition Root / test harness only).
    ``trust_env=False`` is the same security invariant every network client in
    this codebase sets (the ``create_llm_http_client`` precedent); the unit
    suite passes an ``httpx.MockTransport`` through this SAME parameter so the
    real ``timeout``/``trust_env``/``base_url`` choices are exercised."""
    return httpx.AsyncClient(
        base_url=_BASE_URL,
        timeout=httpx.Timeout(timeout_s, connect=_CONNECT_TIMEOUT_S),
        trust_env=False,
        transport=transport,
    )


def _cache_key(query: str) -> str:
    """``search:exa:<sha256(query)>`` (``refs/tools.md`` §1). Global, not
    tenant-scoped: web results are public (the port carries no ``ctx``)."""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return f"search:exa:{digest}"


def _build_body(query: str, limit: int) -> Json:
    """Exa ``/search`` request body (``refs/tools.md`` §1 step 2): ``type:
    "auto"``, top-``limit`` results, text + highlights contents."""
    return {
        "query": query,
        "type": "auto",
        "numResults": limit,
        "contents": {
            "text": {"maxCharacters": 800},
            "highlights": {"numSentences": 3, "highlightsPerUrl": _MAX_HIGHLIGHTS},
        },
    }


def _select_snippet(text: str, highlights: list[str]) -> str | None:
    """Snippet policy (``refs/tools.md`` §1 step 3): prefer the top-2
    highlights joined by ``" … "``; else ``text[:350]``. If the result is
    still shorter than 30 chars, fall back to ``text[:350]``, and if THAT is
    still too thin, drop the result (return ``None``). Capped at 500 chars."""
    snippet = (
        _HIGHLIGHT_JOIN.join(highlights[:_MAX_HIGHLIGHTS])
        if highlights
        else text[:_TEXT_FALLBACK_CHARS]
    )
    if len(snippet) < _SNIPPET_MIN_CHARS:
        snippet = text[:_TEXT_FALLBACK_CHARS]
        if len(snippet) < _SNIPPET_MIN_CHARS:
            return None
    return snippet[:_SNIPPET_MAX_CHARS]


def _shape_hit(entry: object) -> WebSearchHit | None:
    """Map one Exa result object onto a ``WebSearchHit``, R6-safely (every
    field via ``isinstance``). Returns ``None`` for an entry with no usable
    URL or too-thin content (dropped by the caller)."""
    if not isinstance(entry, dict):
        return None
    url = entry.get("url")
    if not isinstance(url, str) or not url:
        return None
    title = entry.get("title")
    text = entry.get("text")
    raw_highlights = entry.get("highlights")
    highlights = (
        [h for h in raw_highlights if isinstance(h, str)]
        if isinstance(raw_highlights, list)
        else []
    )
    snippet = _select_snippet(text if isinstance(text, str) else "", highlights)
    if snippet is None:
        return None
    return WebSearchHit(title=title if isinstance(title, str) else "", url=url, snippet=snippet)


def _shape_results(payload: Json, limit: int) -> tuple[WebSearchHit, ...]:
    """Turn a parsed Exa body into hits: R6-guarded, deduplicated by URL (the
    first occurrence wins — ``refs/tools.md`` §1 step 3), capped at ``limit``."""
    raw = payload.get("results")
    if not isinstance(raw, list):
        return ()
    hits: list[WebSearchHit] = []
    seen: set[str] = set()
    for entry in raw:
        hit = _shape_hit(entry)
        if hit is None or hit.url in seen:
            continue
        seen.add(hit.url)
        hits.append(hit)
        if len(hits) >= limit:
            break
    return tuple(hits)


def _encode_hits(hits: tuple[WebSearchHit, ...]) -> bytes:
    """Serialize hits for the cache (JSON array of ``{title,url,snippet}``)."""
    return json.dumps(
        [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits]
    ).encode("utf-8")


def _decode_hits(raw: bytes) -> tuple[WebSearchHit, ...] | None:
    """Rebuild hits from a cache entry, R6-safely. Any malformed payload
    returns ``None`` (treated as a miss — the cache never breaks a search)."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None
    hits: list[WebSearchHit] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            return None
        title, url, snippet = entry.get("title"), entry.get("url"), entry.get("snippet")
        if not (isinstance(title, str) and isinstance(url, str) and isinstance(snippet, str)):
            return None
        hits.append(WebSearchHit(title=title, url=url, snippet=snippet))
    return tuple(hits)


class ExaWebSearchAdapter:
    """Exa-backed ``WebSearchProvider`` (structural Protocol match)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: CacheProvider,
        api_key: str,
        *,
        cache_ttl_s: int = 600,
    ) -> None:
        if not api_key.strip():
            raise ValidationError("exa web-search api_key must not be empty")
        self._client = client
        self._cache = cache
        self._api_key = api_key
        self._cache_ttl_s = cache_ttl_s

    async def search(self, query: str, *, limit: int = 5) -> tuple[WebSearchHit, ...]:
        """Search the web (best-effort). A blank query, any ``httpx`` failure,
        or a non-2xx status all yield an empty tuple (module docstring)."""
        if not query.strip():
            return ()
        cached = await self._cache_get(query)
        if cached is not None:
            return cached
        hits = await self._fetch(query, limit)
        await self._cache_put(query, hits)
        return hits

    async def _fetch(self, query: str, limit: int) -> tuple[WebSearchHit, ...]:
        """Call Exa and shape the response; empty tuple on any failure."""
        try:
            response = await self._client.post(
                _SEARCH_PATH,
                json=_build_body(query, limit),
                headers={"x-api-key": self._api_key},
            )
            if response.status_code >= _HTTP_OK_CEILING:
                return ()
            body = response.json()
        except httpx.HTTPError:
            return ()
        except ValueError:  # non-JSON 2xx body
            return ()
        if not isinstance(body, dict):
            return ()
        return _shape_results(body, limit)

    async def _cache_get(self, query: str) -> tuple[WebSearchHit, ...] | None:
        """Fail-open cache read: any error (or ``ttl=0``) is a miss."""
        if self._cache_ttl_s <= 0:
            return None
        try:
            raw = await self._cache.get(_cache_key(query))
        except Exception:  # fail-open IS the contract (refs §1) — any cache error is a miss
            return None
        return _decode_hits(raw) if raw is not None else None

    async def _cache_put(self, query: str, hits: tuple[WebSearchHit, ...]) -> None:
        """Fail-open cache write: any error (or ``ttl=0``) is ignored."""
        if self._cache_ttl_s <= 0:
            return
        try:
            await self._cache.set(_cache_key(query), _encode_hits(hits), ttl_s=self._cache_ttl_s)
        except Exception:  # fail-open IS the contract (refs §1) — a cache write error is ignored
            return


if TYPE_CHECKING:
    from app.framework.ports.web_search_provider import WebSearchProvider

    def _conforms(adapter: ExaWebSearchAdapter) -> WebSearchProvider:
        """mypy-gate structural proof that the adapter satisfies the port (the
        resolver/registry ``_conforms`` precedent; the wiring site is the
        Composition Root, deferred with the tool's other seams)."""
        return adapter
