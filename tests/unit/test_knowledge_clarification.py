"""Reading a turn as an ANSWER to the clarification the last turn asked
(``domain/clarification.py`` + ``file_resolution.read_ordinal``, ب-9 of
``docs/rag-agent-scenarios-implementation-plan.md`` §7, gap ف-1أ).

The review measured this path dead. It asked «أيّ ملفّ تقصد؟», tried all three
replies a user can give — the whole name, «الثاني», «2025» — and got
``content`` for every one of them: a similarity search where a summary was
asked for, three times out of three. Nothing was wrong with the resolver, the
seam or the question; there was simply no other end to the conversation.

These tests pin the pure half of the other end. Two properties carry most of
the weight, and neither is obvious from the code alone:

* **Narrowing to the offer is what makes «2025» answerable.** Across a corpus
  it is a fragment several files share and the resolver rightly refuses to
  choose; between the two names the user was actually shown it identifies one.
  The restriction is not a safety filter bolted onto a resolution — it is the
  thing that turns a refusal into an answer.
* **A position indexes what was SHOWN.** «الثاني» is not a name and matches
  nothing; it points. So it is read against the offered list, in the order the
  offer was made, and a file that has since left the corpus makes it resolve
  to nothing rather than sliding onto its neighbour.

Everything here is pure: no ports, no context, no I/O. The corpus is the same
pair of budget files the module's own routing tests use, because it is the
shape the review actually measured — two names that share a prefix and differ
in a year.
"""

from __future__ import annotations

import pytest

from app.modules.knowledge.domain.clarification import resolve_clarification_reply
from app.modules.knowledge.domain.file_resolution import FileCandidate, read_ordinal

_BUDGET_2024 = "الميزانية 2024.pdf"
_BUDGET_2025 = "الميزانية 2025.pdf"
_OFFERED = (_BUDGET_2024, _BUDGET_2025)
_CANDIDATES = (
    FileCandidate("doc-2024", _BUDGET_2024),
    FileCandidate("doc-2025", _BUDGET_2025),
)


# --------------------------------------------------------------------------- #
# the three answers the review measured                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # (أ) the whole name, typed or pasted.
        (_BUDGET_2025, "doc-2025"),
        # (ب) a position, against the list as it was shown.
        ("الثاني", "doc-2025"),
        ("الأول", "doc-2024"),
        # (ج) a fragment of a name — the one the whole corpus cannot resolve.
        ("2025", "doc-2025"),
        ("2024", "doc-2024"),
    ],
)
def test_the_three_shapes_of_answer_all_resolve(reply: str, expected: str) -> None:
    """The review's table, inverted. Every one of these came back `content`
    before ب-9; every one of them names a document now."""
    chosen = resolve_clarification_reply(reply, _OFFERED, _CANDIDATES)

    assert chosen is not None
    assert chosen.document_id == expected


def test_the_fragment_resolves_only_because_the_offer_narrowed_it() -> None:
    """The argument for restricting to the offer, made as a measurement rather
    than asserted in a docstring.

    «2025» against a corpus that also holds a marketing plan for 2025 is
    ambiguous, and the resolver refuses — correctly. Against the two names the
    user was actually shown it is a clean identification. Same reply, same
    algorithm, different choice set.
    """
    wider = (*_CANDIDATES, FileCandidate("doc-plan", "خطة التسويق 2025.docx"))

    # The plan is in the corpus but was never offered, so it cannot be chosen
    # — and the ambiguity it would have caused never arises.
    chosen = resolve_clarification_reply("2025", _OFFERED, wider)

    assert chosen is not None
    assert chosen.document_id == "doc-2025"


def test_a_name_that_was_never_offered_is_not_an_answer() -> None:
    """Decision 3: a user who ignored the question asked something else, and
    something else is what gets answered. Naming a real file that was not on
    the list is ignoring the question — the fall-through is ordinary
    classification, never a summary of a file nobody offered."""
    wider = (*_CANDIDATES, FileCandidate("doc-plan", "خطة التسويق.docx"))

    assert resolve_clarification_reply("خطة التسويق.docx", _OFFERED, wider) is None


def test_an_unrelated_question_is_not_an_answer() -> None:
    """The commonest non-answer, and the reason `None` has to be cheap and
    silent: the user changed the subject."""
    assert resolve_clarification_reply("كم عدد الموظفين؟", _OFFERED, _CANDIDATES) is None


def test_a_thread_that_asked_nothing_can_answer_nothing() -> None:
    """An empty offer short-circuits before any matching runs — which is the
    state almost every turn is in."""
    assert resolve_clarification_reply(_BUDGET_2025, (), _CANDIDATES) is None


# --------------------------------------------------------------------------- #
# order, and what happens when the corpus moves under the offer                #
# --------------------------------------------------------------------------- #
def test_the_position_indexes_the_order_the_user_was_shown() -> None:
    """Not the corpus order, not sorted order — the DISPLAY order. Reversing
    the offer reverses what «الثاني» means, which is the whole reason nothing
    between the thread and here may sort this list."""
    reversed_offer = (_BUDGET_2025, _BUDGET_2024)

    chosen = resolve_clarification_reply("الثاني", reversed_offer, _CANDIDATES)

    assert chosen is not None
    assert chosen.document_id == "doc-2024"


def test_a_position_pointing_at_a_vanished_file_resolves_to_nothing() -> None:
    """⚠️ The failure this shape exists to prevent: a file deleted between the
    question and the answer must not make «الثاني» quietly mean the file that
    moved up into its place. It resolves to nothing, the turn is answered as an
    ordinary question, and the user asks again — seeing a corpus that no longer
    contains what they were pointing at."""
    survivors = (FileCandidate("doc-2024", _BUDGET_2024),)

    assert resolve_clarification_reply("الثاني", _OFFERED, survivors) is None


def test_a_name_whose_file_has_gone_is_not_an_answer_either() -> None:
    """Same rule stated for the naming path: an offer is a list of names, and
    a name only chooses a document while a live candidate still carries it."""
    survivors = (FileCandidate("doc-2024", _BUDGET_2024),)

    assert resolve_clarification_reply(_BUDGET_2025, _OFFERED, survivors) is None


def test_the_name_comes_back_off_the_live_candidate() -> None:
    """What is returned is a candidate from the corpus, not a string from the
    offer — so a caller acting on it holds a document id the corpus vouched for
    this turn."""
    chosen = resolve_clarification_reply("2025", _OFFERED, _CANDIDATES)

    assert chosen is _CANDIDATES[1]


# --------------------------------------------------------------------------- #
# precedence: name, then position, then fragment                               #
# --------------------------------------------------------------------------- #
def test_a_typed_name_outranks_the_position_it_could_be_read_as() -> None:
    """(أ) before (ب). A file literally called «2.pdf» is named, not pointed
    at, and an identification the user made themselves is not something this
    module improves on."""
    offered = ("2.pdf", "3.pdf")
    candidates = (FileCandidate("doc-two", "2.pdf"), FileCandidate("doc-three", "3.pdf"))

    chosen = resolve_clarification_reply("2.pdf", offered, candidates)

    assert chosen is not None
    assert chosen.document_id == "doc-two"


def test_a_bare_number_is_a_position_and_not_a_name_fragment() -> None:
    """(ب) before (ج), and the deliberate trade: among a list of choices a bare
    «2» is where the user is pointing, not a character in a file name. The
    reading that loses here is available by typing the name, which the test
    above proves wins outright."""
    offered = ("تقرير 2.pdf", "تقرير 3.pdf")
    candidates = (FileCandidate("doc-a", "تقرير 2.pdf"), FileCandidate("doc-b", "تقرير 3.pdf"))

    chosen = resolve_clarification_reply("2", offered, candidates)

    assert chosen is not None
    assert chosen.document_id == "doc-b"


# --------------------------------------------------------------------------- #
# `read_ordinal` on its own                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("الأول", 1),
        ("الأولى", 1),
        ("أول", 1),
        ("الثاني", 2),
        ("الثانية", 2),
        ("الثالث", 3),
        ("الرابعة", 4),
        ("الخامس", 5),
        ("first", 1),
        ("the second", 2),
        ("the second one", 2),
        ("third", 3),
        ("2", 2),
        ("رقم 3", 3),
    ],
)
def test_the_shapes_a_position_is_written_in(reply: str, expected: int) -> None:
    """Masculine and feminine, Arabic and English, word and digit. All of them
    normalize through the SAME normalizer that decides every file-name match,
    which is why they live in that module and not beside their caller."""
    assert read_ordinal(reply, 5) == expected


@pytest.mark.parametrize(
    "reply",
    [
        # A year is not a position — and the range bound is what says so,
        # which is why «2025» is free to be read as the name fragment it is.
        "2025",
        # ⚠️ Prose that merely counts something. Reading this as "the third
        # file" would summarise a document instead of answering the question
        # that was asked, so anything beyond a pointing gesture is refused.
        "ما هو الفصل الثالث؟",
        "what does the second chapter say about revenue",
        # Two different positions named: undecidable, so undecided.
        "الأول أو الثاني",
        "",
        "   ",
    ],
)
def test_what_is_not_a_position(reply: str) -> None:
    assert read_ordinal(reply, 5) is None


def test_a_position_past_the_end_of_the_list_is_no_position() -> None:
    """The bound is the LIST, not the vocabulary: «الثالث» is a perfectly good
    ordinal and a perfectly bad answer to a question that offered two files."""
    assert read_ordinal("الثالث", 2) is None
    assert read_ordinal("الثالث", 3) == 3


def test_no_list_means_no_position() -> None:
    """A guard rather than a case: nothing was offered, so nothing is being
    pointed at."""
    assert read_ordinal("الأول", 0) is None
