"""Files inbound port (02-port-contracts §2).

``FilesQuery`` is injected into ``knowledge`` and the agent layer so they can
read a file's metadata without importing the files module directly (ARC-07/08).
``get_readable`` returns a view only for a fully-uploaded, ``ready`` file
(INV-F2) — never a half-uploaded, quarantined, or deleted one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class FileView:
    """A read-only projection of a ``ready`` file, safe to hand to other modules."""

    file_id: str
    name: str
    content_type: str
    size_bytes: int
    storage_key: str
    status: str


class FilesQuery(Protocol):
    """Injected into ``knowledge``/agents so they never import the files module;
    returns a view only when the file is ready."""

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> FileView | None: ...
