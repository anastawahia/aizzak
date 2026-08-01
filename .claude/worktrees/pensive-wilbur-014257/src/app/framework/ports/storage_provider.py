"""StorageProvider driven port (02-port-contracts §1.6 · MinIO)."""

from __future__ import annotations

from typing import Protocol


class StorageProvider(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def presign_get(self, key: str, ttl_s: int) -> str: ...

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str: ...
