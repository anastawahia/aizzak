"""The source-label formatting unit (retrieval plan §3.2, §3.11; ``P-31`` +
``P-39``) — ``format_labeled_chunk`` for ONE chunk, ``format_context_block``
for a whole ranked list of them.

Produces the exact §3.2 shape, prepended above a retrieved chunk's own text:

    [maintenance.pdf p.12 | section: المسؤوليات]
    <chunk text>

**Display-time only.** The label is composed and prepended HERE, never at
indexing time. Retrieval plan §3.2 carries alpha's warning verbatim:
injecting metadata into a chunk's INDEXED text poisons both the embedding
and the IDF — every chunk of a file would share the same tokens, IDF
collapses, and a constant bias is added to the vectors. This module touches
nothing on the indexing path; it only formats a string built from an
ALREADY-retrieved chunk, purely for display/synthesis.

**One shared unit, two live consumers** (retrieval plan §3.2: "وحدة تنسيق
واحدة يتقاسمها مسار التوليف ومسار ``context_text`` الداخليّ — لا صيغتان
تنحرفان"): the RAG agent's synthesis path (plan row 2) and the knowledge
module's internal ``context_text`` capability (plan row 19, §3.11, ``P-39``)
build the identical label from the identical rule — so it lives in exactly
one place rather than two call sites drifting apart.

**Why the JOIN lives here too** (``format_context_block``, plan row 19): the
label alone was not the whole of the rendered shape. The agent used to join
its labelled chunks with a blank line at its own call site, and the moment
``context_text`` needed the same block, that separator would have existed in
two places — the second half of the very drift §3.2 forbids, and the half a
per-chunk formatter cannot prevent. So the block IS the unit: both consumers
call ``format_context_block`` and neither owns a separator of its own.
Prompt scaffolding around the block (the agent's ``Context:`` heading, its
system prompt) stays the agent's, because that is prompt composition rather
than source labelling.

**Order is the CALLER's, and is never rearranged here** (§3.7): retrieval
hands over a descending, best-first, already-truncated prefix, so the most
relevant chunk is ``[#1]`` — first in the block. ``LongContextReorder``,
which would move the strongest chunk to the END of the context, is an
explicitly REJECTED design (§3.7, §7): alpha rejected it because it hurts
the small (≤7B) models that attend to the start of a context. It is a design
note, never code — and this function's contract (input order preserved
exactly) is where that promise is kept.

**Why here, and not in ``knowledge/domain`` or ``knowledge/application``:**
``agents/rag_agent`` declares — as a convention STRONGER than what
``lint-imports`` alone enforces (fact ح-11, retrieval plan §6 risk 7) — that
it imports nothing beyond ``self.deps`` plus this ``framework`` kernel. A
module-owned formatter would force the agent to import
``app.modules.knowledge``, breaking that declared convention even though the
five-layer contract would technically permit it (agents MAY import
modules). ``app.framework`` is the one package both the agent (layer
``app.agents``) and the knowledge module's application layer (layer
``app.modules``) can import without crossing that line — the layered
contract makes framework the inward-most, shared floor. This follows the
exact precedent already set by ``read_text_file`` in this same package
(``file_reading.py``): "a framework-pure agent-runtime utility ... so the
kernel stays 8/0". Pure stdlib formatting here too — no ports, no state, no
technical imports.

**The degradation rule** (any of the three fields may be ``None`` — an older
Qdrant point, or a parser that never emitted one of them, e.g. no
``page_number`` off a DOCX): a missing ``file_name`` degrades to the literal
``"unknown"`` rather than a blank or a crash, and a missing ``page_number``
or ``section`` simply drops its own clause rather than printing a
placeholder for it. The label line is ALWAYS emitted — even when all three
fields are ``None`` — so a chunk's provenance line has one deterministic
shape no matter which fields the source point happened to carry:

    [maintenance.pdf p.12 | section: المسؤوليات]   all three present
    [maintenance.pdf | section: المسؤوليات]         no page_number
    [maintenance.pdf p.12]                          no section
    [maintenance.pdf]                               neither page nor section
    [unknown p.12 | section: المسؤوليات]            no file_name
    [unknown]                                       nothing at all
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

_UNKNOWN_FILE = "unknown"
# What separates two labelled chunks inside one context block: a BLANK line,
# so a label always opens a visually distinct passage even when the preceding
# chunk's text ended mid-sentence (parent expansion truncates at
# `max_parent_chunk_chars`). One home for it — see the module docstring.
_CHUNK_SEPARATOR = "\n\n"


class LabeledChunk(Protocol):
    """The read shape ``format_context_block`` needs from one retrieved
    chunk: its text plus the three citation fields the label is built from.

    Deliberately NARROWER than ``deps_ports.RetrievedChunkView`` (which also
    carries ``document_id``/``chunk_id``/``score``): this module formats, it
    does not identify, and a formatter that demanded an id would be
    unusable on anything but a fully-formed retrieval result. Both the
    module-side ``RetrievedChunk`` dataclass and anything satisfying
    ``RetrievedChunkView`` satisfy this structurally, which is exactly what
    lets the agents layer and the knowledge module's application layer share
    ONE renderer without either importing the other (see the module
    docstring on why this package is their only common floor).

    Read-only ``@property`` members for the reason ``RetrievedChunkView``
    gives: every carrier that satisfies these seams is a ``frozen=True``
    dataclass, and a bare annotation would declare a MUTABLE attribute mypy
    then refuses to match.
    """

    @property
    def text(self) -> str: ...
    @property
    def file_name(self) -> str | None: ...
    @property
    def page_number(self) -> int | None: ...
    @property
    def section(self) -> str | None: ...


def format_labeled_chunk(
    text: str,
    *,
    file_name: str | None,
    page_number: int | None,
    section: str | None,
) -> str:
    """Prepend the ``[file p.N | section: S]`` source label to ``text``.

    One label line, then ``text`` on the next line — retrieval plan §3.2's
    exact shape. See the module docstring for the degradation rule applied
    when any of ``file_name``/``page_number``/``section`` is ``None``.
    """
    label = _format_label(file_name=file_name, page_number=page_number, section=section)
    return f"{label}\n{text}"


def format_context_block(chunks: Iterable[LabeledChunk]) -> str:
    """Render a whole ranked list of chunks as ONE context block — each
    chunk through ``format_labeled_chunk``, joined by a blank line.

    This is what both consumers of §3.2's single formatting unit emit: the
    RAG agent's synthesis path puts it under its ``Context:`` heading in the
    system prompt, and ``RetrievalResult.context_text`` (the internal
    ``P-39`` capability, plan row 19) returns exactly this string. They are
    the same characters by construction, not by two call sites agreeing.

    **The caller's order is preserved exactly, and no chunk is dropped,
    added, re-scored or re-sorted.** Retrieval already delivered a
    descending, best-first, budget-cut prefix (§3.7: "الترتيب هنا: تنازليّ
    ثمّ قصّ، والأكثر صلة في ``[#1]``"), so the strongest chunk opens the
    block. See the module docstring for why ``LongContextReorder`` — the
    rejected design that would move it to the end — is not implemented here
    or anywhere else.

    An empty ``chunks`` yields ``""``, not a stray separator or a
    placeholder: "no context" is a caller's condition to branch on (the
    honest-fallback trust gate, plan row 5 ``P-33``), and a block that
    manufactured text out of nothing would hide it.
    """
    return _CHUNK_SEPARATOR.join(
        format_labeled_chunk(
            chunk.text,
            file_name=chunk.file_name,
            page_number=chunk.page_number,
            section=chunk.section,
        )
        for chunk in chunks
    )


def _format_label(
    *,
    file_name: str | None,
    page_number: int | None,
    section: str | None,
) -> str:
    """Build the ``[file p.N | section: S]`` label alone (no chunk text)."""
    name = file_name if file_name is not None else _UNKNOWN_FILE
    if page_number is not None:
        name = f"{name} p.{page_number}"
    if section is not None:
        name = f"{name} | section: {section}"
    return f"[{name}]"
