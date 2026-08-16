"""The file-existence seam this module needs to accept a pin (BE-RAG-005).

``PinConversationFile`` must refuse a file that cannot be retrieved from —
otherwise the pin is a stored reference to nothing, and the thread silently
answers from an empty scope. Knowing that means asking the ``files`` module,
which this module must not import: modules are siblings, and a nominal import
would make ``conversations`` unbuildable without ``files``.

So — Dependency Inversion, the ``KnowledgeAccess`` pattern applied between two
modules instead of between the framework and one — the CONSUMER declares the
shape it needs here and the Composition Root binds ``files``' own
``FilesQuery`` to it. The binding is structural: mypy checks it at the wiring
site, so a change to ``FilesQuery.get_readable`` turns that line red instead of
drifting silently.

The Protocol is deliberately narrower than ``FilesQuery``: this module asks one
question — "is there a readable file with this id **in this thread's space**?"
(the space half arrived with the spaces plan's §3.5) — and ``ReadableFile``
exposes only what answering it takes, because a name or a size here would be
file metadata ``conversations`` has no business projecting (`03 §1` keeps that
on ``GET /files``, which the client joins by id).

``file_id`` is a read-only ``@property`` and not a bare annotation for the
reason ``deps_ports.RetrievedChunkView`` records: a bare ``x: str`` declares a
MUTABLE attribute, which no ``frozen=True`` dataclass — and ``FileView`` is
one — can satisfy.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class ReadableFile(Protocol):
    """A file that exists and is readable (``status=ready``, INV-F2)."""

    @property
    def file_id(self) -> str: ...

    # The file's owning space (spaces plan §3.5, step 7) -- the ONE widening
    # of this Protocol since it was written, and it earns it: without the
    # space, "is there a readable file with this id?" is the wrong question.
    # Decision 1 scopes a thread to its own space's files, so a pin from
    # another space is a scope entry retrieval can never match (the plan's
    # §3.5 calls that the silent-failure path), and the only place able to
    # refuse it is the one holding both spaces at once.
    #
    # `| None` because the column is NULLable until plan row 8-b, and it is
    # NOT collapsed to "" or to the conversation's own space: those would
    # make a spaceless file compare equal to something, which is exactly the
    # comparison 8-b exists to stop being possible.
    @property
    def space_id(self) -> str | None: ...


class ReadableFiles(Protocol):
    """Structurally satisfied by ``app.modules.files.ports.inbound.FilesQuery``.

    ``None`` covers every reason a pin must be refused — unknown, deleted,
    quarantined, or still uploading — and the use-case deliberately does not
    distinguish them: they are all "retrieval will never reach this", and
    telling a caller which one is true of a file they cannot read is a
    disclosure, not a diagnosis.
    """

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> ReadableFile | None: ...
