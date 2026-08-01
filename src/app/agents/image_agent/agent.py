"""``ImageAgent`` — queues an image-generation job (FR-20.3, event-driven D-04).

All behaviour is the shared ``MediaRequestAgent`` (``app.agents._shared``); this
plugin only binds its manifest and the media ``KIND``. It is the one concrete
``BaseAgent`` subclass in this package, so the loader registers exactly it.
"""

from __future__ import annotations

from app.agents._shared import MediaRequestAgent
from app.agents.image_agent.manifest import METADATA


class ImageAgent(MediaRequestAgent):
    metadata = METADATA
    KIND = "image"
