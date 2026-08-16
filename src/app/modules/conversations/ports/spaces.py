"""The space-existence seam this module needs to open a thread inside one
(``docs/spaces-backend-plan.md`` §3.1, step 7).

``conversations.conversations.space_id`` is an OPAQUE identifier here, exactly
as it is in ``files``: this module stores it, filters listings by it and
compares it against a pinned file's, and knows nothing else about it. An id
that names NOTHING would open a thread on an axis that does not exist —
invisible to every listing forever, and (decision 1) scoped to the files of a
space nobody can name. So the value is proven real before the row is written.

Knowing that means asking the ``spaces`` module, which this module must not
import (import-linter contract 4). So the CONSUMER declares the shape it needs
here and the Composition Root binds ``spaces``' own ``SpacesQuery`` to it —
the ``ports/files.py`` pattern this module already uses, pointed at a second
producer. The binding is structural: mypy checks it at the wiring site.

**This file is a near-copy of ``files/ports/spaces.py``, and that is the
contract working rather than duplication to remove.** Two sibling modules that
must not know each other cannot share a declaration without a shared kernel
that both would then depend on; the codebase already settles this the same way
for ``AgentKey``, which ``conversations``/``memory``/``media`` each declare for
themselves (``media/domain/value_objects.py`` records the reasoning). What is
shared is the ANSWER — the Composition Root binds one ``SpacesQueryService``
instance to both seams — not the declaration.

``ActiveSpace`` exposes only the id. The step-6 docstring predicted this module
would need ``name`` too, for §3.5's refusal sentence; it does not. The refusal
compares the conversation's space with the pinned file's, and both ids are
already in hand — resolving them to names would mean two extra reads on a
rejection path to decorate a message whose ids the client can resolve itself
through ``GET /spaces``.

``space_id`` is a read-only ``@property`` and not a bare annotation for the
reason ``ReadableFile`` records: a bare ``x: str`` declares a MUTABLE
attribute, which no ``frozen=True`` dataclass — and ``SpaceView`` is one — can
satisfy.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class ActiveSpace(Protocol):
    """A space that exists in this workspace and is not soft-deleted."""

    @property
    def space_id(self) -> str: ...


class ActiveSpaces(Protocol):
    """Structurally satisfied by ``app.modules.spaces.ports.inbound.SpacesQuery``.

    ``None`` covers both reasons a thread may not be opened here — unknown and
    deleted — and the caller deliberately does not distinguish them: telling a
    caller that a space they cannot write to nevertheless exists is a
    disclosure, not a diagnosis (``ReadableFiles``' wording, and its reasoning).
    """

    async def get_active(self, ctx: ExecutionContext, space_id: Uuid) -> ActiveSpace | None: ...
