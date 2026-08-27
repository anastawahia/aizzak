"""TF sparse-vector builder for the knowledge module's hybrid BM25-sparse leg
(06-domain-models §7; docs/migration/refs/retrieval.md §4.2, §6.1; 3.k3).

Reuses the sibling ``tokenization.tokenize`` (3.k2) — the very same
Arabic/English tokenizer pipeline anchors both what gets embedded (dense) and
what gets hashed (sparse), so a term only ever needs one canonicalization.
**Okapi BM25 factors, and this module owns two of its three factors**
(fidelity audit §3-ج, closed 2026-08-27). BM25 is::

    score(q,d) = Σ_t IDF(t) · tf(t,d)·(k1+1) / (tf(t,d) + k1·(1 - b + b·|d|/avgdl))

Qdrant's sparse ``Modifier.IDF`` supplies the IDF factor server-side at query
time (deferred-IDF — see ``HybridVectorStore``'s docstring in
``framework/ports/vector_store.py``); everything else is the VALUE this module
puts in a document's sparse vector, and a query vector of 1.0s picks it back
out under Qdrant's dot product. That is the split FastEmbed's ``Bm25`` uses,
and the same one alpha drew between tokenizer (ported in 3.k2) and scorer
(`rank-bm25`'s ``BM25Okapi``, retrieval.md §4.2).

⚠️ **The document side used to be raw ``tf``, which is BM25 with ``k1 = ∞``
and ``b = 0``** — no saturation and no length normalisation. The audit
measured what that costs: a length bias of x4.0 on the sparse leg against
x1.15 on the dense one, ``spearman(score, chunk length) = +0.32`` over 300
positive hits, and 185 pairs of positive scores standing in EXACT whole-number
ratios ≥ 2 — linear repetition, the visible signature of an absent ``k1``.

**Query terms weigh 1.0, not their own ``tf``.** A term written twice in one
question is emphasis, not evidence, and under a dot product its ``tf`` would be
multiplied against the document's own weight for it twice over.

``term_id`` hashes each token to a stable uint32 id via ``hashlib.blake2b``
(``digest_size=4``) — **never** the builtin ``hash()``, which is randomized
per process (``PYTHONHASHSEED``, DD-14) and would make a chunk's sparse
indices unreproducible between the process that indexed it and the process
that later queries it. Collisions are accepted (a classic feature-hashing
trade-off): at 2**32 buckets they are vanishingly rare for any realistic
vocabulary, and even when one lands, its blast radius is bounded — RRF fuses
this sparse leg with a dense leg that carries no such collision risk, so one
accidental term merge in the sparse channel cannot silently dominate the
fused rank.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

from app.modules.knowledge.domain.tokenization import TokenizerType, tokenize


@dataclass(frozen=True, slots=True)
class SparseTerms:
    """One text's sparse term-frequency vector: ascending, de-duplicated
    parallel ``indices``/``values`` — ``values[i]`` is the raw term-frequency
    count of the term hashed to ``indices[i]``."""

    indices: tuple[int, ...]
    values: tuple[float, ...]


def term_id(term: str) -> int:
    """Hash ``term`` to a stable uint32 id (``blake2b`` digest_size=4,
    big-endian) — deterministic across processes and runs, unlike the
    builtin ``hash()`` (see the module docstring)."""
    digest = hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


@dataclass(frozen=True, slots=True)
class Bm25Params:
    """Okapi's two shape parameters plus the length anchor they normalise
    against. A value object, so the three always travel together -- ``b``
    without ``avg_len`` is meaningless, and the pair reaching the builder
    separately is how they would drift.

    ``k1`` -- term-frequency saturation. The weight of a term approaches
    ``k1 + 1`` as ``tf`` grows, so the tenth occurrence of a word adds nearly
    nothing over the ninth. Raw ``tf`` is ``k1 = ∞``: every occurrence worth
    exactly as much as the first, which is what let a long chunk out-score a
    relevant one by repeating a term.

    ``b`` -- how much length normalisation to apply, from 0 (none, the old
    behaviour) to 1 (full). 0.75 is Robertson's value and the field default.

    ``avg_len`` -- the corpus's mean ``|d|`` in SPARSE TERMS, which is what
    ``|d|`` means here: post-tokenisation, post-stopword, de-duplicated term
    count, NOT words and NOT characters.
    """

    k1: float
    b: float
    avg_len: float

    def weight(self, tf: float, doc_len: int) -> float:
        """One term's document-side BM25 weight -- everything but IDF.

        **Anchored at 1.0**, and that is load-bearing: at ``tf = 1`` and
        ``|d| = avg_len`` the normaliser is exactly 1 and this returns
        ``(k1+1)/(1+k1) = 1.0`` -- the same value raw ``tf`` produced. So a
        typical term in a typical chunk scores exactly what it scored before,
        and ``RetrievalSettings.min_bm25_score`` (25.0, the middle of a
        measured [21, 30] plateau) keeps the meaning it was measured with.
        What moves is only the DEVIATION from typical, which is the whole
        point of adding these.

        ``avg_len`` is guarded rather than trusted: a zero or negative value
        would divide by zero or invert the normalisation, and neither should
        be a crash inside an indexing worker.
        """
        anchor = self.avg_len if self.avg_len > 0.0 else 1.0
        norm = 1.0 - self.b + self.b * (doc_len / anchor)
        return tf * (self.k1 + 1.0) / (tf + self.k1 * norm)


def build_sparse_terms(
    text: str,
    *,
    tokenizer_type: TokenizerType = TokenizerType.MULTILINGUAL,
    remove_stopwords: bool = True,
    normalize: bool = True,
) -> SparseTerms:
    """Tokenize ``text`` (sibling ``tokenization.tokenize``) and accumulate
    raw term-frequency counts keyed by ``term_id``, returning ascending-
    index, de-duplicated parallel tuples.

    The identical builder runs at index time (over a chunk's text) and at
    query time (over the search query) — the tokenization/hashing is the
    only thing that has to agree between the two for the sparse leg to find
    a match, so every keyword argument here defaults exactly like
    ``tokenize`` itself. Empty or stopword-only text returns an empty
    ``SparseTerms``.
    """
    counts = _term_counts(
        text,
        tokenizer_type=tokenizer_type,
        remove_stopwords=remove_stopwords,
        normalize=normalize,
    )
    ordered_ids = sorted(counts)
    return SparseTerms(
        indices=tuple(ordered_ids),
        values=tuple(float(counts[term]) for term in ordered_ids),
    )


def build_document_terms(
    text: str,
    params: Bm25Params,
    *,
    tokenizer_type: TokenizerType = TokenizerType.MULTILINGUAL,
    remove_stopwords: bool = True,
    normalize: bool = True,
) -> SparseTerms:
    """The sparse vector STORED for a chunk: each term's Okapi weight
    (``Bm25Params.weight``) rather than its raw count.

    ``|d|`` is the number of TOKENS the text produced, counted with
    repetition -- Okapi's document length, not its vocabulary size. Two
    chunks of the same vocabulary and different length must normalise
    differently, and the distinct-term count cannot tell them apart.

    ⚠️ **Changing ``params`` invalidates every vector already stored.** The
    weights are baked in at index time, so a new ``k1``/``b``/``avg_len``
    means a re-index -- which is why ``avg_len`` is a fixed deployment
    constant and not the live corpus mean. A corpus-derived ``avgdl`` would
    shift on every upload and make every prior document stale, so a true
    corpus-wide value would cost a full re-index per ingest. FastEmbed makes
    the same choice for the same reason.
    """
    counts = _term_counts(
        text,
        tokenizer_type=tokenizer_type,
        remove_stopwords=remove_stopwords,
        normalize=normalize,
    )
    doc_len = sum(counts.values())
    ordered_ids = sorted(counts)
    return SparseTerms(
        indices=tuple(ordered_ids),
        values=tuple(params.weight(counts[term], doc_len) for term in ordered_ids),
    )


def build_query_terms(
    text: str,
    *,
    tokenizer_type: TokenizerType = TokenizerType.MULTILINGUAL,
    remove_stopwords: bool = True,
    normalize: bool = True,
) -> SparseTerms:
    """The sparse vector SENT as a query: 1.0 per distinct term.

    No parameters, because BM25 has none on this side: the whole of ``k1``,
    ``b`` and ``|d|`` is a property of the document. A query vector of 1.0s is
    what makes Qdrant's dot product return the document weight itself, and
    ``Modifier.IDF`` then scales it -- reconstructing the textbook sum exactly.

    ``build_sparse_terms``' raw ``tf`` was wrong here for a second reason
    beyond emphasis: under a dot product a term written twice in the question
    would be multiplied by the document's own weight for it twice over.
    """
    ordered_ids = sorted(
        _term_counts(
            text,
            tokenizer_type=tokenizer_type,
            remove_stopwords=remove_stopwords,
            normalize=normalize,
        )
    )
    return SparseTerms(indices=tuple(ordered_ids), values=(1.0,) * len(ordered_ids))


def _term_counts(
    text: str,
    *,
    tokenizer_type: TokenizerType,
    remove_stopwords: bool,
    normalize: bool,
) -> Counter[int]:
    """``term_id`` -> occurrences, the one tokenise-and-hash step all three
    builders share. It has to be one step: index side and query side only
    ever find each other if they canonicalise a term identically."""
    tokens = tokenize(
        text,
        tokenizer_type=tokenizer_type,
        remove_stopwords=remove_stopwords,
        normalize=normalize,
    )
    return Counter(term_id(token) for token in tokens)
