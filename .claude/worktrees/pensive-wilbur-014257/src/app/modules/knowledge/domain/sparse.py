"""TF sparse-vector builder for the knowledge module's hybrid BM25-sparse leg
(06-domain-models §7; docs/migration/refs/retrieval.md §4.2, §6.1; 3.k3).

Reuses the sibling ``tokenization.tokenize`` (3.k2) — the very same
Arabic/English tokenizer pipeline anchors both what gets embedded (dense) and
what gets hashed (sparse), so a term only ever needs one canonicalization.
This module does NOT implement Okapi BM25 scoring (``k1``/``b``, IDF) —
what's built here is the raw term-frequency *sparse vector*; the 2.5 Qdrant
adapter attaches Qdrant's own IDF modifier server-side at collection-
provisioning time (deferred-IDF — see ``HybridVectorStore``'s docstring in
``framework/ports/vector_store.py``), mirroring alpha's own separation
between tokenizer (ported in 3.k2) and scorer (`rank-bm25`'s ``BM25Okapi``,
out of scope here — retrieval.md §4.2).

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
    tokens = tokenize(
        text,
        tokenizer_type=tokenizer_type,
        remove_stopwords=remove_stopwords,
        normalize=normalize,
    )
    counts: Counter[int] = Counter(term_id(token) for token in tokens)
    ordered_ids = sorted(counts)
    return SparseTerms(
        indices=tuple(ordered_ids),
        values=tuple(float(counts[term]) for term in ordered_ids),
    )
