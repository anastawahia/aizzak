"""``video_agent`` manifest (11 §2, FR-20.4)."""

from __future__ import annotations

from app.framework.agent_runtime.metadata import AgentMetadata

METADATA = AgentMetadata(
    key="video_agent",
    name="Video Generation Agent",
    version="1.0.0",
    description="Queues a video-generation job (heavy work, event-driven).",
    capabilities=frozenset({"video_generation"}),
    # 6.4-ب: `media:generate` — the value shipped here since 4.6-ج — has never
    # existed in 05 §1.2. Nothing read this field until the guards did, and
    # `is_allowed` denies an unparseable permission, so this agent would have
    # been uninvokable by every role including `owner`. The catalog's name for
    # "may queue a generation job" is `media:create`, which is also what
    # `POST /media/jobs` — the route this agent ends up driving — requires.
    required_permissions=frozenset({"agents:invoke", "media:create"}),
)
