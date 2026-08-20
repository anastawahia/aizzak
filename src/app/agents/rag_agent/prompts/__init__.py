"""System prompt(s) for ``rag_agent`` (11 §1 ``prompts/``; content adapted from
the harvested ``alpha`` ``_RAG_SYSTEM``, migration/refs/tools + prompts).

**Header instructions (retrieval plan §4 row 7, ``P-37``):** four sentences
appended below tell the model HOW to use the context ``_messages`` builds
(agent.py), not WHAT the context contains — that stays in ``_messages``
itself (the corpus header, the labeled passages). They exist because a
synthesis LLM left to its own defaults tends toward three failure modes this
plan already documents: reading only the first passage that looks relevant
and stopping there, sampling a few items off a list instead of transcribing
all of them, and narrating its own reasoning instead of just answering. The
citation sentence deliberately replaces the OLD "cite passages by their [n]
index" wording: chunks carry no numeric index any more (retrieval plan §3.2,
row 2, ``P-31``) — ``format_labeled_chunk`` puts a
``[file p.N | section: S]`` label above each passage instead — so the prompt
now asks for exactly that vocabulary, the one label format the model is
actually shown, rather than a shape ``_messages`` never produces.
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
    "Cite the file and section for your claims, using the exact "
    "[file p.N | section: S] label already shown above each passage. "
    "Answer directly — do not narrate your reasoning or think out loud. "
    "Answer in the same language as the question."
)
