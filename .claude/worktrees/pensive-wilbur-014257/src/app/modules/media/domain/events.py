"""Media domain events (pure — 06-domain-models §8, docs/design/events).

In-memory domain events: plain frozen records with no shared base class (the
domain imports no framework code) — the ``files``/``knowledge`` event-module
precedent. Fields mirror the published wire schemas (``docs/design/events/
schemas/media.job.*.v1.json``) plus the envelope fields ``workspace_id``/
``occurred_at`` carried on the domain event itself, exactly like
``files.FileUploaded``/``knowledge.DocumentRegistered``. Dispatch to the event
bus / outbox happens in the application and infrastructure layers, not here.

All three are **global** events (04-event-catalog §4): ``MediaRequested`` is
consumed by the (future) ``media_worker``; ``MediaGenerated``/``MediaFailed``
notify the client over WebSocket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class MediaRequested:
    """A new generation job was queued (wire ``media.job.requested.v1``).

    ``params`` is the plain wire dict (``GenParams.as_dict()``), not the
    value object itself — the published schema's ``params`` property is a
    bare JSON ``object``.
    """

    job_id: str
    workspace_id: str
    kind: str
    prompt: str
    agent_key: str
    params: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class MediaGenerated:
    """A job finished generating successfully (wire
    ``media.job.generated.v1``)."""

    job_id: str
    workspace_id: str
    result_file_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class MediaFailed:
    """A job's generation failed (wire ``media.job.failed.v1``). INV-MJ2:
    recovering means submitting a brand-new job, never resubmitting this
    one."""

    job_id: str
    workspace_id: str
    reason: str
    occurred_at: datetime


MediaEvent = MediaRequested | MediaGenerated | MediaFailed
