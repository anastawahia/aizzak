"""``MinioStorage.read_chunks`` against a fake client (BT29, PLT-03): the chunk sizes and order,
the connection released on every exit, and the error translation, with no server."""

from __future__ import annotations

from typing import Any, cast

import pytest
import urllib3
from minio import Minio
from minio.error import S3Error

from app.framework.errors import AppError, NotFoundError
from app.infrastructure.storage.minio_storage import MinioStorage


def _s3_error(code: str) -> S3Error:
    return S3Error(code, code, "/b/k", "req", "host", cast(Any, None))


class _FakeResponse:
    """A urllib3 response: ``read(n)`` hands out at most ``n`` bytes, ``b""`` at the end."""

    def __init__(self, body: bytes, *, fail_on_read: int | None = None) -> None:
        self._body = body
        self._at = 0
        self._fail_on_read = fail_on_read
        self.read_sizes: list[int] = []
        self.closed = 0
        self.released = 0

    def read(self, amt: int) -> bytes:
        self.read_sizes.append(amt)
        if self._fail_on_read is not None and len(self.read_sizes) == self._fail_on_read:
            raise urllib3.exceptions.ProtocolError("connection reset")
        chunk = self._body[self._at : self._at + amt]
        self._at += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed += 1

    def release_conn(self) -> None:
        self.released += 1


class _FakeClient:
    def __init__(self, response: _FakeResponse | None = None, raises: Exception | None = None):
        self.response = response
        self.raises = raises
        self.opened: list[tuple[str, str]] = []

    def get_object(self, bucket: str, key: str) -> _FakeResponse:
        self.opened.append((bucket, key))
        if self.raises is not None:
            raise self.raises
        assert self.response is not None
        return self.response


def _storage(client: _FakeClient) -> MinioStorage:
    return MinioStorage(cast(Minio, client), "bucket")


async def test_the_object_arrives_in_order_in_chunks_of_at_most_the_asked_size() -> None:
    body = bytes(range(256)) * 10  # 2,560 bytes
    response = _FakeResponse(body)
    client = _FakeClient(response)

    chunks = [c async for c in _storage(client).read_chunks("ws/f1", chunk_bytes=1000)]

    assert [len(c) for c in chunks] == [1000, 1000, 560]
    assert b"".join(chunks) == body
    assert client.opened == [("bucket", "ws/f1")]
    assert set(response.read_sizes) == {1000}  # memory is one chunk, never the whole object


async def test_an_object_that_fits_one_chunk_and_an_empty_one() -> None:
    small = [
        c
        async for c in _storage(_FakeClient(_FakeResponse(b"abc"))).read_chunks("k", chunk_bytes=64)
    ]
    empty_response = _FakeResponse(b"")
    empty = [
        c async for c in _storage(_FakeClient(empty_response)).read_chunks("k", chunk_bytes=64)
    ]

    assert small == [b"abc"]
    assert empty == []
    assert empty_response.closed == 1 and empty_response.released == 1


async def test_the_connection_is_released_when_the_stream_is_fully_consumed() -> None:
    response = _FakeResponse(b"x" * 25)
    _ = [c async for c in _storage(_FakeClient(response)).read_chunks("k", chunk_bytes=10)]

    assert (response.closed, response.released) == (1, 1)


async def test_the_connection_is_released_when_the_caller_stops_early() -> None:
    response = _FakeResponse(b"x" * 100)
    stream = _storage(_FakeClient(response)).read_chunks("k", chunk_bytes=10)

    assert await anext(stream) == b"x" * 10
    assert (response.closed, response.released) == (0, 0)  # still open between chunks
    await cast(Any, stream).aclose()

    assert (response.closed, response.released) == (1, 1)
    assert len(response.read_sizes) == 1  # nothing was read ahead


async def test_a_failed_read_is_common_internal_and_still_releases_the_connection() -> None:
    response = _FakeResponse(b"x" * 100, fail_on_read=2)
    stream = _storage(_FakeClient(response)).read_chunks("k", chunk_bytes=10)

    assert await anext(stream) == b"x" * 10
    with pytest.raises(AppError) as excinfo:
        await anext(stream)

    assert excinfo.value.code == "common.internal"
    assert not isinstance(excinfo.value.__cause__, AppError)
    assert (response.closed, response.released) == (1, 1)


async def test_a_missing_object_is_not_found_before_any_chunk() -> None:
    client = _FakeClient(raises=_s3_error("NoSuchKey"))

    with pytest.raises(NotFoundError):
        await anext(_storage(client).read_chunks("ws/gone", chunk_bytes=10))


@pytest.mark.parametrize(
    "error",
    [
        _s3_error("AccessDenied"),
        _s3_error("NoSuchBucket"),
        urllib3.exceptions.MaxRetryError(cast(Any, None), "/b/k"),
        OSError("network is down"),
    ],
)
async def test_every_other_failure_to_open_is_common_internal(error: Exception) -> None:
    with pytest.raises(AppError) as excinfo:
        await anext(_storage(_FakeClient(raises=error)).read_chunks("k", chunk_bytes=10))

    assert excinfo.value.code == "common.internal"
    assert not isinstance(excinfo.value, NotFoundError)


@pytest.mark.parametrize("size", [0, -1])
async def test_a_chunk_size_that_is_not_positive_is_a_programming_error(size: int) -> None:
    client = _FakeClient(_FakeResponse(b"abc"))

    with pytest.raises(ValueError, match="chunk_bytes"):
        await anext(_storage(client).read_chunks("k", chunk_bytes=size))

    assert client.opened == []  # nothing was requested
