"""WebSearchProvider driven port — the neutral web-search abstraction a
``web_search`` tool calls (Phase 4.4; ``refs/tools.md`` §3).

**A NEW port, not in 02-port-contracts §1** — introduced by the 4.4 example
tool, whose migration reference is explicit that "the Exa call becomes a
driven adapter in ``infrastructure/``, injected" rather than an
``import exa_py`` inside the tool. Provider-neutral exactly like every §1
port: Exa is one adapter, the contract leaks no vendor detail (no Exa
``highlights``/``numResults`` shapes here — those live in the adapter). A
``02 §1`` catalog addition is the doc-sync recommendation this file carries,
the same family as the ``ToolRegistry.specs()`` / ``ToolCatalog`` sync notes.

**No ``ExecutionContext`` parameter — the ``LLMProvider``/``EmbeddingProvider``
precedent.** Driven ports are tenant-neutral technology adapters; the tenant
identity lives one layer up (the tool's ``run(ctx, args)``). Web results are
public and tenant-independent, so the adapter's cache key is global by design
(``refs/tools.md`` §4.6) and there is nothing tenant-scoped to thread through
here.

**The key is the adapter's, not a per-call parameter** — unlike
``LLMProvider.complete(..., api_key)`` (a per-tenant credential resolved by
``CredentialResolver``), web search is a PLATFORM capability with a single
platform key (``refs/tools.md`` §1/§4.1). That key is injected into the
adapter at construction (from ``SecretsProvider`` in the Composition Root, the
MinIO precedent), so it never appears in this signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WebSearchHit:
    """One search result, already shaped to what an agent needs (title + url +
    a short snippet). The adapter owns the provider-specific work of turning a
    raw result into this — highlight selection, text fallback, dedup."""

    title: str
    url: str
    snippet: str


class WebSearchProvider(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> tuple[WebSearchHit, ...]: ...
