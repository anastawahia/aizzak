"""Live proof that the central embedding service reads a WHOLE chunk
(``services/embedding/app.py``, Phase 2.10 -- ``rag-fidelity-audit.md`` §3-د).

**The defect this file exists to keep closed.**
``SentenceTransformer(model_name)`` adopts the checkpoint's own
``sentence_bert_config.json``, and for the baked
``paraphrase-multilingual-MiniLM-L12-v2`` that file says
``max_seq_length: 128`` -- while the same checkpoint's ``config.json`` says
``max_position_embeddings: 512``. Nothing announces the gap. Text past token
128 was dropped *inside* ``model.encode``, with no error, no warning and a
perfectly well-formed 384-dimension vector coming back out, so a chunk sized
by the indexer to a 512-token budget
(``knowledge/domain/chunking.py::max_words_for_token_limit``) had roughly
**four fifths of its Arabic content**  never reach its own vector. It is
``P-16``'s silent-truncation defect one layer below where ``P-16`` looked.

**Why a live test and not a unit test.** The unit suite
(``tests/unit/test_embedding_service_app.py``) can prove the WIRING -- that
the lifespan passes 512 to the loader and that the loader applies it -- but
it fakes the encoder precisely so ``torch``/``sentence_transformers`` stay
out of the dev/CI venv. Only a real model can answer whether a real long
text survives to its vector, and only the deployed image can answer whether
THIS build was configured. Both are asserted below, by measurement:

1. ``GET /health`` publishes the cut point in force -- the check an operator
   can run against any deployed instance.
2. ``POST /embed`` reports a token count ABOVE 128 for a long text. This is
   direct evidence rather than inference: ``SentenceTransformer.tokenize``
   truncates at ``max_seq_length``, so the service's own ``tokens`` field is
   the post-truncation count. Against the pre-fix image it reads exactly 128
   for any long input, and cannot read more.
3. Two texts that are IDENTICAL for their first ~200 words and differ only
   afterwards embed to DIFFERENT vectors. Under the 128-token cut both
   collapse to the same truncated prefix and the two vectors are bit-for-bit
   identical -- the sharpest statement of what the defect actually cost:
   distinct chunks with a shared opening were, to retrieval, one chunk.

⚠️ Requires the image REBUILT since the fix (``docker compose build
embedding``). Against a stale container these fail rather than skip, which
is the intended reading: the handshake proves a service is there, and these
prove it is the fixed one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.live_embedding]

_TIMEOUT_S = 60.0

# The checkpoint's packaged default -- the number this whole file is about.
_CHECKPOINT_DEFAULT_MAX_SEQ_LEN = 128

# ~200 words of ordinary Arabic prose. Sized deliberately between the two
# limits: at the measured ~1.69 tokens/word it is ~340 tokens, so it is far
# past the 128-token cut and still comfortably inside the 512-token ceiling
# -- a text the fixed service reads whole and the broken one reads a fifth
# of. Arabic on purpose: it is the corpus's own language and the ratio that
# makes the truncation worst.
_PREFIX = " ".join(["تتناول هذه الفقرة وصفًا تفصيليًّا لإجراءات التشغيل المعتمدة في القسم"] * 25)


def _post_embed(client: httpx.Client, texts: list[str], model: str) -> dict[str, Any]:
    response = client.post("/embed", json={"texts": texts, "model": model})
    response.raise_for_status()
    return response.json()


def _served_model(client: httpx.Client) -> str:
    """The model name this instance actually loaded -- read off ``/health``
    rather than hard-coded, because ``/embed`` 400s on any mismatch and a
    test that pins the name itself would report a drift as its own failure."""
    return str(client.get("/health").json()["model"])


@pytest.fixture
def client(live_embedding: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=live_embedding, timeout=_TIMEOUT_S, trust_env=False) as c:
        yield c


def test_health_publishes_the_sequence_length_in_force(client: httpx.Client) -> None:
    """The operator-facing half: the cut point is visible from outside the
    container. Its absence is the reason a 128 sat in production unnoticed."""
    body = client.get("/health").json()

    assert "max_seq_length" in body, "this image predates the §3-د fix; rebuild it"
    assert body["max_seq_length"] > _CHECKPOINT_DEFAULT_MAX_SEQ_LEN
    assert body["max_seq_length"] == 512


def test_a_long_text_is_tokenised_past_the_checkpoints_own_default(
    client: httpx.Client,
) -> None:
    """Measured, not inferred: ``tokens`` is counted AFTER truncation, so a
    number above 128 is the model itself reporting that it read further."""
    body = _post_embed(client, [_PREFIX], _served_model(client))

    assert body["tokens"] > _CHECKPOINT_DEFAULT_MAX_SEQ_LEN, (
        f"tokens={body['tokens']} -- the model is still cutting at its "
        f"checkpoint default; this container is running the pre-fix image"
    )
    assert body["tokens"] <= 512


def test_two_chunks_sharing_a_long_opening_do_not_collapse_to_one_vector(
    client: httpx.Client,
) -> None:
    """What the truncation actually cost retrieval: everything past token 128
    was invisible, so two chunks with a common opening were the same point in
    the vector space. Different tails must now produce different vectors."""
    model = _served_model(client)
    body = _post_embed(
        client,
        [
            f"{_PREFIX} وتُصرف المستحقّات المالية في نهاية كلّ شهر ميلاديّ",
            f"{_PREFIX} ويُمنح الموظّف إجازة سنوية مدّتها ثلاثون يومًا",
        ],
        model,
    )
    first, second = body["vectors"]

    assert first != second, (
        "identical vectors for texts that differ only after their shared "
        "opening -- the tail never reached the encoder"
    )
    # Both are L2-normalised (`normalize_embeddings=True`), so their dot
    # product IS the cosine: near-1.0 would mean the difference registered
    # only as float noise rather than as real, differing content.
    cosine = sum(a * b for a, b in zip(first, second, strict=True))
    assert cosine < 0.999
