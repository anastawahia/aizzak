"""Drawings domain errors (pure).

Module-local hierarchy, like ``files/domain/errors.py``: the domain imports no framework
code, and the application layer catches these at its boundary and maps them onto the shared
``AppError`` hierarchy with the catalogue codes (LLD 3.3.13).
"""

from __future__ import annotations

from app.modules.electrical.drawings.domain.pages import StageReason


class DrawingsError(Exception):
    """Base for every drawings domain-rule violation."""


class InvalidProfile(DrawingsError):
    """A drawing profile body failed validation (NFR-CFG-02). ``path`` names the field
    (``classification.garbled_ratio``) so the caller can point at it (-> 422)."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


class InvalidDrawingInput(DrawingsError):
    """A project or document value failed a format / range rule (-> 422)."""


class DocumentStateError(DrawingsError):
    """A document transition that its status does not allow (-> 409)."""


class JobStateError(DrawingsError):
    """A job transition that its status does not allow (-> 409 ``electrical.job_conflict``)."""


class PdfRejected(DrawingsError):
    """ele-parse refuses the file: ``code`` is ``not_pdf``, ``corrupt`` or ``encrypted``.
    Permanent: the job fails with ``electrical.pdf_rejected`` and ``detail`` as its message."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class PageRangeFailed(DrawingsError):
    """A page range timed out or hit the memory limit (or failed to parse). The caller
    re-asks page by page and records only the failing page as failed."""

    def __init__(self, reason: StageReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class InspectionUnavailable(DrawingsError):
    """ele-parse cannot be reached or is busy: a TRANSIENT fault, raised as is so the
    event engine redelivers the message (arch D-19)."""
