"""The PDF inspection port, bound to the ele-parse client (DEC-04).

Errors are ``drawings.domain.errors``: ``PdfRejected(code, detail)`` for a file ele-parse
refuses (corrupt, encrypted, not a PDF), ``PageRangeFailed(reason)`` for a range that timed out
or hit the memory limit, and ``InspectionUnavailable`` for a transport fault (transient:
raised so the engine redelivers). No transport type crosses this port.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.modules.electrical.drawings.domain.pages import PageSignals, PdfSummary
from app.modules.electrical.drawings.ports.source import StagedSource


class PdfInspection(Protocol):
    async def summary(self, source: StagedSource) -> PdfSummary: ...

    async def pages(self, source: StagedSource, first: int, last: int) -> Sequence[PageSignals]: ...
