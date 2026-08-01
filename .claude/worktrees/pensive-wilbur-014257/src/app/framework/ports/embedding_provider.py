"""EmbeddingProvider driven port (02-port-contracts §1.2, D-14, DD-13)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimensions: int
    tokens: int


class EmbeddingProvider(Protocol):
    provider: str

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult: ...

    def dimensions(self, model: str) -> int: ...
