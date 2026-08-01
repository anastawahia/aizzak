"""BM25 multilingual tokenizer -- pure retrieval algorithm (06-domain-models.md
Sec.7 `knowledge`; docs/migration/refs/retrieval.md Sec.4.2).

Ports the *tokenizer/splitter* half of alpha's `rag/indexing/bm25_tokenizer.py`
only -- NOT the Okapi BM25 scorer and NOT the sparse index, both deferred to
3.k3 (docs/implementation-plan.md row 3.k2). De-globalized into pure stdlib
functions: this module imports no framework and no other module
(import-linter contract #2, "domain-purity").

Deliberate deviations from alpha (retrieval.md Sec.4.2, Sec.5):

* **Explicit config, not `os.getenv`.** alpha reads `BM25_NORMALIZE_ARABIC` /
  `BM25_REMOVE_STOPWORDS` / `BM25_STEMMING` / `BM25_LOG_TOKENIZATION` from the
  environment at call time via a `_flag(name, default)` helper -- forbidden
  in a pure domain (no I/O, no hidden global/env-backed state). Every choice
  here is an explicit keyword parameter, defaulted to alpha's shipped env
  default (`remove_stopwords=True`, `normalize=True`,
  `tokenizer_type=MULTILINGUAL`).
* **Dropped `@lru_cache`** on `tokenize()` -- a process-global mutable cache
  is exactly the kind of global state the migration study rules out; every
  function here is fully stateless (a future caller may cache at its own
  layer if it needs to).
* **Dropped all logging** (`BM25_LOG_TOKENIZATION` / `print(...)`) -- no I/O
  in the domain.
* **Dropped English (Porter) stemming** entirely (alpha's `BM25_STEMMING`,
  off by default, an optional `nltk.PorterStemmer` import, English-only, and
  silently a no-op when `nltk` is absent). Deferred, not ported, for v1 --
  `nltk` is not added as a dependency.
* **Dropped `batch_tokenize`** -- a trivial `[tokenize(t) for t in texts]`
  comprehension; callers can map over `tokenize` themselves.

Everything else -- the Arabic/English stop-word lists (values, not just
shape), the Arabic normalization substitution rules, the `detect_language`
ratio thresholds, and the per-tokenizer regexes/length filter -- is carried
over verbatim, because retrieval.md Sec.6.1 flags these as transferring
literally regardless of the surrounding retrieval engine.
"""

from __future__ import annotations

import re
import string
from enum import StrEnum


class TokenizerType(StrEnum):
    """Which tokenizer `tokenize()` should run (alpha env `BM25_TOKENIZER`)."""

    ARABIC = "arabic"
    ENGLISH = "english"
    MULTILINGUAL = "multilingual"


class DetectedLanguage(StrEnum):
    """`detect_language()` result (alpha's plain `str` return, enumerated)."""

    ARABIC = "arabic"
    ENGLISH = "english"
    MIXED = "mixed"
    UNKNOWN = "unknown"


# =============================================================================
# Stop words (alpha `bm25_tokenizer.py`, copied verbatim).
# =============================================================================
ARABIC_STOP_WORDS: frozenset[str] = frozenset(
    {
        "أقل",
        "أكثر",
        "أم",
        "أن",
        "أنا",
        "أنت",
        "أنتم",
        "أنتن",
        "أو",
        "أي",
        "أيضا",
        "أين",
        "إلا",
        "إلى",
        "إليه",
        "إليها",
        "إليهم",
        "إن",
        "التي",
        "الذي",
        "الذين",
        "اللاتي",
        "اللتان",
        "اللذان",
        "اللواتي",
        "ب",
        "بدون",
        "بعد",
        "بعض",
        "به",
        "بها",
        "بهم",
        "بين",
        "تحت",
        "ثم",
        "جدا",
        "حول",
        "حيث",
        "خلال",
        "ذات",
        "ذلك",
        "سوى",
        "ضد",
        "ضمن",
        "عبر",
        "على",
        "عليه",
        "عليها",
        "عن",
        "عند",
        "غير",
        "ف",
        "فوق",
        "في",
        "فيه",
        "فيها",
        "فيهم",
        "قبل",
        "قد",
        "قليل",
        "ك",
        "كان",
        "كثير",
        "كذلك",
        "كل",
        "كيف",
        "ل",
        "لا",
        "لدى",
        "لكن",
        "لماذا",
        "له",
        "لها",
        "لهم",
        "لهن",
        "ليس",
        "ما",
        "متى",
        "مثل",
        "مع",
        "من",
        "منه",
        "منها",
        "منهم",
        "نحن",
        "نحو",
        "نعم",
        "نفس",
        "هذا",
        "هذه",
        "هم",
        "هن",
        "هنا",
        "هناك",
        "هو",
        "هي",
        "و",
        "ولكن",
    }
)

ENGLISH_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "both",
        "but",
        "by",
        "can",
        "each",
        "every",
        "few",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "how",
        "in",
        "is",
        "it",
        "its",
        "just",
        "more",
        "most",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "on",
        "only",
        "other",
        "own",
        "same",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "they",
        "this",
        "to",
        "too",
        "very",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
    }
)

# =============================================================================
# Arabic normalization + tokenizer regexes (alpha's exact ranges; the Arabic
# Unicode-block boundary characters are written literally to match this
# codebase's existing convention -- see e.g. `adapters/parsers/text_plain.py`
# / `image_ocr.py` -- rather than alpha's own `\u0600-\u06FF` escapes, which
# are exactly the same range).
# =============================================================================
_ALEF_VARIANTS_RE = re.compile("[إأآا]")
_ALEF_REPL = "ا"  # noqa: RUF001 -- bare Alef (U+0627), the normalization target
_HAMZA_VARIANTS_RE = re.compile("[ؤئ]")
_HAMZA_REPL = "ء"
# Tashkeel (diacritics) U+064B-U+065F + superscript Alef U+0670 -- alpha's
# exact escaped range (unambiguous ASCII authoring, no combining marks).
_TASHKEEL_RE = re.compile(r"[\u064B-\u065F\u0670]")
_TEH_MARBUTA_FROM = "ة"
_TEH_MARBUTA_TO = "ه"  # noqa: RUF001 -- Heh (U+0647), the normalization target
_ALEF_MAKSURA_FROM = "ى"
_ALEF_MAKSURA_TO = "ي"

_ARABIC_TOKEN_RE = re.compile("[؀-ۿ\\w]+")
_ARABIC_CHAR_RE = re.compile("[؀-ۿ]")
_ENGLISH_CHAR_RE = re.compile(r"[a-zA-Z]")

_ARABIC_RATIO_HIGH = 0.6
_ARABIC_RATIO_LOW = 0.3
_MIN_TOKEN_LEN = 2

_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation)


def normalize_arabic(text: str) -> str:
    """Normalize Arabic text: unify Alef variants to bare Alef, unify Hamza
    variants to bare Hamza, strip tashkeel (diacritics), and unify Teh
    Marbuta/Alef Maksura to their bare-letter forms (alpha `normalize_arabic`,
    substitution rules copied verbatim). Pure and unconditional -- unlike
    alpha's `_flag`-gated version, this always normalizes; `tokenize`'s
    `normalize` parameter decides whether to call it at all (Arabic/mixed
    paths only)."""
    text = _ALEF_VARIANTS_RE.sub(_ALEF_REPL, text)
    text = _HAMZA_VARIANTS_RE.sub(_HAMZA_REPL, text)
    text = _TASHKEEL_RE.sub("", text)
    text = text.replace(_TEH_MARBUTA_FROM, _TEH_MARBUTA_TO)
    text = text.replace(_ALEF_MAKSURA_FROM, _ALEF_MAKSURA_TO)
    return text


def detect_language(text: str) -> DetectedLanguage:
    """Ratio-based language detection over `text`'s letters (Arabic-block vs
    ASCII a-z/A-Z; digits/punctuation/whitespace are not counted either way).
    ``arabic_ratio = arabic_chars / (arabic_chars + english_chars)``:
    ``> 0.6`` -> ARABIC, ``< 0.3`` -> ENGLISH, otherwise MIXED (both boundary
    values, 0.3 and 0.6 themselves, fall into MIXED); zero letters of either
    kind -> UNKNOWN (alpha `detect_language`, ratios verbatim)."""
    arabic_chars = len(_ARABIC_CHAR_RE.findall(text))
    english_chars = len(_ENGLISH_CHAR_RE.findall(text))
    total_chars = arabic_chars + english_chars
    if total_chars == 0:
        return DetectedLanguage.UNKNOWN
    arabic_ratio = arabic_chars / total_chars
    if arabic_ratio > _ARABIC_RATIO_HIGH:
        return DetectedLanguage.ARABIC
    if arabic_ratio < _ARABIC_RATIO_LOW:
        return DetectedLanguage.ENGLISH
    return DetectedLanguage.MIXED


def _tokenize_arabic(text: str, *, remove_stopwords: bool, normalize: bool) -> list[str]:
    """Arabic tokenizer (alpha `arabic_tokenizer`): optionally normalize,
    lowercase, split into Arabic-block-or-word-character runs, optionally
    drop stop words, then unconditionally drop single-character tokens."""
    if normalize:
        text = normalize_arabic(text)
    tokens = _ARABIC_TOKEN_RE.findall(text.lower())
    if remove_stopwords:
        tokens = [token for token in tokens if token not in ARABIC_STOP_WORDS]
    return [token for token in tokens if len(token) >= _MIN_TOKEN_LEN]


def _tokenize_english(text: str, *, remove_stopwords: bool) -> list[str]:
    """English tokenizer (alpha `english_tokenizer`, stemming dropped -- see
    the module docstring): lowercase, strip ASCII punctuation (deleted, not
    replaced by whitespace -- adjacent word-parts glue together, matching
    alpha's `str.translate` exactly), split on whitespace, optionally drop
    stop words, then unconditionally drop single-character tokens."""
    tokens = text.lower().translate(_PUNCTUATION_TABLE).split()
    if remove_stopwords:
        tokens = [token for token in tokens if token not in ENGLISH_STOP_WORDS]
    return [token for token in tokens if len(token) >= _MIN_TOKEN_LEN]


def _tokenize_multilingual(text: str, *, remove_stopwords: bool, normalize: bool) -> list[str]:
    """Detect language and dispatch; MIXED/UNKNOWN runs BOTH tokenizers over
    the full text and merges with order-preserving de-duplication (alpha
    `multilingual_tokenizer`, verbatim behaviour) -- built from an
    insertion-ordered `dict` (the same determinism-friendly style already
    used in `fusion.py`) rather than alpha's `seen`-set-while-iterating-a-
    list; both are equally order-preserving, alpha's own de-dup order is
    unaffected."""
    language = detect_language(text)
    if language is DetectedLanguage.ARABIC:
        return _tokenize_arabic(text, remove_stopwords=remove_stopwords, normalize=normalize)
    if language is DetectedLanguage.ENGLISH:
        return _tokenize_english(text, remove_stopwords=remove_stopwords)

    arabic_tokens = _tokenize_arabic(text, remove_stopwords=remove_stopwords, normalize=normalize)
    english_tokens = _tokenize_english(text, remove_stopwords=remove_stopwords)
    merged: dict[str, None] = dict.fromkeys(arabic_tokens)
    for token in english_tokens:
        merged.setdefault(token, None)
    return list(merged)


def tokenize(
    text: str,
    *,
    tokenizer_type: TokenizerType = TokenizerType.MULTILINGUAL,
    remove_stopwords: bool = True,
    normalize: bool = True,
) -> list[str]:
    """Tokenize `text` for BM25 indexing/querying (alpha `tokenize`, minus
    the process-global `@lru_cache` and logging -- see the module docstring).

    `tokenizer_type` selects the Arabic-only, English-only, or multilingual
    (language-detected, the default) tokenizer. `remove_stopwords` drops the
    matching stop-word list(s); `normalize` additionally runs
    `normalize_arabic` before Arabic tokenization (Arabic/mixed paths only --
    named `normalize`, not `normalize_arabic`, so it does not shadow the
    module-level `normalize_arabic` function). Returns `[]` for empty or
    whitespace-only text.
    """
    if not text or not text.strip():
        return []
    if tokenizer_type is TokenizerType.ARABIC:
        return _tokenize_arabic(text, remove_stopwords=remove_stopwords, normalize=normalize)
    if tokenizer_type is TokenizerType.ENGLISH:
        return _tokenize_english(text, remove_stopwords=remove_stopwords)
    return _tokenize_multilingual(text, remove_stopwords=remove_stopwords, normalize=normalize)
