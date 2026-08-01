"""``VideoAgent`` — queues a video-generation job (FR-20.4, event-driven D-04).

All behaviour is the shared ``MediaRequestAgent`` (``app.agents._shared``); this
plugin only binds its manifest and the media ``KIND``.
"""

from __future__ import annotations

from app.agents._shared import MediaRequestAgent
from app.agents.video_agent.manifest import METADATA


class VideoAgent(MediaRequestAgent):
    metadata = METADATA
    KIND = "video"
