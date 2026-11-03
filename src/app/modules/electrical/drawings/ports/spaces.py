"""The space-existence seam (``files/ports/spaces.py``'s pattern): bound to ``spaces``'
``SpacesQuery`` in the Composition Root. A project is filed under a space that must exist and
not be deleted; ``None`` covers both reasons and the caller does not tell them apart."""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class ActiveSpace(Protocol):
    """A space that exists in this workspace and is not soft-deleted."""

    @property
    def space_id(self) -> str: ...


class ActiveSpaces(Protocol):
    async def get_active(self, ctx: ExecutionContext, space_id: Uuid) -> ActiveSpace | None: ...
