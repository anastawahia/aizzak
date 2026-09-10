"""The central embedding service (Phase 2.10) -- a SEPARATE deployable, its
own Docker image (``services/embedding/Dockerfile``), never imported by the
main ``app`` package and importing NOTHING from it. ``src/app.*``'s
``ExternalEmbeddingProvider`` adapter
(``src/app/infrastructure/ai_providers/embedding/external_embedding.py``)
is the ONLY thing in this platform that talks to this process, and it does
so purely over HTTP -- exactly the way it would talk to any other internal
service.

**Why a separate process at all.** ``sentence-transformers``/``torch`` are
heavy, native/GPU-capable dependencies (``services/embedding/
requirements.txt`` is the ONLY place they are pinned -- deliberately never
added to the main project's ``pyproject.toml``/lockfile, so ``torch`` never
enters ``app.*``'s import graph). Loading the model ONCE, in one process,
and serving every caller (the API's ``/search`` route, the memory/knowledge
workers) over a small internal HTTP API avoids paying a model-load cost per
caller and keeps the heavy dependency out of every OTHER process's image.

**Lazy imports, on purpose.** ``torch``/``sentence_transformers`` are
imported INSIDE ``_load_model``, never at module level -- so this module
stays importable (and therefore unit-testable, ``tests/unit/
test_embedding_service_app.py``) in an environment that has neither
installed, which is exactly the dev/CI venv this repo's ``mypy src``/
``pytest`` gates run in. The startup lifespan is the ONLY caller of
``_load_model`` in production.

**Device policy -- auto (baked, not env-editable):** ``cuda``+fp16 when a
GPU is present, else ``cpu``+fp32; L2-normalisation is ALWAYS on
(``normalize_embeddings=True``), regardless of device -- the platform's
retrieval math (cosine similarity, ``knowledge``'s hybrid Qdrant collections)
assumes unit vectors.

**Sequence length -- SET EXPLICITLY, never left to the checkpoint.**
``SentenceTransformer(model_name)`` adopts whatever ``max_seq_length`` the
checkpoint's own ``sentence_bert_config.json`` ships, and for the baked
``paraphrase-multilingual-MiniLM-L12-v2`` that value is **128** -- a quarter
of the 512 its ``config.json`` (``max_position_embeddings``) actually
supports. Everything past it is dropped INSIDE ``model.encode``: no error,
no warning, a prefix silently standing in for the whole text. Measured in
this image before ``_DEFAULT_MAX_SEQ_LEN`` below existed: a 354-word Arabic
chunk -- the indexer's OWN window (``knowledge/domain/chunking.py::
max_words_for_token_limit`` at ``embedding_max_input_tokens = 512``) --
tokenises to ~598 tokens, of which 128 were embedded. **79% of a
full-size chunk never reached its vector**, on every path whose chunks are
not accidentally tiny.

That is ``P-16``'s own defect ("truncation at an HTTP embedding provider is
SILENT") reproduced one layer lower: not at a third-party provider, but
here. The pin below closes it, and ``GET /health`` publishes the effective
value so the cut point is checkable from outside the container instead of
only by measuring inside it.

**Loud drift detection.** ``POST /embed`` 400s if the caller's ``model``
does not match the one this instance actually loaded -- an adapter/service
image mismatch is a deployment bug, and a silent wrong-model embedding would
poison a collection invisibly.

**Thread pinning -- ``EMB_TORCH_THREADS``, and it is a DEPLOYMENT fact, not
an image one (capacity 4.1).** ``--workers 1`` is correct here (one model
load per process, above) and this service scales by REPLICAS
(``services/embedding/Dockerfile``'s ``CMD`` comment). But torch does not
know that: left alone it sizes its intra-op pool from the HOST's core count,
and MEASURED inside this container on a 10-core host it chose **5 threads
against a 1.0-vCPU cgroup quota** -- five OpenMP workers, each spinning for
work, sharing one core's worth of scheduler share. Three such replicas would
then compete for the same cores and be *slower* than one, which is 4.1's own
warning verbatim: «بلا ذلك تتنافس النسخُ الثلاث على النوى نفسها فتصير أبطأ من
واحدة».

Unset, this changes NOTHING -- torch keeps its own default and this module
never imports it for the purpose. It is set explicitly per replica in
``docker-compose.yml`` (alongside ``OMP_NUM_THREADS``, which OpenMP reads
before torch is even imported), because the right number is a property of
the cgroup a replica runs in, not of the model baked into the image: the
single-container GPU deployment (``deploy/runpod/``) wants a different one.
``GET /health`` reports the EFFECTIVE count for the reason it reports
``max_seq_length``: a pin that silently did not take is invisible from
outside the container, and «أبطأ من واحدة» is exactly what it looks like.

**Dynamic batching -- ``EMB_BATCH_WINDOW_MS``, and the window is the SMALL
half of it (capacity 4.3).** ``model.encode`` is synchronous CPU work, and
until this step it ran ON the event loop of a ``--workers 1`` process: while
one text was being embedded, this process could not read the next request off
its socket, let alone answer ``/health``. So the fleet's unit of work was one
text per forward pass on the query path (``retrieval.py`` embeds a SINGLE
query string), and a forward pass of one is the most expensive shape this
model has -- the tokeniser pads, the matmul is one row wide, and the
per-call overhead is paid whole.

``_Batcher`` changes both halves. ``encode`` moves off the loop
(``asyncio.to_thread``: torch releases the GIL in its native ops, and exactly
one batch is ever in flight, so the "one model, one encode at a time"
property this process is built on is preserved), and requests that arrive
while a batch is encoding are coalesced into the NEXT ``encode`` call.

⚠️ **The 5 ms window is the floor, not the mechanism.** At 40 queries/second
spread over three replicas, a 5 ms window catches almost nothing on its own
-- roughly 0.07 requests. What actually fills a batch is the queue that
accumulates DURING the previous ``encode``, which is why the loop drains the
queue first (free, no added latency) and only sleeps the window when that
drain left it under ``EMB_MAX_BATCH_TEXTS``. A build that slept first and
drained second would pay 5 ms on every request to buy what it already had.

⚠️ **And ``EMB_MAX_BATCH_TEXTS`` is a LATENCY bound, not a throughput knob.**
``EMB_BATCH`` is the forward-pass width; this is the cap on how much work a
single waiter can end up queued behind, and without it one burst makes the
last caller in a batch wait for an unbounded number of forward passes -- the
p95 budget (``07 §2``: RAG retrieval 400 ms) blown by the very mechanism
meant to protect it. A single request LARGER than the cap is always
dispatched whole: the cap bounds coalescing, it never splits a caller.

``EMB_BATCH_WINDOW_MS=0`` builds NO batcher at all and ``/embed`` runs the
pre-4.3 inline path verbatim, on the loop -- the `م-8` kill switch every step
in this plan carries, so a baseline can be measured against the same image.

MEASURED on the live stack through ``embedding-lb``, one text per request
(the query path's shape), 30 s per run, requests/second and p95::

    concurrency   window=0                   window=5
      8           144.9 · 147.1   p95 113ms  176.7 · 177.9   p95 73ms
     16           153.3           p95 234ms  234.6           p95 112ms
     32           156.8           p95 478ms  283.3           p95 195ms

⭐ **The left column is SATURATED at ~155 req/s** -- four times the
concurrency buys 8% more throughput and four times the latency, which is
what "one forward pass holds the event loop" looks like from outside. And
478 ms had spent the whole 400 ms retrieval budget (``07 §2``) on the
embedding call alone, before Qdrant was asked anything.

⚠️ **On the INDEXING shape it buys nothing** -- 0.94 vs 0.95 req/s for
batches of eight long chunks -- because such a call arrives with its batch
already assembled. The gain is entirely on the query shape, which is the one
``§0``'s budget is written against.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Pinned defaults -- MUST match the model baked into the image at build time
# (``services/embedding/Dockerfile``'s own ``RUN python -c "... SentenceTransformer(...)"``
# step). Env-overridable at the SERVICE layer only (``EMBEDDING_MODEL``/
# ``EMBEDDING_DIM``/``EMB_BATCH``, ``docker-compose.yml``'s ``embedding``
# service) -- a different knob from the ADAPTER's own settings
# (``EmbeddingServiceSettings``), which pins these same values independently
# so the two sides can never silently drift without the 400 above catching it.
_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_DEFAULT_DIM = 384
_DEFAULT_BATCH = 8
# The baked model's ARCHITECTURAL ceiling -- `config.json`'s
# `max_position_embeddings`, the longest sequence its position embeddings can
# represent -- and deliberately NOT the checkpoint's packaged
# `sentence_bert_config.json` value of 128, which is what a bare
# `SentenceTransformer(...)` applies instead (module docstring).
#
# This is the SERVICE-side half of one budget whose ADAPTER-side half is
# `EmbeddingServiceSettings.embedding_max_input_tokens` (512, the same number
# pinned independently the way `dimensions` is). The adapter sizes chunks to
# fit under it; this decides where the model actually cuts. Note the failure
# modes are not symmetric: a `model` mismatch between the two sides is caught
# loudly by the 400 in `/embed`, whereas a drift between THESE two shows up
# only as quietly shortened vectors -- which is why `/health` reports the
# effective number rather than leaving it inferable.
#
# Raising it beyond the model's own ceiling buys no extra input: it would
# index position embeddings that do not exist. A model swap (`EMBEDDING_MODEL`)
# must bring its own ceiling with it, via `EMB_MAX_SEQ_LEN`.
_DEFAULT_MAX_SEQ_LEN = 512

_CHARS_PER_TOKEN = 4

# The coalescing window (capacity 4.3, module docstring). 5 ms is the plan's
# own number; `0` builds no batcher at all and restores the pre-4.3 inline
# path (`م-8`).
_DEFAULT_BATCH_WINDOW_MS = 5
# The ceiling on how many texts one coalesced `encode` may carry -- four
# `_DEFAULT_BATCH`-wide forward passes. A LATENCY bound (module docstring),
# and never a splitter: one request larger than this is dispatched whole.
_DEFAULT_MAX_BATCH_TEXTS = 32


class Encoder(Protocol):
    """The read shape this module needs from a loaded ``SentenceTransformer``
    -- narrow on purpose, so the unit suite can fake it without ever
    importing ``torch``/``sentence_transformers`` (module docstring).

    ``max_seq_length`` is the one member here this module WRITES rather than
    reads -- see ``_load_model``."""

    max_seq_length: int

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ) -> object: ...


@dataclass
class _ModelState:
    """Mutable holder the lifespan fills at startup -- a plain module-level
    singleton (this process serves exactly one model, module docstring),
    and what the unit suite fakes directly by monkeypatching ``_load_model``
    before entering the app's lifespan."""

    model: Encoder | None = None
    model_name: str = ""
    dimensions: int = _DEFAULT_DIM
    batch_size: int = _DEFAULT_BATCH
    max_seq_length: int = _DEFAULT_MAX_SEQ_LEN
    # `None` means "never asked" -- torch was left on its own default and this
    # process did not import it to find out. NOT "one thread".
    torch_threads: int | None = None
    # capacity 4.3. `batch_window_ms == 0` is what makes `batcher` stay
    # `None`, and `batcher is None` is the ONE thing `/embed` branches on --
    # so the kill switch has exactly one representation in the running
    # process, not a flag that a second code path could disagree with.
    batch_window_ms: int = _DEFAULT_BATCH_WINDOW_MS
    max_batch_texts: int = _DEFAULT_MAX_BATCH_TEXTS
    batcher: _Batcher | None = None


_state = _ModelState()


def _pin_torch_threads(threads: int) -> int:
    """Pin torch's intra-op pool and report back what actually took effect
    (capacity 4.1, module docstring's "Thread pinning" paragraph).

    Called ONLY when ``EMB_TORCH_THREADS`` is set, and that guard is load-
    bearing rather than defensive: it is what keeps ``import torch`` out of
    this module's path entirely in the dev/CI venv, which has neither torch
    nor ``sentence_transformers`` installed (module docstring's "Lazy
    imports"). The unit suite exercises the pinned path by monkeypatching
    THIS function, the same way it fakes ``_load_model``.

    It returns ``torch.get_num_threads()`` rather than the argument: the two
    can differ (a build with a single-threaded ATen ignores the request), and
    a health endpoint that echoes what was ASKED FOR would report a pin that
    never happened.

    ⚠️ Intra-op only. ``set_num_interop_threads`` is deliberately not called:
    it raises once any parallel work has started, and the inter-op pool sits
    idle for a single sequential ``encode`` anyway -- so the failure mode
    (a boot-time RuntimeError on a service whose model takes seconds to load)
    would cost more than the pool it would tidy.
    """
    import torch  # noqa: PLC0415

    torch.set_num_threads(threads)
    return int(torch.get_num_threads())


def _load_model(model_name: str, max_seq_length: int) -> Encoder:
    """Load the ``SentenceTransformer`` model, device-auto (module
    docstring): ``cuda``+fp16 if ``torch.cuda.is_available()``, else
    ``cpu``+fp32. Imported lazily -- see the module docstring's "Lazy
    imports" paragraph (deliberate, hence the two ``noqa``s below: a
    module-level import here is exactly what this function exists to
    avoid).

    ``max_seq_length`` is applied LAST -- to whatever object is about to be
    served, after the optional ``.half()``. That returns ``self`` today, but
    a loader step that ever returned a NEW object would otherwise carry the
    checkpoint's own 128 straight back into production, which is precisely
    the silent failure this line exists to prevent (module docstring)."""
    import torch  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(model_name, device=device)
    if device == "cuda":
        model = model.half()
    model.max_seq_length = max_seq_length
    return model


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    model_name = os.environ.get("EMBEDDING_MODEL", _DEFAULT_MODEL)
    dimensions = int(os.environ.get("EMBEDDING_DIM", str(_DEFAULT_DIM)))
    batch_size = int(os.environ.get("EMB_BATCH", str(_DEFAULT_BATCH)))
    max_seq_length = int(os.environ.get("EMB_MAX_SEQ_LEN", str(_DEFAULT_MAX_SEQ_LEN)))
    # BEFORE the load, not after: `SentenceTransformer(...)` runs real tensor
    # work while it builds the model, and a pool sized after that is a pool
    # the load itself never saw.
    requested_threads = os.environ.get("EMB_TORCH_THREADS", "").strip()
    _state.torch_threads = _pin_torch_threads(int(requested_threads)) if requested_threads else None
    _state.model = _load_model(model_name, max_seq_length)
    _state.model_name = model_name
    _state.dimensions = dimensions
    _state.batch_size = batch_size
    _state.max_seq_length = max_seq_length
    # capacity 4.3. Built AFTER the model, so the batcher's task can never
    # observe a half-loaded state, and torn down BEFORE it in the `finally`
    # below for the mirror-image reason.
    _state.batch_window_ms = int(
        os.environ.get("EMB_BATCH_WINDOW_MS", str(_DEFAULT_BATCH_WINDOW_MS))
    )
    _state.max_batch_texts = int(
        os.environ.get("EMB_MAX_BATCH_TEXTS", str(_DEFAULT_MAX_BATCH_TEXTS))
    )
    batcher_task: asyncio.Task[None] | None = None
    if _state.batch_window_ms > 0:
        _state.batcher = _Batcher(
            window_s=_state.batch_window_ms / 1000,
            max_texts=max(1, _state.max_batch_texts),
        )
        batcher_task = asyncio.create_task(_state.batcher.run())
    try:
        yield
    finally:
        batcher, _state.batcher = _state.batcher, None
        if batcher_task is not None:
            batcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await batcher_task
        if batcher is not None:
            await batcher.close()
        _state.model = None
        _state.model_name = ""
        _state.torch_threads = None


app = FastAPI(title="AIZZAK Embedding Service", lifespan=_lifespan)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    model: str


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    model: str
    dimensions: int
    tokens: int


class HealthResponse(BaseModel):
    status: str
    model: str
    dimensions: int
    max_seq_length: int
    # `None` when `EMB_TORCH_THREADS` was not set, i.e. torch was left on its
    # own default -- see `_pin_torch_threads`.
    torch_threads: int | None = None
    # capacity 4.3. `0` says this instance builds no batcher and runs the
    # inline path -- the one externally visible difference between an
    # optimised replica and a baseline one.
    batch_window_ms: int = _DEFAULT_BATCH_WINDOW_MS
    max_batch_texts: int = _DEFAULT_MAX_BATCH_TEXTS


def _estimate_tokens(texts: Sequence[str]) -> int:
    """The per-character fallback (the ``ExternalEmbeddingProvider`` adapter's
    OWN fallback formula, kept in sync deliberately: it is what the adapter
    falls back to if this service ever omits ``tokens``)."""
    return sum(max(1, len(text) // _CHARS_PER_TOKEN) for text in texts)


def _token_counts(model: Encoder, texts: Sequence[str]) -> list[int]:
    """The REAL tokenizer's token count PER TEXT via ``model.tokenize`` (a
    real ``SentenceTransformer``'s padded-batch ``attention_mask`` -- summing
    a row, rather than taking the padded ``input_ids`` length, is what keeps
    padding tokens from inflating the count). Falls back to the character
    estimate when the encoder exposes no ``tokenize`` (the unit-test fake,
    module docstring) or an unrecognised shape.

    Per text rather than per call because one coalesced ``encode`` now
    carries SEVERAL callers' texts (``_Batcher``), and each caller must be
    told what ITS OWN texts cost -- a batch total split by any other rule
    would bill one request for another's work. The row count is checked
    against the text count for the same reason every other read of a
    tokenizer's output here is guarded: a shape this module does not
    recognise falls back rather than mis-attributing.
    """
    tokenize = getattr(model, "tokenize", None)
    if callable(tokenize):
        features = tokenize(list(texts))
        mask = features.get("attention_mask") if isinstance(features, dict) else None
        if mask is not None:
            counts = [int(sum(row)) for row in mask]
            if len(counts) == len(texts):
                return counts
    return [max(1, len(text) // _CHARS_PER_TOKEN) for text in texts]


def _count_tokens(model: Encoder, texts: Sequence[str]) -> int:
    """The whole call's token count -- ``_token_counts`` summed."""
    return sum(_token_counts(model, texts))


def _to_vectors(encoded: object) -> list[list[float]]:
    """``model.encode(..., convert_to_numpy=True)`` returns a 2-D numpy
    array; iterating it yields one 1-D row per text, and ``float(x)`` works
    identically over ``numpy.float32``/``numpy.float64``/plain Python
    floats -- so this also accepts a plain nested-list fake with no numpy
    import needed here at all."""
    return [[float(x) for x in row] for row in encoded]  # type: ignore[attr-defined]


def _encode_batch(model: Encoder, texts: list[str]) -> tuple[list[list[float]], list[int]]:
    """One forward pass over ``texts`` plus their per-text token counts --
    the ONLY place this module calls ``model.encode``, so the batched path
    and the ``EMB_BATCH_WINDOW_MS=0`` inline path cannot drift in what they
    ask the model for.

    Synchronous on purpose: ``_Batcher`` hands it to ``asyncio.to_thread``
    and the inline path calls it directly, which is exactly the difference
    between the two paths and the whole of it.

    ``_state.batch_size`` is read here rather than passed in because it is
    written once, by the lifespan, before any request exists -- the same
    reason ``_state.model`` is read this way.
    """
    encoded = model.encode(
        texts,
        batch_size=_state.batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return _to_vectors(encoded), _token_counts(model, texts)


@dataclass(slots=True)
class _Waiter:
    """One in-flight ``POST /embed`` waiting for the batch it was folded
    into. ``texts`` is what it contributed; ``future`` is how its own slice
    of the result gets back to it."""

    texts: list[str]
    future: asyncio.Future[tuple[list[list[float]], int]]


class _Batcher:
    """Coalesce concurrent ``/embed`` calls into one ``encode`` (capacity
    4.3, module docstring).

    One instance per process, built by the lifespan and owning exactly one
    background task -- which is what makes "one encode at a time" structural
    rather than a convention: the queue has a single consumer, so no second
    forward pass can start while one is running, no matter how many requests
    are in flight.
    """

    def __init__(self, *, window_s: float, max_texts: int) -> None:
        self._window_s = window_s
        self._max_texts = max_texts
        self._queue: asyncio.Queue[_Waiter] = asyncio.Queue()

    async def submit(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """Enqueue ``texts`` and wait for this caller's own vectors/tokens.

        ``put_nowait`` on an unbounded queue: a bound here would have to
        choose between blocking the request (which is the queueing this step
        exists to make cheap) and rejecting it (which is the API layer's job,
        not this service's -- ``MAX_IN_FLIGHT_REQUESTS`` and the two Redis
        buckets of capacity 1.2 are where a ceiling belongs, in front of the
        platform rather than inside one of its dependencies).
        """
        waiter = _Waiter(texts=texts, future=asyncio.get_running_loop().create_future())
        self._queue.put_nowait(waiter)
        return await waiter.future

    async def run(self) -> None:
        """The single consumer. Cancelled by the lifespan at shutdown."""
        while True:
            first = await self._queue.get()
            waiters = [first]
            total = self._drain(waiters, len(first.texts))
            # The window is paid ONLY when draining did not already fill the
            # batch -- module docstring's "floor, not the mechanism".
            if total < self._max_texts and self._window_s > 0:
                await asyncio.sleep(self._window_s)
                total = self._drain(waiters, total)
            await self._dispatch(waiters)

    def _drain(self, waiters: list[_Waiter], total: int) -> int:
        """Fold every already-queued request into ``waiters`` until the text
        cap is reached. Never partially takes a request: a waiter whose texts
        would cross the cap is left in the queue for the next batch, because
        splitting one caller across two forward passes would give it two
        different latencies for one call and buy nothing."""
        while total < self._max_texts:
            try:
                nxt = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return total
            waiters.append(nxt)
            total += len(nxt.texts)
        return total

    async def _dispatch(self, waiters: list[_Waiter]) -> None:
        """Run one batch off the event loop and hand each waiter its slice.

        ⚠️ Every failure reaches EVERY waiter in the batch. A batch is a
        service-side implementation detail; a caller that shared one with a
        request that happened to fail must still get an answer, and a caller
        whose own request failed must not be left waiting forever on a future
        nobody will ever set. That is also why ``CancelledError`` is caught
        and re-raised rather than allowed to unwind silently: shutdown must
        close every open request, not abandon it.
        """
        model = _state.model
        texts = [text for waiter in waiters for text in waiter.texts]
        try:
            if model is None:  # the lifespan tore the model down mid-flight
                raise _not_loaded()
            vectors, tokens = await asyncio.to_thread(_encode_batch, model, texts)
        except asyncio.CancelledError:
            _fail(waiters, _not_loaded())
            raise
        except Exception as exc:  # re-raised INTO every waiter below, never swallowed
            _fail(waiters, exc)
            return
        start = 0
        for waiter in waiters:
            end = start + len(waiter.texts)
            if not waiter.future.done():
                waiter.future.set_result((vectors[start:end], sum(tokens[start:end])))
            start = end

    async def close(self) -> None:
        """Fail everything still queued when the process is going down --
        after ``run`` has been cancelled, so nothing can be enqueued behind
        this drain and left unanswered."""
        while True:
            try:
                waiter = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            _fail([waiter], _not_loaded())


def _not_loaded() -> HTTPException:
    """The 503 ``/embed`` already answers before the model is loaded --
    reused for the shutdown case, because from a caller's side "this instance
    has no servable model right now" is the same fact either way."""
    return HTTPException(status_code=503, detail="model not loaded")


def _fail(waiters: list[_Waiter], exc: BaseException) -> None:
    """Deliver ``exc`` to every waiter that is still waiting. ``done()`` is
    checked because a caller that disconnected already cancelled its own
    future, and setting an exception on it would raise here -- turning one
    dropped client into a dead batcher."""
    for waiter in waiters:
        if not waiter.future.done():
            waiter.future.set_exception(exc)


@app.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest) -> EmbedResponse:
    model = _state.model
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    if body.model != _state.model_name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"model mismatch: this instance serves {_state.model_name!r}, got {body.model!r}"
            ),
        )
    batcher = _state.batcher
    if batcher is None:
        # `EMB_BATCH_WINDOW_MS=0` -- the pre-4.3 path, ON the event loop
        # (module docstring's kill switch). Deliberately not `to_thread`:
        # a "baseline" that changed where the work runs would not be one.
        vectors, per_text_tokens = _encode_batch(model, list(body.texts))
        tokens = sum(per_text_tokens)
    else:
        vectors, tokens = await batcher.submit(list(body.texts))
    return EmbedResponse(
        vectors=vectors,
        model=_state.model_name,
        dimensions=_state.dimensions,
        tokens=tokens,
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """200 ONLY after the model is loaded (module docstring) -- 503 before
    it, which is what makes ``docker-compose.yml``'s healthcheck (and any
    orchestrator's readiness probe) wait for a real, servable model rather
    than an accepting-but-unready port.

    ``max_seq_length`` rides along for the reason the module docstring
    gives: it is the token at which this instance stops reading a text, it
    has no loud drift check the way ``model`` does, and it was invisible from
    outside this container for exactly as long as it was wrong.
    ``torch_threads`` rides along for the SAME reason, one step further
    (capacity 4.1): a replica whose pin did not take looks exactly like one
    whose pin did, until the fleet is slower than a single container was.
    ``batch_window_ms``/``max_batch_texts`` complete the set (capacity 4.3):
    they are the difference between a batching replica and a baseline one,
    and nothing else about the process makes that visible.

    ⚠️ **And before 4.3 this route did not merely get slow under load -- it
    FAILED.** ``model.encode`` held the event loop for the whole of a forward
    pass, so a probe arriving mid-encode waited for it. Measured, thirty
    spaced probes against one replica under indexing-shaped load: unbatched,
    seven of thirty took over 1.4 s and the slowest took **5.88 s** -- past
    this service's own ``healthcheck.timeout`` of 5 s in
    ``docker-compose.yml``, so what kept a working replica from being
    restarted was ``retries: 12`` and nothing else. Batched, the slowest of
    the thirty was **3.3 ms**. (7.2 recorded the same defect from the other
    side: eleven ``499``s on ``/health``, all at 4.99 s.)"""
    if _state.model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return HealthResponse(
        status="ok",
        model=_state.model_name,
        dimensions=_state.dimensions,
        max_seq_length=_state.max_seq_length,
        torch_threads=_state.torch_threads,
        batch_window_ms=_state.batch_window_ms,
        max_batch_texts=_state.max_batch_texts,
    )
