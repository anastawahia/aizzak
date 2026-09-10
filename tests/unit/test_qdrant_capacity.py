"""Unit tests for the Qdrant capacity report (``app/ops/qdrant_capacity.py``,
capacity plan step 4.4).

Hermetic: everything here is the tool's PURE half -- how it picks which
tenants to probe, how it folds Qdrant's telemetry, and whether the query
shape it times is still the one ``retrieval.py`` issues. The measurements
themselves need a server with a corpus in it and are not unit-testable by
construction; what IS testable is that the tool measures the right thing, and
that is what these pin.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

import pytest

from app.framework.settings.settings import RetrievalSettings
from app.infrastructure.vector.qdrant_store import _TIMEOUT_S as _REQUEST_TIMEOUT_S
from app.modules.knowledge.domain.collections import knowledge_collection
from app.ops.qdrant_capacity import (
    _BULK_TIMEOUT_S,
    _DENSE_K,
    _SPARSE_K,
    SHADOW_COLLECTION,
    CollectionFacts,
    _perturb,
    _tenant_filter,
    fold_telemetry,
    parse_ef,
    percentile,
    select_bands,
)


def _facts(name: str, points: int) -> CollectionFacts:
    return CollectionFacts(
        name=name,
        points=points,
        indexed_vectors=0,
        segments=8,
        status="green",
        tenant_keys=("space",),
        indexed_keys=("document_id", "space", "workspace_id"),
        quantized=False,
    )


# --------------------------------------------------------------------------- #
# The shape being timed IS the shape the platform issues                      #
# --------------------------------------------------------------------------- #
def test_the_probe_depth_is_the_retrieval_paths_own_depth() -> None:
    """The guard that keeps an archived number meaningful. ``retrieval.py``
    computes its two depths from ``RetrievalSettings``; this tool hard-codes
    them so that a report always describes the SHIPPED shape. If a default
    ever moves, the numbers in ``capacity-status.md`` stop describing the
    platform -- and this test is the only thing that would say so."""
    tuning = RetrievalSettings()
    search_k = min(
        tuning.default_k * max(tuning.search_overfetch, tuning.mmr_overfetch),
        tuning.max_search_candidates,
    )
    assert search_k == _DENSE_K
    assert min(search_k, tuning.max_sparse_candidates) == _SPARSE_K


def test_a_probe_carries_the_tenant_filter_dd04_requires() -> None:
    flt = _tenant_filter("019f3020-59d6-7ee5-a6df-913e44c5ecf0")
    assert flt is not None
    assert flt.must is not None
    assert [condition.key for condition in flt.must] == ["workspace_id"]  # type: ignore[union-attr]


def test_a_corpus_without_workspace_payload_is_searched_unfiltered() -> None:
    """``None``, not a filter matching the empty string: a foreign corpus
    with no ``workspace_id`` payload would otherwise be measured through a
    condition that matches nothing, and every query would time an empty
    result."""
    assert _tenant_filter("") is None


def test_the_probe_query_is_near_the_point_it_was_drawn_from() -> None:
    """Not the point itself (that would measure an exact hit), and not a
    random direction either (a uniformly random 384-dimensional vector is
    near-orthogonal to every stored one and exits the graph in a few hops)."""
    source = [1.0] * 16
    moved = _perturb(source, random.Random(1), 0.15)

    assert moved != source
    assert all(abs(value - 1.0) < 1.0 for value in moved)


# --------------------------------------------------------------------------- #
# Percentiles                                                                 #
# --------------------------------------------------------------------------- #
def test_a_percentile_is_a_latency_that_actually_happened() -> None:
    """Nearest-rank, never interpolated: every number in this report is
    supposed to be a measurement, and an interpolated p95 is an average of
    two of them wearing a measurement's name."""
    values = [1.0, 2.0, 3.0, 4.0, 100.0]

    assert percentile(values, 95.0) == 100.0
    assert percentile(values, 50.0) == 3.0
    assert percentile(values, 100.0) == 100.0


def test_a_percentile_of_nothing_is_zero_not_an_error() -> None:
    assert percentile([], 95.0) == 0.0


# --------------------------------------------------------------------------- #
# Which tenants get probed                                                    #
# --------------------------------------------------------------------------- #
def test_the_probe_set_spans_the_size_distribution() -> None:
    """A uniform sample of a Zipf corpus is 95% tiny collections -- the
    cheapest query there is, reported as the platform's cost."""
    facts = [_facts(f"kn-{i}", points) for i, points in enumerate([170_000, 8_000, 900, 100])]

    picked = select_bands(facts, 2)

    assert [f.points for f in picked] == [170_000, 100]


def test_an_empty_collection_is_never_probed() -> None:
    """It has no point to draw a query from, and a search over nothing is
    the one latency that says nothing about anything."""
    facts = [_facts("kn-a", 0), _facts("kn-b", 10)]

    assert [f.name for f in select_bands(facts, 5)] == ["kn-b"]


def test_asking_for_more_bands_than_there_are_tenants_takes_them_all() -> None:
    facts = [_facts("kn-a", 5), _facts("kn-b", 3)]

    assert len(select_bands(facts, 99)) == 2
    assert select_bands(facts, 1) == [facts[0]]
    assert select_bands(facts, 0) == []


# --------------------------------------------------------------------------- #
# The ef sweep                                                                #
# --------------------------------------------------------------------------- #
def test_zero_means_the_collections_own_default_not_an_ef_of_zero() -> None:
    """``None`` is "send no ``search_params``", which is exactly what the
    shipped adapter does -- the baseline column every other cell is compared
    against."""
    assert parse_ef("0,64,128") == [None, 64, 128]
    assert parse_ef(" 256 ") == [256]
    assert parse_ef("") == []


# --------------------------------------------------------------------------- #
# The telemetry fold -- the number that decides ق-3                           #
# --------------------------------------------------------------------------- #
def _telemetry(*collections: dict[str, Any]) -> dict[str, Any]:
    return {"result": {"collections": {"collections": list(collections)}}}


def _collection(name: str, *segments: tuple[int, int, int]) -> dict[str, Any]:
    return {
        "id": name,
        "init_time_ms": 100,
        "shards": [
            {
                "local": {
                    "segments": [
                        {
                            "info": {
                                "num_deleted_vectors": deleted,
                                "vectors_size_bytes": 1_000,
                                "payloads_size_bytes": 500,
                                "vector_data": {
                                    "": {
                                        "num_vectors": dense,
                                        "num_indexed_vectors": indexed,
                                    },
                                    "text": {"num_indexed_vectors": dense},
                                },
                            }
                        }
                        for dense, indexed, deleted in segments
                    ]
                }
            }
        ],
    }


def test_the_fold_counts_the_DENSE_index_only() -> None:
    """⚠️ The reason this tool reads a 2.4 MB telemetry document instead of
    ``GET /collections/<name>``: that endpoint's ``indexed_vectors_count``
    adds the SPARSE index in, so a collection whose dense vectors are all
    brute-forced still reports a large number there. Measured live: 328,840
    "indexed" against 170,120 points, of which the dense half was 158,720."""
    facts = fold_telemetry(_telemetry(_collection("kn-a", (30_000, 30_000, 0), (5_000, 0, 2))))

    assert facts.dense_vectors == 35_000
    assert facts.dense_indexed == 30_000
    assert facts.brute_forced == 5_000
    assert facts.sparse_indexed == 35_000  # counted, and counted SEPARATELY
    assert facts.deleted_vectors == 2
    assert facts.segments == 2


def test_a_collection_with_no_hnsw_segment_is_reported_as_having_none() -> None:
    """The shipped layout's actual state: HNSW is built per SEGMENT above
    ``indexing_threshold``, so splitting a tenant across segments divides its
    corpus before the threshold applies."""
    facts = fold_telemetry(
        _telemetry(
            _collection("kn-big", (30_000, 30_000, 0)),
            _collection("kn-small", (2_000, 0, 0)),
        )
    )

    assert facts.collections == 2
    assert facts.collections_with_hnsw == 1


def test_the_fold_ignores_collections_outside_the_knowledge_prefix() -> None:
    """``mem-`` collections are provisioned through the narrower
    ``ensure_collection`` contract and carry no tenant payload index at all
    -- the same scoping ``app.ops.payload_indexes`` draws."""
    facts = fold_telemetry(
        _telemetry(_collection("kn-a", (10, 0, 0)), _collection("mem-a", (10, 0, 0)))
    )

    assert facts.collections == 1
    assert facts.dense_vectors == 10


def test_init_time_is_reported_as_a_total_and_a_worst_case() -> None:
    """Boot cost: the total is what the process pays, the worst single
    collection is what an operator can act on."""
    first = _collection("kn-a", (10, 0, 0))
    second = _collection("kn-b", (10, 0, 0))
    second["init_time_ms"] = 2_400

    facts = fold_telemetry(_telemetry(first, second))

    assert facts.init_ms_total == 2_500
    assert facts.init_ms_max == 2_400


# --------------------------------------------------------------------------- #
# The shadow                                                                  #
# --------------------------------------------------------------------------- #
def test_the_shadow_name_is_one_no_workspace_can_ever_produce() -> None:
    """It must be impossible for the application to reach the counterfactual
    by computing a collection name from a workspace id."""
    assert SHADOW_COLLECTION.startswith("kn-")
    assert knowledge_collection("019f3020-59d6-7ee5-a6df-913e44c5ecf0") != SHADOW_COLLECTION
    with pytest.raises(ValueError, match=r"badly formed"):
        uuid.UUID(SHADOW_COLLECTION.removeprefix("kn-"))


def test_the_bulk_timeout_is_not_the_request_paths_timeout() -> None:
    """The shadow build must outlive the fail-fast the request path wants.

    ``qdrant_store._TIMEOUT_S`` is 5 s on purpose -- a request-handling
    coroutine must not hang on a sick Qdrant (07-nfr is sub-second). The
    first full shadow build died on exactly that value at 952,500 points,
    because one 500-point upsert into a single million-point collection
    stopped fitting inside it. So the bulk tool needs its own, larger value,
    and this guard says the two numbers are not allowed to converge: if
    someone "unifies" them, either the request path starts hanging or the
    bulk load starts dying again.
    """
    assert _BULK_TIMEOUT_S > _REQUEST_TIMEOUT_S
