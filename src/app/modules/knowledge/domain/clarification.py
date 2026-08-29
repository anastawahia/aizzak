"""Reading a turn as an ANSWER to the question the last turn asked — pure
(``docs/rag-agent-scenarios-implementation-plan.md`` §7, ب-9, gap ف-1أ).

``file_resolution`` answers "which of this corpus does the user mean?".
This answers a narrower and different question: "the user was shown these
names, in this order, and asked to choose — did they?" The difference is not
cosmetic, and both halves of it matter:

* **The corpus is not the choice set.** What may be chosen is the list that
  was displayed, and nothing else. That is what makes «2025» answerable at
  all: across a whole workspace it is a fragment several files share, and the
  resolver rightly refuses to pick one — but between «تقرير الأداء 2025.pdf»
  and «تقرير الأداء 2024.pdf», the two names the user was actually offered, it
  is unambiguous. Narrowing to the offer is not a filter applied to a
  resolution; it is what turns a refusal into an answer.
* **A position is a valid answer.** «الثاني» names nothing and matches
  nothing — it points. Pointing is only meaningful against a list somebody
  saw, which is the second reason the offer has to be carried across the turn
  and carried IN ORDER.

**Why this is not in ``file_resolution``.** That module is a documented port
of alpha's cascade, and its docstring's contract is what was and was not
carried over. This is not alpha's; it is AIZZAK's own reading of a two-turn
exchange alpha never had. Folding it in would make "what is ported" a
question the reader has to answer by inspection. It composes the cascade
rather than reimplementing any of it: the EXACT and FUZZY layers here are
``resolve_file``'s own, run over a shorter list.

**The order is (أ) name, (ب) position, (ج) fragment**, and each step earns
its place ahead of the next:

1. **An exact name wins outright.** A user who typed a whole file name has
   made the identification themselves, and nothing this module infers can
   improve on that.
2. **Then a position**, because a bare number or «الثاني» is a pointing
   gesture and never a name — but only when the reply is that gesture and
   nothing else (``read_ordinal``).
3. **Then a fragment**, because a partial name is a weaker identification
   than either of the two above, and it must not be allowed to outrank a
   position: «2» among two candidates is the second one, not the file whose
   name happens to contain a 2.

**Nothing is a guess.** Every path returns either a candidate the user's own
words select or ``None``, and ``None`` means "this turn was not an answer" —
which the caller resolves by treating it as an ordinary new question. That
fallback is what makes the whole mechanism safe to try on EVERY turn: a user
who ignored the question and asked something else is answered, not corrected.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.knowledge.domain.file_resolution import (
    FileCandidate,
    ResolutionMethod,
    ResolvedFile,
    read_ordinal,
    resolve_file,
)


def resolve_clarification_reply(
    reply: str,
    offered: Sequence[str],
    candidates: Sequence[FileCandidate],
) -> FileCandidate | None:
    """The candidate ``reply`` chooses out of ``offered``, or ``None``.

    ``offered`` is the list of file NAMES the previous turn displayed, in the
    order it displayed them. ``candidates`` is the corpus as it stands NOW —
    already narrowed to the space and to any caller pin, exactly the list the
    resolver would search.

    The two are separate arguments and not one joined list on purpose. A
    position indexes what was SHOWN, so it has to be read against ``offered``
    even when one of those files has since been deleted; a name has to be
    proved against ``candidates``, because a name that no longer belongs to a
    live document cannot be summarised. Joining them first would force one of
    those two rules to be dropped, and the one that would go is the position.

    ``None`` when the reply chooses nothing, when it chooses a name that no
    longer resolves, or when ``offered`` is empty — a thread that asked
    nothing has nothing to be answered.
    """
    if not offered:
        return None
    # The offer, restricted to what still exists and kept in the order it was
    # SHOWN in rather than the order the corpus walk returned. The lexical
    # layers below are order-stable on ties (`_rank`), so preserving the
    # display order is what keeps a tie between two offered names resolving
    # the way the user read them.
    by_name = {candidate.file_name: candidate for candidate in candidates}
    live = [by_name[name] for name in offered if name in by_name]

    # (أ) — the whole name, typed. `resolve_file`'s own EXACT layer, over the
    # offer instead of over the corpus. Read off a full resolution rather than
    # re-testing membership, so "what counts as naming a file" stays one
    # answer: the same normalization, the same article stripping, the same
    # extension handling that decided every other match in this module.
    resolution = resolve_file(reply, live) if live else None
    if isinstance(resolution, ResolvedFile) and resolution.method is ResolutionMethod.EXACT:
        return by_name[resolution.file_name]

    # (ب) — a position, indexed against the FULL offer. A reply that points at
    # a file which has since been deleted resolves to nothing rather than
    # sliding onto its neighbour: silently shifting what «الثاني» means,
    # because a file disappeared between two turns, is the one failure this
    # whole path exists to avoid.
    position = read_ordinal(reply, len(offered))
    if position is not None:
        return by_name.get(offered[position - 1])

    # (ج) — a fragment of a name. Reached only when the reply named nothing
    # outright and pointed at nothing, which is what keeps it from stealing
    # either of the readings above.
    if isinstance(resolution, ResolvedFile):
        return by_name[resolution.file_name]
    return None
