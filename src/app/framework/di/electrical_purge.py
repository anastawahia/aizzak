"""The ``electrical`` participant of the file and space delete cascades (HLD §8.8, PLT-04).

``DeleteFileService`` and ``DeleteSpaceService`` call ONE ``electrical`` collaborator each, and
that collaborator fans out to WireTwin's sections in a fixed order. INC-1 has one section with a
purge (``drawings``); ``search`` joins in INC-8 and ``circuits`` in INC-6, each by gaining a
keyword-only parameter here, so the cascades never learn how many sections there are.

**Phase 1 only.** A section's ``execute`` takes the file's (or the space's) documents out of
EVERY read, synchronously, in its own transaction, and returns how many it marked. The heavy
phase 2 (sweeping rows, rendered pages and index points) is each section's consumer of
``electrical.document.purged.v1``, off this path and idempotent, so an interrupted cascade is
finished by running ``DELETE`` again, as with ``knowledge``.

**Why this file imports no module.** ``file_deletion.py``'s rule: no module may import another
(import-linter contract 4), so the collaborators are structural ``Protocol``\\ s declared here
and the Composition Root binds ``drawings``' own purge use cases to them, where mypy checks the
fit.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class SectionFilePurge(Protocol):
    """One section's "take this file's documents out of every read"; the count it marked."""

    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> int: ...


class SectionSpacePurge(Protocol):
    """One section's "take this space's documents out of every read"; the count it marked."""

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int: ...


class ElectricalFilePurge:
    """The ONE ``electrical`` participant in ``DeleteFileService`` (HLD §8.8, PLT-04).

    PHASE 1 only: each section takes the file's documents out of every read, synchronously, in
    a fixed order (drawings, then search in INC-8, then circuits in INC-6). Phase 2 is each
    section's consumer of ``electrical.document.purged.v1``. Returns the documents ``drawings``
    marked: the number the cascade reports as ``FileDeletion.drawings``.
    """

    def __init__(self, *, drawings: SectionFilePurge) -> None:
        self._drawings = drawings

    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> int:
        return await self._drawings.execute(ctx, file_id)


class ElectricalSpacePurge:
    """``ElectricalFilePurge`` at the scale of a space (``DeleteSpaceService``)."""

    def __init__(self, *, drawings: SectionSpacePurge) -> None:
        self._drawings = drawings

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        return await self._drawings.execute(ctx, space_id)
