"""Unit tests for the standalone embedding service
(``services/embedding/app.py``, Phase 2.10) -- a SEPARATE deployable OUTSIDE
the ``app`` package (that module's own docstring). The real model loader
(``_load_model``) is monkeypatched to a fake ``Encoder`` in every test that
needs a "loaded" state, so NEITHER ``torch`` NOR ``sentence_transformers``
is ever imported by this suite -- both are absent from the dev/CI venv on
purpose (``services/embedding/requirements.txt``'s own module docstring),
and this file is the proof that absence never breaks ``pytest -m "not
integration"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from services.embedding import app as service_app


class _FakeEncoder:
    """A structural ``Encoder`` fake -- no torch, no real model. Records
    every ``encode`` call's kwargs, and returns one POSITION-dependent
    vector per input text (never a constant), so a batching/ordering bug
    would show up in the response rather than being masked by every row
    looking identical."""

    def __init__(self, *, dim: int = 4) -> None:
        self.dim = dim
        self.encode_calls: list[dict[str, Any]] = []
        # Deliberately NOT 512, and not the checkpoint's own 128 either: a
        # sentinel no code path under test would ever choose, so
        # `max_seq_length == 512` can only mean the lifespan passed it.
        self.max_seq_length = -1

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ) -> list[list[float]]:
        self.encode_calls.append(
            {
                "sentences": list(sentences),
                "batch_size": batch_size,
                "normalize_embeddings": normalize_embeddings,
                "convert_to_numpy": convert_to_numpy,
            }
        )
        return [[float(i)] * self.dim for i in range(len(sentences))]


class _FakeEncoderWithTokenizer(_FakeEncoder):
    """Adds a ``tokenize`` method -- the real ``SentenceTransformer`` shape
    ``_count_tokens`` reads. Each text's per-character mask length stands
    in for a real tokenizer's per-text token count."""

    def tokenize(self, texts: Sequence[str]) -> dict[str, list[list[int]]]:
        return {"attention_mask": [[1] * len(text) for text in texts]}


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    """Every test starts from -- and leaves -- an unloaded model, whether or
    not its own ``with TestClient(...) as client:`` block ran to completion
    (a raised assertion mid-test must not leak load state into the next
    test)."""
    service_app._state.model = None
    service_app._state.model_name = ""
    service_app._state.torch_threads = None
    yield
    service_app._state.model = None
    service_app._state.model_name = ""
    service_app._state.torch_threads = None


def _fake_loader(fake: _FakeEncoder) -> Any:
    """Mirrors the real ``_load_model``'s contract, INCLUDING the part this
    file exists to pin: the loader applies ``max_seq_length`` to the model it
    is about to hand back (``services/embedding/app.py``'s module docstring
    -- the checkpoint ships 128, and nothing but this assignment moves it)."""

    def _load(model_name: str, max_seq_length: int) -> _FakeEncoder:
        fake.max_seq_length = max_seq_length
        return fake

    return _load


# --------------------------------------------------------------------------- #
# GET /health -- gated on the model actually being loaded                     #
# --------------------------------------------------------------------------- #
def test_health_is_503_before_the_lifespan_has_run() -> None:
    """No ``with`` block -- the lifespan never starts, so ``_state.model``
    stays ``None`` (module docstring: 503, never a 200 for an unready
    model)."""
    client = TestClient(service_app.app)
    response = client.get("/health")
    assert response.status_code == 503


def test_health_is_200_with_the_loaded_models_identity_once_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEncoder(dim=4)
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_DIM", "4")
    monkeypatch.delenv("EMB_MAX_SEQ_LEN", raising=False)

    with TestClient(service_app.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model": "test-model",
        "dimensions": 4,
        "max_seq_length": 512,
        # `EMB_TORCH_THREADS` unset -> torch was never asked, and `null` says
        # exactly that rather than standing in for "one" (capacity 4.1).
        "torch_threads": None,
        # capacity 4.3 -- the module defaults, reported so a baseline replica
        # (`EMB_BATCH_WINDOW_MS=0`) is distinguishable from a batching one
        # without reading the compose file.
        "batch_window_ms": 5,
        "max_batch_texts": 32,
    }


def test_health_resets_to_unloaded_after_the_lifespan_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEncoder()
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))

    with TestClient(service_app.app) as client:
        assert client.get("/health").status_code == 200

    # The `with` block's shutdown phase already ran; a fresh, non-context
    # client now sees the model as unloaded again.
    after = TestClient(service_app.app)
    assert after.get("/health").status_code == 503


# --------------------------------------------------------------------------- #
# max_seq_length -- the token at which this service stops reading a text      #
#                                                                             #
# The checkpoint's own `sentence_bert_config.json` says 128 and the model's   #
# `config.json` says its position embeddings reach 512, so a bare             #
# `SentenceTransformer(...)` embeds a QUARTER of what the model can hold and  #
# silently drops the rest -- measured at 79% of a full-size Arabic chunk      #
# lost. These pin the two halves that were missing: that the value is set at  #
# all, and that it is set to the ceiling rather than to the checkpoint's      #
# default.                                                                    #
# --------------------------------------------------------------------------- #
def test_lifespan_raises_the_models_sequence_length_to_the_pinned_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression proper: the loader must receive -- and apply -- 512,
    not the 128 the checkpoint would otherwise impose on itself."""
    fake = _FakeEncoder()
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.delenv("EMB_MAX_SEQ_LEN", raising=False)

    with TestClient(service_app.app):
        pass

    assert fake.max_seq_length == 512
    assert service_app._DEFAULT_MAX_SEQ_LEN == 512


def test_max_seq_length_is_env_overridable_at_the_service_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EMB_MAX_SEQ_LEN` rides the same service-layer seam as
    `EMBEDDING_MODEL`/`EMBEDDING_DIM`/`EMB_BATCH` -- a model swap has to be
    able to bring its own ceiling."""
    fake = _FakeEncoder()
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMB_MAX_SEQ_LEN", "256")

    with TestClient(service_app.app) as client:
        reported = client.get("/health").json()["max_seq_length"]

    assert fake.max_seq_length == 256
    assert reported == 256


def test_health_reports_the_sequence_length_actually_in_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/health` is the ONLY way to see this number from outside the
    container, and its absence is why the 128 went unnoticed. So it reports
    what was applied to the model, never the module default."""
    fake = _FakeEncoder()
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMB_MAX_SEQ_LEN", "384")

    with TestClient(service_app.app) as client:
        reported = client.get("/health").json()["max_seq_length"]

    assert reported == fake.max_seq_length == 384


# --------------------------------------------------------------------------- #
# EMB_TORCH_THREADS -- the pin, and the proof it is reported not assumed      #
# (capacity 4.1)                                                              #
# --------------------------------------------------------------------------- #
def test_no_thread_pin_is_attempted_when_the_env_var_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ THE GUARD IS WHAT KEEPS `import torch` OUT OF THIS SUITE. Unset is
    the default everywhere except `docker-compose.yml`, and on that path
    `_pin_torch_threads` -- the only function in the module that imports torch
    outside `_load_model` -- must not be called at all. A `_pin_torch_threads`
    that raised would prove the same thing; this asserts the stronger property
    that it is never reached."""
    fake = _FakeEncoder()
    calls: list[int] = []
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setattr(service_app, "_pin_torch_threads", calls.append)
    monkeypatch.delenv("EMB_TORCH_THREADS", raising=False)

    with TestClient(service_app.app) as client:
        reported = client.get("/health").json()["torch_threads"]

    assert calls == []
    assert reported is None


def test_health_reports_the_thread_count_that_actually_took_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pin is applied and then READ BACK -- `/health` publishes what torch
    answered, never what the environment asked for. The fake returns a
    different number on purpose: a build that ignores the request (or an env
    var applied too late to matter) must be visible from outside the
    container, which is the whole reason 4.1 puts it on `/health` at all."""
    fake = _FakeEncoder()
    asked: list[int] = []
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setattr(
        service_app, "_pin_torch_threads", lambda threads: (asked.append(threads), 7)[1]
    )
    monkeypatch.setenv("EMB_TORCH_THREADS", "1")

    with TestClient(service_app.app) as client:
        reported = client.get("/health").json()["torch_threads"]

    assert asked == [1]
    assert reported == 7


# --------------------------------------------------------------------------- #
# POST /embed                                                                 #
# --------------------------------------------------------------------------- #
def test_embed_is_503_before_the_lifespan_has_run() -> None:
    client = TestClient(service_app.app)
    response = client.post("/embed", json={"texts": ["hello"], "model": "test-model"})
    assert response.status_code == 503


def test_embed_returns_the_designed_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeEncoder(dim=4)
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_DIM", "4")
    monkeypatch.setenv("EMB_BATCH", "2")

    with TestClient(service_app.app) as client:
        response = client.post("/embed", json={"texts": ["a", "b", "c"], "model": "test-model"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"vectors", "model", "dimensions", "tokens"}
    assert body["model"] == "test-model"
    assert body["dimensions"] == 4
    assert len(body["vectors"]) == 3
    assert all(len(vector) == 4 for vector in body["vectors"])
    assert isinstance(body["tokens"], int)


def test_embed_calls_encode_with_normalize_true_and_the_configured_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device policy (module docstring): L2-normalisation is ALWAYS on,
    regardless of device -- this is the one assertion that survives without
    ever touching torch/cuda."""
    fake = _FakeEncoder(dim=2)
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("EMB_BATCH", "3")

    with TestClient(service_app.app) as client:
        client.post("/embed", json={"texts": ["a", "b"], "model": "test-model"})

    assert len(fake.encode_calls) == 1
    call = fake.encode_calls[0]
    assert call["normalize_embeddings"] is True
    assert call["convert_to_numpy"] is True
    assert call["batch_size"] == 3
    assert call["sentences"] == ["a", "b"]


def test_embed_model_mismatch_is_400_loud_drift_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEncoder()
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "loaded-model")

    with TestClient(service_app.app) as client:
        response = client.post("/embed", json={"texts": ["a"], "model": "a-different-model"})

    assert response.status_code == 400
    assert fake.encode_calls == []  # never even reaches the encoder


def test_embed_rejects_an_empty_texts_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeEncoder()
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")

    with TestClient(service_app.app) as client:
        response = client.post("/embed", json={"texts": [], "model": "test-model"})

    assert response.status_code == 422
    assert fake.encode_calls == []


def test_embed_tokens_uses_the_tokenizer_when_the_encoder_exposes_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEncoderWithTokenizer(dim=2)
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")

    with TestClient(service_app.app) as client:
        response = client.post("/embed", json={"texts": ["ab", "abc"], "model": "test-model"})

    assert response.json()["tokens"] == 2 + 3


# --------------------------------------------------------------------------- #
# _count_tokens -- pure, no HTTP                                              #
# --------------------------------------------------------------------------- #
def test_count_tokens_sums_the_tokenizers_attention_mask() -> None:
    fake = _FakeEncoderWithTokenizer()
    assert service_app._count_tokens(fake, ["ab", "abc"]) == 5


def test_count_tokens_falls_back_to_the_char_estimate_without_a_tokenizer() -> None:
    fake = _FakeEncoder()
    assert service_app._count_tokens(fake, ["hello world"]) == 2  # 11 // 4 = 2


def test_estimate_tokens_never_reports_zero_for_a_non_empty_text() -> None:
    assert service_app._estimate_tokens(["a"]) == 1  # 1 // 4 = 0 -> max(1, 0)


# --------------------------------------------------------------------------- #
# Dynamic batching (capacity 4.3) -- `_Batcher`                                #
#                                                                             #
# Exercised against `_Batcher` directly rather than through `TestClient`:     #
# the behaviour under test is what happens when SEVERAL requests are in       #
# flight at once, and `TestClient` is synchronous -- it cannot produce that   #
# shape at all. `_state.model` is set the way the lifespan sets it, so these  #
# drive the same `_encode_batch` the route does.                              #
# --------------------------------------------------------------------------- #
class _SlowEncoder(_FakeEncoderWithTokenizer):
    """A fake whose ``encode`` blocks for a real interval -- long enough that
    requests submitted while it runs land in the queue behind it, which is the
    condition dynamic batching exists to exploit (module docstring: "what
    actually fills a batch is the queue that accumulates DURING the previous
    encode")."""

    def __init__(self, *, dim: int = 4, delay_s: float = 0.05) -> None:
        super().__init__(dim=dim)
        self._delay_s = delay_s

    def encode(self, sentences: list[str], **kwargs: Any) -> list[list[float]]:
        time.sleep(self._delay_s)
        return super().encode(sentences, **kwargs)


def _loaded(monkeypatch: pytest.MonkeyPatch, encoder: _FakeEncoder) -> None:
    """The state the lifespan would have left behind, without running it --
    these tests own the batcher's task themselves."""
    monkeypatch.setattr(service_app._state, "model", encoder)
    monkeypatch.setattr(service_app._state, "model_name", "test-model")
    monkeypatch.setattr(service_app._state, "batch_size", 8)


async def _with_batcher(
    batcher: service_app._Batcher, calls: list[list[str]]
) -> list[tuple[list[list[float]], int]]:
    """Run ``batcher`` for the duration of ``calls`` submitted together."""
    task = asyncio.create_task(batcher.run())
    try:
        return await asyncio.gather(*(batcher.submit(texts) for texts in calls))
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await batcher.close()


async def test_batcher_coalesces_concurrent_single_text_calls_into_one_encode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE step's own claim: eight concurrent one-text requests -- the query
    path's shape (`retrieval.py` embeds a single string) -- become ONE forward
    pass instead of eight."""
    fake = _SlowEncoder(dim=2)
    _loaded(monkeypatch, fake)
    batcher = service_app._Batcher(window_s=0.005, max_texts=32)

    results = await _with_batcher(batcher, [[f"q{i}"] for i in range(8)])

    assert len(results) == 8
    # One `encode` may have gone out alone (whichever request opened the
    # batch); everything submitted behind it is coalesced. What must NOT
    # happen is eight separate forward passes.
    assert len(fake.encode_calls) <= 2
    # ...and coalescing actually HAPPENED, which `<= 2` alone would not prove
    # (measured here: a single call carrying all eight).
    assert max(len(call["sentences"]) for call in fake.encode_calls) >= 7
    assert sorted(t for call in fake.encode_calls for t in call["sentences"]) == sorted(
        f"q{i}" for i in range(8)
    )


async def test_batcher_gives_each_waiter_its_own_slice_in_its_own_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The correctness half of coalescing: a shared `encode` must not let one
    caller's vectors reach another. The fake returns POSITION-dependent rows,
    so a mis-sliced batch shows up as the wrong numbers rather than as
    identical-looking output."""
    fake = _SlowEncoder(dim=1)
    _loaded(monkeypatch, fake)
    batcher = service_app._Batcher(window_s=0.005, max_texts=32)

    results = await _with_batcher(batcher, [["a"], ["b", "c"], ["d"]])

    # Every caller gets exactly as many vectors as it sent texts...
    assert [len(vectors) for vectors, _ in results] == [1, 2, 1]
    # ...and the rows are the encoder's rows for ITS OWN positions in the
    # coalesced batch, contiguous and in order.
    flat = [row for vectors, _ in results for row in vectors]
    assert flat == [[float(i)] for i in range(4)]
    # Tokens are per caller (the fake tokenises one token per character), not
    # the batch total split by any other rule.
    assert [tokens for _, tokens in results] == [1, 2, 1]


async def test_batcher_never_coalesces_past_the_text_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EMB_MAX_BATCH_TEXTS` is a latency bound (module docstring): a burst
    must not make the last caller wait behind an unbounded number of forward
    passes."""
    fake = _SlowEncoder(dim=1)
    _loaded(monkeypatch, fake)
    batcher = service_app._Batcher(window_s=0.005, max_texts=2)

    await _with_batcher(batcher, [[f"q{i}"] for i in range(6)])

    assert fake.encode_calls, "the batcher never ran"
    assert max(len(call["sentences"]) for call in fake.encode_calls) <= 2


async def test_batcher_dispatches_an_oversized_request_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap bounds COALESCING, never a caller: a single request larger than
    it is still answered in one piece rather than split across two passes with
    two different latencies."""
    fake = _FakeEncoderWithTokenizer(dim=1)
    _loaded(monkeypatch, fake)
    batcher = service_app._Batcher(window_s=0.005, max_texts=2)

    ((vectors, _),) = await _with_batcher(batcher, [["a", "b", "c", "d", "e"]])

    assert len(vectors) == 5
    assert fake.encode_calls[0]["sentences"] == ["a", "b", "c", "d", "e"]


async def test_a_failed_batch_fails_every_waiter_in_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ A batch is a service-side detail. A caller that happened to share
    one with a failing request must still get an answer -- and must never be
    left waiting on a future nobody will set."""

    class _BrokenEncoder(_FakeEncoder):
        def encode(self, sentences: list[str], **kwargs: Any) -> list[list[float]]:
            time.sleep(0.05)
            raise RuntimeError("cuda is on fire")

    _loaded(monkeypatch, _BrokenEncoder())
    batcher = service_app._Batcher(window_s=0.005, max_texts=32)

    task = asyncio.create_task(batcher.run())
    try:
        results = await asyncio.gather(
            *(batcher.submit([f"q{i}"]) for i in range(4)), return_exceptions=True
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await batcher.close()

    assert len(results) == 4
    assert all(isinstance(result, RuntimeError) for result in results)


async def test_close_answers_whatever_is_still_queued_at_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown must CLOSE every open request, not abandon it -- a queued
    waiter whose consumer has been cancelled would otherwise hang until the
    client's own timeout."""
    _loaded(monkeypatch, _FakeEncoder())
    batcher = service_app._Batcher(window_s=0.005, max_texts=32)
    # No `run()` task at all: this is precisely the post-cancellation state.
    pending = asyncio.ensure_future(batcher.submit(["a"]))
    await asyncio.sleep(0)

    await batcher.close()

    with pytest.raises(HTTPException) as caught:
        await pending
    assert caught.value.status_code == 503


def test_window_zero_builds_no_batcher_and_keeps_the_inline_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `م-8` kill switch: the same image, measurable as its own baseline.
    One `encode` per request, and `/health` says so from outside."""
    fake = _FakeEncoder(dim=2)
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_DIM", "2")
    monkeypatch.setenv("EMB_BATCH_WINDOW_MS", "0")

    with TestClient(service_app.app) as client:
        assert (
            client.post("/embed", json={"texts": ["a"], "model": "test-model"}).status_code == 200
        )
        assert (
            client.post("/embed", json={"texts": ["b"], "model": "test-model"}).status_code == 200
        )
        reported = client.get("/health").json()

    assert service_app._state.batcher is None
    assert [call["sentences"] for call in fake.encode_calls] == [["a"], ["b"]]
    assert reported["batch_window_ms"] == 0


def test_health_reports_the_batching_knobs_actually_in_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEncoder()
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMB_BATCH_WINDOW_MS", "12")
    monkeypatch.setenv("EMB_MAX_BATCH_TEXTS", "64")

    with TestClient(service_app.app) as client:
        reported = client.get("/health").json()

    assert reported["batch_window_ms"] == 12
    assert reported["max_batch_texts"] == 64


def test_the_batched_route_still_answers_one_request_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default path (window 5 ms) through the real route -- a lone request
    pays the window and is answered normally, off the event loop."""
    fake = _FakeEncoderWithTokenizer(dim=3)
    monkeypatch.setattr(service_app, "_load_model", _fake_loader(fake))
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_DIM", "3")

    with TestClient(service_app.app) as client:
        payload = client.post("/embed", json={"texts": ["ab", "cde"], "model": "test-model"}).json()

    assert payload["vectors"] == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    assert payload["tokens"] == 2 + 3


# --------------------------------------------------------------------------- #
# _token_counts -- pure, no HTTP                                              #
# --------------------------------------------------------------------------- #
def test_token_counts_reports_one_entry_per_text() -> None:
    fake = _FakeEncoderWithTokenizer()
    assert service_app._token_counts(fake, ["ab", "abc"]) == [2, 3]


def test_token_counts_falls_back_when_the_tokenizer_returns_the_wrong_shape() -> None:
    class _WrongShape(_FakeEncoder):
        def tokenize(self, texts: Sequence[str]) -> dict[str, list[list[int]]]:
            return {"attention_mask": [[1, 1]]}  # one row for two texts

    assert service_app._token_counts(_WrongShape(), ["hello world", "x"]) == [2, 1]
