"""ImageProvider driven port (02-port-contracts §1.3, D-02, DD-13)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str
    width: int
    height: int
    model: str
    extra: Json | None = None


@dataclass(frozen=True, slots=True)
class ImageResult:
    content: bytes
    content_type: str
    model: str


class ImageProvider(Protocol):
    provider: str

    async def generate(self, req: ImageRequest, api_key: str) -> ImageResult: ...
