"""Non-secret configuration contract (DD-11, 05-rbac-config-secrets §2).

``Settings`` is the *only* way the rest of the code sees configuration. It is
immutable and secret-free: passwords, service accounts and provider keys live
in Vault and are resolved through ``SecretsProvider`` at runtime, never here.
The concrete loader (``infrastructure/config``) is the sole reader of ``.env``
and builds this object; everything else receives it via injection.

Numeric limits mirror 07-nfr-slo §4 (approved under OQ-02); they are published
here as configuration, not hard-coded at call sites.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.framework.types import Json

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class DatabaseSettings(BaseModel):
    model_config = _FROZEN

    # Reaches Postgres via PgBouncer (transaction pooling). ``statement_cache_size``
    # MUST be 0 under PgBouncer transaction pooling (OPS-02).
    url: str = "postgresql+asyncpg://app@pgbouncer:6432/app"
    pool_size: int = 10
    max_overflow: int = 20
    statement_cache_size: int = 0


class RedisSettings(BaseModel):
    model_config = _FROZEN

    url: str = "redis://redis:6379/0"


class MetricsSettings(BaseModel):
    """The ``/metrics`` endpoint's OWN Postgres connection (P1-3,
    docs/p1-hardening-plan.md §3 step 10) -- a SEPARATE DSN from
    ``database.url``, never a widened ``app_rw``.

    ``app_rw`` is deliberately INSERT-only on ``platform.outbox``
    (``app.ops.provision``'s own docstring: "a role that can only INSERT can
    neither enumerate the ledger nor un-process an event to force a
    replay"), so the API process cannot answer "how old is the oldest
    unpublished row" over its own main connection at all. This is a FIFTH
    least-privilege role (``metrics_reader``, ``app.ops.provision``), the
    ``outbox_relay``/``retention_sweeper`` precedent applied to a fourth
    distinct job -- read-only observability -- rather than a sixth exception
    carved into ``app_rw``'s own grant.
    """

    model_config = _FROZEN

    database_url: str = "postgresql+asyncpg://metrics_reader@pgbouncer:6432/app"


# SigV4's hard ceiling on a presigned URL's lifetime (7 days, in seconds).
# minio-py enforces exactly this range and refuses to sign outside it.
_MAX_PRESIGN_TTL_S = 604_800


class MinioSettings(BaseModel):
    model_config = _FROZEN

    endpoint: str = "minio:9000"
    bucket: str = "workspace-files"
    secure: bool = False

    # The address a PRESIGNED URL must name (7.1). ``endpoint`` is where the
    # server reaches MinIO -- inside the Compose network (08 §2) that is the
    # service name ``minio:9000``, which no end-user browser can resolve.
    # SigV4 signs the host, so a presigned URL cannot be host-rewritten after
    # the fact: it has to be SIGNED against the address the client will use.
    # Empty => no split deployment, sign against ``endpoint`` (the pre-7.1
    # behaviour, and what the live test harness keeps getting).
    public_endpoint: str = ""
    # ``None`` => inherit ``secure``. Explicit tri-state rather than a bare
    # ``bool`` default, because the public hop is exactly where TLS commonly
    # DIFFERS from the internal one (plaintext inside the network, https at
    # the edge) and a silent ``False`` would sign the wrong scheme.
    public_secure: bool | None = None

    # Pinned so presigning stays a PURELY LOCAL computation. minio-py resolves
    # a bucket's region with a live GetBucketLocation call whenever it does not
    # already know it -- and it makes that call against the CLIENT'S OWN
    # endpoint. For the signing client that endpoint is the public address,
    # which the server generally cannot reach (in the Compose topology
    # `localhost:19000` from inside a container is the container itself), so
    # every presign attempt died on a connection error before signing anything.
    # SigV4 covers the region, so it cannot simply be omitted; naming it is
    # what removes the round trip. "us-east-1" is MinIO's own default.
    region: str = "us-east-1"

    # Presigned-URL lifetimes (3.79). These were a pair of module constants in
    # `modules/files/application/use_cases.py`, refused as settings at the time
    # because 05 §2 defined no key for them; 05 §2 now does, so they live where
    # every other MinIO knob does. PUT is longer than GET because the client
    # still has to move the bytes; GET only has to be clicked.
    #
    # The bounds are the SIGNER'S OWN, not a taste judgement: SigV4 presigning
    # accepts 1s..7d and minio-py raises `ValueError` outside that range
    # (`minio/api.py`), so an out-of-range value is not merely unwise — it is
    # unsignable, and every upload registration would fail at runtime. `ge`/`le`
    # turn that into a boot failure, which is the whole point of validating
    # here rather than discovering it on the first `POST /files`.
    presign_put_ttl_s: int = Field(default=900, ge=1, le=_MAX_PRESIGN_TTL_S)
    presign_get_ttl_s: int = Field(default=300, ge=1, le=_MAX_PRESIGN_TTL_S)

    @property
    def signing_endpoint(self) -> str:
        """The host presigned URLs are signed against -- the ONE place the
        public/internal fallback is decided."""
        return self.public_endpoint or self.endpoint

    @property
    def signing_secure(self) -> bool:
        return self.secure if self.public_secure is None else self.public_secure


class QdrantSettings(BaseModel):
    model_config = _FROZEN

    url: str = "http://qdrant:6333"


class VaultSettings(BaseModel):
    model_config = _FROZEN

    addr: str = "http://vault:8200"
    # AppRole id is non-secret (05 §2); the secret_id is injected as a secret.
    role_id: str | None = None


class FirebaseSettings(BaseModel):
    model_config = _FROZEN

    project_id: str = ""
    jwks_cache_ttl: int = 3600


class AuthSettings(BaseModel):
    """The authentication PATH's own knobs — not the identity provider's.

    Separate from ``FirebaseSettings`` because that models the external issuer
    (which project, how long its keys are cached) while this models what THIS
    platform does with a verified identity. A deployment could swap the issuer
    and keep every number here.

    ``principal_cache_ttl_s`` is capacity-plan step 1.1's knob. **Zero means
    the cache is not built at all** — not "a zero-second TTL": the Composition
    Root wires ``None`` and the authentication path makes no Redis call for it
    whatsoever, which is what a baseline run needs (`م-8` — a measurement taken
    with the optimisation half-installed answers nothing).
    """

    model_config = _FROZEN

    principal_cache_ttl_s: int = 60


class OllamaSettings(BaseModel):
    model_config = _FROZEN

    # The platform's sole local LLMProvider (DD-13, 2.8-a): no API key, no
    # cloud credential -- just a reachable base URL.
    base_url: str = "http://ollama:11434"


class EmbeddingServiceSettings(BaseModel):
    """The central embedding service (2.10, refs ``llm-providers.md`` §5):
    ONE model load behind a small internal HTTP API
    (``services/embedding/app.py``), reached over ``url`` from the
    ``ExternalEmbeddingProvider`` adapter. Only ``url`` is env-editable
    (DD-11, ``infrastructure/config/env_settings.py``'s own docstring) --
    ``model``/``dimensions``/``batch``/``timeout_s``/``max_retries`` are
    pinned defaults that MUST match the baked image
    (``services/embedding/Dockerfile`` bakes exactly this model at build
    time): an env-editable ``dimensions`` would silently break every
    collection provisioned against it (``dim=384``, ``distance=cosine``).
    """

    model_config = _FROZEN

    url: str = "http://embedding:8080"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dimensions: int = 384
    batch: int = 8
    timeout_s: float = 15.0
    max_retries: int = 2
    # rag-indexing-plan.md §3.5 + §4 step 9 (`P-16`, decision س-11): the real
    # token budget a chunk must fit under before embedding, pinned here
    # rather than read off `EmbeddingProvider` (ح-6/ح-7, plan §2) -- the
    # adapter only ever estimates `len(text)//4`
    # (``external_embedding.py``), and one release serves exactly one baked
    # model (this class's own docstring), so the true limit is a fact about
    # THIS deployment, same footing as `dimensions` above. `domain/
    # chunking.py::max_words_for_token_limit` is the pure consumer -- this
    # value is passed to it as a plain argument, never read from Settings
    # inside the domain layer.
    #
    # This is one HALF of a single ceiling. The other half is the embedding
    # service's own `EMB_MAX_SEQ_LEN` (`services/embedding/app.py`,
    # `docker-compose.yml`), the token at which the model stops reading; the
    # two must stay equal, and NOTHING enforces that -- unlike a model-name
    # mismatch, which the service answers with a loud 400, a drift here is
    # silent on both sides. `GET /health` reports the value in force.
    embedding_max_input_tokens: int = 512


class RerankServiceSettings(BaseModel):
    """The cross-encoder rerank service (rag-retrieval-plan.md §3.10,
    ``P-24``, decision س-21): WHERE the reranker is and how patiently to wait
    for it. WHETHER to use it at all is ``RetrievalSettings.rerank_enabled``
    below — a retrieval decision, so it rides row 18's tuning seam with every
    other retrieval knob, while this class is the transport the way
    ``EmbeddingServiceSettings`` is.

    **Nothing here loads a model.** §3.10's rule is "لا أوزان داخل صورة
    العامل", and ``url`` is the whole of how it is kept: the reranker is a
    separate HTTP deployable, reached by ``ExternalRerankProvider`` exactly
    as ``ollama`` and ``embedding_service`` are reached, so no cross-encoder
    weight and no torch dependency enters the API or worker image.

    ``model`` is pinned here rather than resolved per call — this deployment
    talks to exactly one rerank service serving exactly one model, the same
    fact ``EmbeddingServiceSettings.model``/``.dimensions`` pin, and no
    ``PROVIDER_ROUTING`` namespace routes a rerank.

    ⚠️ ``timeout_s`` is DELIBERATELY short and ``max_retries`` DELIBERATELY
    zero, and both are §6 risk ٦ ("مُعيد الترتيب يبطّئ كلّ طلب") answered in
    numbers. Reranking is an accuracy improvement the pipeline can always do
    without: a retry would spend a second helping of the user's latency on an
    OPTIONAL stage, and a long timeout would let a sick service hold every
    answer hostage. One attempt, briefly, then retrieval carries on with the
    order it already had (``RetrieveContext._rerank``).

    Not env-editable, like ``RetrievalSettings`` and ``Limits`` beside it —
    05-rbac-config-secrets §2 owns the flat env-key list and widening it is a
    configuration-contract decision of its own (recorded in the plan's §7).
    """

    model_config = _FROZEN

    url: str = "http://rerank:8080"
    model: str = "BAAI/bge-reranker-v2-m3"
    timeout_s: float = 5.0
    max_retries: int = 0


class EventSettings(BaseModel):
    model_config = _FROZEN

    stream_prefix: str = "stream."
    outbox_poll_interval_ms: int = 500
    consumer_block_ms: int = 5000
    max_retries_before_dlq: int = 5
    # 5.1-ب: rows per `outbox_relay` fetch/publish cycle
    # (``infrastructure/messaging/outbox.py::OutboxRelay``). ``ge=1`` —
    # a batch of zero would make `run_once` indistinguishable from "nothing
    # to publish" by construction, which is not a configuration this system
    # has a sensible meaning for.
    outbox_relay_batch_size: int = Field(default=256, ge=1)
    # 5.1-ج: `COUNT` for every worker's `XREADGROUP`
    # (``infrastructure/messaging/consumers/engine.py::StreamConsumer``) —
    # the `outbox_relay_batch_size` precedent, same `ge=1` reasoning (a
    # batch of zero is not a meaningful "how many entries per read" value).
    consumer_batch_count: int = Field(default=16, ge=1)
    # 7.3: `XADD ... MAXLEN ~ N` — the cap on a stream's retained length.
    #
    # Redis Streams are an APPEND-ONLY log: `XACK` clears a consumer group's
    # pending list, it does NOT delete the entry. So every `stream.<module>`
    # grows without bound for as long as the relay publishes — with all three
    # workers running and acking perfectly, not merely while 7.2 leaves them
    # unable to boot. Measured on the live stack, there is no backstop
    # underneath either: `maxmemory 0`, policy `noeviction`.
    #
    # ⚠️ This is a RETENTION bound, and trimming can drop entries a stalled
    # consumer has not read yet. The outbox row is already marked published
    # by then, so such an event is genuinely lost rather than replayed —
    # which is why the default is generous rather than tight, and why 08 §7
    # tells operators to watch `XLEN` instead of treating this as a solution.
    #
    # `None` disables trimming and is the pre-7.3 behaviour byte for byte;
    # `STREAM_MAXLEN=0` resolves to it.
    stream_maxlen: int | None = Field(default=100_000, ge=1)

    # ت-2 (`docs/operational-findings.md` §2): how often a worker tidies the
    # tombstones other processes left in its own groups, and how idle a
    # consumer must be before it counts as one. `0` disables either half.
    #
    # ⚠️ `consumer_stale_idle_s` is a DEATH threshold, not a cadence. A live
    # worker resets its idle clock every `consumer_block_ms` (5 s), so any
    # value above ~1 min already separates a working sibling from a corpse;
    # the default is two orders of magnitude above the block interval because
    # the cost of waiting is only tidiness, while the cost of being wrong is
    # deleting a live replica's registration. It must stay well ABOVE
    # `consumer_block_ms` -- that relation, not the absolute number, is what
    # makes the sweep safe under multiple replicas (`consumers/sweeper.py`).
    consumer_sweep_interval_s: float = Field(default=300.0, ge=0)
    consumer_stale_idle_s: float = Field(default=900.0, ge=0)
    # ت-2's second layer, inside the API process: how often to destroy
    # `cg.notify.<host>.<pid>` groups whose host no longer exists. Slower
    # than the consumer sweep on purpose -- an orphaned GROUP holds no
    # messages (the rule refuses any group with pending entries), so the only
    # thing urgency would buy is a shorter window of noisy `XINFO GROUPS`
    # output. `0` disables it, leaving only the startup sweep and the
    # operator tool (`app.ops.notify_groups`).
    notify_group_sweep_interval_s: float = Field(default=900.0, ge=0)

    # ت-6 (`docs/operational-findings.md` §6): how often each worker reports
    # what is parked on the DLQs of the streams it consumes
    # (`consumers/dlq_watch.py`). `0` disables it, leaving the DLQ observable
    # only by an operator who thinks to run `python -m app.ops.dlq peek` --
    # which is precisely the state ت-6 recorded.
    #
    # ⚠️ Unlike the two sweeps above, this knob costs nothing to make small
    # and buys nothing by being large: the read is `XLEN` + a one-entry
    # `XRANGE` per stream, it deletes nothing, and it logs nothing at all
    # while the queues are empty (the common case). The default matches the
    # `for: 5m` on `AizzakDlqNotEmpty` (`deploy/prometheus/alerts.yml`) so the
    # log line and the future alert describe a backlog on the same cadence.
    dlq_watch_interval_s: float = Field(default=300.0, ge=0)


class HealthSettings(BaseModel):
    """Liveness reporting for the processes that have no HTTP listener to
    probe (ت-3, ``docs/operational-findings.md`` §3): the three ``worker-*``
    consumers and ``outbox-relay``. See
    ``framework/observability/heartbeat.py`` for why a file's mtime, and
    ``app/ops/healthcheck.py`` for the reader."""

    model_config = _FROZEN

    # Empty string = disabled, and that is a REAL configuration rather than an
    # oversight: a `python -m app.workers.memory_worker` run straight from a
    # developer's shell has no Docker healthcheck watching it, so the file is
    # pure litter there. `app.ops.healthcheck` refuses to report healthy when
    # this is empty (exit 2) instead of passing vacuously.
    heartbeat_dir: str = "/tmp/aizzak-heartbeat"
    # ⚠️ TOLERANCE, not cadence. A worker beats once per completed cycle --
    # every `consumer_block_ms` (5 s) when idle -- so any value here above ~15 s
    # detects a wedged loop; the default is two orders of magnitude larger for
    # ONE reason: a beat cannot happen while a single handler is still running,
    # and a legitimate handler may run for `Limits.media_timeout_s` (300 s).
    # A threshold that flags a worker mid-job as unhealthy would teach
    # operators to ignore the column -- the same signal-destroying pattern ت-6
    # records for a permanently non-empty DLQ. Detection is still bounded:
    # 300 s here plus Compose's `retries x interval` surfaces a dead loop in
    # about six minutes, against the "never" it replaces. Services whose
    # handlers cannot run that long may lower it; `worker-media` raises it.
    heartbeat_max_age_s: int = Field(default=300, ge=1)


class IntegrationsSettings(BaseModel):
    model_config = _FROZEN

    oauth_redirect_base_url: str | None = None
    # Remote transports only in v1 (ARC-15); local stdio MCP is out of scope.
    mcp_allowed_transports: tuple[str, ...] = ("http", "sse")
    oauth_refresh_skew_s: int = 60


class UsageSettings(BaseModel):
    model_config = _FROZEN

    rollup_periods: tuple[str, ...] = ("day", "month")
    default_limits: Json = Field(
        default_factory=lambda: {
            "tokens": {"month": 5_000_000},
            "cost_micros": {"month": 50_000_000},
        }
    )


class SparseSettings(BaseModel):
    """Okapi BM25's shape parameters — the sparse leg's scoring model
    (rag-fidelity-audit.md §3-ج, closed 2026-08-27).

    **Not in ``RetrievalSettings``, and the distinction is not cosmetic.**
    Every knob there is read at QUERY time and can be swept by
    ``tests/eval/run_calibration.py`` against a corpus that never moves.
    These three are baked into the stored sparse vector at INDEX time
    (``domain/sparse.py::build_document_terms``), so sweeping them means
    re-indexing between variants — and filing them next to ``min_bm25_score``
    would invite exactly the sweep that silently measures nothing.
    ``PIPELINE_VERSION`` is what makes a change here reach existing documents.

    The query side takes no parameters at all: the whole of ``k1``, ``b`` and
    ``|d|`` is a property of the document, and Qdrant's ``Modifier.IDF``
    supplies the only factor neither side computes.
    """

    model_config = _FROZEN

    # Term-frequency saturation. A term's weight approaches `k1 + 1`, so the
    # tenth occurrence of a word adds almost nothing over the ninth. Raw `tf`
    # -- what shipped before -- is `k1 = ∞`, and the audit caught it in the
    # act: 185 pairs of positive scores in exact whole-number ratios ≥ 2,
    # `7.800722 = 2 x 3.900361` among them. 1.5 is mid-range of the
    # 1.2-2.0 the literature settles on.
    bm25_k1: float = 1.5
    # Length normalisation, 0 (none, the old behaviour) to 1 (full).
    # Robertson's 0.75, unmodified: this corpus gave no reason to move it,
    # and moving an untested default off the field's own value would make a
    # second thing to explain if the sparse leg misbehaves.
    bm25_b: float = 0.75
    # The corpus's mean `|d|` in SPARSE TERMS -- post-tokenisation,
    # post-stopword, counted with repetition. NOT words and NOT characters:
    # a 192-word chunk measured 133 terms.
    #
    # ⚠️ **MEASURED, and measured on a PROXY.** The 1731-chunk corpus the
    # audit's §4-و numbers came from was wiped on 2026-08-16, so this was
    # taken on `docs/hr-no-table.docx` through the real
    # `parse_docx` -> `chunk_segments` -> `build_sparse_terms` path: 110
    # prose chunks, mean 34.4 terms (median 29, p90 66, max 114), against a
    # table-row mean of 8.4. At the audit's measured 45.8% table-row share
    # that mixes to ~22.5.
    #
    # 32.0 and not 22.5 because the error directions are not symmetric.
    # Too HIGH under-corrects, which is the behaviour that shipped for months;
    # too LOW over-penalises long prose in a corpus that is nearly half
    # single-line table rows, which would be a NEW failure. So this sits at
    # the prose mean, erring toward the known quantity.
    #
    # **Re-measure it against the rebuilt corpus, together with
    # `RetrievalSettings.min_bm25_score`** — they share a scale, and
    # `Bm25Params.weight` is anchored at 1.0 precisely so that today's floor
    # keeps the meaning its own sweep measured.
    #
    # FastEmbed's 256.0 default is not usable here: a chunk cannot exceed 192
    # words, so 256 terms is past this pipeline's ceiling and would leave
    # `|d|/avg_len < 1` for every document in the corpus -- normalisation that
    # only ever boosts, which is not normalisation.
    bm25_avg_len: float = 32.0


class RetrievalSettings(BaseModel):
    """Hybrid-retrieval tuning (rag-retrieval-plan.md §4 row 18, `P-30`
    `P-40`, decision س-24 = أ).

    **Every retrieval knob that used to be a module constant in
    ``knowledge/application/retrieval.py`` lives here**, and reaches the code
    that uses it as a plain ARGUMENT: the Composition Root maps this object
    onto ``RetrieveContext``'s ``RetrievalTuning`` value object, and the
    use-case passes the individual numbers down into the pure domain
    algorithms (``reciprocal_rank_fusion``, ``filter_relevant``,
    ``fit_to_context_budget``). Nothing in ``modules/*/domain`` or
    ``modules/*/application`` reads ``Settings`` or ``os.environ`` to find
    them — that is the whole of س-24, and the ``embedding_max_input_tokens``
    /``ocr_*``/``parser_*`` precedents above are the same shape.

    **There is no per-request override of any of these, by decision** (س-24
    rejected option ب) and no admin endpoint that writes them live (option ج
    — writable global state needing RBAC, auditing and tenant isolation;
    recorded in the plan's §7). Changing one is a redeploy.

    ✅ **The floors carry NUMBERS since 2026-08-27** (س-22 closed by owner
    decision on `P-38`'s evaluation set — 15 questions with reference answers
    over a real handbook, asked in both languages, plus 6 questions the corpus
    provably cannot answer; docs/rag-fidelity-audit.md §4-و). §3.8's rule
    ("الآليّة تُشحَن والأرقام لا") is satisfied, not waived: the numbers below
    are measured, and the two that stayed ``0.0`` stayed there BECAUSE the
    measurement says no number belongs on their scale — not because none was
    tried.

    ⚠️ **Three scales, three separate calibrations, and no number crosses
    between them.** The dense leg is Qdrant cosine over ``[-1, 1]``; the
    sparse leg is an unbounded IDF-weighted dot product; the fused floors live
    on the RRF score (``Σ w/(60+rank)``). A value measured on one says nothing
    about another, and that is the same trap alpha fell into by calibrating
    against FAISS L2 DISTANCE (lower is nearer) — plan §6 risk #3. Each
    field's own comment carries the evidence for ITS scale and no other.

    ``jaccard_threshold`` is untouched by all of it: ``0.95`` is alpha's
    single SCALE-INDEPENDENT constant (plan fact ح-17), a set-overlap ratio
    rather than a score.

    Not env-editable, exactly like ``Limits`` below: 05-rbac-config-secrets
    §2 owns the flat env-key list (``infrastructure/config/env_settings.py``
    is its sole reader), and widening it is a configuration-contract decision
    of its own rather than part of this sweep.
    """

    model_config = _FROZEN

    # RRF fusion (`domain/fusion.py` does NOT normalize internally, so these
    # two already sum to 1.0) and its rank constant.
    weight_dense: float = 0.5
    weight_bm25: float = 0.5
    rrf_k: int = 60
    # How many raw hits EACH leg fetches from the vector store, as a multiple
    # of the caller's `k` -- a search-RECALL concern, unrelated to how many
    # FUSED candidates survive (`fusion_retention` below), which is why the
    # two are separate knobs even though both default to 3.
    search_overfetch: int = 3
    # Absolute ceiling on any single leg's fetch depth, whatever `k *
    # search_overfetch` works out to.
    max_search_candidates: int = 100
    # Ceiling on the BM25-sparse leg's fetch depth ALONE (plan step 16,
    # `P-27`). A cap on a COUNT, never on a score, so س-22 does not reach it;
    # `20` is alpha's own sparse candidate count, and a count is the one class
    # of alpha number that survives the L2 -> cosine direction flip untouched.
    # The dense leg is deliberately NOT capped in the same way (`P-27` names
    # the sparse leg only).
    max_sparse_candidates: int = 20
    # Diversity retention past RRF, as a multiple of `k` (plan step 8,
    # `P-26`): keeping 3x gives the parent-expansion step enough distinct
    # candidates to fill the final `k` with distinct sections.
    fusion_retention: int = 3
    # The `k` used when a caller names none (plan step 18, `P-40`) -- the
    # number that used to be `rag_agent.agent._TOP_K = 5`. `POST
    # /knowledge/search`'s own `k` is unaffected: that is a request's result
    # SIZE on a published contract (03 §2), not a retrieval tuning override.
    default_k: int = 5
    # Per-leg absolute score floors, on two DIFFERENT scales (plan step 16,
    # `P-27`): cosine in [-1, 1] for the dense leg, an unbounded IDF-weighted
    # dot product for the sparse one. `0.0` = disabled by an explicit branch
    # in `_gate_by_score`, never by arithmetic.
    #
    # ⚠️ **BOTH SHIP DISABLED AGAIN as of 2026-08-30 (owner decision), and
    # the evidence below is kept as the RECORD OF A MEASUREMENT, not as a
    # description of what runs.** The numbers were sound; the corpus they
    # were measured on is gone. `tests/eval/README.md` names it: ONE space
    # holding `hr-no-table.docx` (221 chunks, the answerable document) plus
    # `criteria.pdf` (811 chunks) as the deliberate DISTRACTOR. That space
    # now holds `criteria.pdf` alone, re-indexed to 771 chunks — so the only
    # document left is the one both floors were fitted to REJECT, and every
    # question about it lands in the band the sweep recorded for
    # unanswerable questions (dense `[0.2623, 0.6492]`). Measured live on
    # 2026-08-30: `best_dense_score` 0.40547 against a 0.45 floor and
    # `best_bm25_score` 6.30028 against a 25.0 one, both legs gated to zero
    # hits, `fused_count` 0, and the `P-33` trust gate answering a perfectly
    # answerable question with «I don't have enough information».
    #
    # The sparse floor could not have survived the corpus change in any
    # case, and that is the more general lesson: a raw BM25 score is a
    # function of the corpus's own IDF and mean document length, so 25.0 is
    # not a quantity that TRANSFERS. On 771 chunks the largest reachable IDF
    # is about `ln(1 + 771/1.5) ≈ 6.2`, which even at the `k1 + 1 = 2.5`
    # weight ceiling caps a single rare term near 15.6 — under the floor
    # before the query is even asked. `SparseSettings.bm25_avg_len`'s own
    # note ("re-measure it against the rebuilt corpus, together with
    # `min_bm25_score` — they share a scale") predicted exactly this.
    #
    # ⚠️ Disabling the sparse floor REINSTATES the defect it repaired: the
    # zero-scored padding hits described below vote in RRF with the weight
    # of a real hit. That cost is accepted here with open eyes; the fix for
    # it is a floor calibrated on the corpus that is actually indexed, not
    # this one.
    #
    # ── the DENSE floor · cosine ──────────────────────────────────────────
    # `0.45`, calibrated on `P-38`'s set (2026-08-27) and chosen with MARGIN
    # rather than at the frontier:
    #   * the lowest `best_dense_score` any ANSWERABLE question produced is
    #     0.5243 -- so 0.45 sits 14% below the closest thing to a cliff the
    #     measurement found;
    #   * the first FALSE REFUSAL (an answerable question returning zero
    #     chunks) appears at 0.55, so 0.45 is 18% under the failure itself;
    #   * at 0.45 the set keeps 30/30 answers and 4 of 12 unanswerable
    #     questions come back with zero chunks -- which is the trust gate
    #     (`P-33`) firing for the first time on a non-empty corpus.
    # The frontier (0.52, closing 7 of 12) was measured and REJECTED: it
    # touches 0.5243 with no margin at all, and the response surface is not
    # monotone (one question survives a sparse floor of 21 and 10 but not 15),
    # so a value fitted to the edge of 30 samples is not a calibration.
    # ⚠️ Measured on an English corpus asked in BOTH languages, i.e. the
    # HARDEST case for this floor: a same-language corpus scores higher on
    # cosine, which widens the margin rather than narrowing it.
    # ⚠️ 0.0 (disabled) since 2026-08-30 — see the block above.
    min_dense_score: float = 0.0
    # ── the SPARSE floor · IDF-weighted dot product ───────────────────────
    # `25.0`, and this one repairs a measured DEFECT rather than trading
    # quality away. Qdrant answers a filtered sparse query whose terms appear
    # nowhere in the corpus with `k` arbitrary points scored EXACTLY 0.0 --
    # measured at 362 such hits across 42 probes, and for an Arabic question
    # over an English corpus the whole 20-deep leg is zeros. RRF reads RANK,
    # not score, so those zeros used to vote with exactly the weight of the
    # dense leg's real hits: 4 of the 5 chunks delivered for one Arabic
    # question came from an unrelated document, one of them a 26-character
    # fragment. Any floor above 0 removes them and nothing else -- the
    # smallest POSITIVE score in the whole corpus is 1.406.
    # 25.0 rather than a token epsilon because the sweep found [21, 30] to be
    # one stable plateau (identical outcomes at every dense floor tested) and
    # 25 is its middle, while 15 and 21 sit on edges. It costs no recall: the
    # two legs back each other up, so emptying the sparse leg of a question
    # the dense leg answers changes nothing, and 30/30 holds at every sparse
    # floor from 0.01 to 30.
    # ⚠️ 0.0 (disabled) since 2026-08-30 — see the block above.
    min_bm25_score: float = 0.0
    # `domain/relevance.py`'s own two floors, on a THIRD scale entirely -- the
    # FUSED RRF score (`Σ w/(60+rank)`, thousandths however good the
    # candidate). Named `min_fused_score` rather than `min_score` so the scale
    # is legible next to the two above; it is `filter_relevant`'s `min_score`.
    #
    # ⚠️ **Both stay `0.0`, and `P-38` is why -- not what it left undone.**
    # The RRF score is rank arithmetic: it can only ever land in
    # `[w_min/(rrf_k+1), (w_dense+w_bm25)/(rrf_k+1)]` = [0.008197, 0.016393]
    # at the shipped weights, however good or bad the candidate is. Measured
    # over the evaluation set, answerable questions produced gold scores in
    # [0.008065, 0.016393] and UNANSWERABLE ones produced maxima in
    # [0.008197, 0.016261] -- the same interval. So no `min_fused_score` can
    # keep every real answer and reject anything; a floor at 0.0082 holds
    # English at 15/15 and drops Arabic to 7/15, because a cross-lingual
    # question has no both-leg agreement and every one of its candidates sits
    # at the single-leg floor by construction. `relative_floor` is the same
    # quantity as a ratio -- 0.8 costs two answers -- and is refused for the
    # same reason. This is a CLOSED question now, not a deferred one: the
    # scale carries no admissibility signal, so the two knobs above are where
    # a floor belongs and these two are where it does not.
    min_fused_score: float = 0.0
    relative_floor: float = 0.0
    # Near-duplicate dedup (`domain/relevance.py`) -- the one gate shipped ON.
    jaccard_threshold: float = 0.95
    # Length cap on a SUBSTITUTED parent chunk's text (plan step 9, `P-34`),
    # so one oversized section cannot swallow the whole context budget by
    # itself. A candidate's own leaf text is never capped (already
    # window-sized).
    max_parent_chunk_chars: int = 4_000
    # MMR (plan step 20, `P-23`, decision س-20) -- the two fields step 18's §7
    # entry reserved for it, riding this same seam with no second mechanism.
    #
    # `mmr_lambda` weighs relevance against redundancy in
    # `λ·sim(q,d) - (1-λ)·max sim(d,dⱼ)`. `0.7` SHIPS as a number, and §3.8's
    # last row says exactly why that does not contradict س-22: it is a
    # DIVERSITY trade-off, not a "is this good enough" gate -- nothing is
    # admitted or rejected by comparing a score to it. Higher is more
    # relevance-led; `1.0` turns diversity off entirely.
    mmr_lambda: float = 0.7
    # The "search_k موسَّع" of plan row 20 -- how deep each leg fetches, as a
    # multiple of `k`, so MMR has a pool WIDER than what it hands on and can
    # actually DISCARD a near-duplicate instead of merely re-ordering it.
    # `6` is `2 x fusion_retention`: MMR may drop up to half the fused pool as
    # redundant and still deliver step 8's full `3 x k` to parent expansion.
    # A COUNT, like `max_sparse_candidates` -- س-22 governs scores, so it does
    # not reach this either. It raises the fetch depth to
    # `max(search_overfetch, mmr_overfetch) * k` (still capped by
    # `max_search_candidates`), and ⚠️ that depth is also what
    # `with_vectors=True` now ships over the wire per query -- §3.9's declared
    # price, §6 risk #5's accepted one.
    mmr_overfetch: int = 6
    # The cross-encoder reranker (plan row 21, `P-24`, decision س-21) — the
    # deployment switch س-21 asked for in as many words ("مع تفعيل وإيقاف"),
    # and **OFF as it ships**, "مطفأ افتراضيًّا كما في alpha" (§3.10).
    #
    # Turning it on buys accuracy and costs LATENCY ON EVERY REQUEST — §6
    # risk ٦ — so it is a conscious deployment decision, never a request's.
    # There is no per-request equivalent and س-24 is why: configuration lives
    # here and reaches the pipeline as an argument, so `RetrieveContext.
    # execute` has nothing to toggle. §7 records that a per-request toggle
    # "يحتاج قرارًا جديدًا" and is not this row's to invent.
    #
    # A `bool` among floats and counts, and س-22 does not reach it either: it
    # admits or rejects a STAGE, not a candidate by comparing a score to a
    # number nobody calibrated.
    rerank_enabled: bool = False
    # How many candidates may cross the wire to the reranker — §3.10's scope,
    # "أوّل 10-20 مرشّحًا بعد الدمج، لا الكوربوس", as a number. A cross-encoder
    # reads every (query, document) pair, so this is the cost ceiling of the
    # whole stage; at the shipped `k` the pipeline offers it ~15 anyway
    # (`fusion_retention * default_k`) and this caps the tail of a larger `k`.
    # A COUNT, like `max_sparse_candidates` and `mmr_overfetch` — so س-22
    # (which governs SCORES) does not reach it, and, like them, it is
    # unmeasured: reviewing it needs the evaluation set `P-38` waits for.
    rerank_candidates: int = 20
    # How many file names the corpus-awareness header shows before it counts
    # the rest as "N more files" (plan §3.6/§4 row 6, `P-36`, س-23 = ج) — the
    # `limit` `KnowledgeRetrieval.list_document_names` resolves for a caller
    # that names none, exactly as `default_k` above is the `k` a caller that
    # names none gets.
    #
    # ⚠️ **A DISPLAY cap, not a quality knob**, and it is here for `default_k`'s
    # reason rather than س-24's: it used to be `rag_agent.agent.
    # _MAX_CORPUS_NAMES = 50`, a number in the agent layer, and an agent reads
    # no configuration and imports nothing (ح-11) — so a default resolved
    # inside the module is the only route by which a deployment's number can
    # reach that header at all. §3.6 fixes the value at 50 and names the price
    # right there ("سقف عرض 50 اسمًا", ~500 tokens on every request), so what
    # moves it is a shorter prompt budget, never a retrieval measurement.
    max_corpus_names: int = 50


class Limits(BaseModel):
    """Numeric guardrails (07-nfr-slo §4, approved OQ-02)."""

    model_config = _FROZEN

    max_upload_bytes: int = 52_428_800  # 50 MB
    # deferred-adapters-plan.md step 16 (§1-ج) reconciled this whitelist with
    # the ONE thing that actually consumes a `knowledge`-bound upload: the
    # `_ROUTES` dispatch table in `knowledge/adapters/parsers/extractor.py`.
    # The two had drifted in BOTH directions, and the gap only became
    # explosive the day the knowledge worker could boot:
    #   * DOCX was allowed here and routed NOWHERE (3.k1 deferred it — it
    #     needs `python-docx`, not an approved dependency), so every accepted
    #     `.docx` upload was a poison pill: `extract` raises
    #     `UnsupportedTypeError`, the document never leaves `pending`.
    #     Dropped. Re-adding it means adding the parser first.
    #     **Re-added** by plan step 4 (`P-08`): `adapters/parsers/docx.py`
    #     exists and `_ROUTES` keys `.docx`, so the condition that dropped it
    #     is met. Without this line the parser is unreachable — the upload is
    #     rejected before any of it runs.
    #   * `.xlsx`/`.json`/`.csv` have working parsers (excel/json_doc/
    #     text_plain) that no file could ever reach, because this whitelist
    #     rejected the upload at `RegisterUpload`. Added.
    # The poison-pill BEHAVIOUR is fixed independently (the index handler now
    # lands such a document in `failed` with an event); this list is what
    # stops a user from being handed the failure in the first place.
    allowed_mime: tuple[str, ...] = (
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/png",
        "image/jpeg",
        "image/webp",
    )
    max_files_per_workspace: int = 10_000
    # `docs/spaces-backend-plan.md` decision 4 / §3.3 -- 1 GiB of BYTES per
    # SPACE, and deliberately a second limit rather than a replacement for the
    # count above: `max_files_per_workspace` bounds the row count of a whole
    # tenant (what protects the table), this bounds the stored volume of one
    # space (what protects the tenant's own budget). Enforced under a row lock
    # by `framework/di/space_quota.py`, never by a bare read-then-write.
    max_space_bytes: int = 1_073_741_824
    # Embedded-image OCR guardrails (rag-indexing-plan.md §3.8, decision
    # س-10). Plan step 5 (`P-09` `P-11`) reads them here rather than from the
    # environment: alpha spreads eleven OCR knobs across `os.getenv` calls and
    # never writes down what any of them decides, so the values are CHOSEN
    # here, with their reason, and not copied.
    #   * `ocr_min_image_px` -- 200x200 (both sides). Icons and logos are the
    #     most numerous embedded images and the least worth OCRing; alpha's
    #     100x100 lets a favicon through. Standalone image UPLOADS bypass this
    #     filter entirely (`image_ocr.py`, divergence 2): there the image is
    #     the document.
    #   * `ocr_max_images_per_document` -- the queue guard. A 100-page PDF can
    #     carry hundreds of images, and OCR is the slowest thing in the
    #     pipeline.
    #   * `ocr_max_images_per_page` -- stops ONE page from eating the whole
    #     document budget, which is what makes the cap above fair across pages.
    # Passing either cap is DECLARED (`OcrResult.truncated` -> the route's
    # `ocr_truncated`), never a failure: a cap is a cost decision (§3.8).
    ocr_min_image_px: int = 200
    ocr_max_images_per_document: int = 40
    ocr_max_images_per_page: int = 8
    # Zip-bomb guard + parse timeout (rag-indexing-plan.md §3.7, decision
    # س-13). Plan step 14 (`P-01` `P-02`) reads them here rather than from
    # the environment: alpha reads the same three knobs
    # (`PARSER_MAX_UNCOMPRESSED_MB`/`PARSER_MAX_COMPRESSION_RATIO`/
    # `PARSER_TIMEOUT_SECONDS`) through `os.getenv` and never documents their
    # default values as a decision anywhere -- the `ocr_*` precedent above,
    # applied to the guard that runs BEFORE any parser touches the bytes.
    #   * `parser_max_uncompressed_mb` -- 512 MB. The cap on an Office
    #     archive's (`.docx`/`.xlsx`) SUMMED uncompressed member size: a
    #     legitimate upload of this platform's `max_upload_bytes` (50 MB,
    #     compressed) has no business expanding past this on disk/in memory.
    #   * `parser_max_compression_ratio` -- 100:1. A genuine Office file
    #     (already-compressed XML + media) runs 3:1 to 10:1; 100:1 is a BOMB
    #     threshold, not a usage threshold -- wide margin on purpose, so a
    #     dense-but-legitimate spreadsheet never trips it.
    #   * `parser_timeout_seconds` -- 300s. Wall-clock cap on ONE document's
    #     whole parse (`asyncio.wait_for` around the worker's
    #     `asyncio.to_thread` offload, §3.7) -- generous enough for a heavy
    #     PDF under the OCR caps above, the `media_timeout_s` precedent.
    # A guard trip or a timeout is NEVER a silent skip (decision س-13 = أ):
    # the document becomes `status='failed'` with an explicit `error` --
    # alpha's "skip and log" suits a folder scan; in this object model (one
    # document per request) a skip would be a FALSE SUCCESS with zero chunks.
    parser_max_uncompressed_mb: int = 512
    parser_max_compression_ratio: int = 100
    parser_timeout_seconds: int = 300
    max_input_tokens: int = 32_000
    max_output_tokens: int = 4_096
    max_rag_k: int = 50
    # The DUAL context budget (rag-retrieval-plan.md §3.7 / §4 row 10,
    # `P-35`, decision س-24) -- a hard character ceiling and an ESTIMATED
    # token ceiling on the retrieved context handed to the model, of which
    # the SMALLER always wins. Consumed by `RetrieveContext`, which passes
    # them as arguments into the pure `domain/context_budget.py` (س-24: the
    # numbers live here, never in the domain, and there is no per-request
    # override of either).
    #   * `max_context_chars` -- 12000, the plan's own starting suggestion,
    #     and alpha's number exactly. Exactly measurable, so it is the floor
    #     under an estimate that could drift.
    #     ⚠️ It admits TWO widened parents, not three. A parent is capped at
    #     `RetrievalTuning.max_parent_chunk_chars` (4000), but what the budget
    #     measures is the RENDERED string -- §3.2's `[file p.N | section: S]`
    #     label included -- so three cost 3 x ~4055 = ~12165 and the third is
    #     cut. Measured, not derived: 12000 is 3 x 4000 with nothing left over
    #     for a label this pipeline puts on every chunk. Raising it is a
    #     SEPARATE decision from the token cap below (§3-ب) and is not taken
    #     here.
    #   * `max_context_tokens` -- 6000, and this number is DERIVED, not
    #     suggested. `context_budget.estimate_tokens` charges an Arabic-block
    #     character 2.0 chars/token, so a context filled to `max_context_chars`
    #     of pure Arabic estimates at 12000 / 2.0 = 6000 tokens. Any value
    #     BELOW that makes the token cap bite before the character cap on
    #     Arabic and only on Arabic -- the behaviour splits by script, and the
    #     exact, auditable ceiling stops being the one in force. 6000 is the
    #     smallest value that cannot do that, and it is also alpha's own
    #     number (whose flat 4.0 rate put its token cap out of reach the same
    #     way). The plan's `3000` was a suggestion carried over untested; at
    #     it, an Arabic answer is built from ONE 4000-char parent while the
    #     identical English one gets two.
    #     Still far under any prompt ceiling: measured against the deployed
    #     `gemma3:1b` at `OllamaLlmProvider._NUM_CTX` = 8192, a context filled
    #     to 12000 chars of real Arabic assembles into a 5086-token prompt,
    #     system prompt and corpus header included, leaving ~3100 for the
    #     answer. The estimator over-counts it by ~22%, which is the direction
    #     it is documented to err in.
    # These are NOT quality thresholds, so decision س-22 ("thresholds stay
    # 0.0 until a calibration set exists") does not apply: a budget is a cost
    # decision about how much text is sent, not a judgement about whether a
    # chunk is good enough to send.
    max_context_chars: int = 12_000
    # ⚠️ MUST equal `max_context_chars` / `context_budget._ARABIC_CHARS_PER_TOKEN`.
    # `test_the_token_ceiling_cannot_bite_before_the_character_ceiling` pins it.
    max_context_tokens: int = 6_000
    embedding_batch: int = 128
    max_image_dim: int = 2_048
    max_video_seconds: int = 30
    max_workflow_steps: int = 10
    ws_message_bytes: int = 65_536
    ws_connections_per_user: int = 5
    api_rate_per_min: int = 120
    heavy_jobs_per_min: int = 30
    llm_timeout_s: int = 60
    media_timeout_s: int = 300
    # `F-1` (rag-summarization-fix-plan.md §3.1) -- the summarisation
    # map-reduce's OWN per-call budget, on the `media_timeout_s` precedent
    # exactly: a class of call that is minutes-scale BY NATURE gets its own
    # number rather than bending the interactive one.
    #
    # `llm_timeout_s` (60 s) is sized for an agent turn, where failing fast
    # IS the right answer. A map call hands a whole batch of document text to
    # a local model and routinely runs past a minute -- and because the
    # adapters call `complete` (`stream: false`), the provider emits no byte
    # at all until generation ends, so those 60 s cap the ENTIRE call rather
    # than the gap between chunks. Every long document died on that.
    #
    # Raising `llm_timeout_s` instead would have bought this at the price of
    # letting the interactive cycle hang five times longer, since ONE
    # `SettingsProviderResolver` serves indexing, summarisation and the agent
    # loop alike. `workers/bootstrap.py` spends a second pair of HTTP clients
    # instead, which is what an httpx client-level timeout costs.
    summarize_timeout_s: int = 300
    # `F-4` (rag-summarization-fix-plan.md §3.5) -- TOTAL wall-clock cap for
    # ONE summary build: every provider call of a map-reduce plus the waits
    # between them, the `stream_max_duration_s` idea applied to a job instead
    # of a stream. 6 x `summarize_timeout_s`, sized off the worst legitimate
    # shape the constants allow: `_MAX_MAP_CHUNKS / _MAP_BATCH` = 12 map
    # calls, ~3 folds and one reduce, ~16 calls, which only reaches 1,800 s if
    # nearly every one of them runs to a third of its own budget.
    #
    # It exists because the alternative to a bounded failure here is not a
    # slower success, it is an ENDLESS one: a build that never returns is
    # redelivered (DD-09), `SummaryJob.start` is re-entrant from `running`, so
    # it restarts from the first chunk and meets the same wall -- five times,
    # until the DLQ, with the job holding `uq_summary_job_active` throughout
    # so the user cannot even ask again. A timeout converts that into one
    # `failed` job carrying a written reason.
    #
    # `F-9`'s ceiling review made the derivation a two-way link: this number
    # is what actually bounds `_MAX_MAP_CHUNKS` now that `F-2` retired the
    # cost argument for it, so the two move TOGETHER or not at all. Raising
    # the chunk ceiling alone turns a declared truncation -- a real summary
    # of a prefix, saying so -- into a failed job with no summary, for
    # exactly the long documents the raise was meant to serve.
    summarize_job_max_duration_s: int = 1_800
    # ب-10 (خطة السيناريوهات §7، gap ف-7) — how many summary builds ONE
    # workspace may hold queued or running at once.
    #
    # The guard that existed was per KEY: a second build of the same document,
    # kind and language is a 409. Nothing bounded a workspace with fifty
    # documents queueing fifty legitimate builds, and each of them is minutes
    # of provider calls on a worker every tenant shares. `F-5`'s
    # one-message-at-a-time read does not change that either — what serialises
    # builds is a single loop in a single process, not the batch size, so the
    # fifty do not run at once, they run one after another for hours while
    # every other tenant's indexing waits behind them.
    #
    # Three, and not a sacred three (ق-د): it is how many builds may be in
    # flight before the next asker is told to wait, not a quota anyone bought,
    # and a busy tenant raises it. A per-workspace number would need a table,
    # which is not the gap this closes.
    max_active_summary_jobs_per_workspace: int = 3
    # 5.3-أ — TOTAL wall-clock cap for ONE streamed response (a single agent
    # answer or a whole workflow run's stream, SSE and WS alike). The adapters'
    # httpx timeout is between-chunk ONLY, deliberately (§3.23: a whole-call
    # cap there would sever a healthy long stream mid-chunk), so without this
    # nothing bounds a stream that keeps trickling forever. 600 = 10 x
    # `llm_timeout_s`: the worst legitimate case is a `max_workflow_steps`
    # run whose every step spends its full LLM budget.
    stream_max_duration_s: int = 600
    usage_tokens_quota_month: int = 5_000_000
    usage_cost_micros_month: int = 50_000_000
    oauth_timeout_s: int = 10
    mcp_tool_timeout_s: int = 30
    max_connectors: int = 50
    max_mcp_servers: int = 20
    max_discovered_tools: int = 200


class Settings(BaseModel):
    """Root configuration contract — immutable, secret-free."""

    model_config = _FROZEN

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # Provider routing table (D-16, FR-73): capability/agent -> provider+model.
    # Namespaces "llm" | "embedding" | "image" ("image" added in step 18 of
    # deferred-adapters-plan.md). Deliberately stays untyped `Json` here: the
    # ONE strict parse lives in `SettingsProviderResolver`, which is where the
    # wired-adapter mappings are -- a shape Pydantic could check, but the
    # "provider has no wired adapter" half it could not, and two half-validators
    # drift.
    provider_routing: Json = Field(default_factory=dict)

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    minio: MinioSettings = Field(default_factory=MinioSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    vault: VaultSettings = Field(default_factory=VaultSettings)
    firebase: FirebaseSettings = Field(default_factory=FirebaseSettings)
    # capacity-plan wave 1 step 1.1 — the authentication PATH's knobs,
    # beside the issuer's rather than inside them (see `AuthSettings`).
    auth: AuthSettings = Field(default_factory=AuthSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    embedding_service: EmbeddingServiceSettings = Field(default_factory=EmbeddingServiceSettings)
    # rag-retrieval-plan.md §4 row 21 (`P-24`, س-21) — WHERE the cross-encoder
    # rerank service is. Beside `embedding_service` because it is the same
    # kind of thing: a first-party internal HTTP dependency that keeps its
    # model weights out of this image. WHETHER it is called at all is
    # `retrieval.rerank_enabled` (`False` as shipped), and while that stays
    # off nothing here is even read — the Composition Root builds no client.
    rerank_service: RerankServiceSettings = Field(default_factory=RerankServiceSettings)
    events: EventSettings = Field(default_factory=EventSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)
    limits: Limits = Field(default_factory=Limits)
    # rag-retrieval-plan.md §4 row 18 (`P-30` `P-40`, س-24) — the hybrid
    # retrieval tuning knobs, in ONE place instead of scattered across
    # `knowledge/application/retrieval.py` as module constants. Kept beside
    # `limits` rather than inside it because a guardrail and a tuning
    # parameter are different things: `Limits` bounds what the platform will
    # accept (07-nfr-slo §4), this shapes how well retrieval answers. The
    # three retrieval numbers that ARE guardrails — `max_rag_k`,
    # `max_context_chars`, `max_context_tokens` — deliberately stay in
    # `Limits`, and the Composition Root reads the tuning from both.
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    # rag-fidelity-audit.md §3-ج — Okapi's `k1`/`b` and the length anchor they
    # normalise against. Beside `retrieval` and deliberately NOT inside it:
    # these are INDEX-time and reach `IndexDocument`, while everything in
    # `retrieval` is query-time and reaches `RetrieveContext`. See
    # `SparseSettings`' own docstring for why the separation is load-bearing.
    sparse: SparseSettings = Field(default_factory=SparseSettings)
