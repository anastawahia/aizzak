"""The pipeline's output-shape fingerprint (rag-indexing-plan.md §3.6,
decision س-14; §6 risk 4).

``PIPELINE_VERSION`` is a single, hand-raised constant — never derived, never
read from `Settings` — because it answers exactly one question: "would
running the pipeline again over this exact content produce output shaped any
differently than what is already stored?" Only a human who just changed a
parser/chunker/tagging rule knows that; nothing computable does.

**The trap this module exists to close (§6 risk 4).** A re-index that skips
work whenever a document's stored `content_hash` still matches its file's
current one would make ANY parser upgrade silently invisible: identical
bytes hash identically forever, so "unchanged content" would look
indistinguishable from "unchanged output" even the day after a parser's
shape changed underneath it. `content_pipeline_unchanged` below is built so
that can never happen on its own — the fingerprint half and the version half
are BOTH required, and a caller that forgets to pass the live
`PIPELINE_VERSION` fails obviously (a `TypeError` on the missing keyword),
not silently.

**Why `PIPELINE_VERSION = 2`.** This constant did not exist before this
plan — there was no prior explicit version to preserve continuity with, so
"1" is reserved as the IMPLICIT baseline every document indexed before this
column existed represents (their stored `pipeline_version` is `NULL`, which
`content_pipeline_unchanged` treats as "differs from anything", never a
match). Plan steps 2, 3, 4, 5, 7, 9, 10, 11 and 13 have ALL changed the
indexed output's shape since that baseline (block-granularity PDF chunks,
table-row explosion, parent chunks, semantic pre-splitting, the multi-signal
ordering key, the widened citation-key allowlist...) — one bump captures the
whole batch, because §5's clean rebuild does not distinguish "how many
things changed", only "did anything". Raising it here, to the first value
past the implicit baseline, is what makes that rebuild (§5) effective: every
row from before this line differs from `PIPELINE_VERSION` by construction.
"""

from __future__ import annotations

PIPELINE_VERSION = 2


def content_pipeline_unchanged(
    *,
    stored_content_hash: str | None,
    current_content_hash: str,
    stored_pipeline_version: int | None,
    current_pipeline_version: int,
) -> bool:
    """The skip predicate (§3.6, decision س-14 = ب): true only when BOTH the
    content fingerprint AND the pipeline version that produced it are
    unchanged — never on either alone.

    A document that was never (successfully) indexed carries
    `stored_content_hash=None`/`stored_pipeline_version=None`, which never
    equals a real hash/version, so this is `False` for it unconditionally —
    there is nothing to skip re-doing.
    """
    return (
        stored_content_hash == current_content_hash
        and stored_pipeline_version == current_pipeline_version
    )
