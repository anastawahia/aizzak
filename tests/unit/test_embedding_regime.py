"""Unit tests for the corpus-naming half of capacity step 4.5
(``knowledge/domain/collections.py``).

Pure: no store, no settings, no clock. What is pinned here is the RULE that
decides which collection a set of vectors belongs to, because everything else
in 4.5 is downstream of it -- the adapter resolves a name, retrieval computes
one, and ``app.ops.embedding_migration`` parses them back apart. A rule that
drifted would not fail loudly anywhere: it would send writes and reads to two
different collections and answer every question with nothing.

The parsing tests carry more weight than they look like they do. A workspace
id is a UUID and already contains four hyphens, so "split off the revision" is
genuinely ambiguous unless both halves are validated -- and getting it wrong
would make ``adopt`` believe a workspace named ``019f`` exists.
"""

from __future__ import annotations

import pytest

from app.modules.knowledge.domain.collections import (
    REVISION_HEX_LEN,
    EmbeddingRegime,
    knowledge_collection,
    knowledge_collection_revision,
    split_knowledge_collection,
)

_WS = "019f3020-59d6-7ee5-a6df-913e44c5ecf0"
_SHIPPED = EmbeddingRegime(
    model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    dimensions=384,
    max_input_tokens=512,
)


# --------------------------------------------------------------------------- #
# the fingerprint                                                             #
# --------------------------------------------------------------------------- #
def test_the_revision_is_stable_across_calls_and_processes() -> None:
    """A fingerprint, not a counter: two replicas that were never told about
    each other must compute the same corpus name from the same settings, with
    nothing to coordinate. A random or time-derived value here would give
    every process its own corpus."""
    assert (
        _SHIPPED.revision
        == EmbeddingRegime(model=_SHIPPED.model, dimensions=384, max_input_tokens=512).revision
    )
    assert len(_SHIPPED.revision) == REVISION_HEX_LEN
    assert set(_SHIPPED.revision) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    "changed",
    [
        EmbeddingRegime(model="another/model", dimensions=384, max_input_tokens=512),
        EmbeddingRegime(model=_SHIPPED.model, dimensions=768, max_input_tokens=512),
        EmbeddingRegime(model=_SHIPPED.model, dimensions=384, max_input_tokens=256),
    ],
)
def test_every_field_moves_the_revision(changed: EmbeddingRegime) -> None:
    """All three fields, and the third is the one that gets forgotten.
    ``max_input_tokens`` is where the model stops reading, so two deployments
    differing only there produce different vectors for every text longer than
    the smaller of them -- and a fingerprint blind to it would let those
    vectors share one collection."""
    assert changed.revision != _SHIPPED.revision


def test_the_canonical_form_cannot_be_impersonated_across_fields() -> None:
    """Newline-joined, so a value cannot eat the boundary after it: a model
    name that ended in the next field's value would otherwise fingerprint the
    same as a different regime."""
    assert (
        EmbeddingRegime(model="a", dimensions=1, max_input_tokens=23).revision
        != EmbeddingRegime(model="a", dimensions=12, max_input_tokens=3).revision
    )


@pytest.mark.parametrize(
    ("model", "dimensions", "max_input_tokens"),
    [("", 384, 512), ("   ", 384, 512), ("m", 0, 512), ("m", -1, 512), ("m", 384, 0)],
)
def test_a_regime_that_cannot_name_a_corpus_is_refused(
    model: str, dimensions: int, max_input_tokens: int
) -> None:
    """Refused at construction rather than fingerprinted anyway: a blank model
    or a zero width is a settings fault, and hashing it would bury the fault
    inside a plausible-looking collection name."""
    with pytest.raises(ValueError):
        EmbeddingRegime(model=model, dimensions=dimensions, max_input_tokens=max_input_tokens)


# --------------------------------------------------------------------------- #
# the names                                                                   #
# --------------------------------------------------------------------------- #
def test_the_stable_read_name_is_unchanged_by_45() -> None:
    """``kn-<workspace_id>`` is what every ``chunks.collection`` row ever
    written names, and what ``delete_everywhere`` is handed. Changing it would
    have meant rewriting rows to make deletion keep working."""
    assert knowledge_collection(_WS) == f"kn-{_WS}"


def test_a_corpus_name_is_the_read_name_plus_the_revision() -> None:
    assert knowledge_collection_revision(_WS, _SHIPPED.revision) == (
        f"kn-{_WS}-{_SHIPPED.revision}"
    )


@pytest.mark.parametrize("bad", ["", "short", "0123456789abcdef", "0123456789AB", "0123456789ag"])
def test_a_name_is_never_built_from_something_that_is_not_a_revision(bad: str) -> None:
    """Refused rather than concatenated: a name built from a typo resolves to
    a corpus that does not exist, and this platform answers a missing corpus
    with an EMPTY RESULT -- so the mistake would look exactly like a workspace
    that had never indexed anything."""
    with pytest.raises(ValueError):
        knowledge_collection_revision(_WS, bad)


# --------------------------------------------------------------------------- #
# parsing back apart (`app.ops.embedding_migration`'s inventory)              #
# --------------------------------------------------------------------------- #
def test_a_legacy_collection_parses_as_a_workspace_with_no_revision() -> None:
    """``None`` is a state, not a failure: it is a corpus created before 4.5,
    it is what ``adopt`` acts on, and ``status`` reports it."""
    assert split_knowledge_collection(f"kn-{_WS}") == (_WS, None)


def test_a_revisioned_collection_parses_into_both_halves() -> None:
    assert split_knowledge_collection(f"kn-{_WS}-{_SHIPPED.revision}") == (_WS, _SHIPPED.revision)


def test_the_workspace_uuids_own_hyphens_are_not_read_as_a_revision() -> None:
    """The whole reason parsing goes from the RIGHT and validates both halves.
    Splitting on the first hyphen would read this name as workspace ``019f3020``
    with a revision made of everything after it -- and ``adopt`` would then
    create an alias for a workspace that does not exist."""
    workspace_id, revision = split_knowledge_collection(f"kn-{_WS}")  # type: ignore[misc]
    assert workspace_id == _WS
    assert revision is None


@pytest.mark.parametrize(
    "name",
    [
        "mem-019f3020-59d6-7ee5-a6df-913e44c5ecf0",  # another module's corpus
        "aizzak-test-scratch",  # a suite leftover
        "kn-not-a-uuid",
        f"kn-{_WS}-nothex-abcdef",  # a trailing group that is not a revision
        f"kn-{_WS}-{_SHIPPED.revision}-extra",
    ],
)
def test_anything_that_is_not_a_knowledge_corpus_parses_as_none(name: str) -> None:
    """The inventory reads whatever Qdrant holds, including collections this
    platform did not create. A parser that guessed at them would put a
    stranger's collection into a workspace's corpus list, and ``drop`` acts on
    that list."""
    assert split_knowledge_collection(name) is None


def test_a_non_canonical_uuid_spelling_is_not_a_workspace() -> None:
    """``uuid.UUID`` accepts braces, urns and bare hex; only the dashed form is
    what ``knowledge_collection`` has ever produced. Accepting the rest would
    let two spellings of one workspace parse out of two collection names, and
    the inventory would report a tenant twice."""
    bare = _WS.replace("-", "")
    assert split_knowledge_collection(f"kn-{bare}") is None
