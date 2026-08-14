"""The summarisation resolution seam + its result DTO (BE-RAG-009).

``SummarizerResolver`` is the ``EmbeddingResolver`` shape one capability
over: something has to decide WHICH model writes a summary and with whose
key, and the thing that decides is the framework ``ProviderResolver`` reading
the ``summarize`` route out of the configured routing table (D-16). This
module-local Protocol is what lets the application layer state that need
without importing the resolver — the same independence rule
``ports/retrieval.py`` keeps for embeddings.

**``summarize`` is its own routing capability, not a reuse of ``rag``.** The
two calls have opposite shapes: a chat turn is short, latency-bound and
answered from a handful of retrieved chunks, while a ``full`` summary is a
map-reduce over an entire document — the most expensive single call the
platform can make. Sharing one route would mean an operator who wants
summaries on a cheap long-context model must move every RAG conversation
there too, and there is no setting that could express the difference after
the fact. Giving it a key costs one entry in ``PROVIDER_ROUTING`` and buys
the separation permanently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.llm_provider import LLMProvider

# The routing key (D-16, ``PROVIDER_ROUTING``'s ``llm`` namespace) a summary
# build resolves through. Declared HERE, by the module that needs it, rather
# than in the framework: the resolver reads its table by string and knows
# nothing about who asks, so the one place that can name this honestly is the
# feature that spends it. A deployment with no ``summarize`` entry still
# resolves through the table's ``default``, so this adds a knob rather than a
# required setting.
SUMMARIZE_CAPABILITY = "summarize"


@dataclass(frozen=True, slots=True)
class ResolvedSummarizer:
    """The adapter, model and resolved API key one summary build will spend.

    **The provider comes from resolution, not from construction**, unlike the
    ``EmbeddingProvider`` the indexing pipeline holds for its whole lifetime.
    That asymmetry is the routing table's: there is one embedding service, but
    which LLM adapter answers depends on which provider the ``summarize``
    route names — so a pipeline that captured one in its constructor would
    quietly ignore the configuration it exists to honour. This is the shape
    the orchestrator already uses (``resolve_llm`` returns the adapter beside
    the route).

    ``model`` is carried onto the stored ``Summary`` row rather than used and
    forgotten: it is what lets a reader tell why the summary they are looking
    at differs from the one the same document produced last month.
    """

    provider: LLMProvider
    model: str
    api_key: str


class SummarizerResolver(Protocol):
    """Resolves the ``summarize`` route for this workspace (D-16).

    Raises rather than returning ``None`` when no route resolves a key. A
    summary job that cannot reach a model has failed, and the failure belongs
    on the job with its reason — an optional return here would push that
    decision into the pipeline, which has no vocabulary for it.
    """

    async def resolve_summarizer(self, ctx: ExecutionContext) -> ResolvedSummarizer: ...
