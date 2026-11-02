"""What the framework's delete cascades bind to (``framework/di/electrical_purge.py``).

PHASE 1 of a purge only: take the documents out of every read, synchronously, and return how
many were marked. Phase 2 (sweeping rows) is the consumer of ``electrical.document.purged.v1``.
There is no route that purges: only a file or space deletion reaches these.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class DrawingsFilePurge(Protocol):
    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> int: ...


class DrawingsSpacePurge(Protocol):
    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int: ...
