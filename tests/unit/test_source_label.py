"""Unit tests for ``framework/agent_runtime/source_label.py`` (retrieval plan
§3.2, row ٢ — ``P-31``; row ١٩ — ``P-39``).

Purely hermetic: both functions are pure stdlib, no ports, no ``live_*``
marker. Covers the exact §3.2 shape and every degradation case named in the
module docstring (any of ``file_name`` / ``page_number`` / ``section`` may be
``None``, including all three at once), then ``format_context_block`` — the
whole-ranking renderer row ١٩ moved INTO this unit so the RAG agent's
synthesis path and the knowledge module's internal ``context_text`` share one
format instead of two ("لا صيغتان تنحرفان").
"""

from __future__ import annotations

import pytest

from app.framework.agent_runtime.source_label import format_context_block, format_labeled_chunk


def test_the_full_label_matches_the_plan_39_2_example_exactly() -> None:
    result = format_labeled_chunk(
        "chunk body",
        file_name="maintenance.pdf",
        page_number=12,
        section="المسؤوليات",
    )
    assert result == "[maintenance.pdf p.12 | section: المسؤوليات]\nchunk body"


@pytest.mark.parametrize(
    ("file_name", "page_number", "section", "expected_label"),
    [
        # all three present
        ("maintenance.pdf", 12, "المسؤوليات", "[maintenance.pdf p.12 | section: المسؤوليات]"),
        # missing page_number
        ("maintenance.pdf", None, "المسؤوليات", "[maintenance.pdf | section: المسؤوليات]"),
        # missing section
        ("maintenance.pdf", 12, None, "[maintenance.pdf p.12]"),
        # missing both page_number and section
        ("maintenance.pdf", None, None, "[maintenance.pdf]"),
        # missing file_name only -> degrades to "unknown", never a crash
        (None, 12, "المسؤوليات", "[unknown p.12 | section: المسؤوليات]"),
        (None, None, "المسؤوليات", "[unknown | section: المسؤوليات]"),
        (None, 12, None, "[unknown p.12]"),
        # nothing at all -> the label is still emitted, never dropped
        (None, None, None, "[unknown]"),
    ],
)
def test_the_label_degrades_deterministically_per_missing_field(
    file_name: str | None,
    page_number: int | None,
    section: str | None,
    expected_label: str,
) -> None:
    result = format_labeled_chunk(
        "chunk body",
        file_name=file_name,
        page_number=page_number,
        section=section,
    )
    assert result == f"{expected_label}\nchunk body"


def test_the_label_is_always_on_its_own_line_above_the_text() -> None:
    result = format_labeled_chunk(
        "line one\nline two",
        file_name="f.pdf",
        page_number=1,
        section=None,
    )
    label, _, body = result.partition("\n")
    assert label == "[f.pdf p.1]"
    assert body == "line one\nline two"


# --------------------------------------------------------------------------- #
# ``format_context_block`` — the whole ranked list as ONE block (retrieval    #
# plan §3.2/§3.11, row ١٩ — ``P-39``).                                        #
# --------------------------------------------------------------------------- #
class _Chunk:
    """Structurally satisfies ``LabeledChunk`` — the four members the block
    renderer reads. Deliberately NOT a full ``RetrievedChunk``/
    ``RetrievedChunkView``: that the narrow shape is enough is part of the
    contract this file pins."""

    def __init__(
        self,
        text: str,
        *,
        file_name: str | None = None,
        page_number: int | None = None,
        section: str | None = None,
    ) -> None:
        self.text = text
        self.file_name = file_name
        self.page_number = page_number
        self.section = section


def test_the_context_block_is_the_per_chunk_formatter_joined_by_a_blank_line() -> None:
    """§3.2's "وحدة تنسيق واحدة … لا صيغتان تنحرفان": the block is not a
    second rendering of the label — it is ``format_labeled_chunk`` applied
    per chunk. Asserted against that function's own output rather than a
    hand-written literal alone, so a second formatter growing inside
    ``format_context_block`` fails here immediately."""
    chunks = [
        _Chunk("first body", file_name="a.pdf", page_number=1, section="Intro"),
        _Chunk("second body", file_name="b.docx", section="Scope"),
        _Chunk("third body"),
    ]

    block = format_context_block(chunks)

    assert block == "\n\n".join(
        format_labeled_chunk(
            chunk.text,
            file_name=chunk.file_name,
            page_number=chunk.page_number,
            section=chunk.section,
        )
        for chunk in chunks
    )
    assert block == (
        "[a.pdf p.1 | section: Intro]\nfirst body\n\n"
        "[b.docx | section: Scope]\nsecond body\n\n"
        "[unknown]\nthird body"
    )


def test_the_context_block_never_reorders_the_callers_ranking() -> None:
    """§3.7: descending, then truncate — the most relevant chunk is ``[#1]``,
    at the TOP of the block. ``LongContextReorder`` (which would move it to
    the END, hurting the ≤7B models that attend to the start of a context) is
    a REJECTED design recorded in §3.7/§7, never code — so the block comes
    back in exactly the order it was handed."""
    ranked = [_Chunk(f"body {rank}", file_name=f"f{rank}.pdf") for rank in range(5)]

    block = format_context_block(ranked)

    assert block.startswith("[f0.pdf]\nbody 0")
    assert block.endswith("[f4.pdf]\nbody 4")
    assert [passage.splitlines()[1] for passage in block.split("\n\n")] == [
        f"body {rank}" for rank in range(5)
    ]


def test_an_empty_ranking_renders_an_empty_block() -> None:
    """No separator, no placeholder sentence: "no context" is a condition the
    caller branches on (the honest-fallback trust gate, plan row 5,
    ``P-33``), and text manufactured here would hide it."""
    assert format_context_block([]) == ""


def test_a_single_chunk_block_is_exactly_that_chunks_label_and_text() -> None:
    """No trailing separator on the one-chunk case — the block is a join, not
    a per-chunk suffix."""
    assert format_context_block([_Chunk("only body", file_name="a.pdf", page_number=3)]) == (
        "[a.pdf p.3]\nonly body"
    )
