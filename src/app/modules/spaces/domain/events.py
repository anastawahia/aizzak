"""Spaces domain events (pure — 06-domain-models §6).

In-memory domain events: plain frozen records with no shared base class (the
domain imports no framework code).

**One event, and it is internal.** ``SpaceDeleted`` is not in
``events/schemas/`` and 04-event-catalog gives it no row, so nothing publishes
it to the wire and no ``event_mapping`` translates it — the ``FileDeleted``
precedent, minus even the mapping, because there is no other spaces event to
share one with. What it exists for is the cascade (§3.6): the composition-root
``DeleteSpaceService`` is the consumer, and the record is what the marking step
hands it — the id whose contents the remaining six steps erase.

There is deliberately **no ``SpaceCreated``**. An event with no consumer is a
promise nobody can tell you are failing to keep (the ``RenameFile`` docstring's
argument); creation is a synchronous request whose answer is the space itself,
and nothing downstream has to hear about it. The day one does, the event is a
five-line addition — the day it exists unheard, it is a line in a catalog that
lies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SpaceDeleted:
    """A space was soft-deleted; its contents are the cascade's business."""

    space_id: str
    workspace_id: str
    occurred_at: datetime


SpaceEvent = SpaceDeleted
