"""Wire DTOs for the Workflows router (`03-api-spec` §2) — Phase 6.1-د-2.

Pydantic v2 models, ``snake_case``, transcribed from the contract rather than
invented: ``WorkflowOut`` / ``WorkflowRunIn`` / ``WorkflowRunOut`` are §2's
three workflow shapes verbatim.

**``WorkflowRunOut.status`` is the field with a story.** The spec names it and
enumerates no values, and `01-data-model` stores no run row at all — the only
persistent trace of a run is its D-12 conversation. So the value is whatever
its SOURCE can honestly say: a live run handle reports ``running`` /
``completed`` / ``failed`` (6.1-د-1), while a run read back later is derived
from the transcript and can only distinguish ``completed`` from ``unknown``.
The router documents each side at its own route; the DTO deliberately does not
constrain the field to a ``Literal``, because doing so would freeze into the
wire contract a vocabulary that a real runs table is expected to widen.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkflowOut(BaseModel):
    """One catalog entry (`03 §2`). ``steps`` is the ordered list of agent keys
    the pipeline runs — the definition's own step order, which is the only part
    of a ``WorkflowStep`` a client can act on (``input_map`` is internal
    plumbing, D-09)."""

    key: str
    name: str
    steps: list[str]


class WorkflowRunIn(BaseModel):
    """The body of ``POST /workflows/{key}/run`` (`03 §2`).

    ``input`` is the initial blackboard the first step is projected from — free
    JSON by contract, since every workflow defines its own shape.

    ``space_id`` is REQUIRED (spaces plan step 12, §3.7), and unlike
    ``AgentInvokeIn``'s it has no ``None`` case at all: a run always opens its
    own D-12 thread, so there is never an existing thread whose space it could
    inherit.
    """

    space_id: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False


class WorkflowRunOut(BaseModel):
    """A run, as the wire sees it (`03 §2`).

    ``run_id`` and ``conversation_id`` are equal in v1 — deliberately, see
    ``WorkflowRun.run_id``: there is no runs table, so a distinct ``run_id``
    would be an identifier nothing could resolve. Both fields stay so a future
    store can separate them without breaking a client.
    """

    run_id: str
    conversation_id: str
    status: str
