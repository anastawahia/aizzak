"""Media DTOs — the wire shapes of ``/api/v1/media`` (03-api-spec §2, Phase
6.1-هـ-3).

Verbatim from the spec's sketch. ``kind`` on the INPUT is the spec's own
``Literal['image','video']`` — unlike ``WorkflowRunOut.status`` (§3.58), where
a ``Literal`` on an *output* would have promised a closed vocabulary the
platform doesn't own; an input Literal only constrains what clients may send,
which is exactly what the contract says. ``MediaJobOut`` carries the queued
job's full face (``result_file_id``/``error`` are ``null`` at 202-time and
filled in by the Phase-5 worker) — the reason ``submit`` returns the whole
aggregate (§3.60).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MediaJobCreateIn(BaseModel):
    kind: Literal["image", "video"]
    prompt: str
    agent_key: str
    params: dict[str, Any] = Field(default_factory=dict)


class MediaJobOut(BaseModel):
    id: str
    kind: str
    status: str
    result_file_id: str | None
    error: str | None
    created_at: datetime
