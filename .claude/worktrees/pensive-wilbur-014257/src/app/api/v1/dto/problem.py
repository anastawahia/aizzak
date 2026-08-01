"""The RFC 9457 problem body as a WIRE model (03-api-spec §4 · DD-05) —
Phase 6.2-ب.

``api/errors.py`` BUILDS problems as plain dicts, and deliberately keeps doing
so: those builders run inside a live SSE stream and inside exception handlers,
where constructing (and validating) a model would add a failure mode to the
one code path that must never fail. This model exists for the other half of
the contract — the *documented* one. Without it, FastAPI's generated schema
described only the happy path of every operation, while ``openapi.yaml``
attaches ``default: Problem`` to all of them (03 §1); a client generated from
our live ``/openapi.json`` had no error type at all.

Kept faithful to ``components.schemas.Problem`` in ``openapi.yaml``, including
which fields are REQUIRED: ``type``/``title``/``status``/``code``/
``correlation_id`` always ship; ``detail``/``instance``/``errors`` are present
only when there is something to say (``api/errors.problem`` omits them rather
than emitting nulls, and this model must not promise otherwise).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProblemError(BaseModel):
    """One field-level validation failure inside ``errors[]`` (03 §4)."""

    field: str
    message: str


class ProblemDetails(BaseModel):
    """``application/problem+json`` — the body every failing route returns."""

    type: str = Field(examples=["https://errors.platform/files.too_large"])
    title: str = Field(examples=["File exceeds maximum size"])
    status: int = Field(examples=[413])
    code: str = Field(
        description="stable machine code, e.g. files.too_large",
        examples=["files.too_large"],
    )
    correlation_id: str
    detail: str | None = None
    instance: str | None = None
    errors: list[ProblemError] | None = None
