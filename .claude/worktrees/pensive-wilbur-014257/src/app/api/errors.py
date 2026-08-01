"""RFC 9457 Problem Details builders (03-api-spec §4 · DD-05).

First filled by 5.3-ب, because the STREAMING contract needed it before the
routers did: 03 §3.1's terminal ``error`` event carries a full problem object
(``{"type": "https://errors.platform/agent.failed", "title": …, "status":
502, "code": …, "correlation_id": …}``), while everything below the API layer
speaks decision B1's minimal ``{code, status, detail}``. Translating between
the two is PRESENTATION work, so it lives here — the executor/orchestrator
stay ignorant of RFC 9457, and Phase 6's exception handlers will reuse these
same builders for ``application/problem+json`` bodies (6.2 owns registering
them; this module only knows how to build the dict).

``title`` comes from ``ERROR_CATALOG`` (6.2): 5.3-ب derived it from the code
("Files too large") precisely because the catalog did not exist yet and a hand
table here would have been a second copy that drifts. Now there is one table,
in the kernel where the raisers can also reach it, and this module reads it.
The derivation SURVIVES as the fallback — ``problem_from_event`` can be handed
a code we never minted, and a missing title would be a malformed problem.
"""

from __future__ import annotations

from app.framework.errors import AppError, spec_for
from app.framework.types import Json

# The problem `type` namespace, verbatim from every 03 §4 example.
PROBLEM_TYPE_BASE = "https://errors.platform/"

# What a malformed in-band error payload degrades to. Our own executor always
# emits well-formed B1 events, so reaching these means a foreign/buggy
# producer — degrade to the catalog's "unexpected" entry rather than crash
# the encoder mid-stream.
_FALLBACK_CODE = "common.internal"
_FALLBACK_STATUS = 500


def _title_for(code: str) -> str:
    """The catalog's curated title, or a deterministic one derived from the
    code (dots/underscores to spaces, first letter up) for a code we did not
    mint. Never empty — an absent ``title`` would be a malformed problem."""
    spec = spec_for(code)
    if spec is not None:
        return spec.title
    words = code.replace(".", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Error"


def problem(
    code: str,
    status: int,
    *,
    detail: str | None = None,
    correlation_id: str | None = None,
    instance: str | None = None,
    errors: list[Json] | None = None,
) -> Json:
    """One RFC 9457 problem object (03 §4's exact field set).

    ``None`` fields are OMITTED, not emitted as nulls — the 03 §3.1 error
    event example carries only the fields it has, and ``errors[]`` exists
    only for validation failures (03 §4: "يملأ ``errors[]``").
    """
    body: Json = {
        "type": f"{PROBLEM_TYPE_BASE}{code}",
        "title": _title_for(code),
        "status": status,
        "code": code,
    }
    if detail is not None:
        body["detail"] = detail
    if instance is not None:
        body["instance"] = instance
    if correlation_id is not None:
        body["correlation_id"] = correlation_id
    if errors is not None:
        body["errors"] = errors
    return body


def problem_from_error(
    exc: AppError,
    *,
    correlation_id: str | None = None,
    instance: str | None = None,
) -> Json:
    """An ``AppError`` (the shared 10 §5 hierarchy) as a problem object —
    the pre-flight path: the exception's own stable ``code``/``status``
    carry straight through."""
    return problem(
        exc.code,
        exc.status,
        detail=exc.detail,
        correlation_id=correlation_id,
        instance=instance,
    )


def problem_from_event(data: Json, *, correlation_id: str | None = None) -> Json:
    """Decision B1's in-band ``error`` payload (``{code, status, detail}``)
    as a problem object — the in-flight path.

    Defensive on shape (R6 discipline): the payload normally comes from our
    own executor, but this runs INSIDE a live stream where raising would cut
    the connection with no error at all — a malformed payload degrades to
    ``common.internal``/500 instead.
    """
    code = data.get("code")
    status = data.get("status")
    detail = data.get("detail")
    return problem(
        code if isinstance(code, str) and code else _FALLBACK_CODE,
        status if isinstance(status, int) and not isinstance(status, bool) else _FALLBACK_STATUS,
        detail=detail if isinstance(detail, str) else None,
        correlation_id=correlation_id,
    )
