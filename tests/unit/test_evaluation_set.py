"""``P-38``'s evaluation set is a fixture with an owner, so it gets a test.

Nothing here measures retrieval — the harness that does needs a live stack and
an indexed corpus (``tests/eval/run_calibration.py``). What these assertions
protect is the SET: decision س-22 was closed on 15 questions somebody vouched
for, asked in two languages, with reference answers and with gold patterns
read off the real document. Every one of those properties is load-bearing, and
each can decay silently — a question edited out of sync with its Arabic twin,
a gold list emptied during a refactor, a "negative" quietly acquiring an
answer. A calibration whose set has drifted is a calibration nobody can
repeat, and the numbers in ``Settings`` would still be sitting there claiming
otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.eval.hr_handbook_set import NEGATIVES, QUESTIONS

# The size decision س-22 asked for: 15-30 questions over real documents,
# each with a reference answer.
_MIN_QUESTIONS = 15
_MAX_QUESTIONS = 30


def test_the_set_is_the_size_the_decision_asked_for() -> None:
    assert _MIN_QUESTIONS <= len(QUESTIONS) <= _MAX_QUESTIONS


def test_every_question_carries_both_languages_and_a_reference_answer() -> None:
    """The two languages are the SAME questions, which is the whole reason the
    cross-lingual half of the measurement is a controlled comparison. And a
    question without a reference answer is not evaluation material: there
    would be nothing to be right about."""
    for question in QUESTIONS:
        assert question["en"].strip(), question
        assert question["ar"].strip(), question
        assert question["answer"].strip(), question


def test_every_question_carries_gold_patterns_that_compile() -> None:
    """``gold`` is what turns "did retrieval deliver the answer" into a
    question the DOCUMENT answers rather than a judgement. An empty list would
    make its question unmeasurable while every other number stayed plausible,
    and a pattern that does not compile would take the whole run down."""
    for question in QUESTIONS:
        assert question["gold"], question
        for pattern in question["gold"]:
            re.compile(pattern)


def test_question_ids_are_unique_and_dense() -> None:
    """The ids are how a probe record is read back against the quiz files, so
    a duplicate would silently merge two questions' results."""
    ids = [question["id"] for question in QUESTIONS]
    assert ids == list(range(1, len(QUESTIONS) + 1))


def test_the_questions_are_the_owners_quiz_verbatim() -> None:
    """The English questions must be the ones in ``docs/hr-quiz-en.md``,
    character for character.

    Paraphrasing them here would be the quietest possible way to invalidate
    the calibration: the numbers in ``Settings`` are justified by a set
    somebody vouched for, and a set that drifted from it is one nobody did.
    The Arabic file is checked by ``test_both_quiz_files_ask_the_same_
    questions`` below, through the ids the two share."""
    quiz = _quiz_questions("docs/hr-quiz-en.md")
    assert quiz, "docs/hr-quiz-en.md carries no numbered questions"
    for question in QUESTIONS:
        assert question["en"] == quiz[question["id"]], question["id"]


def test_both_quiz_files_ask_the_same_questions() -> None:
    """The Arabic set is a TRANSLATION of the same 15, not a second set."""
    english = _quiz_questions("docs/hr-quiz-en.md")
    arabic = _quiz_questions("docs/hr-quiz.md")
    assert sorted(english) == sorted(arabic) == [question["id"] for question in QUESTIONS]
    for question in QUESTIONS:
        assert question["ar"] == arabic[question["id"]], question["id"]


def test_the_negatives_carry_both_languages_and_no_reference_answer() -> None:
    """The negatives are NOT the owner's and hold no reference answer, because
    there is nothing in the corpus to reference. They exist because a floor is
    a two-sided instrument: without them, "rejects nothing" and "rejects
    exactly the right things" are the same measurement."""
    assert NEGATIVES
    for negative in NEGATIVES:
        assert negative["en"].strip(), negative
        assert negative["ar"].strip(), negative
        assert "answer" not in negative, negative
        assert "gold" not in negative, negative


def test_no_negative_reuses_a_question_id() -> None:
    """Negative ids are strings and question ids are ints, so they cannot
    collide today; this pins that, because a probe record keys on ``qid`` and
    a collision would fold a negative's result into a question's."""
    question_ids = {str(question["id"]) for question in QUESTIONS}
    negative_ids = {negative["id"] for negative in NEGATIVES}
    assert not question_ids & negative_ids
    assert len(negative_ids) == len(NEGATIVES)


def _quiz_questions(path: str) -> dict[int, str]:
    """The numbered questions out of a quiz file's ``## Questions`` section --
    read from the FILE rather than restated here, so the two cannot drift
    without this test noticing."""
    text = Path(path).read_text(encoding="utf-8")
    body = text.split("---", 1)[0]
    return {
        int(match.group(1)): match.group(2).strip()
        for match in re.finditer(r"^(\d+)\.\s+(.*)$", body, flags=re.MULTILINE)
    }
