"""CacheProvider driven port (02-port-contracts §1.7 · Redis)."""

from __future__ import annotations

from typing import Protocol


class CacheProvider(Protocol):
    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def incr(self, key: str, amount: int = 1) -> int: ...

    async def expire(self, key: str, ttl_s: int) -> None: ...
