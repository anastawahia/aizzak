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
    id: Uuid
    score: float
    payload: Json


class VectorStore(Protocol):
    async def ensure_collection(self, name: str, dim: int, distance: str = "cosine") -> None: ...

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None: ...

    async def search(
        self, collection: str, vector: list[float], k: int, flt: Json | None = None
    ) -> list[VectorHit]: ...

    async def delete(self, collection: str, ids: Sequence[Uuid]) -> None: ...


class HybridVectorStore(VectorStore, Protocol):
    """Knowledge-only superset of ``VectorStore`` adding a BM25-sparse search
    leg (3.k3) — see the module docstring for the shared-point/dense-default/
    sparse-named-``"text"`` convention."""

    async def ensure_hybrid_collection(
        self, name: str, dim: int, *, distance: str = "cosine"
    ) -> None: ...

    async def search_sparse(
        self, collection: str, sparse: SparseVector, k: int, flt: Json | None = None
    ) -> list[VectorHit]: ...
