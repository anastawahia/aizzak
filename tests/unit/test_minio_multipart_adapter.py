"""Unit tests for the multipart methods of ``MinioStorage`` against a fake client.

The live tests (``tests/integration/test_minio_storage.py``) never see a listing
longer than one page, so the paging loop of ``list_parts`` -- the one that keeps
a >1000-part upload from looking incomplete -- is proven here with a fake
minio-py client. The error translation is proven here too, with no server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest
import urllib3
from minio import Minio
from minio.error import S3Error

from app.framework.errors import AppError, ConflictError, NotFoundError
from app.framework.ports.storage_provider import StoredPart
from app.infrastructure.storage.minio_storage import MinioStorage


@dataclass
class _Part:
    part_number: int
    size: int | None
    etag: str


@dataclass
class _Page:
    parts: list[_Part]
    is_truncated: bool
    next_part_number_marker: int | None


def _s3_error(code: str) -> S3Error:
    return S3Error(code, code, "/b/k", "req", "host", cast(Any, None))


class _FakeClient:
    def __init__(self, pages: list[_Page] | None = None, raises: Exception | None = None) -> None:
        self.pages = pages or []
        self.raises = raises
        self.list_calls: list[dict[str, Any]] = []
        self.completed: list[tuple[int, str]] = []
        self.stat_size: int | None = 7

    def _list_parts(
        self,
        bucket: str,
        key: str,
        upload_id: str,
        *,
        max_parts: int,
        part_number_marker: str | None,
    ) -> _Page:
        self.list_calls.append({"max_parts": max_parts, "marker": part_number_marker})
        if self.raises is not None:
            raise self.raises
        return self.pages[len(self.list_calls) - 1]

    def _complete_multipart_upload(
        self, bucket: str, key: str, upload_id: str, parts: list[Any]
    ) -> None:
        if self.raises is not None:
            raise self.raises
        self.completed = [(p.part_number, p.etag) for p in parts]

    def stat_object(self, bucket: str, key: str) -> Any:
        if self.raises is not None:
            raise self.raises

        class _Stat:
            size = self.stat_size

        return _Stat()


def _storage(client: _FakeClient) -> MinioStorage:
    return MinioStorage(cast(Minio, client), "bucket")


async def test_list_parts_follows_every_page_by_its_marker() -> None:
    client = _FakeClient(
        [
            _Page([_Part(1, 10, "a"), _Part(2, 10, "b")], True, 2),
            _Page([_Part(3, 10, "c")], True, 3),
            _Page([_Part(4, None, "d")], False, None),
        ]
    )

    parts = await _storage(client).list_parts("k", "u")

    assert [(p.part_number, p.size_bytes, p.etag) for p in parts] == [
        (1, 10, "a"),
        (2, 10, "b"),
        (3, 10, "c"),
        (4, 0, "d"),  # a part without a size reads as 0 bytes, so it is "wrong size"
    ]
    assert [c["marker"] for c in client.list_calls] == [None, "2", "3"]
    assert {c["max_parts"] for c in client.list_calls} == {1000}


async def test_list_parts_of_one_untruncated_page_asks_once() -> None:
    client = _FakeClient([_Page([_Part(1, 5, "a")], False, None)])

    parts = await _storage(client).list_parts("k", "u")

    assert len(parts) == 1
    assert len(client.list_calls) == 1


async def test_list_parts_of_a_vanished_upload_is_not_found() -> None:
    client = _FakeClient(raises=_s3_error("NoSuchUpload"))

    with pytest.raises(NotFoundError):
        await _storage(client).list_parts("k", "u")


@pytest.mark.parametrize(
    "failure",
    [_s3_error("AccessDenied"), urllib3.exceptions.HTTPError("down"), OSError("refused")],
)
async def test_list_parts_folds_every_other_failure_into_internal(failure: Exception) -> None:
    with pytest.raises(AppError) as caught:
        await _storage(_FakeClient(raises=failure)).list_parts("k", "u")

    assert caught.value.code == "common.internal"


async def test_complete_multipart_hands_the_parts_over_in_the_given_order() -> None:
    client = _FakeClient()

    await _storage(client).complete_multipart(
        "k", "u", [StoredPart(1, 5, "e1"), StoredPart(2, 5, "e2"), StoredPart(3, 1, "e3")]
    )

    assert client.completed == [(1, "e1"), (2, "e2"), (3, "e3")]


@pytest.mark.parametrize("code", ["InvalidPart", "InvalidPartOrder", "EntityTooSmall"])
async def test_complete_multipart_rejected_parts_are_a_conflict(code: str) -> None:
    with pytest.raises(ConflictError):
        await _storage(_FakeClient(raises=_s3_error(code))).complete_multipart(
            "k", "u", [StoredPart(1, 5, "e")]
        )


async def test_complete_multipart_of_a_gone_upload_is_not_found_not_a_conflict() -> None:
    with pytest.raises(NotFoundError):
        await _storage(_FakeClient(raises=_s3_error("NoSuchUpload"))).complete_multipart(
            "k", "u", [StoredPart(1, 5, "e")]
        )


async def test_object_size_reports_the_size_and_none_for_a_missing_object() -> None:
    client = _FakeClient()
    assert await _storage(client).object_size("k") == 7

    client.raises = _s3_error("NoSuchKey")
    assert await _storage(client).object_size("k") is None


async def test_object_size_of_other_failures_is_internal() -> None:
    with pytest.raises(AppError) as caught:
        await _storage(_FakeClient(raises=_s3_error("AccessDenied"))).object_size("k")

    assert caught.value.code == "common.internal"
