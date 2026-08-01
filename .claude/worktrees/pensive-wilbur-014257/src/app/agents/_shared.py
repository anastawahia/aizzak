"""Shared base for the media-generation agents (image / video), 4.6-c.

The Image and Video agents are identical bar the media ``KIND``: each takes a
``prompt`` (+ optional ``params``), queues ONE heavy generation job via the
injected ``media`` seam, and yields a single ``final`` naming the queued job.
The work is **event-driven (D-04)** — the agent never waits for the result or
streams tokens; the outcome is delivered later over WebSocket (Phase 5).

This base lives in a ``_``-prefixed module the ``PluginLoader`` ignores (it
scans only non-``_`` child PACKAGES), so the two plugins stay DRY without
importing each other (``agents-independent`` holds — a shared ancestor here is
not a cross-plugin import). Each plugin's own ``agent.py`` defines the ONE
concrete ``BaseAgent`` subclass the loader registers (``__module__`` in the
plugin package); ``MediaRequestAgent`` itself lives in ``app.agents._shared``,
so the loader never counts it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar

from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest, BaseAgent
from app.framework.errors import AppError, ValidationError


class MediaRequestAgent(BaseAgent):
    """Queues a heavy media job and returns its handle (no streaming, D-04)."""

    KIND: ClassVar[str]  # each concrete subclass sets "image" | "video"

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        prompt = self._required_str(req, "prompt")
        media = self.deps.media
        if media is None:
            raise AppError(
                detail=f"{self.metadata.key} has no media seam bound",
                code="common.internal",
                status=500,
            )
        raw_params = req.input.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        view = await media.request(
            self.ctx, agent_key=self.metadata.key, kind=self.KIND, prompt=prompt, params=params
        )
        # A single terminal `final` — the job is queued; the result arrives out
        # of band (Phase 5). No `token` events: generation is not streamed here.
        yield AgentEvent(
            type="final",
            data={"job_id": view.job_id, "status": view.status, "kind": view.kind},
        )

    @staticmethod
    def _required_str(req: AgentRequest, key: str) -> str:
        value = req.input.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"a media agent requires a non-empty {key!r}")
        return value
