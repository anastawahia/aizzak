"""``P-38``'s measurement harness — the program every number in
docs/rag-fidelity-audit.md §4-و came out of.

Runs the REAL ``RetrieveContext.execute`` against a REAL indexed corpus, once
per (question, language, tuning variant), and records what each stage did.
**Nothing about retrieval is re-implemented here.** The only thing this file
owns is measurement plumbing:

* ``_LOG_RANKING_SAMPLE`` is raised so the structured stage log quotes the
  WHOLE ranking rather than its head. ``retrieval.py`` documents that constant
  as a log-SHAPE constant ("Not a ``Settings`` knob"), so raising it changes
  how deep the record quotes and never what the pipeline does.
* a ``logging.Handler`` captures the one ``knowledge.retrieval`` record each
  call emits. That record IS the measurement — every stage count and score a
  calibration reads is already in it, which is what plan step 17 (``P-29``)
  built it for.

⚠️ **Gold ids are keyed on ``chunks.point_id``, never ``chunks.id``.** The id
every retrieval stage reports (``FusedChunk.chunk_id``,
``RetrievedChunk.chunk_id``, ``delivered_chunk_ids``) is the Qdrant POINT id,
and the two columns hold different uuids. Matching on the wrong one reports
every gold chunk as missing while every other number stays plausible.

**Running it.** This needs a live stack and an indexed corpus, which is why it
is not a test — arranging both is a runbook step, not a fixture's job. With
the handbook uploaded and indexed into one space:

    docker cp tests/eval aizzak-app-1:/tmp/eval
    docker exec \\
      -e EVAL_WORKSPACE=<workspace uuid> -e EVAL_USER=<user uuid> \\
      -e EVAL_SPACE=<space uuid> \\
      -e EVAL_VARIANTS='[{"name":"shipped"},{"name":"floor","min_bm25_score":25.0}]' \\
      aizzak-app-1 python /tmp/eval/run_calibration.py > run.jsonl

It prints JSON lines: one ``gold`` record (the resolved gold set, so a broken
pattern shows up immediately instead of as a silent zero), one ``base_tuning``
record (the SHIPPED numbers it varied from, read out of ``Settings`` rather
than assumed), then one ``probe`` record per question, language and variant.

``EVAL_VARIANTS`` is a JSON list of ``RetrievalTuning`` overrides, each with a
``name``. Each is applied with ``dataclasses.replace`` onto the shipped
tuning, so anything a variant does not name keeps its shipped value and a
sweep can never silently drift from the deployment it claims to measure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from dataclasses import asdict, replace
from typing import Any

from hr_handbook_set import NEGATIVES, QUESTIONS
from sqlalchemy import text as sql

from app.framework.context.execution_context import ExecutionContext
from app.framework.di.composition_root import CompositionRoot
from app.framework.identifiers import new_uuid7
from app.infrastructure.ai_providers.embedding.external_embedding import (
    ExternalEmbeddingProvider,
)
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application import retrieval as retrieval_module
from app.modules.knowledge.application.retrieval import RetrievalTuning, RetrieveContext
from app.modules.knowledge.domain.context_budget import estimate_tokens

WORKSPACE = os.environ["EVAL_WORKSPACE"]
USER = os.environ["EVAL_USER"]
SPACE = os.environ["EVAL_SPACE"]

# The log record is the measurement; quote all of it, not its first 20 entries.
retrieval_module._LOG_RANKING_SAMPLE = 10_000

_CHUNK_QUERY = (
    "SELECT c.point_id::text, c.seq, c.text, c.document_id::text, c.parent_id::text "
    "FROM knowledge.chunks c JOIN knowledge.documents d ON d.id = c.document_id "
    "WHERE d.space_id = :space"
)


class _Capture(logging.Handler):
    """Keeps the ``knowledge.retrieval`` records ``execute`` emits, and only
    those — the logger carries other records and a filter here is cheaper
    than sorting them out afterwards."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == "knowledge.retrieval":
            self.records.append(record)


async def _load_corpus(session_factory: Any, ctx: ExecutionContext) -> dict[str, dict[str, Any]]:
    """Every chunk in the space, keyed by POINT id (see the module docstring)."""
    async with session_factory(ctx) as session:
        rows = (await session.execute(sql(_CHUNK_QUERY), {"space": SPACE})).all()
    return {
        row[0]: {"seq": row[1], "text": row[2], "document_id": row[3], "parent_id": row[4]}
        for row in rows
    }


def _resolve_gold(corpus: dict[str, dict[str, Any]]) -> dict[int, set[str]]:
    """Which chunks actually carry each question's answer — decided by the
    INDEXED text, never asserted. A question whose set comes back empty means
    its pattern has drifted from the corpus, not that retrieval failed, and
    the ``gold`` record printed below is what makes that visible."""
    gold: dict[int, set[str]] = {}
    for question in QUESTIONS:
        patterns = [re.compile(pattern) for pattern in question["gold"]]
        gold[question["id"]] = {
            point_id
            for point_id, chunk in corpus.items()
            if any(pattern.search(chunk["text"]) for pattern in patterns)
        }
    return gold


def _first_rank(ids: list[str], wanted: set[str]) -> int | None:
    return next((rank for rank, chunk_id in enumerate(ids) if chunk_id in wanted), None)


def _shipped_tuning(settings: Any) -> RetrievalTuning:
    """The deployment's own numbers, read out of ``Settings`` exactly as the
    Composition Root maps them — so a sweep varies from what is DEPLOYED and
    not from ``RetrievalTuning``'s mirrored defaults, which could drift."""
    retrieval, limits = settings.retrieval, settings.limits
    return RetrievalTuning(
        weight_dense=retrieval.weight_dense,
        weight_bm25=retrieval.weight_bm25,
        rrf_k=retrieval.rrf_k,
        search_overfetch=retrieval.search_overfetch,
        max_search_candidates=retrieval.max_search_candidates,
        max_sparse_candidates=retrieval.max_sparse_candidates,
        fusion_retention=retrieval.fusion_retention,
        default_k=retrieval.default_k,
        max_k=limits.max_rag_k,
        min_dense_score=retrieval.min_dense_score,
        min_bm25_score=retrieval.min_bm25_score,
        min_fused_score=retrieval.min_fused_score,
        relative_floor=retrieval.relative_floor,
        jaccard_threshold=retrieval.jaccard_threshold,
        max_parent_chunk_chars=retrieval.max_parent_chunk_chars,
        mmr_lambda=retrieval.mmr_lambda,
        mmr_overfetch=retrieval.mmr_overfetch,
        rerank_enabled=retrieval.rerank_enabled,
        rerank_candidates=retrieval.rerank_candidates,
        max_context_chars=limits.max_context_chars,
        max_context_tokens=limits.max_context_tokens,
    )


def _probes(
    gold: dict[int, set[str]], corpus: dict[str, dict[str, Any]]
) -> list[tuple[str, str, str, set[str], set[str], list[str]]]:
    """Each question in both languages, then each negative in both — one flat
    list so every variant runs the identical sequence."""
    gold_parents = {
        qid: {corpus[c]["parent_id"] for c in ids if corpus[c]["parent_id"]}
        for qid, ids in gold.items()
    }
    probes = []
    for question in QUESTIONS:
        qid = question["id"]
        for lang in ("en", "ar"):
            probes.append(
                (str(qid), lang, question[lang], gold[qid], gold_parents[qid], question["gold"])
            )
    for negative in NEGATIVES:
        for lang in ("en", "ar"):
            probes.append((negative["id"], lang, negative[lang], set(), set(), []))
    return probes


def _measure(
    record: logging.LogRecord,
    result: Any,
    corpus: dict[str, dict[str, Any]],
    gold_ids: set[str],
    gold_parent_ids: set[str],
    patterns: list[str],
) -> dict[str, Any]:
    fused = list(record.candidates)
    fused_ids = [candidate["chunk_id"] for candidate in fused]
    fused_scores = [candidate["rrf_score"] for candidate in fused]
    # The unit the model actually reads is the PARENT (`P-34` replaces each
    # leaf's text with its parent's), so a candidate whose parent carries the
    # fact is a hit even when its own leaf does not.
    fused_parents = [corpus[c]["parent_id"] if c in corpus else None for c in fused_ids]
    gold_parent_rank = next(
        (rank for rank, parent in enumerate(fused_parents) if parent in gold_parent_ids), None
    )
    top_non_gold = next(
        (
            candidate["rrf_score"]
            for candidate, parent in zip(fused, fused_parents, strict=True)
            if candidate["chunk_id"] not in gold_ids and parent not in gold_parent_ids
        ),
        None,
    )
    dense_scores = list(record.dense_scores)
    sparse_scores = list(record.sparse_scores)
    positive_sparse = [score for score in sparse_scores if score > 0.0]
    compiled = [re.compile(pattern) for pattern in patterns]
    delivered = list(record.delivered_chunk_ids)
    return {
        "dense_count": record.dense_count,
        "sparse_count": record.sparse_count,
        "dense_scores": dense_scores,
        "sparse_scores": sparse_scores,
        # The zero-scored sparse hits `min_bm25_score` was calibrated against:
        # Qdrant answers a filtered sparse query with no matching posting list
        # by returning arbitrary points at exactly 0.0.
        "sparse_zero_count": len(sparse_scores) - len(positive_sparse),
        "sparse_min_positive": min(positive_sparse) if positive_sparse else None,
        "dense_min": min(dense_scores) if dense_scores else None,
        "best_dense_score": record.best_dense_score,
        "best_bm25_score": record.best_bm25_score,
        "dense_kept": record.dense_kept,
        "sparse_kept": record.sparse_kept,
        "fused_count": record.fused_count,
        "fused_scores": fused_scores,
        "origin_counts": dict(record.origin_counts),
        "gold_fused_rank": _first_rank(fused_ids, gold_ids),
        "gold_parent_rank": gold_parent_rank,
        "gold_parent_rrf": None if gold_parent_rank is None else fused_scores[gold_parent_rank],
        "top_non_gold_rrf": top_non_gold,
        "max_rrf": fused_scores[0] if fused_scores else None,
        "mmr_count": record.mmr_count,
        "relevant_count": record.relevant_count,
        "widened_count": record.widened_count,
        "duplicate_text_count": record.duplicate_text_count,
        "budgeted_count": record.budgeted_count,
        "context_nodes": record.context_nodes,
        "delivered_seqs": [corpus[c]["seq"] if c in corpus else None for c in delivered],
        "delivered_docs": [corpus[c]["document_id"] if c in corpus else None for c in delivered],
        "gold_delivered": any(c in gold_ids for c in delivered),
        # The end-to-end measure, and the one to trust: did the DELIVERED
        # CONTEXT carry the fact, whichever candidate it arrived inside.
        "answer_in_context": bool(compiled)
        and any(pattern.search(result.context_text) for pattern in compiled),
        "context_chars": len(result.context_text),
        "context_tokens_est": estimate_tokens(result.context_text),
        "chunk_chars": [len(chunk.text) for chunk in result.chunks],
        "total_ms": record.total_ms,
    }


async def main() -> None:
    root = CompositionRoot.from_env()
    ctx = ExecutionContext(
        workspace_id=WORKSPACE,
        user_id=USER,
        correlation_id=new_uuid7(),
        roles=frozenset({"owner"}),
    )
    embeddings = ExternalEmbeddingProvider(root.embedding_http, root.settings.embedding_service)
    documents = SqlDocumentRepository(root.tenant_session)
    _, resolved = await root.provider_resolver.resolve_embedding(ctx)

    corpus = await _load_corpus(root.tenant_session, ctx)
    gold = _resolve_gold(corpus)
    print(
        json.dumps(
            {
                "kind": "gold",
                "corpus_chunks": len(corpus),
                "gold": {qid: sorted(corpus[c]["seq"] for c in ids) for qid, ids in gold.items()},
            }
        ),
        flush=True,
    )

    base = _shipped_tuning(root.settings)
    print(json.dumps({"kind": "base_tuning", "tuning": asdict(base)}), flush=True)

    capture = _Capture()
    logging.getLogger(retrieval_module.__name__).addHandler(capture)
    probes = _probes(gold, corpus)

    for variant in json.loads(os.environ.get("EVAL_VARIANTS", '[{"name": "shipped"}]')):
        overrides = {key: value for key, value in variant.items() if key != "name"}
        retrieve = RetrieveContext(
            embeddings, root.vector_store, documents, tuning=replace(base, **overrides)
        )
        for qid, lang, query, gold_ids, gold_parent_ids, patterns in probes:
            capture.records.clear()
            result = await retrieve.execute(
                ctx, query=query, model=resolved.model, api_key=resolved.api_key, space_id=SPACE
            )
            measured = _measure(
                capture.records[-1], result, corpus, gold_ids, gold_parent_ids, patterns
            )
            head = {
                "kind": "probe",
                "variant": variant["name"],
                "qid": qid,
                "lang": lang,
                "gold_count": len(gold_ids),
            }
            print(json.dumps(head | measured, ensure_ascii=False), flush=True)

    for disposable in root.disposables():
        with contextlib.suppress(Exception):  # teardown of a one-shot script
            await disposable()


asyncio.run(main())
