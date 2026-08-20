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

``name`` (retrieval plan §3.6, step 6, ``P-36``) rides along for the same
reason: ``ListDocumentNames`` needs the human-readable file name to build the
corpus-awareness header, and this is the only seam that already turns a
``file_id`` into that file's own facts. ``FilesQuery.get_readable``'s
``FileView`` already carries ``.name`` — widening the Protocol costs nothing
at the binding site, it only starts being READ.

``names_for_files`` (branch review §2) is that same name read asked for a
PAGE of files at once, and it is the reason this port has a second method
rather than a second caller of the first: a name lookup per document turned
the two corpus walks into ``D + 50`` sequential round trips on every
answering turn. ``FilesQuery`` grew the plural read for this consumer, so the
binding stays one line and one instance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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

    # Retrieval plan §3.6, step 6 (`P-36`) — the display name `IndexFile`
    # itself never reads (it only checks readability + space), but
    # `ListDocumentNames` does. One Protocol, one binding, two readers.
    @property
    def name(self) -> str: ...


class ReadableFiles(Protocol):
    """Structurally satisfied by ``app.modules.files.ports.inbound.FilesQuery``.

    ``None`` covers every reason indexing must be refused — unknown, deleted,
    quarantined, or still uploading — and ``IndexFile`` deliberately does not
    distinguish them: they are all "there are no bytes to index", and telling
    a caller which one is true of a file they cannot read is a disclosure,
    not a diagnosis.
    """

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> ReadableFile | None: ...

    async def names_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, str]:
        """The name of every READABLE file among ``file_ids``, in ONE read —
        what the corpus walks resolve names through (branch review §2).

        **The singular above could not be called in a loop.** Both walks
        (``ListDocumentNames`` for the header, ``ListFileCandidates`` for the
        resolver) hold a PAGE of file ids and want a name for each, and
        ``get_readable`` is one ``SELECT`` per file: for a ``D``-document
        repository an answering turn paid ``D + 50`` sequential round trips
        before retrieval began. This is the same authority answering the same
        question at the size the question is actually asked in.

        **Absence carries exactly what ``None`` carries above** — unknown,
        deleted, quarantined or still uploading, undistinguished for the same
        reason — so a caller keeps skipping the documents it already skipped.
        A file present with an EMPTY name is a different fact (a readable file
        that is named nothing), and the two stay distinguishable here because
        ``ListFileCandidates`` drops the second and ``ListDocumentNames`` does
        not.

        Only the name is projected: readability and the space belong to the
        one-file question ``IndexFile`` asks, and a walk that showed names has
        no business acting on either.
        """
        ...
