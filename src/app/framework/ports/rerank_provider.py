"""RerankProvider driven port (rag-retrieval-plan.md §3.10, ``P-24``, decision
س-21).

**The same provider model as ``EmbeddingProvider``/``LLMProvider``, not a
third idiom** — §3.10's own words ("يتّسق حرفيًّا مع نموذج المزوّدين القائم"):
a ``Protocol`` with a ``provider: str`` attribute and one ``async`` method
over frozen value objects, implemented structurally by an adapter in
``infrastructure/ai_providers/`` and bound in the Composition Root. Nothing
here knows a URL, a vendor or a wire format.

**No model weights anywhere behind this port** — §3.10 states the rule ("لا
أوزان داخل صورة العامل") and the shipped adapter
(``ai_providers/rerank/external_rerank.py``) honours it exactly the way
``ExternalEmbeddingProvider`` does: a cross-encoder is a SEPARATE deployable
reached over HTTP, so no torch/sentence-transformers dependency and no baked
model ever enters the API or worker image.

**Ranking, not scoring the pipeline trusts.** ``rerank`` returns a
``RerankedDocument`` per input document it ordered — an INDEX back into the
caller's own list plus the cross-encoder's own score — rather than reordered
text. Two consequences the retrieval pipeline depends on:

* The caller keeps its own records (ids, payloads, fused scores) and merely
  re-orders them, so nothing is looked up by text equality and nothing the
  reranker did not return is lost. That is what makes the "never starve
  ``final_top_n``" guard (§3.10) expressible at all: the caller can always
  see which of its candidates came back and top up from its own ordering.
* ``score`` is on the cross-encoder's OWN scale — not cosine, not the fused
  RRF scale ``domain/relevance.py``'s floors read. It is informational (a
  log, a future calibration); the pipeline consumes the ORDER.

**No ``model``/``api_key`` parameters**, deliberately, and that is the one
place this port's shape differs from ``EmbeddingProvider.embed``. Those two
exist there because ``ProviderResolver`` routes an embedding call per
capability and hands the adapter a decrypted credential (DD-13). Nothing
routes a rerank: this platform has one internal rerank service per
deployment, pinned to one model in ``Settings.rerank_service`` exactly the
way ``EmbeddingServiceSettings.dimensions`` pins a baked fact about the
deployment, and it is keyless (``OllamaSettings``' precedent). Accepting an
``api_key`` the adapter documents as never read would be shape for its own
sake.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RerankedDocument:
    """One document's place in the reranked order.

    ``index`` is the position of the document in the ``documents`` sequence
    the caller passed to ``rerank`` — never an id and never the text, so the
    port stays ignorant of whatever the caller is actually ranking.
    """

    index: int
    score: float


class RerankProvider(Protocol):
    """Order a small candidate set by relevance to a query (§3.10).

    **Scope is the caller's, and it is small by contract** — §3.10: "أوّل
    10-20 مرشّحًا بعد الدمج، لا الكوربوس". A cross-encoder reads every
    (query, document) pair, so cost is linear in ``documents``; this port is
    never handed a corpus.
    """

    provider: str

    async def rerank(self, query: str, documents: Sequence[str]) -> list[RerankedDocument]:
        """Rank ``documents`` against ``query``, best first.

        The returned list may be SHORTER than ``documents`` (a service with a
        cap of its own, or one that drops entries it scored below some
        internal threshold) and every caller must treat that as normal — see
        §3.10's guard. It carries no entry for a document it did not rank,
        and it never repeats an index.
        """
        ...
