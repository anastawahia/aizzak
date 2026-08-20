"""Unit tests for ``framework/agent_runtime/source_label.py`` (retrieval plan
§3.2, row ٢ — ``P-31``).

Purely hermetic: ``format_labeled_chunk`` is a pure stdlib function, no
ports, no ``live_*`` marker. Covers the exact §3.2 shape and every
degradation case named in the module docstring (any of ``file_name`` /
``page_number`` / ``section`` may be ``None``, including all three at once).
"""

from __future__ import annotations

import pytest

from app.framework.agent_runtime.source_label import format_labeled_chunk


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
