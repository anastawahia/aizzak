"""Staging the PDF where ele-parse can read it (DEC-04, PLT-03).

The worker streams the file from object storage into the stage volume, hashing it on the way, so
the memory used is one chunk however large the file. ele-parse mounts the volume read-only and
is handed the NAME, never a path it could be tricked out of.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class StagedSource:
    name: str  # "<workspace_id>/<document_id>.pdf", relative to the stage root
    sha256: str
    size_bytes: int


class SourceStager(Protocol):
    async def stage(
        self, ctx: ExecutionContext, *, document_id: Uuid, storage_key: str, expected_size: int
    ) -> StagedSource:
        """ValidationError(code="electrical.source_unreadable") on a size mismatch;
        NotFoundError when the object is gone."""
        ...

    async def discard(self, source: StagedSource) -> None:
        """Idempotent: removes the staged file (and any partial one) if present."""
        ...
