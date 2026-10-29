"""Unit tests for the late-binding storage slot (6.1-هـ-1, debt (ز)).

``framework/di/storage_handle.py`` is what everything storage-needing holds
from ``from_env`` time onward, so its two behaviours are load-bearing on
opposite sides of startup: BEFORE ``bind`` every port method must fail with
the clean ``common.internal`` the old ``deps.storage = None`` guard produced
(never an ``AttributeError`` leaking into a 500 with a stack trace of the
handle's internals), and AFTER ``bind`` every method must delegate its
arguments and return value VERBATIM — a handle that quietly reorders or drops
a parameter would corrupt uploads while every gate stays green.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.framework.di.storage_handle import StorageHandle
from app.framework.errors import AppError
from app.framework.ports.storage_provider import StoredPart


class _RecordingProvider:
    """A fake ``StorageProvider`` that records each call and returns a value
    distinguishable per method, so delegation is proven argument-for-argument."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.calls.append(("put", key, data, content_type))

    async def get(self, key: str) -> bytes:
        self.calls.append(("get", key))
        return b"bytes-for-" + key.encode()

    async def delete(self, key: str) -> None:
        self.calls.append(("delete", key))

    async def presign_get(self, key: str, ttl_s: int) -> str:
        self.calls.append(("presign_get", key, ttl_s))
        return f"https://get/{key}?ttl={ttl_s}"

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        self.calls.append(("presign_put", key, ttl_s, content_type))
        return f"https://put/{key}?ttl={ttl_s}"

    async def start_multipart(self, key: str, content_type: str) -> str:
        self.calls.append(("start_multipart", key, content_type))
        return f"upload-for-{key}"

    async def presign_upload_part(
        self, key: str, upload_id: str, part_number: int, ttl_s: int
    ) -> str:
        self.calls.append(("presign_upload_part", key, upload_id, part_number, ttl_s))
        return f"https://part/{key}?uploadId={upload_id}&partNumber={part_number}"

    async def list_parts(self, key: str, upload_id: str) -> Sequence[StoredPart]:
        self.calls.append(("list_parts", key, upload_id))
        return [StoredPart(1, 5, "etag-1")]

    async def complete_multipart(
        self, key: str, upload_id: str, parts: Sequence[StoredPart]
    ) -> None:
        self.calls.append(("complete_multipart", key, upload_id, tuple(parts)))

    async def object_size(self, key: str) -> int | None:
        self.calls.append(("object_size", key))
        return 42


# --------------------------------------------------------------------------- #
# Unbound: the clean common.internal, on EVERY port method                     #
# --------------------------------------------------------------------------- #
async def test_every_method_of_an_unbound_handle_raises_common_internal() -> None:
    handle = StorageHandle()
    assert handle.is_bound is False

    for attempt in (
        handle.put("k", b"d", "text/plain"),
        handle.get("k"),
        handle.delete("k"),
        handle.presign_get("k", 60),
        handle.presign_put("k", 60, "text/plain"),
        handle.start_multipart("k", "text/plain"),
        handle.presign_upload_part("k", "u", 1, 60),
        handle.list_parts("k", "u"),
        handle.complete_multipart("k", "u", []),
        handle.object_size("k"),
    ):
        with pytest.raises(AppError) as excinfo:
            await attempt
        assert excinfo.value.code == "common.internal"
        assert excinfo.value.status == 500


# --------------------------------------------------------------------------- #
# Bound: verbatim delegation                                                   #
# --------------------------------------------------------------------------- #
async def test_a_bound_handle_delegates_arguments_and_results_verbatim() -> None:
    handle = StorageHandle()
    provider = _RecordingProvider()
    handle.bind(provider)
    assert handle.is_bound is True

    await handle.put("a/k1", b"payload", "application/pdf")
    got = await handle.get("a/k2")
    await handle.delete("a/k3")
    url_get = await handle.presign_get("a/k4", 300)
    url_put = await handle.presign_put("a/k5", 900, "image/png")

    assert provider.calls == [
        ("put", "a/k1", b"payload", "application/pdf"),
        ("get", "a/k2"),
        ("delete", "a/k3"),
        ("presign_get", "a/k4", 300),
        ("presign_put", "a/k5", 900, "image/png"),
    ]
    assert got == b"bytes-for-a/k2"
    assert url_get == "https://get/a/k4?ttl=300"
    assert url_put == "https://put/a/k5?ttl=900"


async def test_a_bound_handle_delegates_the_multipart_methods_verbatim() -> None:
    handle = StorageHandle()
    provider = _RecordingProvider()
    handle.bind(provider)
    parts = [StoredPart(1, 5, "e1"), StoredPart(2, 3, "e2")]

    upload_id = await handle.start_multipart("a/k1", "application/pdf")
    url = await handle.presign_upload_part("a/k1", upload_id, 2, 900)
    held = await handle.list_parts("a/k1", upload_id)
    await handle.complete_multipart("a/k1", upload_id, parts)
    size = await handle.object_size("a/k1")

    assert provider.calls == [
        ("start_multipart", "a/k1", "application/pdf"),
        ("presign_upload_part", "a/k1", "upload-for-a/k1", 2, 900),
        ("list_parts", "a/k1", "upload-for-a/k1"),
        ("complete_multipart", "a/k1", "upload-for-a/k1", tuple(parts)),
        ("object_size", "a/k1"),
    ]
    assert url == "https://part/a/k1?uploadId=upload-for-a/k1&partNumber=2"
    assert list(held) == [StoredPart(1, 5, "etag-1")]
    assert size == 42
