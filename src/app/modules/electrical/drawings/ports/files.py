"""The file seam drawings reads through (``knowledge/ports/files.py``'s pattern).

The CONSUMER declares the shape it needs and the Composition Root binds ``files``' own
``FilesQuery`` to it, structurally, so a change to ``FilesQuery`` turns the wiring line red
instead of drifting. ``get_readable`` is also the "upload finished" guard: it returns a view
only for a ``ready`` file that is not deleted, in the caller's workspace, which is exactly the
precondition STG-00 has. ``None`` covers unknown, foreign, not ready and deleted alike; the
caller does not distinguish them (a disclosure, not a diagnosis).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class DrawingFile(Protocol):
    @property
    def file_id(self) -> str: ...

    @property
    def space_id(self) -> str | None: ...

    @property
    def name(self) -> str: ...

    @property
    def content_type(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...

    @property
    def storage_key(self) -> str: ...

    @property
    def tags(self) -> Mapping[str, str]: ...


class DrawingFiles(Protocol):
    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> DrawingFile | None: ...

    async def names_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, str]: ...
