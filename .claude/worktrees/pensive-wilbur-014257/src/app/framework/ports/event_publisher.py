"""EventPublisher driven port (02-port-contracts §1.8 · Redis Streams, D-18/20).

Used only by the ``outbox_relay`` to publish; business modules never publish
directly — they write the Outbox and the relay forwards (10-code-standards §7).
``event`` is a full CloudEvents 1.0 envelope (DD-07).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class StreamEvent:
    stream: str
    event: Json  # full CloudEvents envelope


class EventPublisher(Protocol):
    async def publish(self, stream: str, event: Json) -> str: ...  # returns stream entry id

    async def publish_batch(self, items: Sequence[StreamEvent]) -> list[str]: ...
