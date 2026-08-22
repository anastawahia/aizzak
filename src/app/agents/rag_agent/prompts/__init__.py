"""System prompt(s) for ``rag_agent`` (11 §1 ``prompts/``; content adapted from
the harvested ``alpha`` ``_RAG_SYSTEM``, migration/refs/tools + prompts).

**Header instructions (retrieval plan §4 row 7, ``P-37``):** four sentences
appended below tell the model HOW to use the context ``_messages`` builds
(agent.py), not WHAT the context contains — that stays in ``_messages``
itself (the corpus header, the labeled passages). They exist because a
synthesis LLM left to its own defaults tends toward three failure modes this
plan already documents: reading only the first passage that looks relevant
and stopping there, sampling a few items off a list instead of transcribing
all of them, and narrating its own reasoning instead of just answering.

⚠️ **The citation sentence asks for the source in the model's OWN words, and
must never ask it to reproduce the label it is shown.** The first wording of
this row said "cite … using the exact ``[file p.N | section: S]`` label
already shown above each passage", and that broke synthesis outright on the
small local models this platform targets: ``format_labeled_chunk`` renders
each label as its own LINE directly above its passage, so an instruction to
emit that exact label — next to "answer directly, do not narrate" — reads to
a ``gemma3:1b`` as an OUTPUT TEMPLATE rather than a citation rule. The model
copied the block instead of answering it, and a real answer came back as::

    [criteria.pdf p.16]
    3.6 Generator Step-Up Transformers (GSUT)

— a label plus one heading line, 61 characters, presented to the user as the
whole answer. Measured against the live model on the exact context that
produced it: the old wording copied a label instead of answering in **24 of
40** samples across four questions, this wording in **2 of 40**, and the
difference is this sentence alone (adding a "never reply with a label" guard
to the old wording changed almost nothing — 11/18 — because the driver is
the instruction to reproduce a shape the context literally contains).

So the label stays exactly as §3.2 fixes it (``P-31`` is untouched: this file
sends no format and ``format_labeled_chunk`` still owns the one shape), and
what changed is that the prompt now names the FIELDS to cite — file, page,
section — with a parenthesised example that is deliberately NOT the bracketed
label shape, plus an explicit refusal of the failure above. Row 7's
requirement ("استشهد بالملفّ والقسم") is met by naming both, not by dictating
their typography.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You are a retrieval-augmented assistant for a workspace. "
    "Answer the user's question using ONLY the provided context passages. "
    "If the context is insufficient, say so plainly instead of inventing facts. "
    "Gather your answer from ALL the passages provided, not just the first one "
    "that looks relevant — the evidence needed for a complete answer is often "
    "spread across more than one passage. "
    "If the answer is a list, include EVERY item found in the context; never "
    "truncate the list or sample from it. "
    "Write the answer in your own sentences, then name the file, page and section "
    "each claim came from — for example (criteria.pdf p.16, section 3.6). Never reply "
    "with a source label alone, and never copy a passage's heading line as the whole "
    "answer. "
    "Answer directly — do not narrate your reasoning or think out loud. "
    "Answer in the same language as the question."
)
