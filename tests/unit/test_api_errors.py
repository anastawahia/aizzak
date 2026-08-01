"""Unit tests for the RFC 9457 problem builders (``app/api/errors.py``,
Phase 5.3-ب — the API layer's first filled module; retitled by 6.2).

What these pin: the exact 03 §4 field set (and that absent fields are
OMITTED, never null), the ``https://errors.platform/<code>`` type namespace,
that ``title`` comes from ``ERROR_CATALOG`` and falls back to derivation only
for a code we did not mint, and — the streaming-path contract — that decision
B1's minimal ``{code, status, detail}`` payload translates into the full
problem object 03 §3.1 shows on the wire, degrading safely on malformed input
instead of raising inside a live stream.
"""

from __future__ import annotations

from app.api.errors import problem, problem_from_error, problem_from_event
from app.framework.errors import ERROR_CATALOG, AppError, NotFoundError

_CORRELATION = "018f0000-0000-7000-8000-0000000000cc"


def test_problem_carries_the_exact_catalog_field_set() -> None:
    body = problem(
        "files.too_large",
        413,
        detail="size_bytes=73400320 exceeds limit 52428800",
        correlation_id=_CORRELATION,
        instance="/api/v1/files",
        errors=[{"field": "size_bytes", "message": "must be <= 52428800"}],
    )

    assert body == {
        "type": "https://errors.platform/files.too_large",
        "title": "File exceeds maximum size",
        "status": 413,
        "code": "files.too_large",
        "detail": "size_bytes=73400320 exceeds limit 52428800",
        "instance": "/api/v1/files",
        "correlation_id": _CORRELATION,
        "errors": [{"field": "size_bytes", "message": "must be <= 52428800"}],
    }


def test_absent_fields_are_omitted_not_nulled() -> None:
    """03 §3.1's error-event example carries only the fields it has; a
    ``"detail": null`` key would be an invented value."""
    body = problem("agent.failed", 502)

    assert set(body) == {"type", "title", "status", "code"}


def test_titles_come_from_the_catalog() -> None:
    """RFC 9457 wants a title that reads the same on every occurrence, so 6.2
    curates it per code instead of spelling the code differently."""
    assert problem("agent.failed", 502)["title"] == ERROR_CATALOG["agent.failed"].title
    assert problem("agent.failed", 502)["title"] == "Agent execution failed"
    assert problem("usage.quota_exceeded", 429)["title"] == "Usage quota exceeded"
    assert problem("common.internal", 500)["title"] == "Unexpected internal error"


def test_an_uncatalogued_code_still_gets_a_title() -> None:
    """The derivation 5.3-ب shipped survives as the FALLBACK: a problem with
    no ``title`` would be malformed, and ``problem_from_event`` can be handed
    a code that never came from us."""
    assert "foreign.thing" not in ERROR_CATALOG
    assert problem("foreign.thing", 500)["title"] == "Foreign thing"
    assert problem("", 500)["title"] == "Error"


def test_an_app_error_passes_its_own_code_and_status_through() -> None:
    exc = NotFoundError("agent 'nope' is not registered", code="agent.unknown")

    body = problem_from_error(exc, correlation_id=_CORRELATION, instance="/api/v1/agents/nope")

    assert body["type"] == "https://errors.platform/agent.unknown"
    assert body["status"] == 404
    assert body["code"] == "agent.unknown"
    assert body["detail"] == "agent 'nope' is not registered"
    assert body["instance"] == "/api/v1/agents/nope"
    assert body["correlation_id"] == _CORRELATION


def test_a_b1_event_payload_becomes_the_wire_problem() -> None:
    """The in-flight path: the executor's ``{code, status, detail}`` arrives
    on the SSE wire as the full problem object, correlation id attached."""
    body = problem_from_event(
        {"code": "agent.failed", "status": 502, "detail": "provider exploded"},
        correlation_id=_CORRELATION,
    )

    assert body == {
        "type": "https://errors.platform/agent.failed",
        "title": "Agent execution failed",
        "status": 502,
        "code": "agent.failed",
        "detail": "provider exploded",
        "correlation_id": _CORRELATION,
    }


def test_a_malformed_event_payload_degrades_instead_of_raising() -> None:
    """This translation runs INSIDE a live stream — raising would cut the
    connection with no error frame at all. Empty/foreign shapes degrade to
    ``common.internal``/500; a boolean status is NOT accepted as an int."""
    for payload in ({}, {"code": "", "status": True, "detail": 7}, {"code": 3}):
        body = problem_from_event(payload)  # type: ignore[arg-type]
        assert body["code"] == "common.internal"
        assert body["status"] == 500
        assert "detail" not in body


def test_the_fallback_never_masks_a_wellformed_code_with_odd_detail() -> None:
    """Only the broken PIECE degrades: a real code with a non-string detail
    keeps the code and drops the detail."""
    body = problem_from_event({"code": "agent.failed", "status": 502, "detail": 7})

    assert body["code"] == "agent.failed"
    assert body["status"] == 502
    assert "detail" not in body


def test_base_app_error_defaults_still_map() -> None:
    """The bare base default is ``common.internal``/500 (6.2) — 03 §4's own
    entry for an unexpected failure. It used to be ``common.error``, a code
    the catalog never defined and no client could switch on."""
    body = problem_from_error(AppError("boom"))

    assert body["type"] == "https://errors.platform/common.internal"
    assert body["code"] == "common.internal"
    assert body["status"] == 500


def test_a_code_alone_decides_the_status() -> None:
    """6.2: the status is a property of the PROBLEM, not of the exception
    class the raiser reached for. ``agent.failed`` is a 502 with no 502
    subclass in the hierarchy and no ``status=`` at the raise site."""
    assert problem_from_error(AppError("provider exploded", code="agent.failed"))["status"] == 502
    assert (
        problem_from_error(NotFoundError("no key", code="credentials.none_available"))["status"]
        == 409
    )


def test_an_explicit_status_still_wins_over_the_catalog() -> None:
    """A code the catalog does not carry — a foreign one, or one mid-fold —
    must stay expressible...

    ...and so must a CATALOGUED code with a status that disagrees, which is
    the case that actually matters: ``_in_band_error`` rebuilds an ``AppError``
    from a B1 event and must echo that event's own status rather than quietly
    correcting it to the catalog's. No production site relies on this today,
    which is exactly why it needs a test — a mutant that made the catalog win
    unconditionally survived the foreign-code case alone.
    """
    assert problem_from_error(AppError("odd", code="foreign.thing", status=418))["status"] == 418
    assert problem_from_error(AppError("x", code="agent.failed", status=503))["status"] == 503
