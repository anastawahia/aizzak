"""The summary-export seam (BE-RAG-012).

One outbound port and two DTOs. The module says WHAT a rendered summary is;
which library turns Markdown into those bytes is the Composition Root's
business, and the adapter lives in ``app.infrastructure.rendering``.

**``render`` is synchronous, deliberately.** Rendering is CPU work with no
I/O in it — no network, no database — and an ``async def`` that never awaits
is a lie about a function's cost that makes callers stop thinking about it.
Declaring it sync is what forces the caller to decide where the work goes,
and ``ExportSummary`` answers that with ``asyncio.to_thread`` so a render
never blocks the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ExportFormat(StrEnum):
    """The formats a summary can be downloaded as (BE-RAG-012).

    Two, and the pair is the Alpha contract's: a summary is prose people
    file, print or paste into a report. PDF is the one that survives being
    sent to someone; DOCX is the one that survives being edited.
    """

    PDF = "pdf"
    DOCX = "docx"


@dataclass(frozen=True, slots=True)
class RenderedSummary:
    """One rendered file, ready to be handed to the client.

    ``content_type`` travels with the bytes rather than being derived at the
    route from the requested format: the renderer is what decided what it
    produced, and a second mapping elsewhere is a second place to be wrong
    about it.
    """

    content: bytes
    content_type: str


class SummaryRenderer(Protocol):
    """Turn a summary's Markdown into a downloadable document.

    ``title`` and ``direction`` are passed rather than inferred inside the
    renderer: the direction of the text is a fact the summary's ``lang``
    already carries, and asking a renderer to guess it from the body would
    make the export of an Arabic summary depend on how much Arabic happened
    to reach the first paragraph.
    """

    def render(
        self, markdown: str, fmt: ExportFormat, *, title: str, rtl: bool
    ) -> RenderedSummary: ...
