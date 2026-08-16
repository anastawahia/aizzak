"""Files inbound port (02-port-contracts §2).

``FilesQuery`` is injected into ``knowledge`` and the agent layer so they can
read a file's metadata without importing the files module directly (ARC-07/08).
``get_readable`` returns a view only for a fully-uploaded, ``ready`` file
(INV-F2) — never a half-uploaded, quarantined, or deleted one.

The view carries the file's ``space_id`` since the spaces plan's step 7: a
consumer that must keep its own rows inside one space (``conversations``'
§3.5 pin rule) can only do that if what it reads back says which space this
file is in. Readability and ownership are different questions, and this port
answers both because both are the files module's own facts.
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
    # The owning space (spaces plan §3.5, step 7). Projected because a
    # consumer has to be able to refuse a file from ANOTHER space --
    # ``PinConversationFile`` is the one that must, and it cannot ask
    # ``spaces`` on this module's behalf. `| None` mirrors the column until
    # plan row 8-b.
    space_id: str | None
    name: str
    content_type: str
    size_bytes: int
    storage_key: str
    status: str


class FilesQuery(Protocol):
    """Injected into ``knowledge``/agents so they never import the files module;
    returns a view only when the file is ready."""

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> FileView | None: ...
