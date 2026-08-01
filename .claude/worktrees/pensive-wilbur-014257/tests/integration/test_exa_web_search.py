"""Live proof for ``ExaWebSearchAdapter`` against the REAL Exa API (Phase 4.4).

Marker ``live_web_search``; auto-skips unless the user has exported
``TEST_EXA_API_KEY`` in their OWN shell (the ``TEST_OPENAI_API_KEY``
precedent) — the secret is never pasted into chat and never read by the
assistant, only by this test at run time::

    TEST_EXA_API_KEY=... wsl -d Ubuntu-24.04 -- bash -lc \\
      'cd /home/AIZZAK && .venv/bin/pytest -m live_web_search'

No wire shape is pinned here: the hermetic suite
(``tests/unit/test_web_search.py``) proves the adapter's OWN logic against a
``MockTransport``; THIS test is what confirms the adapter's beliefs about
Exa's real ``/search`` request/response shape (``refs/tools.md`` §4.4), the
gap ``openai_llm``'s live suite closes for OpenAI. Caching is disabled
(``cache_ttl_s=0``) so no ``CacheProvider`` is needed and every run hits Exa.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from app.framework.ports.web_search_provider import WebSearchHit
from app.infrastructure.web_search.exa_web_search import (
    ExaWebSearchAdapter,
    create_exa_http_client,
)

pytestmark = [pytest.mark.live_web_search]


class _NoCache:
    """A never-consulted ``CacheProvider`` — the adapter runs with
    ``cache_ttl_s=0`` so none of these are called."""

    async def get(self, key: str) -> bytes | None:
        raise AssertionError("cache disabled (ttl=0)")

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        raise AssertionError("cache disabled (ttl=0)")

    async def delete(self, key: str) -> None:
        raise AssertionError("cache disabled (ttl=0)")

    async def incr(self, key: str, amount: int = 1) -> int:
        raise AssertionError("cache disabled (ttl=0)")

    async def expire(self, key: str, ttl_s: int) -> None:
        raise AssertionError("cache disabled (ttl=0)")


@pytest.fixture
async def exa_adapter() -> AsyncIterator[ExaWebSearchAdapter]:
    api_key = os.environ.get("TEST_EXA_API_KEY")
    if not api_key:
        pytest.skip("no TEST_EXA_API_KEY exported -- live_web_search suite skipped")
    client = create_exa_http_client(timeout_s=30.0)
    try:
        yield ExaWebSearchAdapter(client, _NoCache(), api_key, cache_ttl_s=0)
    finally:
        await client.aclose()


async def test_live_search_returns_shaped_hits(exa_adapter: ExaWebSearchAdapter) -> None:
    hits = await exa_adapter.search("python asyncio tutorial", limit=5)
    assert len(hits) >= 1
    for hit in hits:
        assert isinstance(hit, WebSearchHit)
        assert hit.url.startswith("http")
        assert len(hit.snippet) >= 30  # the adapter's own minimum
    assert len({h.url for h in hits}) == len(hits)  # deduplicated
