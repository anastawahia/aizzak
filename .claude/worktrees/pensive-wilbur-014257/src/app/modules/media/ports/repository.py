"""Media persistence port (02-port-contracts §2: "the same pattern repeats
for media").

Outbound repository contract for the ``MediaJob`` aggregate. Every method
takes ``ExecutionContext`` first so the SQL adapter can apply the RLS guard
(``SET LOCAL app.workspace_id``) and the ``WHERE workspace_id`` filter
(DD-04) — the files/knowledge port precedent.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.media.domain.entities import MediaJob


class MediaJobRepository(Protocol):
    """Tenant-scoped persistence for the ``MediaJob`` aggregate.

    ``save`` uses an optimistic lock on ``version`` (02 §2), advanced by the
    adapter rather than the domain — a stale write (e.g. two redelivered
    ``RunMediaJob`` workers racing on the same job) surfaces as a conflict at
    the adapter instead of silently clobbering the other's transition.

    No ``list``/``list_by_status`` method: the current use-cases
    (``RequestMedia``, ``RunMediaJob``, ``GetJobStatus``) only ever fetch a
    single job by id. Add a listing method here only once a use-case actually
    needs one (12-module-authoring-guide: no unconsumed surface).
    """

    async def get(self, ctx: ExecutionContext, job_id: Uuid) -> MediaJob | None: ...

    async def add(self, ctx: ExecutionContext, job: MediaJob) -> None: ...

    async def save(self, ctx: ExecutionContext, job: MediaJob) -> None: ...
