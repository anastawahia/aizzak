"""Live-Qdrant + live-MinIO tests for the two ``app.ops.purge`` (BE-ADM-014)
store primitives: ``drop_collection`` (``infrastructure/vector/
qdrant_store.py``) and ``delete_prefix`` (``infrastructure/storage/
minio_storage.py``). Uses the SAME ``live_qdrant``/``live_minio`` markers and
fixtures ``test_qdrant_store.py``/``test_minio_storage.py`` already
establish -- never a dedicated harness of its own. Neither function is a
port method (no contract-conformance obligation), so this file's only job is
proving their own, narrower contracts against the real services.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from minio import Minio
from qdrant_client import AsyncQdrantClient, models

from app.framework.identifiers import new_uuid7
from app.infrastructure.storage.minio_storage import delete_prefix
from app.infrastructure.vector.qdrant_store import drop_collection
from tests.integration.conftest import LiveMinio

pytestmark = [pytest.mark.live_qdrant, pytest.mark.live_minio]

_DIM = 4


# --------------------------------------------------------------------------- #
# drop_collection (Qdrant)                                                    #
# --------------------------------------------------------------------------- #
async def test_drop_collection_removes_an_existing_collection(
    qdrant_client: AsyncQdrantClient, qdrant_collection: str
) -> None:
    await qdrant_client.create_collection(
        collection_name=qdrant_collection,
        vectors_config=models.VectorParams(size=_DIM, distance=models.Distance.COSINE),
    )
    assert await qdrant_client.collection_exists(qdrant_collection) is True

    existed = await drop_collection(qdrant_client, qdrant_collection)

    assert existed is True
    assert await qdrant_client.collection_exists(qdrant_collection) is False


async def test_drop_collection_is_a_silent_no_op_on_a_name_that_never_existed(
    qdrant_client: AsyncQdrantClient,
) -> None:
    name = f"aizzak-test-never-created-{new_uuid7()}"
    existed = await drop_collection(qdrant_client, name)
    assert existed is False


# --------------------------------------------------------------------------- #
# delete_prefix (MinIO)                                                       #
# --------------------------------------------------------------------------- #
async def _put(client: Minio, bucket: str, key: str) -> None:
    def _do() -> None:
        client.put_object(bucket, key, io.BytesIO(b"x"), length=1, content_type="text/plain")

    await asyncio.to_thread(_do)


async def _list_keys(client: Minio, bucket: str, prefix: str) -> list[str]:
    def _do() -> list[str]:
        return [
            obj.object_name
            for obj in client.list_objects(bucket, prefix=prefix, recursive=True)
            if obj.object_name is not None
        ]

    return await asyncio.to_thread(_do)


async def _sweep(client: Minio, bucket: str, prefix: str) -> None:
    def _do() -> None:
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
            if obj.object_name is not None:
                client.remove_object(bucket, obj.object_name)

    await asyncio.to_thread(_do)


async def test_delete_prefix_removes_exactly_one_workspaces_objects(
    minio_client: Minio, live_minio: LiveMinio
) -> None:
    bucket = live_minio.settings.bucket
    ws_a, ws_b = new_uuid7(), new_uuid7()
    try:
        await _put(minio_client, bucket, f"{ws_a}/file.txt")
        await _put(minio_client, bucket, f"{ws_b}/file.txt")

        affected = await delete_prefix(minio_client, bucket, f"{ws_a}/")

        assert affected == 1
        assert await _list_keys(minio_client, bucket, f"{ws_a}/") == []
        assert await _list_keys(minio_client, bucket, f"{ws_b}/") == [f"{ws_b}/file.txt"]
    finally:
        await _sweep(minio_client, bucket, f"{ws_a}/")
        await _sweep(minio_client, bucket, f"{ws_b}/")


async def test_delete_prefix_dry_run_returns_an_accurate_count_without_deleting(
    minio_client: Minio, live_minio: LiveMinio
) -> None:
    bucket = live_minio.settings.bucket
    ws = new_uuid7()
    try:
        for i in range(3):
            await _put(minio_client, bucket, f"{ws}/f{i}.txt")

        affected = await delete_prefix(minio_client, bucket, f"{ws}/", dry_run=True)

        assert affected == 3
        assert len(await _list_keys(minio_client, bucket, f"{ws}/")) == 3
    finally:
        await _sweep(minio_client, bucket, f"{ws}/")


async def test_delete_prefix_on_a_never_populated_workspace_is_a_no_op(
    minio_client: Minio, live_minio: LiveMinio
) -> None:
    """A workspace that never uploaded anything has no objects at all --
    the same idempotent-delete contract as an already-swept workspace."""
    bucket = live_minio.settings.bucket
    ws = new_uuid7()

    affected = await delete_prefix(minio_client, bucket, f"{ws}/")

    assert affected == 0
