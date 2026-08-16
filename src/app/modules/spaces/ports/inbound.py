"""Spaces inbound port (02-port-contracts §2).

The ``FilesQuery`` precedent, and the reason this module can be a hard
boundary at all. ``files`` and ``conversations`` must refuse a ``space_id``
that names nothing — otherwise a row is stored under an axis that does not
exist and is invisible to every listing forever — but neither may import this
module (§3.1, import-linter contract 4). So each CONSUMER declares the narrow
shape it needs in its own ``ports/spaces.py`` and the Composition Root binds
``SpacesQuery`` to it, structurally, with mypy checking the wiring site.

``SpaceView`` is deliberately thinner than the aggregate: an existence check
needs an id and a label, and handing out ``version``/``deleted_at`` would let
another module reason about optimistic-lock state it must not touch (the
``StartedConversation`` argument).

``name`` is carried anyway, for one use that is not decoration: an error
message. "file belongs to space 'Research', not 'Drafts'" (§3.5's pin refusal)
is actionable; the same sentence with two UUIDs is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class SpaceView:
    """A read-only projection of an ACTIVE space, safe to hand to other modules."""

    space_id: str
    name: str


class SpacesQuery(Protocol):
    """Injected wherever a ``space_id`` arrives from a request and has to be
    proven real before anything is written under it.

    ``None`` covers both reasons a space cannot be written to — unknown and
    deleted — and the callers deliberately do not distinguish them: they are
    both "nothing may be filed here", and telling a caller that a space they
    cannot use nevertheless exists is a disclosure, not a diagnosis
    (``ReadableFiles``' wording, and its reasoning).
    """

    async def get_active(self, ctx: ExecutionContext, space_id: Uuid) -> SpaceView | None: ...
