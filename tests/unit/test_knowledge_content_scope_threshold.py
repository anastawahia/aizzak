"""The bar a named file has to clear before it NARROWS a content search
(``application/routing.py::_content_scope``, branch review §3 over retrieval
plan §4 row 15 ``P-25``).

``_is_exact`` resolves a file whose normalized name appears ANYWHERE inside
the normalized question. On the summarisation route that is the whole point —
the question is about a file, and «لخّص تقرير» has to work. On the CONTENT
route it reads a subject word as a file reference: a corpus that happens to
hold «تقرير.pdf» answers «ما هو الحد الأقصى للإجازات حسب تقرير الأداء
السنوي؟» with ``ResolvedFile(method=EXACT, score=1.0)``, the search is pinned
to that one document, and — because row 15 is STRICT, with no widening and no
second retrieval — the user is told «لا أملك معلومات كافية» about a question
the corpus answers in a different file. Silently: nothing in that answer names
the file that swallowed it.

So these tests pin the two halves of the remedy:

* the CONTENT route requires ``EXACT`` **and** a matched name of more than one
  token (``_MIN_CONTENT_SCOPE_NAME_TOKENS``) before it narrows, and leaves the
  caller's pin exactly as it arrived otherwise;
* the resolver itself is UNCHANGED — the same trap question still resolves
  EXACT with ``score=1.0``, and the summarisation route still acts on it.

The fakes are local (nothing imported from another test module) and the
router under test is the REAL ``RouteQuestion`` over the REAL
``resolve_file``: what is faked is only what is on the far side of the two
seams it was given — the retrieval it hands a scope to, and the corpus of
file names it matches against.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.modules.knowledge.application.retrieval import RetrievalResult
from app.modules.knowledge.application.routing import (
    _MIN_CONTENT_SCOPE_NAME_TOKENS,
    RouteQuestion,
)
from app.modules.knowledge.domain.entities import SummaryJob
from app.modules.knowledge.domain.file_resolution import (
    FileCandidate,
    ResolutionMethod,
    ResolvedFile,
    name_token_count,
    resolve_file,
)
from app.modules.knowledge.domain.intent import Intent, classify_intent
from app.modules.knowledge.domain.value_objects import (
    SummaryJobStatus,
    SummaryKind,
    SummaryLanguage,
)
from app.modules.knowledge.ports.retrieval import RetrievedChunk

# The review's own corpus: one file whose whole name is a word that ordinary
# questions use for its own sake, and one file that actually answers them.
_ONE_WORD_REPORT = "تقرير.pdf"
_LEAVE_POLICY = "سياسة الإجازات السنوية.pdf"
_CORPUS = {"doc-report": _ONE_WORD_REPORT, "doc-leave": _LEAVE_POLICY}

# The reproduction, verbatim from the review: «تقرير» is in it, and it is not
# a question about «تقرير.pdf».
_TRAP_QUESTION = "ما هو الحد الأقصى للإجازات حسب تقرير الأداء السنوي؟"
# The same corpus, asked about properly: the file is NAMED, in full.
_NAMED_QUESTION = "ما هي بنود سياسة الإجازات السنوية؟"


def _ctx(workspace_id: str = "ws1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id, user_id="u1", correlation_id="corr", roles=frozenset({"member"})
    )


class _ScopeSpy:
    """A structural stand-in for ``RetrieveContext``: records the SCOPE each
    call was made under and answers from whatever that scope allows.

    ``RouteQuestion`` calls one method on its retrieval collaborator and reads
    ``.chunks`` off the result, so this records the one thing these tests are
    about — what ``_content_scope`` decided — without a vector store, an
    embedding provider or a document repository standing behind it.
    """

    def __init__(self, corpus: dict[str, str]) -> None:
        self._corpus = corpus
        self.scopes: list[Sequence[str] | None] = []

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        query: str,
        model: str,
        api_key: str,
        k: int | None = None,
        document_ids: Sequence[str] | None = None,
        space_id: str | None,
    ) -> RetrievalResult:
        self.scopes.append(document_ids)
        allowed = (
            self._corpus
            if document_ids is None
            else {doc: name for doc, name in self._corpus.items() if doc in set(document_ids)}
        )
        chunks = [
            RetrievedChunk(
                document_id=doc,
                chunk_id=f"{doc}-c1",
                text=f"محتوى {name}",
                score=1.0,
                file_name=name,
            )
            for doc, name in allowed.items()
        ]
        return RetrievalResult(
            chunks=chunks,
            best_dense_score=1.0 if chunks else None,
            best_bm25_score=None,
        )


class _Corpus:
    """A structural ``FileCandidates``: what this workspace's files are
    called, which is all ``resolve_file`` ever sees."""

    def __init__(self, names: dict[str, str]) -> None:
        self._candidates = tuple(
            FileCandidate(document_id=doc, file_name=name) for doc, name in names.items()
        )
        self.calls: list[ExecutionContext] = []

    async def execute(
        self, ctx: ExecutionContext, *, space_id: str | None
    ) -> Sequence[FileCandidate]:
        # `space_id` is accepted and ignored: this corpus is already the one
        # space these tests search, and what they grade is the token bar, not
        # the space axis. That the router forwards the space it was asked for
        # (branch review §7) is pinned in `test_knowledge_module.py`'s last
        # section, over the REAL `ListFileCandidates`.
        self.calls.append(ctx)
        return self._candidates


class _SummarySpy:
    """A structural ``SummaryStarting``: records what a routed summarisation
    asked for and hands back a queued job."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, SummaryKind, SummaryLanguage]] = []

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        document_id: str,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> SummaryJob:
        self.calls.append((document_id, kind, lang))
        return SummaryJob(
            id=f"job-{len(self.calls)}",
            workspace_id=ctx.workspace_id,
            document_id=document_id,
            kind=kind,
            lang=lang,
            status=SummaryJobStatus.QUEUED,
            total_chunks=0,
            done_chunks=0,
            error=None,
            cancelled_at=None,
            finished_at=None,
            created_at=utc_now(),
        )


def _router(
    corpus: dict[str, str] | None = None,
) -> tuple[RouteQuestion, _ScopeSpy, _Corpus, _SummarySpy]:
    """The REAL ``RouteQuestion`` over the two-file corpus above, plus the
    three seams it was built with so a test can read them back."""
    files = _Corpus(corpus if corpus is not None else _CORPUS)
    retrieval = _ScopeSpy(corpus if corpus is not None else _CORPUS)
    summaries = _SummarySpy()
    # `retrieval`/`summaries`/`files` are structural stand-ins for the three
    # collaborators; two of the three are already Protocols on the router's
    # constructor, and the third is called through one method.
    router = RouteQuestion(retrieval, summaries, files)  # type: ignore[arg-type]
    return router, retrieval, files, summaries


async def _ask(
    router: RouteQuestion,
    question: str,
    document_ids: Sequence[str] | None = None,
) -> tuple[Intent, set[str]]:
    routed = await router.execute(
        _ctx(),
        question=question,
        model="embed-1",
        api_key="key-1",
        document_ids=document_ids,
        space_id=None,
    )
    return routed.intent, {chunk.document_id for chunk in routed.chunks}


# --------------------------------------------------------------------------- #
# The review's reproduction (§3)                                              #
# --------------------------------------------------------------------------- #
async def test_a_one_word_file_name_inside_a_subject_question_never_narrows_the_search() -> None:
    """**THE case.** «تقرير» is a word this question uses for its own sake and
    also, by coincidence, a whole file name. Before the bar, that coincidence
    pinned the search to «تقرير.pdf» — with ``score=1.0``, and with no second,
    wider attempt behind it, because row 15 is strict. The corpus answers this
    question in «سياسة الإجازات السنوية.pdf», and that is what has to come
    back.
    """
    router, retrieval, _files, summaries = _router()

    intent, answered = await _ask(router, _TRAP_QUESTION)

    # The search was made ONCE, and unscoped: the pin (`None`) reached
    # retrieval exactly as it arrived.
    assert retrieval.scopes == [None]
    # So the file that DOES answer the question is still reachable...
    assert "doc-leave" in answered
    # ...and the one-word file did not hide it.
    assert answered == {"doc-report", "doc-leave"}
    assert intent is Intent.CONTENT
    assert summaries.calls == []


async def test_the_trap_question_still_resolves_exact_because_the_resolver_is_untouched() -> None:
    """The remedy is a bar on the CONTENT route, NOT a change to
    ``_is_exact`` — so the resolver still answers the review's question the
    same confident, wrong way it always did.

    This test exists to fail if somebody "fixes" the substring test itself:
    that is a different decision (it would change the summarisation route
    too), and it should not be able to happen quietly under this one.
    """
    candidates = [FileCandidate(document_id=doc, file_name=name) for doc, name in _CORPUS.items()]

    resolution = resolve_file(_TRAP_QUESTION, candidates)

    assert isinstance(resolution, ResolvedFile)
    assert resolution.document_id == "doc-report"
    assert resolution.method is ResolutionMethod.EXACT
    assert resolution.score == 1.0


# --------------------------------------------------------------------------- #
# No regression: a question that really does name a file                      #
# --------------------------------------------------------------------------- #
async def test_a_question_that_names_a_multi_token_file_is_still_searched_inside_it() -> None:
    """Row 15's narrowing, intact. The question names «سياسة الإجازات
    السنوية» — three tokens, matched EXACT — so the search reaches retrieval
    scoped to that one document, exactly as before the bar existed.
    """
    router, retrieval, _files, summaries = _router()
    assert name_token_count(_LEAVE_POLICY) >= _MIN_CONTENT_SCOPE_NAME_TOKENS

    intent, answered = await _ask(router, _NAMED_QUESTION)

    assert retrieval.scopes == [["doc-leave"]]
    assert answered == {"doc-leave"}
    assert intent is Intent.CONTENT
    assert summaries.calls == []


async def test_the_two_questions_differ_only_in_the_matched_names_token_count() -> None:
    """What the bar actually grades, side by side: both questions resolve to
    a file EXACT-ly and with full confidence, and the ONLY thing that tells
    them apart is how discriminating the name that matched is — one token
    against three (``_MIN_CONTENT_SCOPE_NAME_TOKENS``, the single knob).
    """
    router, retrieval, _files, _summaries = _router()

    await _ask(router, _TRAP_QUESTION)
    await _ask(router, _NAMED_QUESTION)

    assert name_token_count(_ONE_WORD_REPORT) < _MIN_CONTENT_SCOPE_NAME_TOKENS
    assert name_token_count(_LEAVE_POLICY) >= _MIN_CONTENT_SCOPE_NAME_TOKENS
    assert retrieval.scopes == [None, ["doc-leave"]]


@pytest.mark.parametrize(
    ("file_name", "tokens"),
    [
        ("تقرير.pdf", 1),  # the review's file
        ("report.pdf", 1),  # and its English twin
        ("سياسة الإجازات السنوية.pdf", 3),
        ("التقرير الشمالي.pdf", 2),
        # Separators are tokens too, which is why the count has to come from
        # the resolver's own normalizer and not from `str.split()`.
        ("Q3_Report.pdf", 2),
        ("", 0),
        (".pdf", 0),
    ],
)
def test_name_token_count_measures_the_name_the_resolver_matched_on(
    file_name: str, tokens: int
) -> None:
    """The domain helper the bar is measured with: the extension is dropped,
    separators are tokens, and a name with nothing left in it counts zero —
    the same nothing ``_is_exact`` refuses to match on."""
    assert name_token_count(file_name) == tokens


# --------------------------------------------------------------------------- #
# The pin is left as it arrived — never narrowed, never widened               #
# --------------------------------------------------------------------------- #
async def test_a_one_word_match_leaves_the_callers_pin_exactly_as_it_arrived() -> None:
    """Not narrowing is not widening. A conversation pinned to both documents
    asks the trap question, and the scope that reaches retrieval is the pin —
    both documents, unchanged — rather than the single document the one-word
    name would have collapsed it to.
    """
    router, retrieval, _files, _summaries = _router()

    _intent, answered = await _ask(router, _TRAP_QUESTION, ["doc-report", "doc-leave"])

    assert retrieval.scopes == [["doc-report", "doc-leave"]]
    assert answered == {"doc-report", "doc-leave"}


async def test_a_fuzzy_resolution_no_longer_narrows_a_content_search() -> None:
    """The second half of the bar: ``method is EXACT``. «سياسة الاجازات
    السنوي» is close enough to the file's name to resolve FUZZY at ~0.79 —
    a fair guess at which file a user MEANT when the whole question is a file
    reference, and too weak a reason to hide the rest of the corpus from a
    question that is not one.
    """
    question = "ما هي سياسة الاجازات السنوي؟"
    candidates = [FileCandidate(document_id=doc, file_name=name) for doc, name in _CORPUS.items()]
    resolution = resolve_file(question, candidates)
    # The premise: this really is a confident FUZZY resolution, not a tie.
    assert isinstance(resolution, ResolvedFile)
    assert resolution.method is ResolutionMethod.FUZZY
    assert classify_intent(question) is Intent.CONTENT
    router, retrieval, _files, _summaries = _router()

    _intent, answered = await _ask(router, question)

    assert retrieval.scopes == [None]
    assert answered == {"doc-report", "doc-leave"}


# --------------------------------------------------------------------------- #
# The summarisation route is untouched                                        #
# --------------------------------------------------------------------------- #
async def test_the_summarisation_route_still_acts_on_a_one_word_name() -> None:
    """The route ``_is_exact`` was written for, unchanged. «لخص لي تقرير»
    names one file and nothing else, the EXACT layer identifies it, and the
    build is queued against THAT document — the one-token bar is a CONTENT
    scoping rule and never reaches this path.
    """
    router, retrieval, _files, summaries = _router()

    routed = await router.execute(
        _ctx(),
        question="لخص لي تقرير",
        model="embed-1",
        api_key="key-1",
        space_id=None,
    )

    assert routed.intent is Intent.SUMMARIZE_DOC
    assert summaries.calls == [("doc-report", SummaryKind.OVERVIEW, SummaryLanguage.AUTO)]
    assert routed.summary_job_id == "job-1"
    assert routed.clarification_options == ()
    # The two routes are alternatives: nothing was retrieved, so nothing was
    # scoped either.
    assert retrieval.scopes == []


async def test_a_summarisation_question_naming_a_multi_token_file_is_unaffected_too() -> None:
    """The same, for the name that WOULD clear the content bar: the bar is not
    a filter on resolutions, it is a condition on one caller's use of them."""
    router, _retrieval, _files, summaries = _router()

    routed = await router.execute(
        _ctx(),
        question="لخص لي سياسة الإجازات السنوية",
        model="embed-1",
        api_key="key-1",
        space_id=None,
    )

    assert routed.intent is Intent.SUMMARIZE_DOC
    assert summaries.calls == [("doc-leave", SummaryKind.OVERVIEW, SummaryLanguage.AUTO)]
