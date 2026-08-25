"""Unit tests for the knowledge module's pure retrieval algorithms -- 3.k2's
Reciprocal Rank Fusion (`domain/fusion.py`) and BM25 multilingual tokenizer
(`domain/tokenization.py`), plus the dual context budget
(`domain/context_budget.py`, rag-retrieval-plan.md §3.7/§4 row 10, `P-35`).
Pure unit tests: no markers, no Docker, no optional dependencies.

Most Arabic test fixtures are built from explicit `chr(codepoint)` sequences
rather than embedded as literal source text -- precise, unambiguous, and
trivially diffable against the Unicode standard. One sentence (see
`test_tokenize_default_pipeline_on_a_literal_arabic_sentence`) is written as a
genuine literal Arabic string, exercising real Unicode source-file handling
end-to-end.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.framework.settings.settings import Limits
from app.modules.knowledge.domain import context_budget, fusion, tokenization


# --------------------------------------------------------------------------- #
# fusion.reciprocal_rank_fusion -- Reciprocal Rank Fusion                     #
# --------------------------------------------------------------------------- #
def test_rrf_both_lists_empty_returns_empty() -> None:
    assert fusion.reciprocal_rank_fusion([], [], top_k=10) == []


def test_rrf_one_list_empty_still_computes_rank_based_rrf_scores() -> None:
    # Deliberate deviation from alpha: alpha would return the other list
    # truncated with ITS RAW scores; here both lists always get uniform
    # rank-based RRF scores (retrieval.md Sec.4.1).
    result = fusion.reciprocal_rank_fusion(["a", "b", "c"], [], top_k=10)
    assert [r.chunk_id for r in result] == ["a", "b", "c"]
    expected_scores = [0.5 / (60 + rank + 1) for rank in range(3)]
    assert [r.score for r in result] == pytest.approx(expected_scores)
    assert expected_scores == sorted(expected_scores, reverse=True)
    assert len(set(expected_scores)) == 3  # strictly decreasing, no accidental ties


def test_rrf_one_list_empty_bm25_side_too() -> None:
    result = fusion.reciprocal_rank_fusion([], ["x", "y"], top_k=10)
    assert [r.chunk_id for r in result] == ["x", "y"]
    assert result[0].score > result[1].score


def test_rrf_overlap_id_sums_contributions_and_outranks_singletons() -> None:
    result = fusion.reciprocal_rank_fusion(["a", "b"], ["b", "c"], top_k=10)
    assert [r.chunk_id for r in result] == ["b", "a", "c"]
    by_id = {r.chunk_id: r.score for r in result}
    assert by_id["b"] == pytest.approx(0.5 / 62 + 0.5 / 61)  # dense rank1 + bm25 rank0, summed
    assert by_id["b"] > by_id["a"]
    assert by_id["b"] > by_id["c"]


def test_rrf_weight_dense_only_makes_dense_order_dominate() -> None:
    dense = ["a", "b", "c"]
    bm25 = ["c", "b", "a"]  # reverse order
    result = fusion.reciprocal_rank_fusion(dense, bm25, top_k=10, weight_dense=1.0, weight_bm25=0.0)
    assert [r.chunk_id for r in result] == dense


def test_rrf_weight_bm25_only_makes_bm25_order_dominate() -> None:
    dense = ["a", "b", "c"]
    bm25 = ["c", "b", "a"]
    result = fusion.reciprocal_rank_fusion(dense, bm25, top_k=10, weight_dense=0.0, weight_bm25=1.0)
    assert [r.chunk_id for r in result] == bm25


def test_rrf_top_k_truncates_to_requested_count() -> None:
    result = fusion.reciprocal_rank_fusion(["a", "b", "c", "d"], [], top_k=2)
    assert [r.chunk_id for r in result] == ["a", "b"]


@pytest.mark.parametrize("top_k", [0, -1, -100])
def test_rrf_non_positive_top_k_returns_empty(top_k: int) -> None:
    assert fusion.reciprocal_rank_fusion(["a"], ["b"], top_k=top_k) == []


def test_rrf_exact_formula_hand_computed() -> None:
    result = fusion.reciprocal_rank_fusion(
        dense_ids=["a", "b", "c"], bm25_ids=["b", "d"], top_k=4, rrf_k=60
    )
    by_id = {r.chunk_id: r.score for r in result}
    assert by_id["a"] == pytest.approx(0.5 / 61)  # dense rank 0 only
    assert by_id["b"] == pytest.approx(0.5 / 62 + 0.5 / 61)  # dense rank 1 + bm25 rank 0
    assert by_id["c"] == pytest.approx(0.5 / 63)  # dense rank 2 only
    assert by_id["d"] == pytest.approx(0.5 / 62)  # bm25 rank 1 only
    assert [r.chunk_id for r in result] == ["b", "a", "d", "c"]


def test_rrf_tied_scores_break_deterministically_dense_ids_first() -> None:
    # "a" (dense rank 0) and "z" (bm25 rank 0) get an EXACT tie (both
    # 0.5 * 1/61); repeated calls must always break it the same way.
    for _ in range(25):
        result = fusion.reciprocal_rank_fusion(["a"], ["z"], top_k=10)
        assert [r.chunk_id for r in result] == ["a", "z"]
        assert result[0].score == pytest.approx(result[1].score)


def test_rrf_tie_order_independent_of_pythonhashseed() -> None:
    """retrieval.md Sec.7 risk #8: alpha's set()-based id union has an
    iteration order that depends on PYTHONHASHSEED, so a tied score could
    break differently between process runs. This module builds the union
    from an insertion-ordered dict instead (never a set) -- prove it by
    running the fusion in fresh subprocesses under different hash seeds and
    checking the tie always breaks the same (dense-ids-first) way."""
    script = (
        "from app.modules.knowledge.domain.fusion import reciprocal_rank_fusion\n"
        "r = reciprocal_rank_fusion(['a'], ['z'], top_k=10)\n"
        "print(','.join(c.chunk_id for c in r))\n"
    )
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    outputs = []
    for seed in ("0", "1", "42"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": src_path}
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=True,
        )
        outputs.append(proc.stdout.strip())
    assert outputs == ["a,z", "a,z", "a,z"]


def test_fused_chunk_is_frozen() -> None:
    chunk = fusion.FusedChunk(chunk_id="x", score=1.0)
    with pytest.raises(AttributeError):
        chunk.score = 2.0  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# tokenization.normalize_arabic                                               #
# --------------------------------------------------------------------------- #
def test_normalize_arabic_alef_variants_to_bare_alef() -> None:
    bare_alef = chr(0x0627)
    for variant in (0x0623, 0x0625, 0x0622, 0x0627):  # hamza-above/below, madda, bare (no-op)
        assert tokenization.normalize_arabic(chr(variant)) == bare_alef


def test_normalize_arabic_hamza_variants_to_bare_hamza() -> None:
    bare_hamza = chr(0x0621)
    for variant in (0x0624, 0x0626):  # waw-hamza, yeh-hamza
        assert tokenization.normalize_arabic(chr(variant)) == bare_hamza


def test_normalize_arabic_strips_tashkeel_diacritics() -> None:
    kaf, teh, beh = chr(0x0643), chr(0x062A), chr(0x0628)
    fatha, damma, kasra, shadda, sukun, superscript_alef = (
        chr(0x064E),
        chr(0x064F),
        chr(0x0650),
        chr(0x0651),
        chr(0x0652),
        chr(0x0670),
    )
    diacriticized = kaf + fatha + teh + shadda + damma + beh + kasra + superscript_alef
    assert tokenization.normalize_arabic(diacriticized) == kaf + teh + beh
    assert tokenization.normalize_arabic(sukun) == ""


def test_normalize_arabic_teh_marbuta_to_heh() -> None:
    assert tokenization.normalize_arabic(chr(0x0629)) == chr(0x0647)


def test_normalize_arabic_alef_maksura_to_yeh() -> None:
    assert tokenization.normalize_arabic(chr(0x0649)) == chr(0x064A)


def test_normalize_arabic_leaves_english_and_digits_untouched() -> None:
    assert tokenization.normalize_arabic("Hello 123") == "Hello 123"


# --------------------------------------------------------------------------- #
# tokenization.detect_language                                                #
# --------------------------------------------------------------------------- #
def _arabic_run(n: int) -> str:
    return chr(0x0645) * n  # ARABIC LETTER MEEM, repeated


def _english_run(n: int) -> str:
    return "b" * n


@pytest.mark.parametrize(
    ("arabic_n", "english_n", "expected"),
    [
        (5, 0, tokenization.DetectedLanguage.ARABIC),  # ratio 1.0
        (0, 5, tokenization.DetectedLanguage.ENGLISH),  # ratio 0.0
        (7, 4, tokenization.DetectedLanguage.ARABIC),  # ratio 7/11 ~= 0.636 > 0.6
        (2, 8, tokenization.DetectedLanguage.ENGLISH),  # ratio 0.2 < 0.3
        (6, 4, tokenization.DetectedLanguage.MIXED),  # ratio 0.6 exactly -> boundary, MIXED
        (3, 7, tokenization.DetectedLanguage.MIXED),  # ratio 0.3 exactly -> boundary, MIXED
    ],
)
def test_detect_language_ratio_boundaries(
    arabic_n: int, english_n: int, expected: tokenization.DetectedLanguage
) -> None:
    text = _arabic_run(arabic_n) + " " + _english_run(english_n)
    assert tokenization.detect_language(text) is expected


def test_detect_language_empty_string_is_unknown() -> None:
    assert tokenization.detect_language("") is tokenization.DetectedLanguage.UNKNOWN


def test_detect_language_digits_and_punctuation_only_is_unknown() -> None:
    assert tokenization.detect_language("12345 !!! ...") is tokenization.DetectedLanguage.UNKNOWN


# --------------------------------------------------------------------------- #
# tokenization.tokenize -- Arabic                                             #
# --------------------------------------------------------------------------- #
_ARABIC_CONTENT_WORD = chr(0x0643) + chr(0x062A) + chr(0x0628)  # not a stopword, len 3
_ARABIC_STOPWORD = next(w for w in tokenization.ARABIC_STOP_WORDS if len(w) >= 2)
_ARABIC_SINGLE_CHAR = chr(0x0648)  # a single Arabic letter (waw), length 1


def test_tokenize_arabic_removes_stopwords_when_enabled() -> None:
    text = f"{_ARABIC_STOPWORD} {_ARABIC_CONTENT_WORD}"
    result = tokenization.tokenize(
        text, tokenizer_type=tokenization.TokenizerType.ARABIC, normalize=False
    )
    assert result == [_ARABIC_CONTENT_WORD]


def test_tokenize_arabic_keeps_stopwords_when_disabled() -> None:
    text = f"{_ARABIC_STOPWORD} {_ARABIC_CONTENT_WORD}"
    result = tokenization.tokenize(
        text,
        tokenizer_type=tokenization.TokenizerType.ARABIC,
        remove_stopwords=False,
        normalize=False,
    )
    assert result == [_ARABIC_STOPWORD, _ARABIC_CONTENT_WORD]


def test_tokenize_arabic_drops_single_character_tokens_unconditionally() -> None:
    text = f"{_ARABIC_SINGLE_CHAR} {_ARABIC_CONTENT_WORD}"
    result = tokenization.tokenize(
        text,
        tokenizer_type=tokenization.TokenizerType.ARABIC,
        remove_stopwords=False,
        normalize=False,
    )
    assert result == [_ARABIC_CONTENT_WORD]


def test_tokenize_arabic_normalize_flag_controls_whether_normalize_arabic_runs() -> None:
    raw = chr(0x0623) + _ARABIC_CONTENT_WORD  # alef-hamza-above prefix, unnormalized
    normalized_form = chr(0x0627) + _ARABIC_CONTENT_WORD  # bare-alef prefix

    with_normalize = tokenization.tokenize(
        raw, tokenizer_type=tokenization.TokenizerType.ARABIC, remove_stopwords=False
    )
    without_normalize = tokenization.tokenize(
        raw,
        tokenizer_type=tokenization.TokenizerType.ARABIC,
        remove_stopwords=False,
        normalize=False,
    )
    assert with_normalize == [normalized_form]
    assert without_normalize == [raw]


# --------------------------------------------------------------------------- #
# tokenization.tokenize -- English                                            #
# --------------------------------------------------------------------------- #
def test_tokenize_english_removes_stopwords_when_enabled() -> None:
    result = tokenization.tokenize(
        "the quick fox", tokenizer_type=tokenization.TokenizerType.ENGLISH
    )
    assert result == ["quick", "fox"]


def test_tokenize_english_keeps_stopwords_when_disabled() -> None:
    result = tokenization.tokenize(
        "the quick fox", tokenizer_type=tokenization.TokenizerType.ENGLISH, remove_stopwords=False
    )
    assert result == ["the", "quick", "fox"]


def test_tokenize_english_drops_single_character_tokens() -> None:
    result = tokenization.tokenize(
        "a b cat", tokenizer_type=tokenization.TokenizerType.ENGLISH, remove_stopwords=False
    )
    assert result == ["cat"]


def test_tokenize_english_strips_punctuation_by_deletion_not_replacement() -> None:
    # alpha's `str.translate` DELETES punctuation rather than replacing it
    # with whitespace, so adjacent word-parts glue together -- verbatim.
    result = tokenization.tokenize(
        "word1,word2 Hello-World!",
        tokenizer_type=tokenization.TokenizerType.ENGLISH,
        remove_stopwords=False,
    )
    assert result == ["word1word2", "helloworld"]


# --------------------------------------------------------------------------- #
# tokenization.tokenize -- multilingual dispatch + empty/whitespace           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["", "   ", "\n\t  "])
def test_tokenize_empty_or_whitespace_returns_empty_list(text: str) -> None:
    assert tokenization.tokenize(text) == []


def test_tokenize_multilingual_pure_arabic_dispatches_to_arabic_tokenizer() -> None:
    assert tokenization.tokenize(_ARABIC_CONTENT_WORD) == [_ARABIC_CONTENT_WORD]


def test_tokenize_multilingual_pure_english_dispatches_to_english_tokenizer() -> None:
    assert tokenization.tokenize("the quick fox") == ["quick", "fox"]


def test_tokenize_multilingual_mixed_runs_both_and_dedups_preserving_order() -> None:
    arabic_part = _arabic_run(6)
    text = f"{arabic_part} year 2024 report"
    assert tokenization.detect_language(text) is tokenization.DetectedLanguage.MIXED

    result = tokenization.tokenize(text, tokenizer_type=tokenization.TokenizerType.MULTILINGUAL)

    arabic_only = tokenization.tokenize(text, tokenizer_type=tokenization.TokenizerType.ARABIC)
    english_only = tokenization.tokenize(text, tokenizer_type=tokenization.TokenizerType.ENGLISH)
    expected: dict[str, None] = dict.fromkeys(arabic_only)
    for token in english_only:
        expected.setdefault(token, None)

    assert result == list(expected)
    assert result.count("2024") == 1  # produced by both per-language tokenizers, de-duplicated


def test_tokenize_default_pipeline_on_a_literal_arabic_sentence() -> None:
    """A genuinely literal Arabic sentence (real Unicode source text, not
    chr()-built) -- exercises the default pipeline end-to-end (language
    detection -> Arabic tokenizer, normalize + stop-word removal)."""
    text = "هذا التقرير السنوي يتحدث عن الأرباح"
    assert tokenization.detect_language(text) is tokenization.DetectedLanguage.ARABIC

    with_stopwords = tokenization.tokenize(text, remove_stopwords=False)
    without_stopwords = tokenization.tokenize(text)

    assert len(with_stopwords) == 6  # six space-separated words, none dropped by the length filter
    removed = set(with_stopwords) - set(without_stopwords)
    assert removed and removed <= tokenization.ARABIC_STOP_WORDS
    assert set(without_stopwords) <= set(with_stopwords)


# --------------------------------------------------------------------------- #
# context_budget -- the dual context budget (retrieval plan §3.7/§4 row 10,    #
# `P-35`). Pure: every ceiling arrives as an ARGUMENT (س-24), so these tests   #
# need no Settings, no environment and no network.                            #
# --------------------------------------------------------------------------- #
_LATIN_100 = "b" * 100  # 100 chars -> 25 estimated tokens (4 chars/token)
_ARABIC_100 = chr(0x0645) * 100  # 100 chars -> 50 estimated tokens (2 chars/token)


def _pairs(*rendered: str) -> list[tuple[str, str]]:
    """`(item, rendered)` pairs whose ITEM is a plain name -- so a test can
    assert on identity/order without the rendered text getting in the way."""
    return [(f"item-{index}", text) for index, text in enumerate(rendered)]


def test_estimate_tokens_is_zero_only_for_the_empty_string() -> None:
    assert context_budget.estimate_tokens("") == 0
    assert context_budget.estimate_tokens("a") == 1  # rounded UP, never floored to zero


def test_estimate_tokens_latin_uses_the_four_chars_per_token_rate() -> None:
    assert context_budget.estimate_tokens("b" * 4) == 1
    assert context_budget.estimate_tokens("b" * 400) == 100


def test_estimate_tokens_arabic_costs_more_per_character_than_latin() -> None:
    """The reason the budget is dual at all: at IDENTICAL character counts
    Arabic costs roughly twice the tokens (poor BPE coverage), so a
    single-ceiling budget would be honest in one language and wrong in the
    other."""
    assert context_budget.estimate_tokens(_ARABIC_100) == 50
    assert context_budget.estimate_tokens(_LATIN_100) == 25
    assert len(_ARABIC_100) == len(_LATIN_100)


def test_estimate_tokens_mixed_text_counts_each_script_at_its_own_rate() -> None:
    mixed = chr(0x0645) * 8 + "b" * 8
    assert context_budget.estimate_tokens(mixed) == 4 + 2


def test_context_budget_cuts_at_the_character_ceiling_when_it_is_the_smaller() -> None:
    candidates = _pairs(_LATIN_100, _LATIN_100, _LATIN_100)

    kept = context_budget.fit_to_context_budget(candidates, max_chars=250, max_tokens=10_000)

    assert kept == ["item-0", "item-1"]  # 200 chars fit, 300 would not
    # The CHARACTER ceiling is what did it: widen only that one and the third
    # candidate comes back.
    assert context_budget.fit_to_context_budget(candidates, max_chars=300, max_tokens=10_000) == [
        "item-0",
        "item-1",
        "item-2",
    ]


def test_context_budget_cuts_at_the_token_ceiling_when_it_is_the_smaller() -> None:
    candidates = _pairs(_LATIN_100, _LATIN_100, _LATIN_100)  # 25 tokens apiece

    kept = context_budget.fit_to_context_budget(candidates, max_chars=10_000, max_tokens=60)

    assert kept == ["item-0", "item-1"]  # 50 tokens fit, 75 would not
    # The TOKEN ceiling is what did it -- the character ceiling never moved.
    assert context_budget.fit_to_context_budget(candidates, max_chars=10_000, max_tokens=75) == [
        "item-0",
        "item-1",
        "item-2",
    ]


def test_context_budget_takes_whichever_ceiling_is_smaller() -> None:
    """§3.7's "و يُؤخَذ الأصغر", from both sides: with the SAME candidates, the
    cut lands at two candidates whichever of the two ceilings is the tight
    one."""
    candidates = _pairs(_LATIN_100, _LATIN_100, _LATIN_100, _LATIN_100)

    chars_tight = context_budget.fit_to_context_budget(candidates, max_chars=200, max_tokens=10_000)
    tokens_tight = context_budget.fit_to_context_budget(candidates, max_chars=10_000, max_tokens=50)

    assert chars_tight == tokens_tight == ["item-0", "item-1"]


def test_context_budget_same_length_arabic_is_cut_earlier_than_latin() -> None:
    """Identical character counts, identical ceilings -- and the Arabic pair
    is cut where the Latin pair is not, because the TOKEN ceiling is the
    smaller one for Arabic. This is the dual budget earning its keep."""
    latin = context_budget.fit_to_context_budget(
        _pairs(_LATIN_100, _LATIN_100), max_chars=10_000, max_tokens=50
    )
    arabic = context_budget.fit_to_context_budget(
        _pairs(_ARABIC_100, _ARABIC_100), max_chars=10_000, max_tokens=50
    )

    assert latin == ["item-0", "item-1"]
    assert arabic == ["item-0"]


def test_context_budget_keeps_a_descending_prefix_and_never_reorders() -> None:
    """The survivors are the best-first PREFIX of the input, in the input's
    own order: the highest-scoring candidate stays `[#1]`. `LongContextReorder`
    (best chunk moved to the END of the context) is an explicitly REJECTED
    design -- retrieval plan §3.7 and §7."""
    candidates = _pairs(*["b" * 50] * 6)

    kept = context_budget.fit_to_context_budget(candidates, max_chars=150, max_tokens=10_000)

    assert kept == ["item-0", "item-1", "item-2"]
    assert kept == [item for item, _ in candidates][: len(kept)]  # a prefix, not a subset
    assert kept[0] == "item-0"  # the best candidate is FIRST, never last


def test_context_budget_stops_at_the_first_breach_instead_of_skipping_ahead() -> None:
    """A cut, not a cherry-pick: a small LOW-ranked candidate is never
    promoted past a large higher-ranked one that broke the budget, which
    would silently reorder a descending ranking."""
    candidates = _pairs("b" * 50, "b" * 5_000, "b")

    kept = context_budget.fit_to_context_budget(candidates, max_chars=100, max_tokens=10_000)

    assert kept == ["item-0"]
    assert "item-2" not in kept  # it would have fitted -- and is still not taken


def test_context_budget_keeps_one_oversized_candidate_rather_than_emptying() -> None:
    """A single candidate bigger than the whole budget must NOT produce an
    empty context: zero chunks is exactly the signal the trust gate (plan step
    5, `P-33`) reads as "retrieval found nothing", so a budget that emptied
    the context here would make the agent tell the user the workspace has no
    answer while a real, relevant passage was in hand."""
    kept = context_budget.fit_to_context_budget(_pairs("b" * 5_000), max_chars=10, max_tokens=1)

    assert kept == ["item-0"]


def test_context_budget_oversized_first_candidate_does_not_drag_the_rest_in() -> None:
    kept = context_budget.fit_to_context_budget(
        _pairs("b" * 5_000, "b" * 10, "b" * 10), max_chars=10, max_tokens=1
    )

    assert kept == ["item-0"]  # the guarantee is exactly ONE survivor, not a free pass


@pytest.mark.parametrize("ceiling", [0, -1])
def test_context_budget_non_positive_ceilings_still_keep_the_best_candidate(
    ceiling: int,
) -> None:
    kept = context_budget.fit_to_context_budget(
        _pairs("b" * 10, "b" * 10), max_chars=ceiling, max_tokens=ceiling
    )

    assert kept == ["item-0"]


def test_context_budget_empty_input_is_the_only_empty_output() -> None:
    assert context_budget.fit_to_context_budget([], max_chars=10_000, max_tokens=10_000) == []


def test_context_budget_measures_the_rendered_string_not_the_item() -> None:
    """The budget is computed on the RENDERED text (source label included --
    the caller pairs each item with it), never on the item itself: a huge item
    paired with a short rendering costs a short rendering's worth."""
    huge_item = "x" * 10_000

    kept = context_budget.fit_to_context_budget(
        [(huge_item, "b" * 10), (huge_item, "b" * 10)], max_chars=20, max_tokens=10_000
    )

    assert kept == [huge_item, huge_item]


def test_context_budget_ceilings_are_required_arguments_with_no_domain_defaults() -> None:
    """س-24, mechanically: the pure function cannot supply either number
    itself -- both are REQUIRED keyword-only arguments, so the value can only
    have come from `Settings` via the caller."""
    parameters = inspect.signature(context_budget.fit_to_context_budget).parameters

    for name in ("max_chars", "max_tokens"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty


def test_context_budget_module_reads_no_environment_and_hardcodes_no_budget() -> None:
    """The other half of س-24, read off the module's own AST rather than a
    string scan (its prose discusses `os.getenv` precisely to forbid it):

    * the only imports are three stdlib names -- no `os` at all, so there is
      no environment to read, and no `Settings` to reach behind the caller's
      back (import-linter contract 2 forbids the latter anyway; this pins the
      former, which no contract covers);
    * no numeric literal equals a shipped budget default
      (`Settings.Limits.max_context_chars` = 12000 / `.max_context_tokens` =
      6000) -- a duplicated default is a second source of truth that drifts
      the day one of them moves.
    """
    tree = ast.parse(inspect.getsource(context_budget))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {"__future__", "math", "re", "collections.abc"}

    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float)
    }
    assert not literals & {12_000, 6_000}


def test_the_token_ceiling_cannot_bite_before_the_character_ceiling() -> None:
    """§3-ب, as arithmetic rather than as a pinned number -- the property the
    `6000` exists to hold, and the one that actually breaks if it is lowered.

    The budget is DUAL and the smaller cap wins. `max_context_chars` is exact;
    `max_context_tokens` is an estimate. For the exact cap to be the one in
    force, the estimated cap must be unreachable within it -- and the estimate
    is at its most expensive on Arabic, which `estimate_tokens` charges at
    `_ARABIC_CHARS_PER_TOKEN`. So a context filled to the character ceiling
    with pure Arabic is the worst case the pair can produce, and it must not
    trip the token ceiling.

    Below that point the behaviour SPLITS BY SCRIPT: the same 12000-character
    context passes in English and is cut in half in Arabic, and the ceiling
    actually in force is no longer the auditable one. That is precisely what
    the plan's carried-over `3000` did.
    """
    limits = Limits()
    # ALEF, via `chr` -- this module's stated convention for Arabic fixtures.
    worst_case_arabic = chr(0x0627) * limits.max_context_chars

    assert context_budget.estimate_tokens(worst_case_arabic) <= limits.max_context_tokens
    # And it is the SMALLEST such value: one less, and Arabic is cut first.
    assert context_budget.estimate_tokens(worst_case_arabic) > limits.max_context_tokens - 1
