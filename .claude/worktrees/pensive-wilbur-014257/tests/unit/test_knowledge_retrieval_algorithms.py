"""Unit tests for the knowledge module's two pure retrieval algorithms (3.k2)
-- Reciprocal Rank Fusion (`domain/fusion.py`) and the BM25 multilingual
tokenizer (`domain/tokenization.py`). Pure unit tests: no markers, no Docker,
no optional dependencies.

Most Arabic test fixtures are built from explicit `chr(codepoint)` sequences
rather than embedded as literal source text -- precise, unambiguous, and
trivially diffable against the Unicode standard. One sentence (see
`test_tokenize_default_pipeline_on_a_literal_arabic_sentence`) is written as a
genuine literal Arabic string, exercising real Unicode source-file handling
end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.knowledge.domain import fusion, tokenization


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
