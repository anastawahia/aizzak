"""The consumer-idempotency claim (DD-09), as ``drawings`` consumes it.

Bound in the worker's composition root to the platform's ``SqlProcessedEventLedger`` with the
group name closed over (``cg.electrical.document``), so the application layer names neither
the group nor the table. ``claim`` joins the caller's unit of work: the claim and the effect
commit or roll back together. ``False`` means the event was already processed.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class ProcessedEvents(Protocol):
    async def claim(self, ctx: ExecutionContext, *, event_id: Uuid) -> bool: ...
