"""Unit tests for the memory domain — value objects, aggregate, invariants.
Pure: no infrastructure, no ports."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.memory.domain.entities import MemoryItem
from app.modules.memory.domain.errors import InvalidMemoryInput, MemoryError
from app.modules.memory.domain.value_objects import AgentKey, MemoryKind, VectorRef

_TS = datetime(2026, 7, 10, tzinfo=UTC)


def _item(*, deleted_at: datetime | None = None) -> MemoryItem:
    return MemoryItem(
        id="m1",
        workspace_id="w1",
        agent_key=AgentKey("rag-agent"),
        kind=MemoryKind.SEMANTIC,
        content="the user prefers tea over coffee",
        vector_ref=None,
        salience=0.0,
        created_at=_TS,
        deleted_at=deleted_at,
    )


# --------------------------------------------------------------------------- #
# value objects                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["rag-agent", "RAG_Agent ", "a", "a1-2_3", "x" * 64])
def test_agent_key_accepts_valid(value: str) -> None:
    assert AgentKey(value).value == value.strip().lower()


@pytest.mark.parametrize("value", ["", "   ", "-agent", "_agent", "Agent Key", "x" * 65, "agent!"])
def test_agent_key_rejects_invalid(value: str) -> None:
    with pytest.raises(InvalidMemoryInput):
        AgentKey(value)


def test_vector_ref_accepts_valid() -> None:
    ref = VectorRef(collection="mem-w1", point_id="p1")
    assert ref.collection == "mem-w1"
    assert ref.point_id == "p1"


def test_vector_ref_rejects_empty_collection() -> None:
    with pytest.raises(InvalidMemoryInput):
        VectorRef(collection="", point_id="p1")


def test_vector_ref_rejects_empty_point_id() -> None:
    with pytest.raises(InvalidMemoryInput):
        VectorRef(collection="mem-w1", point_id="")


def test_memory_kind_values() -> None:
    assert MemoryKind.SEMANTIC.value == "semantic"
    assert MemoryKind.EPISODIC.value == "episodic"


# --------------------------------------------------------------------------- #
# MemoryItem aggregate                                                        #
# --------------------------------------------------------------------------- #
def test_attach_vector_sets_ref() -> None:
    item = _item()
    ref = VectorRef(collection="mem-w1", point_id="p1")
    item.attach_vector(ref)
    assert item.vector_ref is ref


def test_forget_sets_deleted_at() -> None:
    item = _item()
    later = datetime(2026, 7, 11, tzinfo=UTC)
    item.forget(later)
    assert item.deleted_at == later


def test_forget_is_idempotent() -> None:
    first = datetime(2026, 7, 11, tzinfo=UTC)
    item = _item(deleted_at=first)
    item.forget(datetime(2026, 7, 12, tzinfo=UTC))
    assert item.deleted_at == first  # unchanged — no-op on an already-forgotten item


def test_attach_vector_raises_after_forget() -> None:
    item = _item(deleted_at=_TS)
    with pytest.raises(MemoryError):
        item.attach_vector(VectorRef(collection="mem-w1", point_id="p1"))
