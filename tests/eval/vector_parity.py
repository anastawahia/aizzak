"""gpu-compose 2.4's measuring tool: do the CUDA image's vectors agree with the CPU image's
(AC-4.9), and does the CUDA image hold its memory limit under load (AC-4.11)?

**This is a tool, not a test.** The file is not named ``test_*`` and defines no ``Test*``
class, so pytest collects nothing from it (the same position as ``run_calibration.py``, see
``README.md``). Its pure functions are unit-tested in ``tests/unit/test_vector_parity.py``;
what it measures needs two running embedding images, which is a runbook step
(``docs/delivery/gpu-compose/design.md`` §5.5, P-4 and P-5), never a fixture's job.

Three subcommands:

``build-set --out set.json``
    Builds the fixed input set, once, so every later comparison embeds the SAME texts. It
    needs the platform package (the parser and the chunker are the platform's own), so run
    it with the project interpreter: ``.venv/bin/python tests/eval/vector_parity.py ...``.
    The set is the 42 question texts of ``hr_handbook_set.py`` (15 answerable questions in
    two languages, 6 unanswerable ones in two languages) plus the chunks of the HR
    handbook, parsed by the platform's ``.docx`` parser and cut by its own chunker at
    ``embedding_max_input_tokens = 512``. ``docs/hr-no-table.docx`` is no longer in the
    tree (``15a60b7`` removed it), so by default it is read from the commit that added it
    (``e105e47``) and the set records which: its source and its sha256, next to the sha256
    of every text in order (``fingerprint``), which ``compare`` re-checks before it embeds.

``compare --cpu URL --gpu URL --set set.json``
    Standard library only. Sends the whole set to both services, then judges Q-3 (a) and
    (b), written before any measurement (requirements.md, owner decision at gate 1):

    (a) the cosine similarity between the CPU vector and the CUDA vector of the same text is
        at least 0.999 for EVERY text; the mean is reported;
    (b) for every question, the 10 chunks scoring highest by inner product (ranked inside
        each image's own vector space) overlap by at least 9 of 10, and the top chunk is the
        same for all questions but at most one (41 of 42).

    Prints one JSON document and exits 0 only when every condition holds. It refuses
    (exit 3) a comparison that is not CPU against CUDA, because two images on the same
    device agree with themselves and prove nothing.

``soak --url URL --minutes 15 --concurrency 8 --batch 8``
    Standard library only. AC-4.11's load: ``concurrency`` concurrent ``POST /embed`` calls,
    each a batch of ``batch`` Arabic texts of about 350 words, built deterministically from
    the Arabic questions of the set, for ``minutes`` minutes. It measures the load and the
    errors it saw; the container's memory is read from outside it afterwards
    (``/sys/fs/cgroup/memory.peak`` and ``docker inspect ... OOMKilled``).

Exit codes: 0 ok · 1 a Q-3 condition failed (compare) or the soak saw an error or was
interrupted · 2 bad usage · 3 a service or the set is unusable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNUSABLE = 3

# Q-3 (a) and (b): the margin, written before the measurement.
MIN_COSINE = 0.999
TOP_K = 10
MIN_OVERLAP = 9
MAX_TOP1_MISMATCHES = 1

SET_VERSION = 1
# The handbook the set's chunks come from, and the commit that still holds it.
DEFAULT_DOCUMENT = "docs/hr-no-table.docx"
DOCUMENT_FALLBACK_REV = "e105e47"
# `embedding_max_input_tokens`, the platform default the indexer chunks at.
MAX_INPUT_TOKENS = 512

SOAK_WORDS = 350
HEALTH_TIMEOUT_S = 30.0
# A prime step, so consecutive soak texts start at different places in the corpus.
_SOAK_STRIDE = 37

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]


class ToolError(RuntimeError):
    """A condition the tool cannot continue from. The message never holds a text of the set."""


# ── the maths (pure; unit-tested) ───────────────────────────────────────────


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vectors of different length: {len(a)} and {len(b)}")
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. A zero vector has no direction, so it raises instead of
    answering 0.0 (a service that returned zeros must not look merely "different")."""
    norm_a = math.sqrt(dot(a, a))
    norm_b = math.sqrt(dot(b, b))
    if norm_a == 0.0 or norm_b == 0.0:
        raise ValueError("a zero vector has no cosine similarity")
    return dot(a, b) / (norm_a * norm_b)


def top_k(query: Sequence[float], chunks: Sequence[Sequence[float]], k: int) -> list[int]:
    """Indexes of the ``k`` chunks with the largest inner product with ``query``, best first.
    Ties break toward the lower index, so the answer is a function of the numbers alone."""
    scored = sorted(((-dot(query, chunk), index) for index, chunk in enumerate(chunks)))
    return [index for _, index in scored[:k]]


def overlap(first: Iterable[int], second: Iterable[int]) -> int:
    return len(set(first) & set(second))


def fingerprint(texts: Iterable[str]) -> str:
    """sha256 over the texts in order. Each text is framed by its byte length, so
    ``["ab", "c"]`` and ``["a", "bc"]`` cannot collide."""
    digest = hashlib.sha256()
    for text in texts:
        raw = text.encode("utf-8")
        digest.update(f"{len(raw)}:".encode("ascii"))
        digest.update(raw)
        digest.update(b"\n")
    return digest.hexdigest()


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (``p`` in 0..100). 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True)
class Thresholds:
    min_cosine: float = MIN_COSINE
    top_k: int = TOP_K
    min_overlap: int = MIN_OVERLAP
    max_top1_mismatches: int = MAX_TOP1_MISMATCHES


def evaluate(
    *,
    question_ids: Sequence[str],
    chunk_ids: Sequence[str],
    cpu_questions: Sequence[Sequence[float]],
    cpu_chunks: Sequence[Sequence[float]],
    gpu_questions: Sequence[Sequence[float]],
    gpu_chunks: Sequence[Sequence[float]],
    thresholds: Thresholds | None = None,
) -> dict[str, Any]:
    """Q-3 (a) and (b) over one CPU result and one CUDA result of the same set."""
    limits = thresholds or Thresholds()
    if not (
        len(question_ids) == len(cpu_questions) == len(gpu_questions) and len(question_ids) > 0
    ):
        raise ValueError("the question vectors do not match the question ids")
    if not len(chunk_ids) == len(cpu_chunks) == len(gpu_chunks):
        raise ValueError("the chunk vectors do not match the chunk ids")
    if len(chunk_ids) < limits.top_k:
        raise ValueError(
            f"{len(chunk_ids)} chunks cannot be ranked {limits.top_k} deep: the set is too small"
        )

    # (a) every text, questions and chunks alike.
    labelled = [
        *zip(question_ids, cpu_questions, gpu_questions, strict=True),
        *zip(chunk_ids, cpu_chunks, gpu_chunks, strict=True),
    ]
    cosines = [(text_id, cosine(cpu, gpu)) for text_id, cpu, gpu in labelled]
    values = [value for _, value in cosines]
    below = [(text_id, value) for text_id, value in cosines if value < limits.min_cosine]

    # (b) ranking: each image ranks the chunks in its own vector space.
    overlaps: list[int] = []
    top1_same = 0
    for index in range(len(question_ids)):
        cpu_top = top_k(cpu_questions[index], cpu_chunks, limits.top_k)
        gpu_top = top_k(gpu_questions[index], gpu_chunks, limits.top_k)
        overlaps.append(overlap(cpu_top, gpu_top))
        top1_same += int(cpu_top[0] == gpu_top[0])
    questions = len(question_ids)
    below_overlap = [
        question_ids[i] for i, count in enumerate(overlaps) if count < limits.min_overlap
    ]
    mismatches = questions - top1_same

    conditions = {
        "cosine_each_text": not below,
        "overlap_each_question": not below_overlap,
        "top1": mismatches <= limits.max_top1_mismatches,
    }
    return {
        "thresholds": {
            "min_cosine": limits.min_cosine,
            "top_k": limits.top_k,
            "min_overlap": limits.min_overlap,
            "max_top1_mismatches": limits.max_top1_mismatches,
        },
        "cosine": {
            "texts": len(values),
            "min": min(values),
            "mean": math.fsum(values) / len(values),
            "below_threshold": len(below),
            "worst": [
                {"text_id": text_id, "cosine": value}
                for text_id, value in sorted(cosines, key=lambda item: item[1])[:5]
            ],
        },
        "ranking": {
            "questions": questions,
            "top_k": limits.top_k,
            "min_overlap": min(overlaps),
            "mean_overlap": math.fsum(overlaps) / questions,
            "below_min_overlap": below_overlap,
            "top1_same": top1_same,
            "top1_mismatches": mismatches,
        },
        "conditions": conditions,
        "pass": all(conditions.values()),
    }


# ── the set ─────────────────────────────────────────────────────────────────


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ToolError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def question_items(
    questions: Sequence[Mapping[str, Any]], negatives: Sequence[Mapping[str, Any]]
) -> list[dict[str, str]]:
    """Every question in both languages, in a fixed order: the answerable ones as
    ``q<id>-en``/``q<id>-ar``, then the unanswerable ones as ``n<id>-en``/``n<id>-ar``."""
    items: list[dict[str, str]] = []
    for question in questions:
        for lang in ("en", "ar"):
            items.append({"id": f"q{question['id']}-{lang}", "lang": lang, "text": question[lang]})
    for negative in negatives:
        for lang in ("en", "ar"):
            items.append({"id": f"{negative['id']}-{lang}", "lang": lang, "text": negative[lang]})
    return items


def _arabic_corpus() -> list[str]:
    handbook = _load_module(_HERE / "hr_handbook_set.py", "hr_handbook_set")
    return [q["ar"] for q in handbook.QUESTIONS] + [n["ar"] for n in handbook.NEGATIVES]


def soak_text(words: Sequence[str], index: int, *, length: int = SOAK_WORDS) -> str:
    """The ``index``-th soak text: ``length`` words taken cyclically from ``words`` from a
    start that moves with ``index``. A function of its arguments alone, so every run sends
    the same load and two different indexes send different texts."""
    if not words:
        raise ValueError("no words to build a soak text from")
    start = (index * _SOAK_STRIDE) % len(words)
    return " ".join(words[(start + offset) % len(words)] for offset in range(length))


def read_document(path: Path | None, *, repo: Path, rev: str) -> tuple[bytes, dict[str, str]]:
    """The handbook's bytes and where they came from. An explicit ``path`` must exist; with
    none, the default file is used when present and otherwise the commit ``rev`` that added it."""
    if path is not None:
        if not path.is_file():
            raise ToolError(f"--document {path} does not exist")
        data = path.read_bytes()
        return data, {"source": str(path), "sha256": hashlib.sha256(data).hexdigest()}
    default = repo / DEFAULT_DOCUMENT
    if default.is_file():
        data = default.read_bytes()
        return data, {"source": DEFAULT_DOCUMENT, "sha256": hashlib.sha256(data).hexdigest()}
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "show", f"{rev}:{DEFAULT_DOCUMENT}"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise ToolError(f"{DEFAULT_DOCUMENT} is missing and git could not be run: {err}") from err
    if done.returncode != 0:
        raise ToolError(
            f"{DEFAULT_DOCUMENT} is missing and `git show {rev}:{DEFAULT_DOCUMENT}` failed: "
            f"{done.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return done.stdout, {
        "source": f"git:{rev}:{DEFAULT_DOCUMENT}",
        "sha256": hashlib.sha256(done.stdout).hexdigest(),
    }


def chunk_document(data: bytes, *, max_input_tokens: int) -> tuple[list[str], dict[str, Any]]:
    """The platform's own parse and chunking of a ``.docx``: its parser, its table-row
    explosion (``domain.tables.explode_table``, the call the indexer makes), then its
    word-window chunker at the word budget ``embedding_max_input_tokens`` implies.

    Two things the real indexer does are left out on purpose: semantic pre-splitting (it
    needs the very embedding service this tool measures) and parent chunks (they are not
    leaves). So the count is lower than the indexed corpus's (174 against the 221 recorded
    in README.md): the set is a stand-in with the same text, tables and chunk width."""
    try:
        from app.modules.knowledge.adapters.parsers.docx import parse_docx  # noqa: PLC0415
        from app.modules.knowledge.domain.chunking import (  # noqa: PLC0415
            SPLIT_OVERLAP_RATIO,
            SourceSegment,
            chunk_segments,
            max_words_for_token_limit,
        )
        from app.modules.knowledge.domain.tables import explode_table  # noqa: PLC0415
        from app.modules.knowledge.ports.content_extractor import (  # noqa: PLC0415
            ParsedChunkKind,
        )
    except ImportError as err:
        raise ToolError(
            "build-set needs the platform package: run it with the project interpreter "
            f"(.venv/bin/python tests/eval/vector_parity.py build-set ...): {err}"
        ) from err
    parsed = parse_docx(data)
    max_words = max_words_for_token_limit(max_input_tokens)
    overlap_words = int(max_words * SPLIT_OVERLAP_RATIO)
    segments: list[Any] = []
    text_blocks = table_blocks = 0
    for block in parsed:
        if block.kind == ParsedChunkKind.TEXT:
            text_blocks += 1
            segments.append(
                SourceSegment(
                    text=block.text,
                    order=block.order,
                    kind=str(block.kind),
                    metadata=dict(block.metadata),
                )
            )
        elif block.kind == ParsedChunkKind.TABLE:
            table_blocks += 1
            try:
                payload = json.loads(block.text)
                exploded = explode_table(payload["headers"], payload["rows"])
            except (ValueError, KeyError, TypeError) as err:
                raise ToolError(
                    f"a table block of the document is not {{headers, rows}}: {err!r}"
                ) from err
            sentences = list(exploded.row_sentences)
            if exploded.truncated and exploded.overflow_text:
                sentences.append(exploded.overflow_text)
            for row_index, sentence in enumerate(sentences):
                segments.append(
                    SourceSegment(
                        text=sentence,
                        order=block.order,
                        kind=str(block.kind),
                        metadata=dict(block.metadata),
                        sub_order=row_index,
                    )
                )
    chunks = chunk_segments(segments, max_tokens=max_words, overlap_tokens=overlap_words)
    return [chunk.text for chunk in chunks], {
        "embedding_max_input_tokens": max_input_tokens,
        "max_words": max_words,
        "overlap_words": overlap_words,
        "text_blocks": text_blocks,
        "table_blocks": table_blocks,
        "segments": len(segments),
        "semantic_pre_splitting": False,
    }


def build_set(
    *, document: Path | None, max_input_tokens: int = MAX_INPUT_TOKENS, repo: Path = _REPO
) -> dict[str, Any]:
    handbook = _load_module(_HERE / "hr_handbook_set.py", "hr_handbook_set")
    questions = question_items(handbook.QUESTIONS, handbook.NEGATIVES)
    data, source = read_document(document, repo=repo, rev=DOCUMENT_FALLBACK_REV)
    texts, chunking = chunk_document(data, max_input_tokens=max_input_tokens)
    if not texts:
        raise ToolError("the document produced no chunk")
    chunks = [{"id": f"chunk-{index:04d}", "text": text} for index, text in enumerate(texts)]
    return {
        "version": SET_VERSION,
        "fingerprint": fingerprint([*(q["text"] for q in questions), *texts]),
        "document": source,
        "chunking": chunking,
        "questions": questions,
        "chunks": chunks,
    }


def load_set(path: Path) -> dict[str, Any]:
    """Read a set and re-derive its fingerprint, so an edited file is refused."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["version"] != SET_VERSION:
            raise ToolError(f"{path}: unknown set version {data['version']!r}")
        texts = [item["text"] for item in data["questions"]] + [
            item["text"] for item in data["chunks"]
        ]
        for item in (*data["questions"], *data["chunks"]):
            if not isinstance(item["id"], str) or not isinstance(item["text"], str):
                raise ToolError(f"{path}: an entry has no string id and text")
        stored = data["fingerprint"]
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise ToolError(f"{path} is not a vector-parity set: {err!r}") from err
    if fingerprint(texts) != stored:
        raise ToolError(f"{path}: the fingerprint does not match its texts (edited by hand?)")
    parity_set: dict[str, Any] = data
    return parity_set


# ── HTTP (standard library) ─────────────────────────────────────────────────


def _opener() -> urllib.request.OpenerDirector:
    # No proxy, whatever the environment says: these are services on a private network.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url: str, body: dict[str, Any] | None, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with _opener().open(request, timeout=timeout_s) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as err:
        raise ToolError(f"{url} answered HTTP {err.code}") from err
    except (urllib.error.URLError, HTTPException, OSError, ValueError) as err:
        raise ToolError(f"{url} failed: {err!r}") from err
    if not isinstance(payload, dict):
        raise ToolError(f"{url} did not answer a JSON object")
    return payload


def service_health(base_url: str, timeout_s: float) -> dict[str, Any]:
    """``GET /health``. Answers at once or not at all, so it never waits as long as an embed."""
    return _request(f"{base_url.rstrip('/')}/health", None, min(timeout_s, HEALTH_TIMEOUT_S))


def embed_texts(
    base_url: str, model: str, texts: Sequence[str], *, batch: int, timeout_s: float
) -> list[list[float]]:
    """Every text's vector, in order, ``batch`` texts per request."""
    url = f"{base_url.rstrip('/')}/embed"
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch):
        chunk = list(texts[start : start + batch])
        payload = _request(url, {"texts": chunk, "model": model}, timeout_s)
        got = payload.get("vectors")
        if not isinstance(got, list) or len(got) != len(chunk):
            raise ToolError(
                f"{url} returned {0 if not isinstance(got, list) else len(got)} "
                f"vectors for {len(chunk)} texts"
            )
        vectors.extend([float(x) for x in vector] for vector in got)
    return vectors


# ── compare ─────────────────────────────────────────────────────────────────


def _check_pair(cpu: Mapping[str, Any], gpu: Mapping[str, Any], *, allow_same_device: bool) -> None:
    for field in ("model", "dimensions", "max_seq_length"):
        if cpu.get(field) != gpu.get(field):
            raise ToolError(
                f"the two services differ in {field}: {cpu.get(field)!r} against {gpu.get(field)!r}"
            )
    if allow_same_device:
        return
    if "device" not in cpu or "device" not in gpu:
        raise ToolError(
            "a service's /health does not report `device`: it is an image from before "
            "gpu-compose, so this cannot be shown to be CPU against CUDA"
        )
    if cpu["device"] != "cpu" or gpu["device"] != "cuda":
        raise ToolError(
            f"not a CPU-against-CUDA comparison: --cpu reports {cpu['device']!r}, "
            f"--gpu reports {gpu['device']!r}"
        )


def run_compare(
    *,
    cpu_url: str,
    gpu_url: str,
    set_path: Path,
    thresholds: Thresholds,
    batch: int,
    timeout_s: float,
    allow_same_device: bool = False,
) -> tuple[int, dict[str, Any]]:
    parity_set = load_set(set_path)
    cpu_health = service_health(cpu_url, timeout_s)
    gpu_health = service_health(gpu_url, timeout_s)
    _check_pair(cpu_health, gpu_health, allow_same_device=allow_same_device)
    model = str(cpu_health["model"])

    question_texts = [item["text"] for item in parity_set["questions"]]
    chunk_texts = [item["text"] for item in parity_set["chunks"]]
    everything = [*question_texts, *chunk_texts]
    cpu_vectors = embed_texts(cpu_url, model, everything, batch=batch, timeout_s=timeout_s)
    gpu_vectors = embed_texts(gpu_url, model, everything, batch=batch, timeout_s=timeout_s)
    cut = len(question_texts)
    try:
        result = evaluate(
            question_ids=[item["id"] for item in parity_set["questions"]],
            chunk_ids=[item["id"] for item in parity_set["chunks"]],
            cpu_questions=cpu_vectors[:cut],
            cpu_chunks=cpu_vectors[cut:],
            gpu_questions=gpu_vectors[:cut],
            gpu_chunks=gpu_vectors[cut:],
            thresholds=thresholds,
        )
    except ValueError as err:
        raise ToolError(str(err)) from err
    result["set"] = {
        "fingerprint": parity_set["fingerprint"],
        "questions": len(question_texts),
        "chunks": len(chunk_texts),
        "document": parity_set["document"],
        "chunking": parity_set["chunking"],
    }
    result["services"] = {
        "cpu": {"url": cpu_url, "health": cpu_health},
        "gpu": {"url": gpu_url, "health": gpu_health},
    }
    return (EXIT_OK if result["pass"] else EXIT_FAILED), result


# ── soak ────────────────────────────────────────────────────────────────────


@dataclass
class _SoakState:
    lock: threading.Lock
    stop: threading.Event
    next_request: int = 0
    ok: int = 0
    failed: int = 0
    latencies: list[float] | None = None
    errors: dict[str, int] | None = None


def _soak_worker(
    state: _SoakState,
    *,
    url: str,
    model: str,
    corpus: Sequence[str],
    length: int,
    batch: int,
    deadline: float,
    timeout_s: float,
) -> None:
    while not state.stop.is_set() and time.monotonic() < deadline:
        with state.lock:
            number = state.next_request
            state.next_request += 1
        texts = [
            soak_text(corpus, number * batch + offset, length=length) for offset in range(batch)
        ]
        started = time.monotonic()
        try:
            payload = _request(f"{url}/embed", {"texts": texts, "model": model}, timeout_s)
            vectors = payload.get("vectors")
            if not isinstance(vectors, list) or len(vectors) != batch:
                raise ToolError("the answer does not hold one vector per text")
        except ToolError as err:
            with state.lock:
                state.failed += 1
                assert state.errors is not None
                state.errors[str(err)] = state.errors.get(str(err), 0) + 1
            continue
        elapsed = time.monotonic() - started
        with state.lock:
            state.ok += 1
            assert state.latencies is not None
            state.latencies.append(elapsed)


def run_soak(
    *,
    url: str,
    minutes: float,
    concurrency: int,
    batch: int,
    timeout_s: float,
    words: int = SOAK_WORDS,
    progress: bool = False,
) -> tuple[int, dict[str, Any]]:
    base = url.rstrip("/")
    health_before = service_health(base, timeout_s)
    model = str(health_before["model"])
    corpus = " ".join(_arabic_corpus()).split()
    state = _SoakState(threading.Lock(), threading.Event(), latencies=[], errors={})
    started = time.monotonic()
    deadline = started + minutes * 60.0
    workers = [
        threading.Thread(
            target=_soak_worker,
            kwargs={
                "state": state,
                "url": base,
                "model": model,
                "corpus": corpus,
                "length": words,
                "batch": batch,
                "deadline": deadline,
                "timeout_s": timeout_s,
            },
            daemon=True,
        )
        for _ in range(concurrency)
    ]
    for worker in workers:
        worker.start()
    interrupted = False
    last_report = started
    try:
        while any(worker.is_alive() for worker in workers):
            time.sleep(0.2)
            now = time.monotonic()
            if progress and now - last_report >= 60.0:
                last_report = now
                with state.lock:
                    sys.stderr.write(
                        f"soak: {(now - started) / 60.0:.1f}/{minutes:g} min "
                        f"ok={state.ok} failed={state.failed}\n"
                    )
                    sys.stderr.flush()
    except KeyboardInterrupt:
        interrupted = True
        state.stop.set()
        for worker in workers:
            worker.join(timeout=timeout_s)
    duration = time.monotonic() - started
    try:
        health_after: dict[str, Any] | str = service_health(base, timeout_s)
    except ToolError as err:
        health_after = f"unreachable after the soak: {err}"
    assert state.latencies is not None and state.errors is not None
    latencies = state.latencies
    result: dict[str, Any] = {
        "url": url,
        "minutes_requested": minutes,
        "duration_s": round(duration, 3),
        "concurrency": concurrency,
        "batch": batch,
        "words_per_text": words,
        "requests_ok": state.ok,
        "requests_failed": state.failed,
        "texts_embedded": state.ok * batch,
        "requests_per_second": round(state.ok / duration, 3) if duration > 0 else 0.0,
        "latency_s": {
            "p50": round(percentile(latencies, 50), 4),
            "p95": round(percentile(latencies, 95), 4),
            "p99": round(percentile(latencies, 99), 4),
            "max": round(max(latencies, default=0.0), 4),
        },
        "errors": dict(sorted(state.errors.items(), key=lambda item: -item[1])[:5]),
        "interrupted": interrupted,
        "health_before": health_before,
        "health_after": health_after,
    }
    clean = state.failed == 0 and state.ok > 0 and not interrupted
    return (EXIT_OK if clean else EXIT_FAILED), result


# ── command line ────────────────────────────────────────────────────────────


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _positive_float(raw: str) -> float:
    value = float(raw)
    if not value > 0 or math.isinf(value):
        raise argparse.ArgumentTypeError("must be a positive number")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vector_parity.py",
        description="gpu-compose 2.4: CPU-against-CUDA vector parity (AC-4.9) and memory soak "
        "(AC-4.11). A measuring tool, not a test.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build-set", help="write the fixed input set (needs the platform)")
    build.add_argument("--out", type=Path, required=True, help="where to write the set (JSON)")
    build.add_argument(
        "--document",
        type=Path,
        default=None,
        help=f"the .docx (default {DEFAULT_DOCUMENT}, else commit {DOCUMENT_FALLBACK_REV})",
    )
    build.add_argument("--max-input-tokens", type=_positive_int, default=MAX_INPUT_TOKENS)

    compare = commands.add_parser("compare", help="judge Q-3 (a) and (b) for two services")
    compare.add_argument("--cpu", required=True, metavar="URL", help="the CPU image's base URL")
    compare.add_argument("--gpu", required=True, metavar="URL", help="the CUDA image's base URL")
    compare.add_argument("--set", required=True, type=Path, dest="set_path")
    compare.add_argument("--batch", type=_positive_int, default=16)
    compare.add_argument("--timeout", type=_positive_float, default=300.0)
    compare.add_argument("--min-cosine", type=float, default=MIN_COSINE)
    compare.add_argument("--top-k", type=_positive_int, default=TOP_K)
    compare.add_argument("--min-overlap", type=_positive_int, default=MIN_OVERLAP)
    compare.add_argument("--max-top1-mismatches", type=int, default=MAX_TOP1_MISMATCHES)
    compare.add_argument(
        "--allow-same-device",
        action="store_true",
        help="skip the CPU-against-CUDA check (only to exercise the tool itself)",
    )

    soak = commands.add_parser("soak", help="AC-4.11's load against one embedding service")
    soak.add_argument("--url", required=True, metavar="URL", help="the service's base URL")
    soak.add_argument("--minutes", type=_positive_float, default=15.0)
    soak.add_argument("--concurrency", type=_positive_int, default=8)
    soak.add_argument("--batch", type=_positive_int, default=8)
    soak.add_argument("--timeout", type=_positive_float, default=300.0)
    return parser


def _emit(result: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code) if isinstance(exit_.code, int) else EXIT_USAGE
    try:
        if args.command == "build-set":
            built = build_set(document=args.document, max_input_tokens=args.max_input_tokens)
            args.out.write_text(
                json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
            sys.stdout.write(
                f"vector_parity: wrote {args.out}: {len(built['questions'])} questions, "
                f"{len(built['chunks'])} chunks, document {built['document']['source']}, "
                f"fingerprint {built['fingerprint']}\n"
            )
            return EXIT_OK
        if args.command == "compare":
            code, result = run_compare(
                cpu_url=args.cpu,
                gpu_url=args.gpu,
                set_path=args.set_path,
                thresholds=Thresholds(
                    min_cosine=args.min_cosine,
                    top_k=args.top_k,
                    min_overlap=args.min_overlap,
                    max_top1_mismatches=args.max_top1_mismatches,
                ),
                batch=args.batch,
                timeout_s=args.timeout,
                allow_same_device=args.allow_same_device,
            )
            _emit(result)
            return code
        code, result = run_soak(
            url=args.url,
            minutes=args.minutes,
            concurrency=args.concurrency,
            batch=args.batch,
            timeout_s=args.timeout,
            progress=True,
        )
        _emit(result)
        return code
    except ToolError as err:
        sys.stderr.write(f"vector_parity: ERROR: {err}\n")
        return EXIT_UNUSABLE
    except OSError as err:
        sys.stderr.write(f"vector_parity: ERROR: {err}\n")
        return EXIT_UNUSABLE


if __name__ == "__main__":
    sys.exit(main())
