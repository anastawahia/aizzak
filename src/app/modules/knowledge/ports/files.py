"""The file-existence seam this module needs to index a file ON REQUEST.

``IndexFile`` (the manual-indexing route) is handed a FILE id by a person who
just uploaded one, and it has to answer two questions before it mints a
document: **is that file readable at all**, and **which space does it belong
to**. Both are the ``files`` module's own facts, and this module must not
import it — modules are siblings, and a nominal import would make
``knowledge`` unbuildable without ``files``.

So — Dependency Inversion, exactly as ``conversations/ports/files.py`` does it
for a pin: the CONSUMER declares the shape it needs here and the Composition
Root binds ``files``' own ``FilesQuery`` to it. The binding is structural, so
a change to ``FilesQuery.get_readable`` turns that wiring line red instead of
drifting silently.

**``get_readable`` is also the "upload finished" guard, and that is why this
port is the whole answer rather than half of it.** It returns a view only for
a ``ready`` file (INV-F2) — never a half-uploaded, quarantined or deleted one
— which is precisely the precondition manual indexing has: the bytes must be
in storage before a worker is told to go parse them. Asking a second authority
would mean two answers to one question.

``space_id`` rides along because a document is filed under its FILE's space
(spaces plan, step 8) and this module deliberately owns no ``spaces`` port:
the space was proven real when the file was registered, and re-asking here
would be a second authority on that too.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class ReadableFile(Protocol):
    """A file that exists and whose bytes are in storage (``status=ready``)."""

    @property
    def file_id(self) -> str: ...

    # `| None` mirrors the column, and is NOT collapsed to `""`: a spaceless
    # file produces a spaceless document, which is exactly what the pre-plan
    # corpus already looks like (03 §1's `GET /knowledge/documents` note).
    @property
    def space_id(self) -> str | None: ...


class ReadableFiles(Protocol):
    """Structurally satisfied by ``app.modules.files.ports.inbound.FilesQuery``.

    ``None`` covers every reason indexing must be refused — unknown, deleted,
    quarantined, or still uploading — and ``IndexFile`` deliberately does not
    distinguish them: they are all "there are no bytes to index", and telling
    a caller which one is true of a file they cannot read is a disclosure,
    not a diagnosis.
    """

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> ReadableFile | None: ...
