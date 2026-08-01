"""VideoProvider driven port (02-port-contracts §1.4, D-02, DD-13).

Video generation may return inline bytes or a remote URL (heavy results are
fetched/stored asynchronously by the media worker).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class VideoRequest:
    prompt: str
    duration_s: int
    model: str
    extra: Json | None = None


@dataclass(frozen=True, slots=True)
class VideoResult:
    content: bytes | None
    remote_url: str | None
    content_type: str
    model: str


class VideoProvider(Protocol):
    provider: str

    async def generate(self, req: VideoRequest, api_key: str) -> VideoResult: ...
