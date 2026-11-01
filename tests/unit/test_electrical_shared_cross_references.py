"""FR-ID-06 / INC1-AC-4: cross-reference formats and the dominant one."""

from __future__ import annotations

import pytest

from app.modules.electrical.shared.cross_references import dominant_format
from app.modules.electrical.shared.kinds import ConventionTally, IdentifierKind
from tests.unit.support_electrical_shared import PARSER, fw, parse


@pytest.mark.parametrize(
    ("text", "variant"),
    [
        ("/12.3", "slash_page_dot_column"),
        ("12.3", "page_dot_column"),
        ("=A1/12.3", "prefixed_slash"),
        ("12/3", "page_slash_column"),
        (fw("/12.3"), "slash_page_dot_column"),
    ],
)
def test_each_format_of_the_srs(text: str, variant: str) -> None:
    found = parse(text)
    assert found.kind is IdentifierKind.CROSS_REFERENCE
    assert found.variant == variant


def test_the_prefixed_reference_is_not_read_as_a_designation() -> None:
    assert parse("=A1/12.3").kind is IdentifierKind.CROSS_REFERENCE


def test_the_tally_counts_formats_and_the_dominant_one_wins() -> None:
    tally = PARSER.tally(["/12.3", "/4.1", "/5.2", "12/3", "KM1", "hello", "I0.0", ""])
    assert tally.cross_reference_formats == {"slash_page_dot_column": 3, "page_slash_column": 1}
    assert dominant_format(tally) == "slash_page_dot_column"


def test_a_tie_goes_to_the_alphabetically_first_format() -> None:
    tally = PARSER.tally(["/12.3", "12/3"])
    assert dominant_format(tally) == "page_slash_column"


def test_no_cross_reference_means_no_dominant_format() -> None:
    assert dominant_format(PARSER.tally(["KM1", "-K5"])) is None
    empty = ConventionTally({}, {}, {}, 0, 0)
    assert dominant_format(empty) is None
