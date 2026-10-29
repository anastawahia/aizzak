"""Alarm for the private minio-py methods the multipart adapter relies on.

minio-py 7.2 exposes create/list/complete of a multipart upload only through
underscore methods, and the dependency is not pinned (INC0-D-4, INC0-R-1).
Pure introspection -- no server -- so an upgrade that renames or reshapes one
fails CI here, not a user's upload in production.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from minio import Minio
from minio.datatypes import ListPartsResult, Part


def _params(name: str) -> list[str]:
    return list(inspect.signature(getattr(Minio, name)).parameters)


def test_create_multipart_upload_takes_bucket_object_headers() -> None:
    assert _params("_create_multipart_upload")[:4] == [
        "self",
        "bucket_name",
        "object_name",
        "headers",
    ]


def test_list_parts_takes_a_page_size_and_a_marker() -> None:
    params = inspect.signature(Minio._list_parts).parameters
    assert list(params)[:4] == ["self", "bucket_name", "object_name", "upload_id"]
    assert "max_parts" in params
    assert "part_number_marker" in params


def test_complete_multipart_upload_takes_the_parts() -> None:
    assert _params("_complete_multipart_upload")[:5] == [
        "self",
        "bucket_name",
        "object_name",
        "upload_id",
        "parts",
    ]


def test_presign_and_stat_are_public_with_the_expected_keywords() -> None:
    presign = inspect.signature(Minio.get_presigned_url).parameters
    assert list(presign)[:4] == ["self", "method", "bucket_name", "object_name"]
    assert "expires" in presign
    assert "extra_query_params" in presign
    assert list(_params("stat_object"))[:3] == ["self", "bucket_name", "object_name"]


@pytest.mark.parametrize("attr", ["is_truncated", "next_part_number_marker", "parts"])
def test_list_parts_result_exposes_the_paging_fields(attr: str) -> None:
    assert attr in ListPartsResult.__dataclass_fields__


def test_part_is_built_from_a_number_and_an_etag() -> None:
    part: Any = Part(3, "etag")
    assert (part.part_number, part.etag) == (3, "etag")
    assert "size" in Part.__dataclass_fields__
