"""Media inbound port (02-port-contracts §2, "النمط نفسه يتكرّر لـ … media").

``MediaRequests`` is injected into the agent layer so the Image/Video agents can
queue a heavy generation job without importing the media module directly
(ARC-07/08) — the ``files.FilesQuery`` / ``knowledge.KnowledgeRetrieval``
precedent. 4.6-c deliberately deferred this file to 4.7-d, because until the
Outbox producer seam existed (4.7-d-1) there was nothing to publish the queued
job with.

The port returns a HANDLE, never a result: queuing is event-driven (D-04) and
the outcome is delivered later over WebSocket (Phase 5). An agent that awaited
a generated image here would hold an HTTP request open for minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class RequestedMedia:
    """A read-only handle on a freshly queued job — enough for the caller to
    name the job it should await, and nothing more (the ``FileView``
    precedent: a projection, not the aggregate)."""

    job_id: str
    status: str
    kind: str


class MediaRequests(Protocol):
    """Injected into agents so they never import the media module; validates,
    queues, and publishes ONE generation job per call.

    ``params`` stays a raw ``Json`` mapping at this boundary: its shape is
    kind-dependent and is validated downstream by ``GenParams`` (image:
    width/height/model · video: duration_seconds/model), so the port does not
    duplicate that rule — the same reasoning that keeps the numeric ceilings in
    ``RequestMedia`` rather than in the value object (INV-MJ3).
    """

    async def request(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        kind: str,
        prompt: str,
        params: Json,
    ) -> RequestedMedia: ...
