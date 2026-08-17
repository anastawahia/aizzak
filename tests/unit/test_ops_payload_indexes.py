"""Unit tests for ``app.ops.payload_indexes`` -- the one-off pass that gives
payload indexes to Qdrant collections created before the adapter provisioned
them (docs/spaces-backend-plan.md §5-ب, step 16).

No marker, no Docker, no network: a stub client that answers
``get_collections``/``get_collection`` from a dict of collection -> payload
schema and records every ``create_payload_index``, built out of the REAL
``qdrant_client.models`` value objects so the schema this code reads back is
shaped exactly like the server's (``test_qdrant_mapping.py``'s own
convention).

What each test pins is a distinct way the pass could look successful and be
wrong: indexing collections it must leave alone, writing during a dry run,
asserting the wrong keys or the wrong tenant flag, re-indexing a collection
that is already done, leaving a mis-flagged tenant key as it found it, and
-- the failure this whole module exists for -- reporting a collection as
provisioned without reading the server back.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.infrastructure.vector.qdrant_store import HYBRID_PAYLOAD_INDEXES, QdrantVectorStore
from app.ops.payload_indexes import (
    backfill_all,
    backfill_collection,
    list_target_collections,
    pending_indexes,
)

_EXPECTED = {("workspace_id", False), ("document_id", False), ("space", True)}
_KN = "kn-11111111-1111-7111-8111-111111111111"
_MEM = "mem-11111111-1111-7111-8111-111111111111"


def _schema(**fields: bool | None) -> dict[str, models.PayloadIndexInfo]:
    """One collection's ``payload_schema``. ``None`` builds the params-less
    shape Qdrant returns for an index created through its older
    ``field_schema="keyword"`` shorthand (measured live, 1.13)."""
    return {
        field: models.PayloadIndexInfo(
            data_type=models.PayloadSchemaType.KEYWORD,
            params=(
                None
                if tenant is None
                else models.KeywordIndexParams(
                    type=models.KeywordIndexType.KEYWORD, is_tenant=tenant
                )
            ),
            points=0,
        )
        for field, tenant in fields.items()
    }


class _StubClient:
    """Stand-in for ``AsyncQdrantClient`` over an in-memory schema map.

    ``apply`` decides whether a recorded ``create_payload_index`` also
    UPDATES that map: ``False`` reproduces a server that answers the call and
    keeps the collection unindexed -- the exact silence the read-back exists
    to catch.
    """

    def __init__(
        self,
        schemas: dict[str, dict[str, models.PayloadIndexInfo]],
        *,
        apply: bool = True,
    ) -> None:
        self._schemas = schemas
        self._apply = apply
        self.indexes: list[tuple[str, str, bool]] = []

    async def get_collections(self) -> models.CollectionsResponse:
        return models.CollectionsResponse(
            collections=[models.CollectionDescription(name=name) for name in self._schemas]
        )

    async def get_collection(self, collection_name: str) -> Any:
        # A namespace rather than a real ``models.CollectionInfo``: that model
        # demands a whole ``CollectionConfig`` (vectors, HNSW, optimizers,
        # quantization) that nothing on this path reads. The values INSIDE
        # the schema are the real models, which is the part the adapter
        # actually traverses.
        return SimpleNamespace(payload_schema=self._schemas[collection_name])

    async def create_payload_index(self, **kwargs: Any) -> None:
        collection = str(kwargs["collection_name"])
        field = str(kwargs["field_name"])
        tenant = bool(kwargs["field_schema"].is_tenant)
        self.indexes.append((collection, field, tenant))
        if self._apply:
            self._schemas[collection].update(_schema(**{field: tenant}))


def _client(
    schemas: dict[str, dict[str, models.PayloadIndexInfo]], *, apply: bool = True
) -> tuple[_StubClient, QdrantVectorStore, AsyncQdrantClient]:
    stub = _StubClient(schemas, apply=apply)
    typed = cast(AsyncQdrantClient, stub)
    return stub, QdrantVectorStore(typed), typed


# --------------------------------------------------------------------------- #
# which collections the pass is allowed to touch                              #
# --------------------------------------------------------------------------- #
async def test_only_knowledge_collections_are_targeted() -> None:
    """``mem-`` is provisioned through the narrower ``VectorStore`` contract,
    which asks for no index and carries no ``space`` key (spaces plan §3.4):
    a pass that indexed every collection it found would invent a policy no
    other code path in this repository holds."""
    stub, _, client = _client({_MEM: _schema(), "aizzak-test-x": _schema(), _KN: _schema()})

    assert await list_target_collections(client) == [_KN]
    assert stub.indexes == []


async def test_narrowing_to_a_collection_outside_the_target_set_is_refused() -> None:
    """An operator who names a specific collection must not get an empty,
    successful-looking run -- which is what "silently index nothing" would
    look like on the terminal."""
    _, _, client = _client({_KN: _schema(), _MEM: _schema()})

    with pytest.raises(ValueError) as excinfo:
        await list_target_collections(client, collection=_MEM)

    assert _MEM in str(excinfo.value)


# --------------------------------------------------------------------------- #
# what the pass writes                                                        #
# --------------------------------------------------------------------------- #
async def test_an_unindexed_collection_gains_every_key_with_its_own_tenant_flag() -> None:
    """The §5-ب starting state: a collection created before the adapter
    indexed anything carries ZERO payload indexes (spaces plan §2-ب --
    ``create_payload_index`` was never called anywhere in this project).

    Asserted as ``(field, is_tenant)`` pairs so both halves are pinned by one
    test: dropping a key fails it, and so does spending the tenant flag on
    the wrong one.
    """
    stub, store, client = _client({_KN: _schema()})

    [result] = await backfill_all(store, client)

    assert {(field, tenant) for _, field, tenant in stub.indexes} == _EXPECTED
    assert {collection for collection, _, _ in stub.indexes} == {_KN}
    assert set(result.pending) == {field for field, _ in HYBRID_PAYLOAD_INDEXES}
    assert result.complete is True


async def test_a_dry_run_writes_nothing_and_still_reports_the_whole_gap() -> None:
    """The preview an operator runs first (module docstring) must be exactly
    that: same read-only predicate, no ``create_payload_index`` at all, and
    ``complete`` still False afterwards because nothing was done."""
    stub, store, client = _client({_KN: _schema()})

    [result] = await backfill_all(store, client, dry_run=True)

    assert stub.indexes == []
    assert set(result.pending) == {field for field, _ in HYBRID_PAYLOAD_INDEXES}
    assert result.indexed == ()
    assert result.complete is False


async def test_a_collection_that_already_holds_every_index_is_left_alone() -> None:
    """Re-running the pass is the normal case (a second sweep after fixing a
    fault, module docstring), and it must cost nothing: nothing pending,
    nothing written, still complete."""
    stub, store, client = _client({_KN: _schema(workspace_id=False, document_id=False, space=True)})

    [result] = await backfill_all(store, client)

    assert stub.indexes == []
    assert result.pending == ()
    assert result.complete is True


async def test_a_tenant_key_indexed_without_its_flag_is_re_asserted() -> None:
    """``space`` indexed as a plain keyword is NOT done: without
    ``is_tenant`` Qdrant does not lay its points out together, which is the
    only thing that index was created to buy -- and from the call site the
    two are indistinguishable. Re-asserting replaces it (measured live), so
    the repair needs no drop."""
    stub, store, client = _client(
        {_KN: _schema(workspace_id=False, document_id=False, space=False)}
    )

    [result] = await backfill_all(store, client)

    assert stub.indexes == [(_KN, "space", True)]
    assert result.pending == ("space",)
    assert result.complete is True


async def test_a_params_less_legacy_index_counts_as_untenanted() -> None:
    """Qdrant returns ``params=None`` for an index created through its older
    ``field_schema="keyword"`` shorthand: reading that as "tenant flag
    absent" is what makes the pass repair it instead of walking past it."""
    _, _, client = _client({_KN: _schema(workspace_id=False, document_id=False, space=None)})

    assert await pending_indexes(client, _KN) == ("space",)


# --------------------------------------------------------------------------- #
# what the pass reports                                                       #
# --------------------------------------------------------------------------- #
async def test_a_collection_the_server_did_not_actually_index_is_reported_incomplete() -> None:
    """The failure this module is built to refuse: every call succeeded, and
    the collection is still unindexed. ``complete`` is read back off the
    server, never inferred from the calls made -- and it is what the CLI's
    non-zero exit keys off."""
    stub, store, client = _client({_KN: _schema()}, apply=False)

    result = await backfill_collection(store, client, _KN)

    assert {(field, tenant) for _, field, tenant in stub.indexes} == _EXPECTED
    assert result.indexed == ()
    assert result.complete is False
