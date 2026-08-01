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

**Loud drift detection.** ``POST /embed`` 400s if the caller's ``model``
does not match the one this instance actually loaded -- an adapter/service
image mismatch is a deployment bug, and a silent wrong-model embedding would
poison a collection invisibly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
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

_CHARS_PER_TOKEN = 4


class Encoder(Protocol):
    """The read shape this module needs from a loaded ``SentenceTransformer``
    -- narrow on purpose, so the unit suite can fake it without ever
    importing ``torch``/``sentence_transformers`` (module docstring)."""

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


_state = _ModelState()


def _load_model(model_name: str) -> Encoder:
    """Load the ``SentenceTransformer`` model, device-auto (module
    docstring): ``cuda``+fp16 if ``torch.cuda.is_available()``, else
    ``cpu``+fp32. Imported lazily -- see the module docstring's "Lazy
    imports" paragraph (deliberate, hence the two ``noqa``s below: a
    module-level import here is exactly what this function exists to
    avoid)."""
    import torch  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(model_name, device=device)
    if device == "cuda":
        model = model.half()
    return model


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    model_name = os.environ.get("EMBEDDING_MODEL", _DEFAULT_MODEL)
    dimensions = int(os.environ.get("EMBEDDING_DIM", str(_DEFAULT_DIM)))
    batch_size = int(os.environ.get("EMB_BATCH", str(_DEFAULT_BATCH)))
    _state.model = _load_model(model_name)
    _state.model_name = model_name
    _state.dimensions = dimensions
    _state.batch_size = batch_size
    try:
        yield
    finally:
        _state.model = None
        _state.model_name = ""


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


def _estimate_tokens(texts: Sequence[str]) -> int:
    """The per-character fallback (the ``ExternalEmbeddingProvider`` adapter's
    OWN fallback formula, kept in sync deliberately: it is what the adapter
    falls back to if this service ever omits ``tokens``)."""
    return sum(max(1, len(text) // _CHARS_PER_TOKEN) for text in texts)


def _count_tokens(model: Encoder, texts: Sequence[str]) -> int:
    """Sum the REAL tokenizer's per-text token count via ``model.tokenize``
    (a real ``SentenceTransformer``'s padded-batch ``attention_mask`` --
    summing it, rather than the padded ``input_ids`` length, is what keeps
    padding tokens from inflating the count). Falls back to the character
    estimate when the encoder exposes no ``tokenize`` (the unit-test fake,
    module docstring) or an unrecognised shape."""
    tokenize = getattr(model, "tokenize", None)
    if callable(tokenize):
        features = tokenize(list(texts))
        mask = features.get("attention_mask") if isinstance(features, dict) else None
        if mask is not None:
            return int(sum(int(sum(row)) for row in mask))
    return _estimate_tokens(texts)


def _to_vectors(encoded: object) -> list[list[float]]:
    """``model.encode(..., convert_to_numpy=True)`` returns a 2-D numpy
    array; iterating it yields one 1-D row per text, and ``float(x)`` works
    identically over ``numpy.float32``/``numpy.float64``/plain Python
    floats -- so this also accepts a plain nested-list fake with no numpy
    import needed here at all."""
    return [[float(x) for x in row] for row in encoded]  # type: ignore[attr-defined]


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
    encoded = model.encode(
        body.texts,
        batch_size=_state.batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return EmbedResponse(
        vectors=_to_vectors(encoded),
        model=_state.model_name,
        dimensions=_state.dimensions,
        tokens=_count_tokens(model, body.texts),
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """200 ONLY after the model is loaded (module docstring) -- 503 before
    it, which is what makes ``docker-compose.yml``'s healthcheck (and any
    orchestrator's readiness probe) wait for a real, servable model rather
    than an accepting-but-unready port."""
    if _state.model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return HealthResponse(status="ok", model=_state.model_name, dimensions=_state.dimensions)
