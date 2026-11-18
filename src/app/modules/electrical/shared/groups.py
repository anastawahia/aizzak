"""FR-ID-19 (F3): a list, a range and a suffix list in ONE text, expanded to their members.

``X7:41,42,43`` (a list: a later bare label inherits the first item's head), ``QS7-QS9`` (a
range: the same head and tail, the number counts up) and ``20XYZ10AB131/2/3`` or ``QS7\\8`` (a
suffix list: the digits replace the trailing digits of the first item). Pure string functions:
the parser reads each member with its own rules (``IdentifierParser._group``), so a group is
only as good as its members and one that cannot be read is reported, never guessed.

The bound is the rules' ``[groups] max_members`` and is checked BEFORE anything is generated
(``QS7-QS99`` produces nothing); the text is at most 200 characters (``MAX_TEXT_CHARS``) and
every split is linear.
"""

from __future__ import annotations

import re
from typing import Final

ISSUE_MEMBER_UNREADABLE: Final = "group.member_unreadable"
ISSUE_MEMBER_INVALID: Final = "group.member_invalid"
ISSUE_TOO_MANY: Final = "group.too_many_members"
ISSUE_EMPTY_MEMBER: Final = "group.empty_member"
LIST_SEPARATOR: Final = ","  # after normalisation: U+FF0C by NFKC, U+3001 by the F3 fold
RANGE_SEPARATOR: Final = "-"
_END: Final = re.compile(r"\d+$", re.ASCII)
# the head: letters (never a digit at its end), the number, the tail: letters
_ENDPOINT: Final = re.compile(r"(?P<head>[A-Z]*)(?P<number>\d{1,4})(?P<tail>[A-Z]*)", re.ASCII)


def list_items(canonical: str) -> tuple[str, ...] | None:
    """Two or more items split at ``,``, or None. Empty items are kept (as ``""``) so the
    parser can report them."""
    if LIST_SEPARATOR not in canonical:
        return None
    return tuple(canonical.split(LIST_SEPARATOR))


def range_members(canonical: str, max_members: int) -> tuple[tuple[str, ...], bool] | None:
    """``HEAD<n>TAIL-HEAD<m>TAIL`` with one ``-``, the same HEAD (letters) and TAIL (letters),
    HEAD and TAIL not both empty, ``n < m``: the members ``HEAD<k>TAIL`` for ``k`` in ``n..m``,
    zero-padded to the width of ``n``. Returns ``(members, too_many)``; when ``m - n + 1`` is
    more than ``max_members`` nothing is expanded. None when the text is no range: it does not
    start with an endpoint, and the readings of today (``X1-14``, ``L1-5``, ``27-1``, ``12.3-1``,
    ``-K5-13``, ``1X-25``) are no range by construction (their two ends differ in head or tail,
    or both are empty, or there is more than one ``-``)."""
    if canonical.count(RANGE_SEPARATOR) != 1:
        return None
    left_text, right_text = canonical.split(RANGE_SEPARATOR)
    left, right = _ENDPOINT.fullmatch(left_text), _ENDPOINT.fullmatch(right_text)
    if left is None or right is None:
        return None
    same = left["head"] == right["head"] and left["tail"] == right["tail"]
    if not same or not (left["head"] or left["tail"]):
        return None
    first, last = int(left["number"]), int(right["number"])
    if first >= last:
        return None
    if last - first + 1 > max_members:
        return (), True
    width = len(left["number"])
    return (
        tuple(f"{left['head']}{str(k).zfill(width)}{left['tail']}" for k in range(first, last + 1)),
        False,
    )


def inherit(first: str, point: str, label: str) -> str:
    """A list's later bare label takes the first item's head: ``("X7:41", "41", "42")`` gives
    ``"X7:42"``. ``point`` is the first item's connection point (its end)."""
    if point and first.endswith(point):
        return first[: len(first) - len(point)] + label
    return label


def suffix_members(first: str, tails: tuple[str, ...]) -> tuple[str, ...] | None:
    """The first item and, for each tail of digits, ``first`` with as many trailing digits
    replaced (``20XYZ10AB131`` with ``2``, ``3`` gives ``...131``, ``...132``, ``...133``). None
    when a tail is not ASCII digits, or is longer than ``first``'s trailing run of digits."""
    run = _END.search(first)
    if run is None:
        return None
    members = [first]
    for tail in tails:
        if not tail or not tail.isascii() or not tail.isdigit() or len(tail) > len(run[0]):
            return None
        members.append(first[: len(first) - len(tail)] + tail)
    return tuple(members)
