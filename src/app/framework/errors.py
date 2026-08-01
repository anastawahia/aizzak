"""Application/API error hierarchy + the error CATALOG (10-code-standards §5,
03-api-spec §4, DD-05).

The domain of each module stays pure and raises stdlib exceptions or its own
module-local errors. The *application* and *API* layers work with this shared
hierarchy: every error carries a stable machine ``code`` and an HTTP
``status``, which the API's RFC 9457 handler maps to
``application/problem+json``. Adapters translate technical failures into these
errors — they never leak ``asyncpg.Error``/driver types upward.

**Phase 6.2 put the catalog here, not in ``app.api``.** The rendering of a
problem (the ``type`` URI, the media type, the response headers) is API work
and stays in ``api/errors.py``; but the *code → status → title* table is the
contract itself, and its readers are the raisers — which live in
``app.modules``, ``app.framework`` and ``app.infrastructure``, none of which
may import ``app.api`` (import-linter contract 1). A table in the API layer
would therefore have been a table nobody could consult, leaving every raise
site to restate a status from memory. Here it is the kernel's, so:

* a ``code`` alone determines its ``status`` (see ``AppError.__init__``) —
  the status is a property of the PROBLEM, not of the exception class a
  caller happened to reach for;
* ``api/errors.py`` reads the ``title`` from one place instead of keeping a
  second, drifting copy;
* ``tests/unit/test_error_catalog.py`` scans the source both ways: no code is
  emitted that the catalog does not define, and no catalog entry exists that
  nothing emits. The catalog is a MIRROR of behaviour, never a wish list —
  which is why 6.2 removed the two 03 §4 entries v1 has no site for (see the
  ``knowledge``/``files`` notes below).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class ProblemSpec:
    """One catalog row: the HTTP status a code always carries, and the short
    human ``title`` RFC 9457 asks for.

    ``title`` is curated rather than derived from the code, because RFC 9457
    wants "a short, human-readable summary of the problem type" that stays the
    same from occurrence to occurrence — and 03 §4's own example says "File
    exceeds maximum size", not "Files too large". A derived title is still the
    fallback for a code that is not in the catalog (``api/errors.py``), which
    can only happen for a payload that did not come from us.
    """

    status: int
    title: str


# --------------------------------------------------------------------------- #
# The catalog — 03-api-spec §4, verbatim in scope, exhaustive in practice.     #
# --------------------------------------------------------------------------- #
# Grouped by prefix; the prefix is the module (or `common`/`auth`/`authz`) the
# problem belongs to, and it is the first thing a client switches on.
ERROR_CATALOG: Final[Mapping[str, ProblemSpec]] = MappingProxyType(
    {
        # -- common ------------------------------------------------------- #
        # `common.internal` is also the BASE default: an `AppError` raised with
        # no code at all is, by definition, the unexpected failure this names.
        # (Before 6.2 the default was `common.error`, a code 03 §4 never
        # defined and no client could switch on.)
        "common.internal": ProblemSpec(500, "Unexpected internal error"),
        "common.not_found": ProblemSpec(404, "Resource not found"),
        "common.conflict": ProblemSpec(409, "Conflicting state"),
        # ONE validation code, not two. Until 6.2 a DTO failure caught by
        # FastAPI answered `common.validation_error` while the identical
        # invariant checked one layer deeper answered `common.validation` —
        # the same problem under two names, split by nothing a client can see
        # or predict (§3.66 ي). 03 §4 names only this one.
        "common.validation_error": ProblemSpec(422, "Request validation failed"),
        # API-02's cursor is opaque; a client that mangles one gets a shape
        # error, not a 404 for the page it asked about (6.3 owns the cursor).
        "common.invalid_cursor": ProblemSpec(422, "Malformed pagination cursor"),
        "common.rate_limited": ProblemSpec(429, "Rate limit exceeded"),
        # The GENERIC size/type pair, distinct from the `files.*` ones on
        # purpose: `files.too_large` is the upload limit (01 §2.6), while this
        # is any other bounded payload — e.g. an agent reading a file into a
        # prompt budget (`agent_runtime/file_reading.py`), which is not an
        # upload and must not claim to be one.
        "common.too_large": ProblemSpec(413, "Payload exceeds maximum size"),
        "common.unsupported_type": ProblemSpec(415, "Unsupported content type"),
        # Starlette's router, not ours: a path that exists under another verb.
        # No exception class raises it — the 6.2-ب handler for
        # `StarletteHTTPException` is its only site (nothing in `src/app`
        # raises `HTTPException` at all).
        "common.method_not_allowed": ProblemSpec(405, "Method not allowed"),
        # -- auth / authz --------------------------------------------------- #
        "auth.missing_token": ProblemSpec(401, "Missing bearer token"),
        "auth.invalid_token": ProblemSpec(401, "Invalid or expired token"),
        "authz.forbidden": ProblemSpec(403, "Insufficient permissions"),
        # -- agents / workflows --------------------------------------------- #
        "agent.unknown": ProblemSpec(404, "Unknown agent"),
        "agent.failed": ProblemSpec(502, "Agent execution failed"),
        "workflow.unknown": ProblemSpec(404, "Unknown workflow"),
        # -- files ---------------------------------------------------------- #
        # `files.not_ready` (409 in 03 §4) is deliberately ABSENT: v1 has no
        # site for it. `FilesQuery.get_readable` collapses "missing" and "not
        # ready" into one `None` on purpose — a read face must not tell a
        # caller that a file it cannot read nevertheless exists — and the
        # file routes answer a not-ready file with a body (`download_url:
        # null`), not a problem (§3.61). A code no server can emit is a
        # promise no client can use.
        "files.too_large": ProblemSpec(413, "File exceeds maximum size"),
        "files.unsupported_type": ProblemSpec(415, "Unsupported file type"),
        "files.too_many": ProblemSpec(409, "Workspace file limit reached"),
        # -- knowledge ------------------------------------------------------ #
        # `knowledge.not_indexed` (409 in 03 §4) is absent for the same reason
        # (§3.64): none of the three knowledge routes is per-document
        # retrieval, and forcing the code onto "search matched nothing" would
        # turn a legitimate empty result into a failure.
        "knowledge.unsupported_type": ProblemSpec(415, "Unsupported document type"),
        "knowledge.empty_content": ProblemSpec(422, "Document has no content"),
        "knowledge.parse_failed": ProblemSpec(422, "Document could not be parsed"),
        "knowledge.search_unavailable": ProblemSpec(503, "Knowledge search is not available"),
        # -- media ---------------------------------------------------------- #
        "media.invalid_params": ProblemSpec(422, "Invalid generation parameters"),
        # -- credentials ----------------------------------------------------- #
        "credentials.provider_unknown": ProblemSpec(422, "Unknown provider"),
        "credentials.none_available": ProblemSpec(409, "No credential available for provider"),
        # -- integrations ---------------------------------------------------- #
        "integrations.connector_unknown": ProblemSpec(422, "Unknown connector"),
        "integrations.not_connected": ProblemSpec(409, "Connector is not connected"),
        "integrations.oauth_failed": ProblemSpec(502, "OAuth exchange failed"),
        "integrations.oauth_state_invalid": ProblemSpec(422, "Invalid or expired OAuth state"),
        "integrations.too_many": ProblemSpec(409, "Workspace connector limit reached"),
        "integrations.mcp_transport_unsupported": ProblemSpec(422, "Unsupported MCP transport"),
        "integrations.mcp_unreachable": ProblemSpec(502, "MCP server unreachable"),
        "integrations.tools_unavailable": ProblemSpec(503, "Tool catalog is not available"),
        # -- usage ------------------------------------------------------------ #
        "usage.quota_exceeded": ProblemSpec(429, "Usage quota exceeded"),
        "usage.budget_exceeded": ProblemSpec(429, "Budget exceeded"),
    }
)


def spec_for(code: str) -> ProblemSpec | None:
    """The catalog row for ``code``, or ``None`` for a code we did not mint."""
    return ERROR_CATALOG.get(code)


class AppError(Exception):
    """Base for all application-surfaced errors. Maps to an RFC 9457 problem."""

    code: str = "common.internal"
    status: int = 500

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
    ) -> None:
        if code is not None:
            self.code = code
            # The code NAMES the problem, so the catalog — not the class the
            # caller reached for — decides its status. Every site agrees with
            # its class today, so this changes no behaviour; it removes the
            # possibility of disagreeing, and it is why a 502 needs no
            # `AppError` subclass of its own (`agent.failed`,
            # `integrations.oauth_failed`, `integrations.mcp_unreachable`
            # each used to restate `status=502` by hand).
            spec = ERROR_CATALOG.get(code)
            if spec is not None:
                self.status = spec.status
        # An explicit `status` still wins: a code the catalog does not carry
        # (a not-yet-folded one, a foreign one) must remain expressible.
        if status is not None:
            self.status = status
        self.detail = detail or self.code
        super().__init__(self.detail)


class NotFoundError(AppError):
    """Requested resource does not exist (or is not visible to this tenant)."""

    code = "common.not_found"
    status = 404


class ConflictError(AppError):
    """Optimistic-lock or uniqueness conflict (10-code-standards §6)."""

    code = "common.conflict"
    status = 409


class ValidationError(AppError):
    """Input failed a business/format invariant."""

    code = "common.validation_error"
    status = 422


class ForbiddenError(AppError):
    """Authenticated principal lacks the required permission (RBAC)."""

    code = "authz.forbidden"
    status = 403


class UnauthorizedError(AppError):
    """Missing or invalid authentication."""

    code = "auth.invalid_token"
    status = 401


class TooLargeError(AppError):
    """Payload exceeds a configured numeric limit (07-nfr-slo §4)."""

    code = "common.too_large"
    status = 413


class UnsupportedTypeError(AppError):
    """Unsupported media/content type."""

    code = "common.unsupported_type"
    status = 415


class RateLimitedError(AppError):
    """Rate limit / quota exceeded — carries an optional ``Retry-After`` hint.

    The hint was documented here from the start and had no producer until 3.79
    (03 §4: "لا مُنتِج له"). It does now — ``LimitDecision.retry_after_s``,
    computed from the binding limit's period boundary — so the attribute is
    real, and ``api/main.py``'s ``AppError`` handler renders it as the RFC 9110
    ``Retry-After`` header on the 429.

    ``None`` stays the default and the common case: a rate limit whose reset
    nobody can compute must ship NO header rather than a plausible-looking
    number, which is the whole reason v1 shipped without one.
    """

    code = "common.rate_limited"
    status = 429

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
        retry_after_s: int | None = None,
    ) -> None:
        super().__init__(detail, code=code, status=status)
        # Guarded rather than trusted: RFC 9110's delay-seconds is a
        # non-negative integer, and a `Retry-After: 0` (or negative) header
        # invites the immediate retry a 429 exists to prevent. A bad value is
        # dropped, never clamped into a number we made up.
        self.retry_after_s = (
            retry_after_s if retry_after_s is not None and retry_after_s > 0 else None
        )
