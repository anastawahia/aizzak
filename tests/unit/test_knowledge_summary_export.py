"""Unit tests for summary export (BE-RAG-012) — the REAL renderer, against
real WeasyPrint and real ``python-docx``.

No stub, deliberately. The one thing this feature can get wrong that matters
is Arabic: PDF text shaping is exactly where right-to-left breaks, and a
renderer double asserting "render was called" would pass just as happily
against a library that emits disconnected letters in reverse order. So these
tests read the bytes back out and check the script survived.

That makes them slower than the rest of the unit suite and dependent on the
Pango/Cairo stack being present — which is the point: the Dockerfile installs
those libraries and the fonts, and a test that skipped when they were missing
would go green in exactly the environment where the export is broken.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import pypdf
import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError
from app.modules.knowledge.adapters.summary_renderer import MarkdownSummaryRenderer
from app.modules.knowledge.application.use_cases import ExportSummary, _is_rtl
from app.modules.knowledge.domain.entities import Summary
from app.modules.knowledge.domain.value_objects import SummaryKind, SummaryLanguage
from app.modules.knowledge.ports.export import ExportFormat
from tests.unit.support_knowledge import InMemorySummaryRepository

_W1 = "ws1"
_AT = datetime(2026, 8, 11, 9, 0, 0, tzinfo=UTC)

_ARABIC_BODY = """# نظرة عامة

هذا المستند يشرح سياسة الاسترجاع في المنصّة. الحروف يجب أن تتصل ببعضها.

- البند الأول
- البند الثاني
"""

_ENGLISH_BODY = """# Overview

This document explains the platform's retrieval policy.

- First point
- Second point
"""


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_W1, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


def _summary(*, text: str, lang: SummaryLanguage) -> Summary:
    return Summary(
        id="sum-1",
        workspace_id=_W1,
        document_id="doc-1",
        kind=SummaryKind.FULL,
        lang=lang,
        text=text,
        model="test-model",
        source_chunks=4,
        truncated=False,
        built_at=_AT,
    )


def _has_arabic(text: str) -> bool:
    return any("؀" <= char <= "ۿ" for char in text)


def _docx_text(content: bytes) -> str:
    """Read a DOCX's body text without depending on ``python-docx`` to read
    back what it wrote. A ``.docx`` is a zip with an XML part in it, and
    going at the XML directly is what makes this an assertion about the FILE
    rather than about one library round-tripping its own output."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


# --------------------------------------------------------------------------- #
# The renderer                                                                 #
# --------------------------------------------------------------------------- #


def test_an_arabic_summary_survives_into_the_pdf_as_arabic() -> None:
    """The acceptance bar for the whole export decision. WeasyPrint was
    chosen over ReportLab because Pango/HarfBuzz shape and order Arabic with
    nothing asked of the caller; if this fails, the PDF is a page of boxes or
    of reversed disconnected letters and the format choice was wrong."""
    rendered = MarkdownSummaryRenderer().render(
        _ARABIC_BODY, ExportFormat.PDF, title="تقرير الربع", rtl=True
    )

    assert rendered.content_type == "application/pdf"
    assert rendered.content.startswith(b"%PDF-")
    page = pypdf.PdfReader(io.BytesIO(rendered.content)).pages[0]
    text = page.extract_text()
    assert _has_arabic(text)
    assert "تقرير الربع" in text


def test_an_arabic_summary_survives_into_the_docx_as_arabic_and_is_marked_rtl() -> None:
    """Word decides bidi per PARAGRAPH, so ``w:bidi`` has to be on each one —
    a document-level property would leave every paragraph untouched and every
    Arabic line left-aligned."""
    rendered = MarkdownSummaryRenderer().render(
        _ARABIC_BODY, ExportFormat.DOCX, title="تقرير الربع", rtl=True
    )

    assert rendered.content_type.endswith("wordprocessingml.document")
    xml = _docx_text(rendered.content)
    assert _has_arabic(xml)
    assert "تقرير الربع" in xml
    assert 'bidi="1"' in xml


def test_an_english_summary_is_not_marked_rtl() -> None:
    rendered = MarkdownSummaryRenderer().render(
        _ENGLISH_BODY, ExportFormat.DOCX, title="Quarterly report", rtl=False
    )
    assert 'bidi="0"' in _docx_text(rendered.content)


def test_markdown_structure_reaches_the_docx_rather_than_its_source_syntax() -> None:
    """A DOCX carrying a literal ``# Overview`` and ``- First point`` would be
    a text file with the wrong extension."""
    xml = _docx_text(
        MarkdownSummaryRenderer()
        .render(_ENGLISH_BODY, ExportFormat.DOCX, title="Report", rtl=False)
        .content
    )

    assert "# Overview" not in xml
    assert "Overview" in xml
    # `Heading1`, not `Heading 1`: the DISPLAY name is what `add_paragraph`
    # takes, and what lands in `w:pStyle` is the style ID Word resolves it to.
    # Asserting on the id is what makes this a claim about the file.
    assert 'w:val="Heading1"' in xml
    assert "• First point" in xml


def test_markup_in_the_body_is_escaped_rather_than_rendered() -> None:
    """A summary is MODEL-GENERATED text, built from a document the tenant
    uploaded. Treating any of it as markup would let an uploaded file decide
    what the exported document says.

    This test found a real bug: the ``commonmark`` preset sets ``html=True``,
    because CommonMark's specification says raw HTML passes through. So
    ``<h1>injected</h1>`` became a real heading in the PDF and the
    ``<script>`` block vanished from the output entirely. The renderer now
    overrides the preset with ``html=False``, and both arrive as visible
    characters — which is what a reader should see.
    """
    body = "Normal text.\n\n<script>alert(1)</script>\n\n</style><h1>injected</h1>"
    rendered = MarkdownSummaryRenderer().render(body, ExportFormat.PDF, title="T", rtl=False)

    text = pypdf.PdfReader(io.BytesIO(rendered.content)).pages[0].extract_text()
    assert "<script>" in text
    assert "<h1>injected</h1>" in text


def test_an_empty_summary_still_produces_a_valid_file() -> None:
    """A summary can legitimately be short. Neither format may fail on it —
    an export that errors on a two-word body is an export that errors."""
    renderer = MarkdownSummaryRenderer()
    assert renderer.render("", ExportFormat.PDF, title="T", rtl=False).content.startswith(b"%PDF-")
    assert len(renderer.render("", ExportFormat.DOCX, title="T", rtl=False).content) > 0


# --------------------------------------------------------------------------- #
# Direction resolution                                                         #
# --------------------------------------------------------------------------- #


def test_an_explicit_language_decides_the_direction_without_reading_the_text() -> None:
    assert _is_rtl(_summary(text=_ENGLISH_BODY, lang=SummaryLanguage.AR)) is True
    assert _is_rtl(_summary(text=_ARABIC_BODY, lang=SummaryLanguage.EN)) is False


def test_auto_reads_the_majority_script_not_the_mere_presence_of_arabic() -> None:
    """An English summary quoting one Arabic term is an English document.
    Laying it out right to left because of that quote would be a worse
    mistake than the one the check exists to avoid."""
    mostly_english = "This report discusses the term الاسترجاع at length, repeatedly, in English."
    assert _is_rtl(_summary(text=mostly_english, lang=SummaryLanguage.AUTO)) is False
    assert _is_rtl(_summary(text=_ARABIC_BODY, lang=SummaryLanguage.AUTO)) is True
    # No letters at all is not a reason to flip the page.
    assert _is_rtl(_summary(text="123 456", lang=SummaryLanguage.AUTO)) is False


# --------------------------------------------------------------------------- #
# The use-case                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_exporting_a_missing_summary_is_the_same_404_the_json_read_gives() -> None:
    """One definition of "this summary exists", not two."""
    export = ExportSummary(InMemorySummaryRepository(), MarkdownSummaryRenderer())

    with pytest.raises(NotFoundError):
        await export.execute(
            _ctx(),
            document_id="doc-1",
            kind=SummaryKind.FULL,
            lang=SummaryLanguage.AUTO,
            fmt=ExportFormat.PDF,
            title="T",
        )


@pytest.mark.asyncio
async def test_the_use_case_renders_the_stored_body_in_the_requested_format() -> None:
    summaries = InMemorySummaryRepository()
    summaries.rows[("doc-1", "full", "ar")] = _summary(text=_ARABIC_BODY, lang=SummaryLanguage.AR)
    export = ExportSummary(summaries, MarkdownSummaryRenderer())

    pdf = await export.execute(
        _ctx(),
        document_id="doc-1",
        kind=SummaryKind.FULL,
        lang=SummaryLanguage.AR,
        fmt=ExportFormat.PDF,
        title="تقرير",
    )
    docx = await export.execute(
        _ctx(),
        document_id="doc-1",
        kind=SummaryKind.FULL,
        lang=SummaryLanguage.AR,
        fmt=ExportFormat.DOCX,
        title="تقرير",
    )

    assert pdf.content.startswith(b"%PDF-")
    assert 'bidi="1"' in _docx_text(docx.content)
