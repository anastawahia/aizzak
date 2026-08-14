"""``SummaryRenderer`` adapter — Markdown to PDF/DOCX (BE-RAG-012).

Implements ``knowledge.ports.export.SummaryRenderer`` structurally (no
inheritance, this codebase's Protocol convention). It is the ONLY place that
imports WeasyPrint, ``python-docx`` or ``markdown-it-py``; nothing above it
knows a summary is rendered rather than stored that way.

**A module-local adapter, not an ``app.infrastructure`` one** — the
``adapters/parsers/`` precedent, and the import-linter is what settled it.
``app.infrastructure`` may be imported only by the Composition Root, which is
``app.framework``; an adapter placed there that implements a MODULE's port
drags ``app.modules`` into ``app.framework`` transitively and breaks the
"layered architecture (inward only)" contract. Adapters for a module's own
port live in that module, where importing a third-party library is ordinary —
``pdf_text.py`` imports PyMuPDF for exactly the same reason.

**WeasyPrint for PDF, because this platform is Arabic-first.** PDF text
shaping is exactly where right-to-left breaks: ReportLab is pure Python and
adds no system libraries, but Arabic through it means driving
``arabic-reshaper`` and ``python-bidi`` by hand, and it has no notion of
Markdown at all. WeasyPrint renders HTML through Pango/HarfBuzz, which shapes
and orders Arabic correctly with nothing asked of this file. The price is
paid in the image (``libpango``/``libcairo``/``libharfbuzz``, and the Noto
Arabic face — DejaVu has no Arabic coverage, so a PDF built without Noto is a
page of empty boxes), and it was taken deliberately: a PDF whose Arabic is
disconnected letters in reverse order is not an export.

**DOCX is walked by hand rather than rendered.** ``python-docx`` builds a
document from paragraphs and runs; there is no HTML in it to hand off to. So
this file walks the same ``markdown-it-py`` token stream the PDF path parses
and emits headings, paragraphs and list items — the shapes a summary
actually contains, given the prompts that produced it (``_REDUCE_SYSTEM``
asks for "short headings and paragraphs"). Anything richer degrades to a
paragraph rather than failing: an export that refuses a summary because it
contained a table is worse than one that flattens the table.

**The HTML is built from the token stream too, never from a template with
the body interpolated in.** ``markdown_it.render`` escapes what it emits, and
a summary is model-generated text: a body carrying ``<script>`` or a
``</style>`` must not be able to reach the renderer as markup. WeasyPrint
executes no JavaScript, so the risk here is a broken document rather than a
compromised one — but the escaping is free and the alternative is a rule
someone has to remember.
"""

from __future__ import annotations

import io

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from markdown_it import MarkdownIt
from markdown_it.token import Token
from weasyprint import HTML

from app.modules.knowledge.ports.export import ExportFormat, RenderedSummary

_PDF_TYPE = "application/pdf"
_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# `commonmark` rather than `gfm-like`: the summary prompts ask for headings,
# paragraphs and lists, and every GFM extension beyond that (tables,
# strikethrough, autolinks) is surface a model can wander into and this
# renderer would then have to have an opinion about. CommonMark is the
# smallest grammar that covers what is actually produced.
#
# **`html=False` is the load-bearing half of that line, and it overrides what
# the preset asks for.** The `commonmark` preset sets `html=True`, because
# CommonMark's own specification says raw HTML passes through — which meant a
# summary body containing `<h1>injected</h1>` became a real heading in the
# PDF, and a `<script>` block silently vanished from the output. A summary is
# MODEL-GENERATED text built from a document the tenant uploaded, so treating
# any of it as markup lets an uploaded file decide what the exported document
# says. With this off, both arrive as visible characters, which is what a
# reader should see.
_md = MarkdownIt("commonmark", {"html": False})

# Deliberately not a fingerprint of a corporate template. The point of the
# stylesheet is legibility and correct direction, and every rule here earns
# its place: a serif face for body text that has to be read at length,
# generous line height for Arabic diacritics, and page margins that leave a
# summary printable. `@page` size is A4 -- the default this platform's users
# print on.
_STYLESHEET = """
@page { size: A4; margin: 20mm 18mm; }
body {
  font-family: "Noto Naskh Arabic", "Noto Sans Arabic", "DejaVu Serif", serif;
  font-size: 11pt;
  line-height: 1.9;
  color: #1a1a1a;
}
h1 { font-size: 18pt; margin: 0 0 12pt; }
h2 { font-size: 14pt; margin: 18pt 0 8pt; }
h3 { font-size: 12pt; margin: 14pt 0 6pt; }
p  { margin: 0 0 9pt; }
ul, ol { margin: 0 0 9pt; padding-inline-start: 18pt; }
li { margin: 0 0 4pt; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 10pt; }
blockquote { margin: 0 0 9pt; padding-inline-start: 12pt; border-inline-start: 2pt solid #ccc; }
table { border-collapse: collapse; margin: 0 0 9pt; }
td, th { border: 0.5pt solid #999; padding: 3pt 6pt; }
"""

# The token types this DOCX walker knows how to open. Anything else becomes a
# plain paragraph -- see the module docstring.
_HEADING_STYLES = {"h1": "Heading 1", "h2": "Heading 2", "h3": "Heading 3"}


class MarkdownSummaryRenderer:
    """Structural ``SummaryRenderer``. Stateless and safe to share: the one
    ``MarkdownIt`` instance is module-level and re-entrant, and every render
    builds its own buffers."""

    def render(self, markdown: str, fmt: ExportFormat, *, title: str, rtl: bool) -> RenderedSummary:
        if fmt is ExportFormat.PDF:
            return RenderedSummary(
                content=self._pdf(markdown, title=title, rtl=rtl), content_type=_PDF_TYPE
            )
        return RenderedSummary(
            content=self._docx(markdown, title=title, rtl=rtl), content_type=_DOCX_TYPE
        )

    def _pdf(self, markdown: str, *, title: str, rtl: bool) -> bytes:
        body = _md.render(markdown)
        direction = "rtl" if rtl else "ltr"
        # `title` goes through the same escaping the body does: it is a file
        # name the user chose, and a name containing `<` must not become a tag.
        heading = _md.renderInline(title)
        html = (
            f"<html><head><style>{_STYLESHEET}</style></head>"
            f'<body dir="{direction}"><h1>{heading}</h1>{body}</body></html>'
        )
        # `base_url=None` is load-bearing, not a default left alone: without a
        # base, a relative `<img src>` or `url()` that reached the body has
        # nothing to resolve against, so a summary cannot make the renderer
        # fetch anything from the network or the local filesystem.
        rendered = HTML(string=html, base_url=None).write_pdf()
        # WeasyPrint's signature allows `None` when a target is given; there
        # is none here, so this is bytes. The check keeps mypy honest about
        # that rather than asserting it in a comment.
        if rendered is None:  # pragma: no cover - unreachable without a target
            raise RuntimeError("the PDF renderer produced no output")
        return bytes(rendered)

    def _docx(self, markdown: str, *, title: str, rtl: bool) -> bytes:
        document = DocxDocument()
        alignment = WD_ALIGN_PARAGRAPH.RIGHT if rtl else WD_ALIGN_PARAGRAPH.LEFT

        def _add(text: str, style: str | None = None) -> None:
            if not text.strip():
                return
            paragraph = document.add_paragraph(text, style=style)
            paragraph.alignment = alignment
            # Word decides bidi per paragraph, not per document, so the flag
            # is set on each one. A document-level property would leave every
            # paragraph added after it untouched.
            paragraph.paragraph_format.element.set(_bidi_key(), "1" if rtl else "0")

        _add(title, "Title")
        for text, style in _walk(_md.parse(markdown)):
            _add(text, style)

        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()


def _bidi_key() -> str:
    """The OOXML attribute name for a paragraph's reading direction.

    Spelled out here rather than inline so the namespace URI appears once:
    ``python-docx`` exposes no typed setter for ``w:bidi``, and a mistyped
    namespace fails silently — the attribute is simply ignored and every
    Arabic paragraph comes out left-aligned.
    """
    return "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}bidi"


def _walk(tokens: list[Token]) -> list[tuple[str, str | None]]:
    """Flatten a CommonMark token stream into ``(text, style)`` pairs.

    A deliberately lossy walk. Headings keep their level, list items are
    prefixed with a bullet, and everything else — tables, quotes, code
    fences — arrives as a plain paragraph carrying its text. That degradation
    is the design: an export that refused a summary because it contained a
    table would be a worse export than one that flattens it, and the prompts
    that produce these summaries ask for headings and paragraphs anyway.
    """
    out: list[tuple[str, str | None]] = []
    style: str | None = None
    in_list = False

    for token in tokens:
        if token.type == "heading_open":
            style = _HEADING_STYLES.get(token.tag, "Heading 3")
        elif token.type in ("bullet_list_open", "ordered_list_open"):
            in_list = True
        elif token.type in ("bullet_list_close", "ordered_list_close"):
            in_list = False
        elif token.type == "inline":
            text = token.content.strip()
            if not text:
                continue
            if style is not None:
                out.append((text, style))
                style = None
            elif in_list:
                out.append((f"• {text}", None))
            else:
                out.append((text, None))
        elif token.type == "fence":
            out.append((token.content.rstrip(), None))
    return out
