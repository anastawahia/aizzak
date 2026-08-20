"""File-name resolution — pure algorithm (rag-retrieval-plan.md §3.5 / §4 row
13, ``P-04``; rag-gaps-and-ports.md ``P-04``).

Maps what the user *said* to one of this workspace's documents. File names are
lexical by nature, so lexical matching leads and semantics only picks up what
lexical matching cannot: a file the user **described** instead of named. Ports
alpha's ``rag/retrieval/file_resolver.py::resolve_file`` cascade, in order:

1. **EXACT** — a normalized file name occurs in the query, or equals the
   query's descriptive core.
2. **FUZZY** — token containment/Jaccard blended with a ``difflib`` character
   ratio, over names whose Arabic definite article «ال» has been stripped.
3. **SEMANTIC** — cosine over pre-embedded labels, for the described-not-named
   case.

**It does not guess.** The single most important line of the port is the one
alpha wrote and this module keeps: when several files score within ``_BAND``
of the best, alpha returns them as *candidates* rather than picking one. Plan
§3.5 states the reason as a rule for AIZZAK too — "«أعلى مرشّح دائمًا» أسوأ
فشل ممكن هنا: **يلخّص الملفّ الخطأ بثقة**" — a wrong file summarized with full
confidence is worse than no answer, because nothing downstream can detect it.
That is why the result is a UNION and not an optional document id:

    FileResolution = ResolvedFile | AmbiguousFiles | NoFileMatch

``AmbiguousFiles`` carries ``candidates`` and nothing else — no
``document_id``, no ``best``, no indexing or iteration protocol — so a caller
cannot reach a single answer out of it by accident, and ``mypy --strict``
rejects any access to ``.document_id`` that has not first narrowed the union.
There is deliberately **no** "just take the top one" convenience path in this
module; deciding what to do with an undecided outcome (plan §4 row 14: ask the
user, in ordinary text) belongs to the caller.

Ambiguity is not only "several files". A *single* candidate that scores above
the usable floor but below the confidence bar is also returned as
``AmbiguousFiles`` — alpha's behaviour, and the honest one: "I think you may
mean X, confirm?" is a different statement from "this is X".

**What is ported and what is left behind**

* Ported: the three-layer order, the ``_norm_name``/``_query_core``
  normalization (including the "strip «ال» from tokens longer than four
  characters" rule and the request-word stop list), the blended lexical score
  ``max(0.6·containment + 0.4·difflib_ratio, jaccard)``, the ``_HIGH`` /
  ``_BAND`` / ``_LOW`` thresholds, the candidate cap, and the tie rule at both
  the lexical and the semantic layer.
* **Not** ported — alpha's JSON *registry* storage (plan §3.5: "تُنقَل
  الخوارزمية لا تخزين JSON الذي يستعمله alpha"). Candidates arrive as an
  argument; this module never reads a file, a database or a network. Its
  production source is ``ListDocuments`` (plan §4 row 13), assembled by the
  application layer.
* **Not** ported — the ``embed_model`` argument and the ``numpy`` embedding
  call inside the semantic layer. A pure domain cannot call an embedding
  provider, so the vectors are *given*: ``FileCandidate.label_vector`` and
  ``resolve_file(..., query_vector=...)``. Cosine is computed here in plain
  stdlib arithmetic, with alpha's ``1e-8`` norm epsilon. The semantic layer
  stays optional exactly as in alpha ("Enabled only when an embed model is
  supplied") — omit ``query_vector`` and the cascade simply ends after FUZZY.
* **Not** ported — alpha's ``title`` arm of the fuzzy and semantic scoring.
  AIZZAK's ``Document`` carries no human-authored title, so the arm would be a
  field that is always ``None`` and a ``max()`` that always collapses to the
  name arm — dead code, and the exact "a field whose value is always None"
  smell plan §6 risk 1 rules out. Recorded in §7; re-adding the arm is one
  ``max()`` term once a title exists.
* **Not** ported — the bare ``except Exception`` + ``print`` around the
  semantic layer, which turned a wiring bug into a silent downgrade to "no
  match". Vector-shape violations raise ``InvalidKnowledgeInput`` here.
* **Not** ported — ``round(score, 3)``. alpha rounded on the way into a JSON
  payload; rounding is a display concern (§3.2's "at display time, not at
  indexing" applied to numbers), so the score returned is the score computed.

**About the numbers.** The plan's standing warning (header, §3.3, §6 risk 3)
is that alpha's retrieval thresholds are calibrated for **L2 distance**, where
lower is closer, and must not be copied onto this project's cosine/RRF scales
where higher is better. That warning does not reach the lexical thresholds
below: ``_HIGH``/``_BAND``/``_LOW`` grade a token-overlap-and-``difflib``
similarity in ``[0, 1]`` that is computed *here*, from the same formula, on
the same scale — metric-independent constants of alpha's own algorithm, the
same standing as the ``0.95`` Jaccard dedup constant in ``relevance.py``
(plan §2, verified fact 17). ``_BAND = 0.10`` is named verbatim in §4 row 13.

The three SEMANTIC thresholds are a weaker case and are labelled as such:
alpha's semantic layer already scores **cosine**, so the direction agrees and
nothing inverts — but the magnitudes were read off alpha's embedding model,
and no evaluation set exists here to re-derive them (the input ``P-38`` is
waiting on; plan §7). They are carried over with their failure direction
noted: set too high, this layer returns ``NoFileMatch`` or ``AmbiguousFiles``
and the caller asks the user — it never becomes a confidently wrong file.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from operator import itemgetter

from app.modules.knowledge.domain.errors import InvalidKnowledgeInput
from app.modules.knowledge.domain.tokenization import normalize_arabic

# --------------------------------------------------------------------------- #
# Normalization (alpha `_norm_name` / `_query_core`)                           #
# --------------------------------------------------------------------------- #
_EXTENSION_RE = re.compile(r"\.(pdf|docx?|xlsx?|csv|json|txt|pptx?|md|html?)$", re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"[_\-./\\]+")
_WHITESPACE_RE = re.compile(r"\s+")
_DEFINITE_ARTICLE_RE = re.compile(r"^ال")

# alpha strips «ال» only from tokens LONGER than four characters: «الرد» (4)
# would otherwise become «رد», and short Arabic words that merely begin with
# those two letters are far likelier to be words than to be articled stems.
_ARTICLE_STRIP_MIN_LENGTH = 4

# Tokens shorter than this are ignored when building the overlap sets — a
# one-letter fragment matches everything and discriminates nothing.
_MIN_TOKEN_LENGTH = 2

# Words that describe the REQUEST, not the file (alpha `_STOP`, verbatim).
# Written in NORMALIZED form — bare alef, no tashkeel, teh-marbuta and
# alef-maksura folded to their bare letters — for the same reason
# `intent.py`'s patterns are written that way: they are compared against
# `_norm_name` output, never against raw input. A handful of the entries
# («الملف», «المستند», «الكتاب») can no longer be reached once the article
# stripping above has run; they are kept because trimming alpha's calibrated
# list is a calibration change, not a cleanup.
_REQUEST_WORDS: frozenset[str] = frozenset(
    {
        # Arabic — asking for a summary
        "لخص",
        "تلخيص",
        "ملخص",
        "ملخصا",
        "خلاصه",
        "نبذه",
        "لمحه",
        "فكره",
        "اعطني",
        "اعرض",
        # Arabic — interrogative and connective scaffolding
        "ما",
        "هو",
        "هي",
        "هذا",
        "هذه",
        "الذي",
        "محتوي",
        "محتوى",
        "ماذا",
        "يحتوي",
        "يوجد",
        "عن",
        "في",
        "تكلم",
        "تحدث",
        "احكي",
        "اخبرني",
        "وش",
        "ايش",
        "ايه",
        "فيه",
        "لي",
        # Arabic — the document nouns themselves
        "ملف",
        "مستند",
        "كتاب",
        "كتب",
        "مرجع",
        "مراجع",
        "الملف",
        "المستند",
        "الكتاب",
        # English
        "summarize",
        "summarise",
        "summary",
        "overview",
        "brief",
        "gist",
        "tldr",
        "the",
        "file",
        "document",
        "of",
        "about",
        "what",
        "is",
        "in",
        "tell",
        "me",
        "give",
    }
)

# --------------------------------------------------------------------------- #
# Thresholds (alpha's, see the module docstring on which ones transfer)        #
# --------------------------------------------------------------------------- #
_CONTAINMENT_WEIGHT = 0.6
_RATIO_WEIGHT = 0.4

_HIGH = 0.75  # confident enough to resolve to a single file
_BAND = 0.10  # anything within this of the best is a TIE — plan §4 row 13
_LOW = 0.40  # below this there is no usable lexical match at all

_SEMANTIC_FLOOR = 0.45  # cosine, alpha-calibrated — see the docstring
_SEMANTIC_HIGH = 0.60
_SEMANTIC_BAND = 0.05

_MAX_CANDIDATES = 5  # how many ambiguous candidates are worth showing a user
_NORM_EPSILON = 1e-8  # alpha's zero-vector guard, kept so a zero label scores 0

_EXACT_SCORE = 1.0


class ResolutionMethod(StrEnum):
    """Which layer of the cascade produced a resolution (alpha's ``method``)."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class FileCandidate:
    """One document the query could be referring to.

    ``label_vector`` is the embedding of this document's label, supplied by
    the application layer when — and only when — the semantic layer is wanted
    (the domain cannot embed anything itself). Either every candidate in a
    call carries one and ``query_vector`` is given, or none of it applies.
    """

    document_id: str
    file_name: str
    label_vector: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class ResolvedFile:
    """The query named exactly one document, confidently enough to act on."""

    document_id: str
    file_name: str
    method: ResolutionMethod
    score: float


@dataclass(frozen=True, slots=True)
class AmbiguousFiles:
    """The query did not resolve to one document — plural, or single but not
    confident — and this module refuses to choose (plan §3.5).

    Intentionally minimal: ``candidates`` and the layer that produced them.
    No ``document_id``, no ``best``, no ``__getitem__``. Collapsing this into
    one answer has to be a decision somebody writes down (plan §4 row 14 says
    that decision is "ask the user"), not a field access nobody notices.
    """

    candidates: tuple[FileCandidate, ...]
    method: ResolutionMethod

    def __post_init__(self) -> None:
        if not self.candidates:
            raise InvalidKnowledgeInput("AmbiguousFiles requires at least one candidate")


@dataclass(frozen=True, slots=True)
class NoFileMatch:
    """No layer of the cascade found anything usable — the query does not name
    or describe any document in the given set."""


FileResolution = ResolvedFile | AmbiguousFiles | NoFileMatch


def resolve_file(
    query: str,
    candidates: Sequence[FileCandidate],
    *,
    query_vector: Sequence[float] | None = None,
) -> FileResolution:
    """Resolve ``query`` against ``candidates`` through EXACT → FUZZY →
    SEMANTIC, stopping at the first layer that has an opinion.

    The semantic layer runs only when ``query_vector`` is passed; without it
    the cascade ends after FUZZY and returns ``NoFileMatch`` — the same
    optionality alpha expressed with ``embed_model=None``, minus the I/O.

    Raises ``InvalidKnowledgeInput`` if ``query_vector`` is given but empty,
    or if it is given and any candidate lacks a ``label_vector`` or carries
    one of a different dimension. Skipping such candidates instead would run
    the semantic layer over a silently partial corpus and could return a
    confident match while the right file sat outside the comparison.
    """
    if not candidates:
        return NoFileMatch()

    query_norm = _norm_name(query)
    core = _query_core(query)

    exact = tuple(
        candidate
        for candidate in candidates
        if _is_exact(candidate, query_norm=query_norm, core=core)
    )
    if len(exact) == 1:
        return ResolvedFile(
            document_id=exact[0].document_id,
            file_name=exact[0].file_name,
            method=ResolutionMethod.EXACT,
            score=_EXACT_SCORE,
        )
    if exact:
        return AmbiguousFiles(exact[:_MAX_CANDIDATES], ResolutionMethod.EXACT)

    lexical = _rank(
        (_lexical_score(core, _norm_name(candidate.file_name)), candidate)
        for candidate in candidates
    )
    best = lexical[0][0]
    if best >= _LOW:
        return _decide(
            ranked=lexical,
            best=best,
            band=_BAND,
            floor=_LOW,
            high=_HIGH,
            method=ResolutionMethod.FUZZY,
        )

    if query_vector is None:
        return NoFileMatch()
    return _resolve_semantically(candidates, query_vector)


def name_token_count(file_name: str) -> int:
    """How many tokens ``file_name`` normalizes to for matching purposes —
    the same tokens every layer of the cascade above compares.

    A pure measure of how DISCRIMINATING a name is, offered because the
    resolution alone does not say: a one-token «تقرير.pdf» and a four-token
    «تقرير الأداء السنوي 2024.pdf» both come back as ``ResolvedFile`` with
    ``method=EXACT`` and ``score=1.0``, and the first of those two matches
    is a far weaker statement about what the user meant — the shorter and
    commoner the word, the likelier it appeared in the question for its own
    sake. A caller that pays a HIGH price for a wrong match can ask for more
    than the cascade's own bar before acting on one
    (``application/routing.py::_content_scope``), and this is the only
    honest way to ask: re-tokenizing a name in the application layer would
    be a second normalizer, free to drift from ``_norm_name`` — which is the
    one that decided the match in the first place.

    Additive and side-effect free: nothing in the cascade calls it, and no
    resolution changes because it exists. An empty or extension-only name
    counts ``0``, the same nothing ``_is_exact`` refuses to match on.
    """
    return len(_norm_name(file_name).split())


# --------------------------------------------------------------------------- #
# Layer 1 — exact                                                              #
# --------------------------------------------------------------------------- #
def _is_exact(candidate: FileCandidate, *, query_norm: str, core: str) -> bool:
    """alpha's exact test: the normalized file name appears somewhere in the
    normalized query, or equals the query's descriptive core. An empty
    normalized name never matches — otherwise ``"" in query_norm`` would make
    a name-less candidate match every query."""
    name = _norm_name(candidate.file_name)
    if not name:
        return False
    return name in query_norm or name == core


# --------------------------------------------------------------------------- #
# Layer 2 — fuzzy, and the shared tie rule                                     #
# --------------------------------------------------------------------------- #
def _rank(scored: Iterable[tuple[float, FileCandidate]]) -> list[tuple[float, FileCandidate]]:
    """Sort ``(score, candidate)`` pairs best-first, stably: candidates that
    score identically keep the order they were given in (newest-first, as
    ``ListDocuments`` hands them over), so the same corpus always produces the
    same candidate list."""
    pairs = list(scored)
    pairs.sort(key=itemgetter(0), reverse=True)
    return pairs


def _decide(
    *,
    ranked: Sequence[tuple[float, FileCandidate]],
    best: float,
    band: float,
    floor: float,
    high: float,
    method: ResolutionMethod,
) -> FileResolution:
    """alpha's tie rule, shared by the fuzzy and semantic layers: everything
    within ``band`` of ``best`` (and not below ``floor``) is tied, and only a
    LONE tie member that also clears ``high`` becomes a resolution. Every
    other shape is ambiguity, including a lone member below ``high``."""
    tied = tuple(
        candidate for score, candidate in ranked if score >= floor and best - score <= band
    )
    if len(tied) == 1 and best >= high:
        return ResolvedFile(
            document_id=tied[0].document_id,
            file_name=tied[0].file_name,
            method=method,
            score=best,
        )
    return AmbiguousFiles(tied[:_MAX_CANDIDATES], method)


def _lexical_score(core: str, name: str) -> float:
    """alpha's blended similarity in ``[0, 1]``:
    ``max(0.6·containment + 0.4·difflib_ratio, jaccard)``.

    Containment (``|a∩b| / min(|a|, |b|)``) is what lets a short query core
    match a long file name it is wholly contained in; the ``difflib`` ratio
    absorbs typos and inflection the token sets cannot; and the raw Jaccard is
    kept as a floor so a perfect token match is never dragged down by a
    character ratio that happens to be poor.
    """
    core_tokens = _token_set(core)
    name_tokens = _token_set(name)
    if core_tokens and name_tokens:
        overlap = len(core_tokens & name_tokens)
        jaccard = overlap / len(core_tokens | name_tokens)
        containment = overlap / min(len(core_tokens), len(name_tokens))
    else:
        jaccard = containment = 0.0
    ratio = SequenceMatcher(None, core, name).ratio()
    return max(_CONTAINMENT_WEIGHT * containment + _RATIO_WEIGHT * ratio, jaccard)


def _token_set(text: str) -> frozenset[str]:
    return frozenset(token for token in text.split() if len(token) >= _MIN_TOKEN_LENGTH)


# --------------------------------------------------------------------------- #
# Layer 3 — semantic (vectors in, no embedding call)                           #
# --------------------------------------------------------------------------- #
def _resolve_semantically(
    candidates: Sequence[FileCandidate], query_vector: Sequence[float]
) -> FileResolution:
    if not query_vector:
        raise InvalidKnowledgeInput("query_vector must be non-empty to run the semantic layer")
    query_norm = math.sqrt(sum(value * value for value in query_vector)) + _NORM_EPSILON
    ranked = _rank(
        (_cosine(query_vector, _label_vector(candidate, len(query_vector)), query_norm), candidate)
        for candidate in candidates
    )
    best = ranked[0][0]
    if best < _SEMANTIC_FLOOR:
        return NoFileMatch()
    return _decide(
        ranked=ranked,
        best=best,
        band=_SEMANTIC_BAND,
        # alpha applies no floor to the tie members of the semantic layer —
        # anything within 0.05 of a best that already cleared 0.45 is close
        # enough to be worth showing the user.
        floor=float("-inf"),
        high=_SEMANTIC_HIGH,
        method=ResolutionMethod.SEMANTIC,
    )


def _label_vector(candidate: FileCandidate, dimension: int) -> tuple[float, ...]:
    if candidate.label_vector is None:
        raise InvalidKnowledgeInput(
            f"candidate {candidate.document_id!r} has no label_vector; the semantic layer "
            "needs one for every candidate or none at all"
        )
    if len(candidate.label_vector) != dimension:
        raise InvalidKnowledgeInput(
            f"candidate {candidate.document_id!r} label_vector has dimension "
            f"{len(candidate.label_vector)}, expected {dimension}"
        )
    return candidate.label_vector


def _cosine(query_vector: Sequence[float], label: Sequence[float], query_norm: float) -> float:
    """Cosine similarity with alpha's epsilon-padded norms — a zero vector
    scores ``0.0`` instead of dividing by zero."""
    dot = sum(a * b for a, b in zip(query_vector, label, strict=True))
    label_norm = math.sqrt(sum(value * value for value in label)) + _NORM_EPSILON
    return dot / (query_norm * label_norm)


# --------------------------------------------------------------------------- #
# Normalization helpers                                                        #
# --------------------------------------------------------------------------- #
def _norm_name(text: str) -> str:
    """Normalize a name or a query for matching (alpha ``_norm_name``): lower
    case, Arabic normalization, drop a known extension, unify separators to
    spaces, then strip the definite article «ال» from each long token so
    «التقرير» matches a file called «تقرير» — alpha's note calls that "a very
    common mismatch".

    The lower-casing is part of alpha's ``normalize_ar`` rather than an
    addition; this module's ``normalize_arabic`` is the substitution half
    only, and case folding is load-bearing here because file names are Latin
    at least as often as they are Arabic (``Q3_Report.pdf``).
    """
    normalized = normalize_arabic(text).lower()
    normalized = _EXTENSION_RE.sub("", normalized)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return " ".join(_strip_definite_article(token) for token in normalized.split())


def _strip_definite_article(token: str) -> str:
    if len(token) > _ARTICLE_STRIP_MIN_LENGTH:
        return _DEFINITE_ARTICLE_RE.sub("", token)
    return token


def _query_core(query: str) -> str:
    """Strip the words that describe the REQUEST, leaving the part that names
    the file (alpha ``_query_core``). A query made entirely of request words
    falls back to the whole normalized query rather than to the empty string —
    otherwise "لخص الملف" would have nothing left to match with.
    """
    normalized = _norm_name(query)
    core = " ".join(token for token in normalized.split() if token not in _REQUEST_WORDS).strip()
    return core or normalized
