"""Request/job-scoped execution context (02-port-contracts §0).

``ExecutionContext`` is the single carrier of tenant identity through every
layer. It is *stateless* and *injected* (10-code-standards §4) — never read
from a thread-local global. Its ``workspace_id`` drives both application-level
filtering and the ``SET LOCAL app.workspace_id`` RLS guard (DD-04).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Immutable per-request/per-job identity + correlation envelope."""

    workspace_id: Uuid
    user_id: Uuid | None
    correlation_id: Uuid
    roles: frozenset[str]
    request_id: Uuid | None = None
