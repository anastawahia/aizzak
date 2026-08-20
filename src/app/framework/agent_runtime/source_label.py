"""``format_labeled_chunk`` — the source-label formatting unit (retrieval plan
§3.2, §3.11; ``P-31``, half of ``P-39``).

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

**One shared unit, two consumers-to-be:** the RAG agent's synthesis path
(this step) and the knowledge module's internal ``context_text`` capability
(retrieval plan §3.11, ``P-39``, a later step) both build the identical
label from the identical rule — so it lives in exactly one place rather than
two call sites drifting apart.

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

_UNKNOWN_FILE = "unknown"


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
