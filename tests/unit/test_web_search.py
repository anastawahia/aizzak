"""Unit tests for the ``web_search`` example tool vertical (Phase 4.4).

Hermetic, no ``live_*`` marker — the Exa adapter is exercised through an
``httpx.MockTransport`` (the 2.8-b-1 OpenAI-adapter precedent), so nothing
here touches the network. The live proof against the real Exa API lives in
``tests/integration/test_exa_web_search.py`` behind the ``live_web_search``
marker and auto-skips without ``TEST_EXA_API_KEY``.

Sections grow one per step:

* Step 1 — the ``WebSearchProvider`` port contract (frozen ``WebSearchHit``)
  and the ``AgentDependencies`` growth (new ``web_search`` field, still
  frozen/sealed, still constructible empty).
* Step 2 — the ``ExaWebSearchAdapter`` over an ``httpx.MockTransport``:
  request shaping, R6-safe response mapping, dedup, snippet fallback,
  best-effort empty-on-failure, and fail-open caching.
* Step 3 — the ``WebSearchTool`` (BaseTool): Json shaping, R6 input guard,
  the unwired-provider 500, and registry/resolver compatibility.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import httpx
import pytest

from app.framework.agent_runtime import AgentDependencies
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.web_search_provider import WebSearchHit, WebSearchProvider
from app.framework.tools import InMemoryToolRegistry, ToolResolver, WebSearchTool
from app.framework.types import Json
from app.infrastructure.web_search.exa_web_search import (
    ExaWebSearchAdapter,
    create_exa_http_client,
)

# --------------------------------------------------------------------------- #
# Step 1 — port contract + AgentDependencies growth                           #
# --------------------------------------------------------------------------- #


def test_web_search_hit_is_a_frozen_value_object() -> None:
    hit = WebSearchHit(title="T", url="https://e.x/a", snippet="s")
    assert (hit.title, hit.url, hit.snippet) == ("T", "https://e.x/a", "s")
    with pytest.raises(FrozenInstanceError):
        hit.url = "https://evil"  # type: ignore[misc]


def test_agent_dependencies_still_constructs_empty_and_defaults_web_search_none() -> None:
    # The bundle stays permissive: every prior `AgentDependencies()` call site
    # (agent_runtime, tool_system, lifecycle tests) keeps compiling and the new
    # field defaults to None.
    deps = AgentDependencies()
    assert deps.web_search is None


def test_agent_dependencies_carries_an_injected_web_search_provider() -> None:
    class _FakeProvider:
        async def search(self, query: str, *, limit: int = 5) -> tuple[WebSearchHit, ...]:
            return ()

    provider: WebSearchProvider = _FakeProvider()
    deps = AgentDependencies(web_search=provider)
    assert deps.web_search is provider


def test_agent_dependencies_stays_frozen_and_sealed_after_growth() -> None:
    deps = AgentDependencies()
    # Known field → FrozenInstanceError (frozen); unknown name → TypeError (the
    # frozen+slots `__setattr__` artifact). Both mean "sealed": the growth to a
    # dataclass did not reopen the bundle for mutation or ad-hoc attributes.
    with pytest.raises(FrozenInstanceError):
        deps.web_search = object()  # type: ignore[assignment,misc]
    with pytest.raises((AttributeError, TypeError)):
        deps.llm = object()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Step 2 — ExaWebSearchAdapter over httpx.MockTransport                        #
# --------------------------------------------------------------------------- #


class FakeCache:
    """A minimal in-memory ``CacheProvider`` for the happy path; individual
    tests swap in raising variants to prove fail-open."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        self.set_calls.append((key, value, ttl_s))
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def incr(self, key: str, amount: int = 1) -> int:
        raise AssertionError("web search never calls incr")

    async def expire(self, key: str, ttl_s: int) -> None:
        raise AssertionError("web search never calls expire")


def _exa_result(
    url: str, *, title: str = "T", text: str = "", highlights: list[str] | None = None
) -> dict:
    entry: dict[str, object] = {"url": url, "title": title, "text": text}
    if highlights is not None:
        entry["highlights"] = highlights
    return entry


def _adapter_returning(
    body: object,
    *,
    status: int = 200,
    cache: FakeCache | None = None,
    cache_ttl_s: int = 600,
    captured: list[httpx.Request] | None = None,
) -> ExaWebSearchAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, content=str(body).encode())

    client = create_exa_http_client(timeout_s=5.0, transport=httpx.MockTransport(handler))
    return ExaWebSearchAdapter(client, cache or FakeCache(), "exa-key", cache_ttl_s=cache_ttl_s)


async def test_search_shapes_hits_and_sends_the_expected_exa_request() -> None:
    captured: list[httpx.Request] = []
    snippet = "python asyncio is a stdlib library for concurrency"  # >= 30 chars
    body = {"results": [_exa_result("https://a.x", title="A", highlights=[snippet])]}
    adapter = _adapter_returning(body, captured=captured)

    hits = await adapter.search("python asyncio", limit=3)

    assert hits == (WebSearchHit(title="A", url="https://a.x", snippet=snippet),)
    # The outgoing request carries the key header + the documented body shape.
    request = captured[0]
    assert request.headers["x-api-key"] == "exa-key"
    sent = json.loads(request.content)
    assert sent["query"] == "python asyncio"
    assert sent["type"] == "auto"
    assert sent["numResults"] == 3


async def test_highlights_are_joined_and_text_is_the_fallback() -> None:
    body = {
        "results": [
            _exa_result(
                "https://h.x", highlights=["first highlight here", "second highlight here"]
            ),
            _exa_result("https://t.x", text="x" * 400),  # no highlights -> text[:350]
        ]
    }
    hits = await _adapter_returning(body).search("q")
    assert hits[0].snippet == "first highlight here … second highlight here"
    assert hits[1].snippet == "x" * 350


async def test_thin_results_are_dropped() -> None:
    # Highlights too short AND text too short -> the result is dropped entirely.
    body = {"results": [_exa_result("https://thin.x", text="short", highlights=["tiny"])]}
    assert await _adapter_returning(body).search("q") == ()


async def test_results_are_deduplicated_by_url_and_capped_at_limit() -> None:
    body = {
        "results": [
            _exa_result("https://dup.x", title="first", text="a" * 40),
            _exa_result("https://dup.x", title="second", text="b" * 40),  # same url -> dropped
            _exa_result("https://other.x", text="c" * 40),
        ]
    }
    hits = await _adapter_returning(body).search("q", limit=1)
    assert [h.url for h in hits] == ["https://dup.x"]  # first wins, capped at 1
    assert hits[0].title == "first"


async def test_dedup_is_observable_beyond_the_cap() -> None:
    # A high limit so the URL-dedup (not the cap) is what removes the second
    # duplicate — otherwise a broken dedup would hide behind the limit.
    body = {
        "results": [
            _exa_result("https://dup.x", title="first", text="a" * 40),
            _exa_result("https://dup.x", title="second", text="b" * 40),
            _exa_result("https://other.x", text="c" * 40),
        ]
    }
    hits = await _adapter_returning(body).search("q", limit=5)
    assert [h.url for h in hits] == ["https://dup.x", "https://other.x"]


@pytest.mark.parametrize(
    "body",
    [
        {"results": "not-a-list"},
        {"no_results_key": 1},
        {"results": [123, "str", None, {"no_url": 1}, {"url": ""}]},
        [1, 2, 3],  # top-level not an object
    ],
    ids=["results_not_list", "missing_key", "junk_entries", "top_level_list"],
)
async def test_malformed_exa_body_degrades_to_empty_never_raises(body: object) -> None:
    assert await _adapter_returning(body).search("q") == ()


async def test_non_json_2xx_body_degrades_to_empty() -> None:
    assert await _adapter_returning("<html>not json</html>").search("q") == ()


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
async def test_error_status_is_best_effort_empty_not_fatal(status: int) -> None:
    assert (
        await _adapter_returning(
            {"results": [_exa_result("https://a.x", text="a" * 40)]}, status=status
        ).search("q")
        == ()
    )


async def test_transport_failure_is_best_effort_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("exa down")

    client = create_exa_http_client(timeout_s=5.0, transport=httpx.MockTransport(handler))
    adapter = ExaWebSearchAdapter(client, FakeCache(), "exa-key")
    assert await adapter.search("q") == ()


async def test_blank_query_short_circuits_without_calling_exa() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    client = create_exa_http_client(timeout_s=5.0, transport=httpx.MockTransport(handler))
    adapter = ExaWebSearchAdapter(client, FakeCache(), "exa-key")
    assert await adapter.search("   ") == ()
    assert called is False


async def test_empty_api_key_is_a_wiring_bug_at_construction() -> None:
    client = create_exa_http_client(
        timeout_s=5.0, transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    with pytest.raises(ValidationError):
        ExaWebSearchAdapter(client, FakeCache(), "   ")


async def test_cache_hit_returns_without_calling_exa() -> None:
    cache = FakeCache()
    hits = (
        WebSearchHit(
            title="cached", url="https://c.x", snippet="from cache thirty plus chars long"
        ),
    )
    cache.store["search:exa:" + __import__("hashlib").sha256(b"q").hexdigest()] = json.dumps(
        [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits]
    ).encode()
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    client = create_exa_http_client(timeout_s=5.0, transport=httpx.MockTransport(handler))
    adapter = ExaWebSearchAdapter(client, cache, "exa-key")
    assert await adapter.search("q") == hits
    assert called is False  # served from cache, Exa never called


async def test_result_is_written_to_cache_with_ttl() -> None:
    cache = FakeCache()
    body = {"results": [_exa_result("https://a.x", text="a" * 40)]}
    adapter = _adapter_returning(body, cache=cache, cache_ttl_s=900)
    await adapter.search("q")
    assert len(cache.set_calls) == 1
    key, _value, ttl = cache.set_calls[0]
    assert key.startswith("search:exa:")
    assert ttl == 900


async def test_cache_get_error_fails_open_and_still_searches() -> None:
    class RaisingGet(FakeCache):
        async def get(self, key: str) -> bytes | None:
            raise RuntimeError("redis down")

    body = {"results": [_exa_result("https://a.x", text="a" * 40)]}
    hits = await _adapter_returning(body, cache=RaisingGet()).search("q")
    assert hits[0].url == "https://a.x"  # cache error did not break the search


async def test_cache_set_error_fails_open_and_still_returns_results() -> None:
    class RaisingSet(FakeCache):
        async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
            raise RuntimeError("redis down")

    body = {"results": [_exa_result("https://a.x", text="a" * 40)]}
    hits = await _adapter_returning(body, cache=RaisingSet()).search("q")
    assert hits[0].url == "https://a.x"  # set error swallowed, results still returned


async def test_ttl_zero_disables_cache_entirely() -> None:
    cache = FakeCache()
    body = {"results": [_exa_result("https://a.x", text="a" * 40)]}
    adapter = _adapter_returning(body, cache=cache, cache_ttl_s=0)
    await adapter.search("q")
    assert cache.set_calls == []  # nothing cached when ttl=0


async def test_corrupt_cache_entry_is_a_miss_and_refetches() -> None:
    cache = FakeCache()
    cache.store["search:exa:" + __import__("hashlib").sha256(b"q").hexdigest()] = b"not-json{{{"
    body = {"results": [_exa_result("https://fresh.x", text="a" * 40)]}
    hits = await _adapter_returning(body, cache=cache).search("q")
    assert hits[0].url == "https://fresh.x"  # corrupt entry ignored, Exa re-queried


def test_adapter_structurally_satisfies_the_web_search_port() -> None:
    provider: WebSearchProvider = _adapter_returning({"results": []})
    assert provider is not None


# --------------------------------------------------------------------------- #
# Step 3 — WebSearchTool (BaseTool)                                            #
# --------------------------------------------------------------------------- #


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


class StubProvider:
    """A ``WebSearchProvider`` that records its call and returns fixed hits."""

    def __init__(self, hits: tuple[WebSearchHit, ...]) -> None:
        self._hits = hits
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int = 5) -> tuple[WebSearchHit, ...]:
        self.calls.append((query, limit))
        return self._hits


class EmptyCatalog:
    """A dynamic catalog with nothing in it (for the resolver-compat test)."""

    async def list_tools(self, ctx: ExecutionContext) -> list:
        return []

    async def invoke_tool(self, ctx: ExecutionContext, name: str, args: Json) -> Json:
        raise AssertionError("static tool must resolve without touching the catalog")


def test_web_search_tool_spec_is_registry_shaped() -> None:
    spec = WebSearchTool.spec
    assert spec.name == "web_search"
    assert spec.parameters["required"] == ["query"]
    assert spec.parameters["properties"]["query"]["type"] == "string"


async def test_run_shapes_provider_hits_into_json() -> None:
    provider = StubProvider(
        (
            WebSearchHit(
                title="A", url="https://a.x", snippet="snippet a is thirty plus chars long"
            ),
            WebSearchHit(
                title="B", url="https://b.x", snippet="snippet b is thirty plus chars long"
            ),
        )
    )
    tool = WebSearchTool(AgentDependencies(web_search=provider))
    result = await tool.run(_ctx(), {"query": "search me"})
    assert result == {
        "query": "search me",
        "results": [
            {"title": "A", "url": "https://a.x", "snippet": "snippet a is thirty plus chars long"},
            {"title": "B", "url": "https://b.x", "snippet": "snippet b is thirty plus chars long"},
        ],
        "total": 2,
    }
    assert provider.calls == [("search me", 5)]  # default limit


async def test_run_empty_results_is_a_clean_zero_total() -> None:
    tool = WebSearchTool(AgentDependencies(web_search=StubProvider(())))
    assert await tool.run(_ctx(), {"query": "nothing"}) == {
        "query": "nothing",
        "results": [],
        "total": 0,
    }


@pytest.mark.parametrize(
    "args",
    [{}, {"query": ""}, {"query": "   "}, {"query": 5}, {"query": None}],
    ids=["missing", "empty", "blank", "int", "none"],
)
async def test_run_bad_query_is_a_caller_bug_422(args: Json) -> None:
    tool = WebSearchTool(AgentDependencies(web_search=StubProvider(())))
    with pytest.raises(ValidationError) as excinfo:
        await tool.run(_ctx(), args)
    assert excinfo.value.status == 422


async def test_run_without_a_wired_provider_is_a_misconfiguration_500() -> None:
    # Registered but deps.web_search is None (no Exa key wired): a server bug,
    # surfaced as 500 — never a silent empty result.
    tool = WebSearchTool(AgentDependencies())
    with pytest.raises(AppError) as excinfo:
        await tool.run(_ctx(), {"query": "q"})
    assert excinfo.value.status == 500


async def test_tool_is_registry_and_resolver_compatible_end_to_end() -> None:
    # The whole point of BaseTool: an agent resolves it BY NAME through the
    # ToolResolver, source-blind, and it runs over its injected port.
    provider = StubProvider((WebSearchHit(title="T", url="https://t.x", snippet="x" * 40),))
    deps = AgentDependencies(web_search=provider)
    registry = InMemoryToolRegistry()
    registry.register(WebSearchTool)
    resolver = ToolResolver(registry, EmptyCatalog(), deps)

    ctx = _ctx()
    specs = await resolver.list(ctx)
    assert any(spec.name == "web_search" for spec in specs)

    result = await resolver.invoke(ctx, "web_search", {"query": "hello"})
    assert result["total"] == 1
    assert result["results"][0]["url"] == "https://t.x"
    assert provider.calls == [("hello", 5)]
