"""Unit tests for the Qdrant adapter's pure translation helpers
(``_to_distance``, ``_build_filter``, ``_is_missing_collection`` --
``infrastructure/vector/qdrant_store.py``, Phase 2.5) and for the one
error-policy branch they drive: a collection that was never created reads as
EMPTY, everything else still fails loudly. No marker, no Docker, no network:
plain functions over ``qdrant_client.models`` value objects, plus a
stub client whose every call raises a chosen driver exception.

Since the spaces plan's step 9 this file also pins the adapter's PROVISIONING
policy over a recording stub client: which payload keys a new hybrid
collection is indexed on, which single one carries Qdrant's ``is_tenant``,
that a lost create-race still ends indexed, and that an already-existing
collection deliberately gains nothing (the plan's §5-ب, made executable).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.common.client_exceptions import QdrantException
from qdrant_client.http.exceptions import UnexpectedResponse

from app.framework.errors import AppError, ValidationError
from app.framework.ports.vector_store import SparseVector, VectorPoint
from app.infrastructure.vector.qdrant_store import (
    QdrantVectorStore,
    _build_filter,
    _is_missing_collection,
    _to_distance,
)


# --------------------------------------------------------------------------- #
# _to_distance                                                                #
# --------------------------------------------------------------------------- #
def test_to_distance_maps_known_names_case_insensitively() -> None:
    assert _to_distance("cosine") == models.Distance.COSINE
    assert _to_distance("COSINE") == models.Distance.COSINE
    assert _to_distance("euclid") == models.Distance.EUCLID
    assert _to_distance("Euclidean") == models.Distance.EUCLID
    assert _to_distance("dot") == models.Distance.DOT
    assert _to_distance("DOT") == models.Distance.DOT
    assert _to_distance("manhattan") == models.Distance.MANHATTAN
    assert _to_distance("Manhattan") == models.Distance.MANHATTAN


def test_to_distance_unknown_name_raises_validation_error() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _to_distance("hamming")

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# _build_filter                                                               #
# --------------------------------------------------------------------------- #
def test_build_filter_none_or_empty_dict_is_no_filter() -> None:
    assert _build_filter(None) is None
    assert _build_filter({}) is None


def test_build_filter_scalar_values_become_must_field_conditions() -> None:
    result = _build_filter({"workspace_id": "ws-1", "is_active": True, "version": 3})

    assert result == models.Filter(
        must=[
            models.FieldCondition(key="workspace_id", match=models.MatchValue(value="ws-1")),
            models.FieldCondition(key="is_active", match=models.MatchValue(value=True)),
            models.FieldCondition(key="version", match=models.MatchValue(value=3)),
        ]
    )


def test_build_filter_list_value_becomes_match_any() -> None:
    result = _build_filter({"tag": ["a", "b"]})

    assert result == models.Filter(
        must=[models.FieldCondition(key="tag", match=models.MatchAny(any=["a", "b"]))]
    )


def test_build_filter_heterogeneous_or_float_list_raises_validation_error() -> None:
    """R6 guard (verifier follow-up, 2.5 step 3): a mixed-type or
    float-bearing list would make Qdrant's own ``MatchAny`` raise a *pydantic*
    ``ValidationError`` from inside the search call -- a foreign exception the
    adapter must never leak. Rejected up front with the framework's
    ``ValidationError`` instead (same DD-04 fail-loudly rationale)."""
    for bad in ([1, "two", 3.0], [1.5, 2.5], ["a", 1]):
        with pytest.raises(ValidationError) as excinfo:
            _build_filter({"tag": bad})

        assert excinfo.value.code == "common.validation_error"
        assert excinfo.value.status == 422


def test_build_filter_unsupported_value_type_raises_validation_error() -> None:
    """DD-04 guard: an unsupported shape (here a nested ``dict``, also
    covering ``None``/``float`` per the same branch) must fail loudly rather
    than being silently dropped from the filter -- a silently-dropped
    ``workspace_id`` condition would be a tenant-isolation bypass."""
    with pytest.raises(ValidationError) as excinfo:
        _build_filter({"workspace_id": {"nested": "not-a-scalar"}})

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# missing collection: reads are empty, everything else still fails loudly     #
# --------------------------------------------------------------------------- #
_DIM_VECTOR = [1.0, 0.0, 0.0, 0.0]


def _response(status_code: int, body: bytes) -> UnexpectedResponse:
    return UnexpectedResponse(
        status_code=status_code, reason_phrase="", content=body, headers=cast(Any, {})
    )


def _missing_collection() -> UnexpectedResponse:
    """Qdrant's literal 404 for a workspace nobody has indexed into yet
    (measured against a live server; the collection is created lazily at
    first index, so it does not exist until then)."""
    return _response(
        404,
        b'{"status":{"error":"Not found: Collection `kn-ws-1` doesn\'t exist!"},"time":0.0}',
    )


class _RaisingClient:
    """Stand-in for ``AsyncQdrantClient`` whose every call raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def query_points(self, **_kwargs: object) -> object:
        raise self._exc

    async def create_payload_index(self, **_kwargs: object) -> None:
        raise self._exc

    async def upsert(self, **_kwargs: object) -> None:
        raise self._exc

    async def delete(self, **_kwargs: object) -> None:
        raise self._exc


def _store(exc: Exception) -> QdrantVectorStore:
    return QdrantVectorStore(cast(AsyncQdrantClient, _RaisingClient(exc)))


def test_is_missing_collection_recognises_qdrants_own_404() -> None:
    assert _is_missing_collection(_missing_collection()) is True


def test_is_missing_collection_is_false_for_every_other_failure() -> None:
    """The downgrade must be narrow: a 404 about something other than a
    collection, any other status, and non-HTTP driver failures all stay
    ``AppError`` so a broken store never masquerades as an empty one."""
    others: list[Exception] = [
        _response(404, b'{"status":{"error":"Not found: No point with id 7"},"time":0.0}'),
        _response(500, b'{"status":{"error":"Service internal error"},"time":0.0}'),
        _response(400, b'{"status":{"error":"Collection `kn-ws-1` bad request"},"time":0.0}'),
        QdrantException("connection refused"),
    ]
    for exc in others:
        assert _is_missing_collection(exc) is False


async def test_search_on_a_missing_collection_returns_no_hits() -> None:
    """The rag_agent regression: the first query in a never-indexed
    workspace is a normal "no documents yet", not a 500."""
    assert await _store(_missing_collection()).search("kn-ws-1", _DIM_VECTOR, k=5) == []


async def test_search_sparse_on_a_missing_collection_returns_no_hits() -> None:
    sparse = SparseVector(indices=[1, 2], values=[1.0, 1.0])
    assert await _store(_missing_collection()).search_sparse("kn-ws-1", sparse, k=5) == []


async def test_delete_on_a_missing_collection_is_a_silent_noop() -> None:
    await _store(_missing_collection()).delete("kn-ws-1", ["00000000-0000-0000-0000-000000000001"])


async def test_upsert_on_a_missing_collection_still_raises_common_internal() -> None:
    """``upsert``'s caller must have provisioned the collection first, so a
    missing one there is a real fault -- swallowing it would drop data."""
    point = VectorPoint(id="00000000-0000-0000-0000-000000000001", vector=_DIM_VECTOR, payload={})
    with pytest.raises(AppError) as excinfo:
        await _store(_missing_collection()).upsert("kn-ws-1", [point])

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


@pytest.mark.parametrize(
    "exc",
    [
        _response(500, b'{"status":{"error":"Service internal error"},"time":0.0}'),
        _response(404, b'{"status":{"error":"Not found: No point with id 7"},"time":0.0}'),
        QdrantException("connection refused"),
    ],
)
async def test_other_qdrant_failures_still_surface_as_common_internal(exc: Exception) -> None:
    for call in (
        lambda store: store.search("kn-ws-1", _DIM_VECTOR, k=5),
        lambda store: store.search_sparse("kn-ws-1", SparseVector(indices=[1], values=[1.0]), k=5),
        lambda store: store.delete("kn-ws-1", ["00000000-0000-0000-0000-000000000001"]),
    ):
        with pytest.raises(AppError) as excinfo:
            await call(_store(exc))

        assert excinfo.value.code == "common.internal"
        assert excinfo.value.status == 500


# --------------------------------------------------------------------------- #
# payload indexes: what a NEW hybrid collection is provisioned with           #
# (spaces plan step 9 / §3.4)                                                 #
# --------------------------------------------------------------------------- #
# `(field, is_tenant)` -- the whole policy, in the order it is asserted as a
# SET: the two plain lookup keys, and the one ownership axis Qdrant is asked
# to lay out on disk.
_EXPECTED_INDEXES = {("workspace_id", False), ("document_id", False), ("space", True)}


class _ProvisioningClient:
    """Stand-in for ``AsyncQdrantClient`` that records provisioning calls.

    ``exists`` decides which branch ``ensure_hybrid_collection`` takes;
    ``create_raises`` injects the lost-create-race 409 the adapter swallows.
    """

    def __init__(self, *, exists: bool = False, create_raises: Exception | None = None) -> None:
        self._exists = exists
        self._create_raises = create_raises
        self.created: list[str] = []
        self.indexes: list[tuple[str, str, bool]] = []

    async def collection_exists(self, collection_name: str) -> bool:
        return self._exists

    async def create_collection(self, **kwargs: Any) -> None:
        if self._create_raises is not None:
            raise self._create_raises
        self.created.append(str(kwargs["collection_name"]))

    async def create_payload_index(self, **kwargs: Any) -> None:
        schema = kwargs["field_schema"]
        self.indexes.append(
            (str(kwargs["collection_name"]), str(kwargs["field_name"]), bool(schema.is_tenant))
        )


async def test_a_new_hybrid_collection_is_provisioned_with_its_payload_indexes() -> None:
    """Every key ``_build_filter`` is ever handed gets an index, and the
    tenant flag is spent on ``space`` ALONE.

    Asserted as a set of ``(field, is_tenant)`` pairs, so both halves of the
    policy are pinned by one test: dropping a key fails it, and so does
    flagging (or forgetting to flag) the wrong one as the tenant axis.
    """
    client = _ProvisioningClient()
    store = QdrantVectorStore(cast(AsyncQdrantClient, client))

    await store.ensure_hybrid_collection("kn-ws-1", dim=4)

    assert client.created == ["kn-ws-1"]
    assert {(field, tenant) for _, field, tenant in client.indexes} == _EXPECTED_INDEXES
    assert {collection for collection, _, _ in client.indexes} == {"kn-ws-1"}


async def test_an_existing_collection_gains_no_payload_index_here() -> None:
    """The §5-ب detector, as a test rather than a note in a plan.

    ``ensure_hybrid_collection`` returns early for a collection that already
    exists -- so every collection created before this code shipped stays
    unindexed until the one-off operational pass (step 16) runs. This test
    exists so that limitation is stated somewhere executable: if it ever
    starts failing, the operational task has become unnecessary, and the plan
    is the thing that is now wrong.
    """
    client = _ProvisioningClient(exists=True)
    store = QdrantVectorStore(cast(AsyncQdrantClient, client))

    await store.ensure_hybrid_collection("kn-ws-1", dim=4)

    assert client.created == []
    assert client.indexes == []


async def test_a_lost_create_race_still_provisions_the_payload_indexes() -> None:
    """A concurrent caller won ``create_collection`` (409, swallowed) -- the
    loser must still assert the indexes, or the interleaving in which the
    winner's own call has not reached them yet leaves the collection indexed
    by nobody. Re-creating an index is idempotent, so the cost is a no-op."""
    client = _ProvisioningClient(create_raises=_response(409, b'{"status":{"error":"exists"}}'))
    store = QdrantVectorStore(cast(AsyncQdrantClient, client))

    await store.ensure_hybrid_collection("kn-ws-1", dim=4)

    assert {(field, tenant) for _, field, tenant in client.indexes} == _EXPECTED_INDEXES


async def test_ensure_payload_index_on_a_missing_collection_raises_common_internal() -> None:
    """Provisioning is the ``upsert`` policy, not the read paths' one: a
    collection that does not exist is a real fault here. Absorbing it would
    leave a collection silently unindexed forever."""
    with pytest.raises(AppError) as excinfo:
        await _store(_missing_collection()).ensure_payload_index("kn-ws-1", "space", tenant=True)

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500
