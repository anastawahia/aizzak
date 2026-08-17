"""Live-Qdrant tests for ``app.ops.payload_indexes`` (docs/spaces-backend-
plan.md §5-ب, step 16).

The unit suite pins which calls the pass MAKES; this pins what the real
server is left holding -- and, first of all, that the starting state the
whole step exists for is real: a collection created the way every collection
in this deployment was created before step 9 comes back with an EMPTY
``payload_schema``, and no write path will ever give it one.

Every collection here is created directly through the raw client, never
through ``ensure_hybrid_collection`` -- that method would provision the
indexes itself and this file would be testing nothing. The names are real
``kn-``/``mem-`` names over a throwaway UUID (the prefix is what the pass
selects on), and both are dropped in teardown so the shared local Qdrant
keeps no leftovers. The pass itself is always narrowed with ``collection=``
so a developer's own collections in that same Qdrant are never touched.

Runs against the real local Compose Qdrant (``tests/integration/conftest.py``)
and auto-skips via ``live_qdrant`` when it is unreachable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.framework.identifiers import new_uuid7
from app.infrastructure.vector.qdrant_store import QdrantVectorStore
from app.modules.knowledge.domain.collections import knowledge_collection
from app.modules.memory.domain.collections import memory_collection
from app.ops.payload_indexes import backfill_all, list_target_collections

pytestmark = [pytest.mark.live_qdrant]

_DIM = 4


@pytest.fixture
async def legacy_collections(qdrant_client: AsyncQdrantClient) -> AsyncIterator[tuple[str, str]]:
    """One ``kn-`` and one ``mem-`` collection for the same throwaway
    workspace id, created the pre-step-9 way: ``create_collection`` alone,
    with no payload index anywhere -- exactly what every collection in a
    deployment older than that code looks like."""
    workspace_id = new_uuid7()
    knowledge = knowledge_collection(workspace_id)
    memory = memory_collection(workspace_id)
    await qdrant_client.create_collection(
        collection_name=knowledge,
        vectors_config=models.VectorParams(size=_DIM, distance=models.Distance.COSINE),
        sparse_vectors_config={"text": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    await qdrant_client.create_collection(
        collection_name=memory,
        vectors_config=models.VectorParams(size=_DIM, distance=models.Distance.COSINE),
    )
    try:
        yield knowledge, memory
    finally:
        await qdrant_client.delete_collection(knowledge)
        await qdrant_client.delete_collection(memory)


async def test_a_pre_existing_collection_really_does_carry_no_payload_index(
    qdrant_client: AsyncQdrantClient, legacy_collections: tuple[str, str]
) -> None:
    """§5-ب's premise, measured rather than asserted in prose: this is the
    state every collection created before step 9 is in, and nothing on a
    write path will change it (``ensure_hybrid_collection`` returns early for
    a collection that exists)."""
    knowledge, _ = legacy_collections

    info = await qdrant_client.get_collection(knowledge)

    assert info.payload_schema == {}


async def test_the_pass_leaves_the_server_holding_every_index_with_the_right_flag(
    qdrant_client: AsyncQdrantClient,
    qdrant_store: QdrantVectorStore,
    legacy_collections: tuple[str, str],
) -> None:
    """The step itself: after one run the SERVER reports all three keys, with
    ``is_tenant`` spent on ``space`` alone. Read back off the live schema
    because a ``KeywordIndexParams`` built without the flag is identical from
    the call site -- and the flag is the whole reason ``space`` is indexed
    differently from the other two."""
    knowledge, _ = legacy_collections

    [result] = await backfill_all(qdrant_store, qdrant_client, collection=knowledge)

    assert set(result.pending) == {"workspace_id", "document_id", "space"}
    assert result.complete is True

    info = await qdrant_client.get_collection(knowledge)
    assert set(info.payload_schema) == {"workspace_id", "document_id", "space"}
    assert info.payload_schema["space"].params is not None
    assert info.payload_schema["space"].params.is_tenant is True
    for plain in ("workspace_id", "document_id"):
        assert info.payload_schema[plain].params is not None
        assert info.payload_schema[plain].params.is_tenant is False


async def test_a_dry_run_leaves_the_live_collection_exactly_as_it_found_it(
    qdrant_client: AsyncQdrantClient,
    qdrant_store: QdrantVectorStore,
    legacy_collections: tuple[str, str],
) -> None:
    """The preview an operator runs first: it names the whole gap and writes
    nothing -- verified against the server, not against the tool's own
    report."""
    knowledge, _ = legacy_collections

    [result] = await backfill_all(qdrant_store, qdrant_client, collection=knowledge, dry_run=True)

    assert set(result.pending) == {"workspace_id", "document_id", "space"}
    assert result.complete is False
    info = await qdrant_client.get_collection(knowledge)
    assert info.payload_schema == {}


async def test_a_second_run_is_a_no_op_against_the_live_server(
    qdrant_client: AsyncQdrantClient,
    qdrant_store: QdrantVectorStore,
    legacy_collections: tuple[str, str],
) -> None:
    """Re-running is how a pass interrupted by a fault is finished (module
    docstring's reason for having no per-collection error handling), so the
    second run must find nothing pending and leave the schema alone."""
    knowledge, _ = legacy_collections
    await backfill_all(qdrant_store, qdrant_client, collection=knowledge)

    [result] = await backfill_all(qdrant_store, qdrant_client, collection=knowledge)

    assert result.pending == ()
    assert result.complete is True
    info = await qdrant_client.get_collection(knowledge)
    assert set(info.payload_schema) == {"workspace_id", "document_id", "space"}


async def test_a_memory_collection_is_neither_targeted_nor_touched(
    qdrant_client: AsyncQdrantClient,
    qdrant_store: QdrantVectorStore,
    legacy_collections: tuple[str, str],
) -> None:
    """``mem-`` is provisioned through the narrower ``VectorStore`` contract,
    which asks for no index (spaces plan §3.4): it is absent from the
    discovered set, refused by name, and still unindexed after a full pass
    over everything the tool DOES discover."""
    knowledge, memory = legacy_collections

    targets = await list_target_collections(qdrant_client)
    assert knowledge in targets
    assert memory not in targets

    with pytest.raises(ValueError):
        await list_target_collections(qdrant_client, collection=memory)

    await backfill_all(qdrant_store, qdrant_client, collection=knowledge)
    info = await qdrant_client.get_collection(memory)
    assert info.payload_schema == {}
