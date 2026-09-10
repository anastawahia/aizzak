"""VectorStore driven port (02-port-contracts §1.5, D-01 · Qdrant).

Every search ``flt`` MUST carry ``workspace_id`` so vector retrieval respects
tenant isolation the same way SQL does (DD-04).

Collections are provisioned lazily, at first write. Searching a collection
that does not exist yet is therefore a NORMAL state (a workspace nobody has
indexed anything into), not an error: ``search``/``search_sparse`` return an
empty list for it, and ``delete`` is a no-op. ``upsert`` is the one method
that still fails — its caller must have run ``ensure_collection``/
``ensure_hybrid_collection`` first.

``HybridVectorStore`` (3.k3, docs/migration/refs/retrieval.md §7 risk #1) is
an additive, knowledge-only superset of ``VectorStore``: a second Protocol
rather than new methods bolted onto ``VectorStore`` itself, so ``memory``
(which only ever needs dense search) keeps depending on the narrower
``VectorStore`` contract, unchanged (Interface Segregation). A hybrid
collection keeps ``VectorStore``'s **unnamed default dense vector** — so the
inherited ``search``/``upsert``/``ensure_collection``/``delete`` behave
exactly as they do for ``memory`` — and adds a **named sparse vector
``"text"``**; the 2.5 Qdrant adapter is what actually provisions that sparse
vector (with Qdrant's own IDF modifier — deferred-IDF: ``SparseVector.values``
here are raw term frequencies, and the adapter, not this port, applies IDF at
index/query time). This dense-default + named-sparse-``"text"`` layout is a
documented port convention, not something the type system enforces. One
``VectorPoint`` per chunk carries both a dense ``.vector`` and an optional
``.sparse``, sharing one payload and one ``delete`` call — the two legs are
two facets of the same point, never two separate points.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Json, Uuid


@dataclass(frozen=True, slots=True)
class SparseVector:
    """A sparse term vector: parallel uint32 term-id ``indices`` and raw
    term-frequency ``values`` (IDF is applied server-side by the adapter —
    see the module docstring)."""

    indices: list[int]
    values: list[float]


@dataclass(frozen=True, slots=True)
class VectorPoint:
    id: Uuid
    vector: list[float]
    payload: Json
    sparse: SparseVector | None = None


@dataclass(frozen=True, slots=True)
class VectorHit:
    """One search result. ``vector`` is the point's own DENSE vector and is
    populated ONLY when the search asked for it (``with_vectors=True`` on
    ``HybridVectorStore``); every other read leaves it ``None``, which is why
    it is the one optional field here.

    It exists for MMR (rag-retrieval-plan.md §3.9, ``P-23``, decision س-20):
    the diversity term needs candidate-to-candidate similarity, which nothing
    but the vectors themselves can give. ``None`` means "not requested" (or a
    store that does not return vectors), never "this point has no vector" —
    every indexed point has one.
    """

    id: Uuid
    score: float
    payload: Json
    vector: list[float] | None = None


class VectorStore(Protocol):
    """``ensure_collection`` RESOLVES as well as provisions, and it is the
    only method here that may be handed a name that is not a collection.

    ``name`` is the caller's STABLE name for one corpus (``kn-<workspace_id>``,
    ``mem-<workspace_id>``). ``revision`` is the fingerprint of the embedding
    regime the caller is about to write vectors under (capacity step 4.5,
    ``knowledge.domain.collections.EmbeddingRegime``), and the return value is
    the PHYSICAL collection that write must go to — which may not be ``name``.

    **Why the return value exists at all.** With a revision, ``name`` is not
    necessarily a collection: the corpus this regime owns may be a collection
    of its own, or an alias over the pre-4.5 one, or not exist yet. A writer
    that took ``name`` at face value would put its vectors wherever that name
    happens to land — including a collection another model filled — and a
    search over two embedding spaces returns results that look right and are
    random, the failure 4.5 exists to make unreachable. So provisioning and
    resolution are ONE call: there is no way to obtain a write target without
    having asked which one it is.

    **A swap never re-points anything under a running process.** Each
    deployment resolves the corpus its own regime built, so a rolling upgrade
    (7.2) that runs two model versions at once is two coherent fleets rather
    than one mixed corpus — and rolling back is redeploying the previous
    regime, whose corpus is still standing. The store never has to make a
    switch atomic with a deploy, because there is no switch.

    ``revision=None`` keeps the pre-4.5 contract exactly: ``name`` is a
    physical collection, it is provisioned, and it is returned unchanged.
    That is what the operational paths pass (they name a collection outright)
    and it is what keeps every caller that has no regime honest rather than
    guessing one.

    **Provisioning stays lazy and searching a missing collection stays
    normal** (module docstring) — a corpus is still created at first write,
    and this method is still that write's precondition.
    """

    async def ensure_collection(
        self, name: str, dim: int, distance: str = "cosine", *, revision: str | None = None
    ) -> str: ...

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None: ...

    async def search(
        self, collection: str, vector: list[float], k: int, flt: Json | None = None
    ) -> list[VectorHit]: ...

    async def delete(self, collection: str, ids: Sequence[Uuid]) -> None: ...


class HybridVectorStore(VectorStore, Protocol):
    """Knowledge-only superset of ``VectorStore`` adding a BM25-sparse search
    leg (3.k3) — see the module docstring for the shared-point/dense-default/
    sparse-named-``"text"`` convention.

    ``ensure_payload_index`` provisions a keyword index over ONE payload key
    so filtered retrieval stops scanning (spaces plan §3.4). It is
    **idempotent** — re-creating an existing index with the same shape is a
    success, so callers never have to ask first — and it is a PROVISIONING
    call, so a collection that does not exist is a real fault here (the
    ``upsert`` policy, not the read paths' empty-result one).

    ``tenant=True`` asks the store to keep points sharing that key
    physically together (Qdrant's ``is_tenant``); it is reserved for the
    ownership axis a query is almost always narrowed by — ``space`` — and
    left ``False`` for keys that merely need lookup (``workspace_id``,
    ``document_id``). It stays on the HYBRID Protocol and not on
    ``VectorStore``: ``memory`` filters one small collection per workspace
    and asks for nothing here, and the module docstring's Interface
    Segregation rule is what keeps its narrower contract unchanged.

    An implementation is free to call this itself from
    ``ensure_hybrid_collection`` (the Qdrant adapter does). It stays on the
    port regardless, because collections provisioned BEFORE the indexes
    existed can only gain them through an explicit operational call — see
    the spaces plan §5-ب.

    ``delete_everywhere`` is the counterpart, and it is on the HYBRID port for
    the same Interface-Segregation reason: ``memory`` has one corpus per
    workspace and always will (its items are rebuildable from rows, so it was
    never revisioned), while ``knowledge`` can have two at once and a
    ``VectorRef`` recorded under one of them must not be able to leave a copy
    alive in the other. ``name`` here is the workspace's STABLE name
    (``kn-<workspace_id>``), never a resolved corpus: the whole point is to
    reach the corpora the caller does not know about.

    ``ensure_hybrid_collection`` carries ``revision`` and returns the resolved
    physical name for the SAME reason ``ensure_collection`` does — that
    method's docstring is the whole argument, and it applies here unchanged.
    Knowledge is in fact the corpus 4.5 was written for: it is the one whose
    read path (``knowledge/application/retrieval.py``) must keep answering
    while a second copy of it is built.

    ``with_vectors`` asks BOTH legs to return each hit's own dense vector
    (``VectorHit.vector``) alongside its payload — MMR's input
    (rag-retrieval-plan.md §3.9, ``P-23``, decision س-20). It defaults to
    ``False`` because it is not free: a full float vector per candidate
    crosses the network, the price §3.9 declares openly and §6 risk #5
    accepts, bounded by the widened ``search_k`` and never the corpus.

    This is why ``search`` is REDECLARED here rather than gaining the flag on
    ``VectorStore`` itself: ``memory`` never asks for a vector back, so its
    narrower contract stays exactly as it was (the module docstring's
    Interface Segregation rule). The redeclaration only ADDS an optional
    keyword, so any implementation of this Protocol still satisfies
    ``VectorStore`` unchanged — one ``QdrantVectorStore`` continues to serve
    both.
    """

    async def ensure_hybrid_collection(
        self, name: str, dim: int, *, distance: str = "cosine", revision: str | None = None
    ) -> str: ...

    async def search(
        self,
        collection: str,
        vector: list[float],
        k: int,
        flt: Json | None = None,
        *,
        with_vectors: bool = False,
    ) -> list[VectorHit]: ...

    async def search_sparse(
        self,
        collection: str,
        sparse: SparseVector,
        k: int,
        flt: Json | None = None,
        *,
        with_vectors: bool = False,
    ) -> list[VectorHit]: ...

    async def ensure_payload_index(
        self, collection: str, field: str, *, tenant: bool = False
    ) -> None: ...

    async def delete_everywhere(self, name: str, ids: Sequence[Uuid]) -> None: ...
