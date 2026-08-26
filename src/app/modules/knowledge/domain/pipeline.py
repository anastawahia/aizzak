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

**Summary invalidation (§3.6's last clause, plan §4 step 15) — satisfied,
and NOT by a path in this module.** The plan states it as "a changed
`content_hash` deletes the document's `summaries` rows", which reads like a
missing `DELETE`. There is none, because a stored `content_hash` can never
CHANGE — the three rules that make that true are worth naming here, since
this is where anyone chasing the clause will look:

1. a file is immutable once `ready` (INV-F4 — only its name may change), so
   the bytes behind a document never move under it;
2. `Document.complete_indexing` is the ONLY writer of the column and refuses
   any status but `indexing`, and INV-K3 forbids a document ever returning
   to that status — so the value goes `NULL -> hash` exactly once, per
   document, for the document's whole life;
3. re-processing content therefore means a NEW document, and the old one is
   destroyed by `DocumentRepository.purge`, whose FIRST statement deletes
   that document's summaries (INV-K4).

So "a summary that outlived the text it was written from" is unreachable,
and an invalidation path keyed on a hash change would be code no input can
execute. What WOULD make it real is an in-place re-index — a document
returning to `indexing` and completing a second time. That is the change to
make this a live requirement again, and `test_a_documents_content_hash_is_
stamped_once_and_can_never_change` is the tripwire that fails when someone
tries.

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

**Why it is now 3.** Decision س-27 = أ (rag-indexing-plan.md, step 22)
added the SECOND producer of `knowledge.parent_chunks`:
`application/indexing.py`'s `_attach_text_parents` now mints a page parent
for prose, where only the table exploder did before. That changes the stored
output's shape for every document in the corpus — new `parent_chunks` rows,
and a `chunks.parent_id` that is no longer null on prose — which is exactly
the question this constant answers. Without the bump, a re-index of an
already-indexed document would be SKIPPED on its unchanged `content_hash`
and would keep its old, parentless rows: the feature would ship and change
nothing until someone re-uploaded the file.

**Why it is now 4** (rag-fidelity-audit.md §3-د and §3-ج, wave ب items 1-2).
Two changes landed after the `3` above, and BOTH answer this constant's one
question with "yes":

1. the embedding service now pins `EMB_MAX_SEQ_LEN = 512` where it silently
   adopted the checkpoint's own `128` before, so every stored VECTOR changes
   -- a chunk that reached its vector one quarter at a time now reaches it
   whole (measured: 79% of a 354-word chunk never got embedded);
2. `domain/chunking.py`'s `_TOKENS_PER_WORD` was re-measured `1.3` -> `2.4`,
   which moves `max_words_for_token_limit(512)` from 354 words to 192. That
   is the chunk BOUNDARY: the same document now yields a different number of
   `chunks` rows, at different `seq`, carrying different text.

Neither raised this constant when it landed, and that gap is what §5's
step 3 exists to catch. It matters in two directions. Forward: without the
bump, `IndexFile` answers "already current" (`_reflects_current_pipeline`)
for a document built by the OLD chunker, and the upgrade ships invisibly --
§6 risk 4, verbatim. Backward: a clean rebuild would stamp its output `3`,
the same value already worn by rows the 1.3-factor chunker produced, so the
one column that exists to tell those two apart would say they are the same.

**Why it is now 5** (rag-fidelity-audit.md §3-هـ, wave ب). ``domain/tables.py``
restored the two cell cleanings lost in the port from alpha: whitespace
inside a cell -- and inside a column NAME -- is folded to single spaces, and a
cell that is nothing but a stringified ``None``/``null`` renders as empty
instead of as ``"Notes: None"``. Both run through ``row_to_sentence``, which
IS the text of a table-row chunk and of a header-only parent, so the stored
``chunks.text``, its vector, its sparse terms and the ``parent_chunks.text``
above it all change -- for every table in the corpus. Measured on the corpus
rebuilt 2026-08-25: 792 of 1731 chunks are table rows.

The §3-و half of the same wave -- the truncation marker
``application/retrieval.py`` appends when ``max_parent_chunk_chars`` actually
cuts a parent -- is deliberately NOT a reason to raise this. It runs at READ
time over text already in the database and writes nothing, which is the line
``content_pipeline_unchanged`` below draws: this constant answers "would
re-indexing this document produce different ROWS?", and a retrieval change
never can.

**Why it is now 6** (rag-fidelity-audit.md §4-هـ-1, decision س-30, wave ب).
``adapters/parsers/pdf_tables.py`` now drops a table whose data rows are at
least half dot-leader rows: a table of contents is a FALSE table, and it is
dropped whole -- with the rows `P-13` would have exploded it into. That is a
parse-time change that DELETES rows: the corpus's one ToC occupies 41 chunks
(``seq 27..67``) and the parent above them, and a document re-indexed under
the guard produces a strictly different set from the one already stored --
which is this constant's one question, answered "yes".

The guard's other half -- it keeps the table's REGION after dropping its
chunk, so the text pass does not hand the same lines back as prose -- writes
no rows of its own. It is not an independent reason to raise this, but it is
what makes the deletion above stick rather than merely change shape.
"""

from __future__ import annotations

PIPELINE_VERSION = 6


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
