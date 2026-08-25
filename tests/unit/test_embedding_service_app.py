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

from collections.abc import Sequence
from typing import Any

import pytest
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
    yield
    service_app._state.model = None
    service_app._state.model_name = ""


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
