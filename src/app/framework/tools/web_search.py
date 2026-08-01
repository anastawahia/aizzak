"""``WebSearchTool`` — the ``web_search`` example static tool (Phase 4.4; the
first concrete ``BaseTool``, ``refs/tools.md`` §3, authoring guide 11 §4).

A thin adapter over the injected ``WebSearchProvider`` port: it shapes the
port's ``WebSearchHit`` tuple into the ``Json`` an agent/LLM consumes, and
knows nothing about Exa, httpx, caching or keys (those live in
``infrastructure/web_search/exa_web_search.py``, injected). This is the whole
point of the ``BaseTool`` contract — an agent resolves it BY NAME and never
learns its implementation (D-08, FR-51).

**Reaches its port through ``deps`` — the reason 4.4 grew
``AgentDependencies``** (see ``base_agent.py``): a ``BaseTool``'s only
injection channel is the uniform ``tool_cls(deps)`` construction, and a
subclass may not narrow ``deps``'s type (LSP/mypy), so the port had to become
a field on the bundle. ``deps.web_search`` is ``WebSearchProvider | None``; a
``None`` here means the tool was registered but web search was never wired
(no Exa key configured, ``exa_web_search`` module docstring) — a server
misconfiguration surfaced as ``common.internal``/500 at ``run`` time, never a
silent empty result that an agent would read as "the web has nothing".

**Registration is deferred (the 2.9/4.3 precedent).** 4.4 ships the tool
CLASS and proves it is registry-compatible (``tests/unit/test_web_search.py``
registers it into an ``InMemoryToolRegistry`` and drives it through a
``ToolResolver``). Wiring the real platform ``ToolRegistry`` at boot — and
populating ``deps.web_search`` from the composition root — is the
orchestrator's job (4.7), exactly as the tool-catalog wiring and every other
``deps`` field were deferred there.

``run`` validates its input R6-safely (the registry/resolver discipline): a
missing or non-string ``query`` is a caller bug (``ValidationError``/422),
never a raw ``KeyError``/``TypeError`` from indexing an untrusted ``args``.
"""

from __future__ import annotations

from app.framework.agent_runtime.base_agent import AgentDependencies
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ValidationError
from app.framework.tools.base_tool import BaseTool, ToolSpec
from app.framework.types import Json


class WebSearchTool(BaseTool):
    """Search the public web for the given query (static platform tool)."""

    spec = ToolSpec(
        name="web_search",
        description=(
            "Search the public web and return the most relevant results, each "
            "with a title, url and a short snippet."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query."}},
            "required": ["query"],
        },
    )

    def __init__(self, deps: AgentDependencies) -> None:
        super().__init__(deps)
        self._provider = deps.web_search

    async def run(self, ctx: ExecutionContext, args: Json) -> Json:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("web_search requires a non-empty 'query' string")
        if self._provider is None:
            # Registered but never wired (no Exa key) — a server-side
            # misconfiguration, not an empty-web answer.
            raise AppError("web_search is not configured", code="common.internal", status=500)
        hits = await self._provider.search(query)
        return {
            "query": query,
            "results": [
                {"title": hit.title, "url": hit.url, "snippet": hit.snippet} for hit in hits
            ],
            "total": len(hits),
        }
