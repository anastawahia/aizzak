"""The worker's heartbeat, beaten between page ranges so a long document does not look dead."""

from __future__ import annotations

from typing import Protocol


class Liveness(Protocol):
    def beat(self) -> None: ...
