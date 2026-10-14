# `P-38` — the retrieval evaluation set

The one item in [the port-fidelity audit](../../docs/rag-fidelity-audit.md)
that waited on an **input from the owner** rather than on a decision or on
work: a set of questions with reference answers, over real documents. It
arrived on 2026-08-27 and closed decision **س-22**.

| file | what it is |
|---|---|
| `hr_handbook_set.py` | the set itself — 15 questions in two languages with reference answers and gold patterns, plus 6 questions the corpus cannot answer |
| `run_calibration.py` | the harness — runs the real `RetrieveContext.execute` per question, language and tuning variant, and prints one JSON line per probe |
| `../unit/test_evaluation_set.py` | pins the set's shape, and checks the questions against `docs/hr-quiz-en.md` / `docs/hr-quiz.md` character for character |
| `vector_parity.py` | **not `P-38`** — gpu-compose 2.4's measuring tool: CPU-against-CUDA vector parity (`compare`), the fixed input set (`build-set`) and the memory soak (`soak`); see the last section |
| `../unit/test_vector_parity.py` | tests of that tool's maths, its set, and its two network subcommands against a fake service |

Neither module is named `test_*`, so pytest collects nothing here: the harness
needs a live stack and an indexed corpus, and arranging both is a runbook step
rather than a fixture's job.

## The corpus the recorded numbers were measured on

**One space, two documents** — `docs/hr-no-table.docx` (the handbook the quiz
is about: 221 chunks, 23 parents, zero Arabic characters) and an unrelated
electrical design criteria PDF (811 chunks). The second is not contamination:
it is the distractor that makes *"did the answer come from the right
document"* a question with an answer at all. Half the questions are asked in
Arabic against that English corpus, which is the hardest cross-lingual case
the platform actually serves.

## Running it

```bash
docker cp tests/eval aizzak-app-1:/tmp/eval
docker exec \
  -e EVAL_WORKSPACE=<workspace uuid> \
  -e EVAL_USER=<user uuid> \
  -e EVAL_SPACE=<space uuid> \
  -e EVAL_VARIANTS='[{"name":"shipped"},{"name":"floor","min_bm25_score":25.0}]' \
  aizzak-app-1 python /tmp/eval/run_calibration.py > run.jsonl
```

`EVAL_VARIANTS` is a JSON list of `RetrievalTuning` overrides, each with a
`name`. Each is applied with `dataclasses.replace` onto the **shipped** tuning
read out of `Settings`, so anything a variant does not name keeps its deployed
value.

> ⚠️ **The two per-leg floors below were withdrawn on 2026-08-30 and both
> ship at `0.0` again.** Nothing in the measurement was wrong; the corpus it
> was measured on is gone. The space now holds `criteria.pdf` **alone**
> (re-indexed to 771 chunks) — the *distractor* of the very corpus described
> above, with the handbook it was meant to distract from no longer indexed.
> So the only document left is the one both floors were fitted to reject, and
> every question about it scores in the band this sweep recorded for
> *unanswerable* questions. Measured live on 2026-08-30: `best_dense_score`
> 0.40547 against the 0.45 floor and `best_bm25_score` 6.30028 against the
> 25.0 one — both legs gated to zero, `fused_count` 0, and an answerable
> question refused by the `P-33` trust gate.
>
> The numbers below therefore stand as the record of what was measured **on
> that corpus**, and re-running this harness on the corpus that is actually
> indexed is what would restore a floor. Note that `min_bm25_score` will not
> transfer even then: a raw BM25 score is a function of the corpus's own IDF
> and mean document length.

## What it measured, and what each ladder answered

Three scales, three separate calibrations, and **no number crosses between
them** — the trap alpha fell into by copying floors calibrated against FAISS
L2 distance onto a cosine/RRF pipeline.

| ladder | quantity | verdict |
|---|---|---|
| **dense** | Qdrant cosine, `[-1, 1]` | `min_dense_score = 0.45`. Answerable questions scored `[0.5243, 0.9066]`, unanswerable ones `[0.2623, 0.6492]`; the first **false refusal** appears at `0.55`, so `0.45` sits 18% under it. |
| **sparse** | IDF-weighted dot product, `[0, ∞)` | `min_bm25_score = 25.0`. Repairs a measured **defect**, not a quality trade — see below. `[21, 30]` is one stable plateau; `25` is its middle. |
| **fused** | RRF, `Σ w/(60+rank)` | **no floor is possible.** Both floors stay `0.0`, now by measurement. |

### Why the RRF scale cannot carry a floor

The score is rank arithmetic, bounded into `[0.008197, 0.016393]` at the
shipped weights **however good or bad the candidate is**. Measured: answerable
questions produced gold scores across `[0.008065, 0.016393]` and unanswerable
ones produced maxima across `[0.008197, 0.016261]` — the same interval. A
floor at `0.0082` holds English at 15/15 and cuts Arabic to **7/15**, because
a cross-lingual question has no both-leg agreement and every candidate it
produces sits at the single-leg minimum by construction. `relative_floor` is
the same quantity as a ratio and costs two answers at `0.8`.

### The defect the sparse floor repairs

Qdrant answers a **filtered** sparse query whose terms appear nowhere in the
corpus with `k` arbitrary points scored **exactly `0.0`** — 362 such hits
across 42 probes, and for an Arabic question over an English corpus the entire
20-deep leg. RRF reads *rank*, not score, so those zeros used to vote with
exactly the weight of the dense leg's real hits. Four of the five chunks
delivered for one Arabic question came from the unrelated engineering
document, one of them a 26-character fragment. The smallest **positive** score
anywhere in the corpus is `1.406`, so any floor above zero removes the zeros
and nothing else.

`FakeHybridVectors` in `tests/unit/test_knowledge_pipeline.py` drops
non-positive dot products, modelling an idealised inverted index — which is
exactly why no unit test ever saw this.

### Result at the chosen operating point

| | recall (EN + AR) | unanswerable questions answered with nothing | chunks from the right document | context |
|---|---|---|---|---|
| before | 29/30 | 0 of 12 | 64.2% | 9422 chars |
| after | **30/30** | **4 of 12** | **94.4%** | 8828 chars |

Zero false refusals: no answerable question returned an empty context at
either floor.

## `vector_parity.py` — gpu-compose 2.4 (not part of `P-38`)

A measuring tool for the CUDA embedding image, written by QA for
[`docs/delivery/gpu-compose`](../../docs/delivery/gpu-compose/design.md) §5.2. It needs two
running embedding images, so it is a runbook step (design §5.5, P-4 and P-5), not a test:
the file is not named `test_*`, and nothing here is collected by pytest.

| subcommand | needs | what it does |
|---|---|---|
| `build-set --out set.json` | the platform package (`.venv/bin/python`) | the 42 question texts of `hr_handbook_set.py` plus the chunks of the HR handbook, parsed and chunked by the platform at `embedding_max_input_tokens = 512`; records the document's source and sha256 and a sha256 over every text in order |
| `compare --cpu URL --gpu URL --set set.json` | the standard library | judges Q-3 (a) cosine >= 0.999 for every text, and (b) the top 10 chunks per question overlap by >= 9 with the same top chunk for all questions but one; prints one JSON document, exits 0 only if both hold, and refuses (exit 3) a pair that is not CPU against CUDA |
| `soak --url URL --minutes 15 --concurrency 8 --batch 8` | the standard library | AC-4.11's load: concurrent `/embed` batches of ~350-word Arabic texts; reports requests, errors and latency (the container's memory is read from outside) |

`docs/hr-no-table.docx` is no longer in the tree (`15a60b7` removed it), so `build-set` reads it
from the commit that added it (`e105e47`) unless `--document` names a file. The chunk count
is lower than the 221 recorded above: the tool skips semantic pre-splitting (it would need the
embedding service it is measuring) and parent chunks.
