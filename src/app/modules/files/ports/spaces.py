"""The space-existence seam this module needs to file a row under one
(``docs/spaces-backend-plan.md`` §3.1, step 6).

``files.files.space_id`` is an OPAQUE identifier: this module stores it,
filters by it and totals bytes by it, and knows nothing else about it. But an
id that names NOTHING is worse than no id at all — the row is then owned by an
axis that does not exist, invisible to every listing forever and uncountable
against every quota. So the value has to be proven real before it is written.

Knowing that means asking the ``spaces`` module, which this module must not
import: modules are siblings, and a nominal import would make ``files``
unbuildable without ``spaces`` (import-linter contract 4). So — the
``conversations/ports/files.py`` pattern, pointed the other way — the CONSUMER
declares the shape it needs here and the Composition Root binds ``spaces``' own
``SpacesQuery`` to it. The binding is structural: mypy checks it at the wiring
site, so a change to ``SpacesQuery.get_active`` turns that line red instead of
drifting silently.

``ActiveSpace`` exposes only the id, deliberately. ``SpaceView`` also carries
``name`` — for the error messages of the module that needs to say "file belongs
to space 'Research', not 'Drafts'" (§3.5, ``conversations``). ``files`` has no
such sentence to write: it asks one yes/no question, and a name it projected
would be space metadata that belongs on ``GET /spaces``.

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

    ``None`` covers both reasons nothing may be filed here — unknown and
    deleted — and the caller deliberately does not distinguish them: telling a
    caller that a space they cannot write to nevertheless exists is a
    disclosure, not a diagnosis (``ReadableFiles``' wording, and its reasoning).
    """

    async def get_active(self, ctx: ExecutionContext, space_id: Uuid) -> ActiveSpace | None: ...
