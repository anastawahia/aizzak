"""Unit tests for the Qdrant adapter's two pure translation helpers
(``_to_distance``, ``_build_filter`` -- ``infrastructure/vector/qdrant_store.py``,
Phase 2.5). No marker, no Docker, no network: plain functions over
``qdrant_client.models`` value objects.
"""

from __future__ import annotations

import pytest
from qdrant_client import models

from app.framework.errors import ValidationError
from app.infrastructure.vector.qdrant_store import _build_filter, _to_distance


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
