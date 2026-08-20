"""Live-Qdrant tests for ``QdrantVectorStore`` (02-port-contracts §1.5, D-01,
Phase 2.5).

Runs against the real local Compose Qdrant (see
``tests/integration/conftest.py``); auto-skips via ``live_qdrant`` when
unreachable. Every test gets its own fresh, uniquely-named collection
(``qdrant_collection``), created here (never by the fixture itself) and
always swept in teardown.

Behaviours pinned here beyond the plain round-trip: ``ensure_collection``/
``ensure_hybrid_collection`` are idempotent and survive a lost create-race;
``search``'s ``flt`` genuinely isolates by ``workspace_id`` (DD-04's
tenant-isolation contract for vector retrieval -- test 4 is the governing
security case) and ANDs multiple keys together; ``upsert`` is idempotent by
id (last write wins); ``delete`` is silently idempotent; a collection that
was never created reads as EMPTY (``search``/``search_sparse`` -> no hits,
``delete`` -> no-op) while ``upsert`` into one still fails as
``common.internal`` -- see the adapter's module docstring for why; an empty
collection searches clean; a hybrid collection provisions its
named ``"text"`` sparse vector with Qdrant's server-side IDF modifier, keeps
its inherited dense leg working unchanged, and a single point with both legs
shares one payload and is removed by one ``delete`` call; ``search_sparse``
ranks by term overlap and respects ``flt`` exactly like the dense leg; a new
hybrid collection comes back from the server carrying payload indexes on
``workspace_id``/``document_id``/``space`` with ``is_tenant`` set on ``space``
alone (spaces plan step 9), re-asserting one is idempotent, and asking to
index a collection that was never created fails loudly; ``with_vectors=True``
brings each hit's DENSE vector back on both legs of a hybrid collection while
the default leaves it absent (rag-retrieval-plan.md §3.9, ``P-23`` -- MMR's
input, and the one thing a unit test cannot prove, since the shape Qdrant
chooses is the server's decision); and every driver failure surfaces as the
framework's ``common.internal`` -- never a raw ``qdrant_client`` exception.
"""

from __future__ import annotations

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.framework.errors import AppError
from app.framework.identifiers import new_uuid7
from app.framework.ports.vector_store import SparseVector, VectorPoint
from app.framework.settings.settings import QdrantSettings
from app.infrastructure.vector.qdrant_store import QdrantVectorStore, create_qdrant_client

pytestmark = [pytest.mark.live_qdrant]

# Small, arbitrary dense dimensionality -- fast to index/search, plenty to
# exercise dense-vector ranking with two or three distinguishable points.
_DIM = 4


# --------------------------------------------------------------------------- #
# (1) ensure_collection idempotency                                          #
# --------------------------------------------------------------------------- #
async def test_ensure_collection_is_idempotent(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)  # second call: no error

    assert await qdrant_client.collection_exists(qdrant_collection) is True


# --------------------------------------------------------------------------- #
# (2)-(3) dense round trip + exact k                                         #
# --------------------------------------------------------------------------- #
async def test_dense_round_trip_returns_the_matching_point_with_its_literal_payload(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)
    id_a, id_b = new_uuid7(), new_uuid7()
    await qdrant_store.upsert(
        qdrant_collection,
        [
            VectorPoint(id=id_a, vector=[1.0, 0.0, 0.0, 0.0], payload={"label": "a"}),
            VectorPoint(id=id_b, vector=[0.0, 1.0, 0.0, 0.0], payload={"label": "b"}),
        ],
    )

    hits = await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=1)

    assert len(hits) == 1
    assert hits[0].id == id_a
    assert hits[0].payload == {"label": "a"}


async def test_search_returns_exactly_k_hits_when_more_points_exist(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)
    points = [
        VectorPoint(id=new_uuid7(), vector=[float(i + 1), 0.0, 0.0, 0.0], payload={})
        for i in range(5)
    ]
    await qdrant_store.upsert(qdrant_collection, points)

    hits = await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=3)

    assert len(hits) == 3


# --------------------------------------------------------------------------- #
# (4)-(5) filter isolation -- (4) is the governing tenant-isolation case      #
# --------------------------------------------------------------------------- #
async def test_search_filter_isolates_by_workspace_id(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    """DD-04's governing security case: a ``flt={"workspace_id": ...}`` search
    must return points from ONLY that workspace, never leaking another
    tenant's point even though both share a collection."""
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)
    ws_a, ws_b = new_uuid7(), new_uuid7()
    await qdrant_store.upsert(
        qdrant_collection,
        [
            VectorPoint(
                id=new_uuid7(), vector=[1.0, 0.0, 0.0, 0.0], payload={"workspace_id": ws_a}
            ),
            VectorPoint(
                id=new_uuid7(), vector=[1.0, 0.0, 0.0, 0.0], payload={"workspace_id": ws_b}
            ),
        ],
    )

    hits = await qdrant_store.search(
        qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=10, flt={"workspace_id": ws_a}
    )

    assert len(hits) == 1
    assert hits[0].payload["workspace_id"] == ws_a


async def test_search_filter_ands_multiple_keys(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)
    ws = new_uuid7()
    id_match = new_uuid7()
    await qdrant_store.upsert(
        qdrant_collection,
        [
            VectorPoint(
                id=id_match,
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={"workspace_id": ws, "agent_key": "writer"},
            ),
            VectorPoint(
                id=new_uuid7(),
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={"workspace_id": ws, "agent_key": "researcher"},
            ),
            VectorPoint(
                id=new_uuid7(),
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={"workspace_id": new_uuid7(), "agent_key": "writer"},
            ),
        ],
    )

    hits = await qdrant_store.search(
        qdrant_collection,
        [1.0, 0.0, 0.0, 0.0],
        k=10,
        flt={"workspace_id": ws, "agent_key": "writer"},
    )

    assert [hit.id for hit in hits] == [id_match]


# --------------------------------------------------------------------------- #
# (6) upsert idempotency                                                      #
# --------------------------------------------------------------------------- #
async def test_upsert_same_id_twice_keeps_one_point_with_the_latest_payload(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)
    point_id = new_uuid7()
    await qdrant_store.upsert(
        qdrant_collection,
        [VectorPoint(id=point_id, vector=[1.0, 0.0, 0.0, 0.0], payload={"version": 1})],
    )

    await qdrant_store.upsert(
        qdrant_collection,
        [VectorPoint(id=point_id, vector=[1.0, 0.0, 0.0, 0.0], payload={"version": 2})],
    )

    hits = await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=10)
    assert len(hits) == 1
    assert hits[0].payload == {"version": 2}


# --------------------------------------------------------------------------- #
# (7)-(8) delete                                                              #
# --------------------------------------------------------------------------- #
async def test_delete_removes_the_point_from_search_results(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)
    point_id = new_uuid7()
    await qdrant_store.upsert(
        qdrant_collection, [VectorPoint(id=point_id, vector=[1.0, 0.0, 0.0, 0.0], payload={})]
    )

    await qdrant_store.delete(qdrant_collection, [point_id])

    assert await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=10) == []


async def test_delete_of_absent_ids_is_a_silent_noop(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)

    await qdrant_store.delete(qdrant_collection, [new_uuid7(), new_uuid7()])  # no error


# --------------------------------------------------------------------------- #
# (9)-(10) missing / empty collection                                        #
# --------------------------------------------------------------------------- #
async def test_search_on_a_nonexistent_collection_returns_no_hits(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    """Collections are created lazily at first index, so a workspace nobody
    has indexed into has none -- and its first query is a normal "no
    documents yet", not a 500. This is also the live guard on the wording
    ``_is_missing_collection`` matches: if Qdrant ever rewords its 404, this
    test goes red instead of production silently regressing to
    ``common.internal``.

    ``qdrant_collection`` hands out a fresh unique name; this test
    deliberately never creates it."""
    assert await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=5) == []
    assert (
        await qdrant_store.search_sparse(
            qdrant_collection, SparseVector(indices=[1], values=[1.0]), k=5
        )
        == []
    )
    await qdrant_store.delete(qdrant_collection, [new_uuid7()])  # silent no-op

    with pytest.raises(AppError) as excinfo:  # writes still fail loudly
        await qdrant_store.upsert(
            qdrant_collection,
            [VectorPoint(id=new_uuid7(), vector=[1.0, 0.0, 0.0, 0.0], payload={})],
        )

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


async def test_search_on_an_empty_collection_returns_no_hits(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_collection(qdrant_collection, dim=_DIM)

    assert await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=5) == []


# --------------------------------------------------------------------------- #
# (11) hybrid collection provisions the sparse "text" leg with IDF           #
# --------------------------------------------------------------------------- #
async def test_ensure_hybrid_collection_provisions_sparse_text_with_idf(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)

    info = await qdrant_client.get_collection(qdrant_collection)
    sparse = info.config.params.sparse_vectors

    assert sparse is not None
    assert sparse["text"].modifier == models.Modifier.IDF


# --------------------------------------------------------------------------- #
# (12) one point, two legs, one payload, one delete                          #
# --------------------------------------------------------------------------- #
async def test_hybrid_point_shares_one_payload_and_is_removed_by_one_delete(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)
    point_id = new_uuid7()
    sparse = SparseVector(indices=[1, 2], values=[0.5, 0.25])
    await qdrant_store.upsert(
        qdrant_collection,
        [
            VectorPoint(
                id=point_id,
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={"label": "two-legged"},
                sparse=sparse,
            )
        ],
    )

    dense_hits = await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=1)
    sparse_hits = await qdrant_store.search_sparse(qdrant_collection, sparse, k=1)
    assert len(dense_hits) == 1
    assert len(sparse_hits) == 1
    assert dense_hits[0].id == sparse_hits[0].id == point_id
    assert dense_hits[0].payload == sparse_hits[0].payload == {"label": "two-legged"}

    await qdrant_store.delete(qdrant_collection, [point_id])

    assert await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=1) == []
    assert await qdrant_store.search_sparse(qdrant_collection, sparse, k=1) == []


# --------------------------------------------------------------------------- #
# (13) search_sparse ranks by term overlap                                   #
# --------------------------------------------------------------------------- #
async def test_search_sparse_ranks_the_term_overlapping_point_first(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)
    id_match, id_other = new_uuid7(), new_uuid7()
    await qdrant_store.upsert(
        qdrant_collection,
        [
            VectorPoint(
                id=id_match,
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={},
                sparse=SparseVector(indices=[10, 20], values=[1.0, 1.0]),
            ),
            VectorPoint(
                id=id_other,
                vector=[0.0, 1.0, 0.0, 0.0],
                payload={},
                sparse=SparseVector(indices=[30, 40], values=[1.0, 1.0]),
            ),
        ],
    )

    hits = await qdrant_store.search_sparse(
        qdrant_collection, SparseVector(indices=[10], values=[1.0]), k=5
    )

    assert len(hits) == 1
    assert hits[0].id == id_match


# --------------------------------------------------------------------------- #
# (14) dense search keeps working on a hybrid collection                     #
# --------------------------------------------------------------------------- #
async def test_dense_search_still_works_on_a_hybrid_collection(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)
    id_a = new_uuid7()
    await qdrant_store.upsert(
        qdrant_collection,
        [
            VectorPoint(
                id=id_a,
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={"label": "dense-still-works"},
                sparse=SparseVector(indices=[1], values=[1.0]),
            )
        ],
    )

    hits = await qdrant_store.search(qdrant_collection, [1.0, 0.0, 0.0, 0.0], k=1)

    assert len(hits) == 1
    assert hits[0].id == id_a
    assert hits[0].payload == {"label": "dense-still-works"}


# --------------------------------------------------------------------------- #
# (14b) with_vectors returns the DENSE vector on both legs (P-23, §3.9)      #
# --------------------------------------------------------------------------- #
async def test_with_vectors_returns_the_dense_vector_on_both_legs(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    """MMR's input, proven against the real server (rag-retrieval-plan.md
    §3.9, ``P-23``, decision س-20). This is exactly the assertion no unit test
    can make: a HYBRID collection has NAMED vectors, so Qdrant answers
    ``with_vectors=True`` with a dict keyed by vector name -- not the bare
    list a plain collection returns -- and which key holds the dense leg is
    the server's convention, not ours. The sparse leg is asked too, because a
    candidate only BM25 found still has to be diversity-checked.

    The default is asserted in the same test: leaving ``with_vectors`` alone
    must return NO vector, so nothing but MMR pays §3.9's declared network
    price."""
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)
    point_id = new_uuid7()
    dense = [1.0, 0.0, 0.0, 0.0]
    sparse = SparseVector(indices=[1, 2], values=[0.5, 0.25])
    await qdrant_store.upsert(
        qdrant_collection,
        [VectorPoint(id=point_id, vector=dense, payload={"label": "mmr"}, sparse=sparse)],
    )

    dense_hits = await qdrant_store.search(qdrant_collection, dense, k=1, with_vectors=True)
    sparse_hits = await qdrant_store.search_sparse(
        qdrant_collection, sparse, k=1, with_vectors=True
    )

    assert dense_hits[0].vector == pytest.approx(dense)
    assert sparse_hits[0].vector == pytest.approx(dense)

    # ... and the default costs nothing on the wire.
    assert (await qdrant_store.search(qdrant_collection, dense, k=1))[0].vector is None
    assert (await qdrant_store.search_sparse(qdrant_collection, sparse, k=1))[0].vector is None


# --------------------------------------------------------------------------- #
# (15) search_sparse respects flt just like the dense leg                    #
# --------------------------------------------------------------------------- #
async def test_search_sparse_filter_isolates_by_workspace_id(
    qdrant_store: QdrantVectorStore, qdrant_collection: str
) -> None:
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)
    ws_a, ws_b = new_uuid7(), new_uuid7()
    shared_terms = SparseVector(indices=[5], values=[1.0])
    await qdrant_store.upsert(
        qdrant_collection,
        [
            VectorPoint(
                id=new_uuid7(),
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={"workspace_id": ws_a},
                sparse=shared_terms,
            ),
            VectorPoint(
                id=new_uuid7(),
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={"workspace_id": ws_b},
                sparse=shared_terms,
            ),
        ],
    )

    hits = await qdrant_store.search_sparse(
        qdrant_collection, shared_terms, k=10, flt={"workspace_id": ws_a}
    )

    assert len(hits) == 1
    assert hits[0].payload["workspace_id"] == ws_a


# --------------------------------------------------------------------------- #
# (16) payload indexes land on the real server, with the real tenant flag    #
# --------------------------------------------------------------------------- #
async def test_ensure_hybrid_collection_provisions_its_payload_indexes(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, qdrant_collection: str
) -> None:
    """The unit test pins which calls the adapter MAKES; this pins what the
    server is left holding -- the two are not the same claim, and until this
    step the project had no payload index anywhere at all (spaces plan §2-ب).

    ``is_tenant`` read back off the live schema is the point: it is the flag
    that makes Qdrant lay ``space``'s points out together on disk, and a
    ``KeywordIndexParams`` built without it would provision an index that
    looks identical from the call site.
    """
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)

    info = await qdrant_client.get_collection(qdrant_collection)
    schema = info.payload_schema

    assert set(schema) == {"space", "workspace_id", "document_id"}
    assert schema["space"].params is not None
    assert schema["space"].params.is_tenant is True
    for plain in ("workspace_id", "document_id"):
        assert schema[plain].params is not None
        assert schema[plain].params.is_tenant is False


async def test_ensure_payload_index_is_idempotent(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, qdrant_collection: str
) -> None:
    """Re-asserting an existing index is a success, not a conflict -- which
    is what lets ``ensure_hybrid_collection`` call it unconditionally and
    what will let the step-16 operational pass run over every collection
    without asking which ones already have it."""
    await qdrant_store.ensure_hybrid_collection(qdrant_collection, dim=_DIM)

    await qdrant_store.ensure_payload_index(qdrant_collection, "space", tenant=True)

    info = await qdrant_client.get_collection(qdrant_collection)
    assert info.payload_schema["space"].params is not None
    assert info.payload_schema["space"].params.is_tenant is True


async def test_ensure_payload_index_on_a_never_created_collection_fails_loudly(
    qdrant_store: QdrantVectorStore,
) -> None:
    """Provisioning does NOT get ``search``'s empty-result treatment: asking
    to index a collection that was never created is a fault, and swallowing
    it would report success over a collection that stays unindexed."""
    with pytest.raises(AppError) as excinfo:
        await qdrant_store.ensure_payload_index(f"never-created-{new_uuid7()}", "space")

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


# --------------------------------------------------------------------------- #
# (17) failure translation: never a raw qdrant_client exception              #
# --------------------------------------------------------------------------- #
async def test_connection_failure_surfaces_as_common_internal() -> None:
    # A closed local port refuses instantly (ECONNREFUSED) -- no timeout wait.
    dead_client = create_qdrant_client(QdrantSettings(url="http://127.0.0.1:6399"))
    store = QdrantVectorStore(dead_client)
    try:
        with pytest.raises(AppError) as excinfo:
            await store.search("irrelevant-collection", [1.0, 0.0, 0.0, 0.0], k=1)
        assert excinfo.value.code == "common.internal"
        assert excinfo.value.status == 500
    finally:
        await dead_client.close()
