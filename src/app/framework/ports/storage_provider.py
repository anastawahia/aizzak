"""StorageProvider driven port (02-port-contracts §1.6 · MinIO), and its sibling
``MultipartStorage`` for resumable uploads (PLT-01)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class StorageProvider(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def presign_get(self, key: str, ttl_s: int) -> str: ...

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str: ...


@dataclass(frozen=True, slots=True)
class StoredPart:
    """One part storage holds for an in-flight multipart upload."""

    part_number: int
    size_bytes: int
    etag: str


class MultipartStorage(Protocol):
    """Resumable-upload faces of object storage (02 §1.6, PLT-01). Bound into
    `files` ONLY: agents keep `StorageProvider`, which cannot open one."""

    async def start_multipart(self, key: str, content_type: str) -> str:
        """CreateMultipartUpload; returns the storage upload id."""
        ...

    async def presign_upload_part(
        self, key: str, upload_id: str, part_number: int, ttl_s: int
    ) -> str:
        """A presigned UploadPart PUT, signed for the PUBLIC address (7.1)."""
        ...

    async def list_parts(self, key: str, upload_id: str) -> Sequence[StoredPart]:
        """Every part held, all pages. NotFoundError once the upload is gone."""
        ...

    async def complete_multipart(
        self, key: str, upload_id: str, parts: Sequence[StoredPart]
    ) -> None:
        """CompleteMultipartUpload. NotFoundError if the upload is gone;
        ConflictError if storage rejects the parts (InvalidPart, order, size)."""
        ...

    async def object_size(self, key: str) -> int | None:
        """HEAD; None when there is no object under `key`."""
        ...


class ObjectStorage(StorageProvider, MultipartStorage, Protocol):
    """Both faces -- what `MinioStorage` and `StorageHandle` are."""
