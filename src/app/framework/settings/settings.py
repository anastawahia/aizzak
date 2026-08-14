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
        "image/png",
        "image/jpeg",
        "image/webp",
    )
    max_files_per_workspace: int = 10_000
    max_input_tokens: int = 32_000
    max_output_tokens: int = 4_096
    max_rag_k: int = 50
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
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    embedding_service: EmbeddingServiceSettings = Field(default_factory=EmbeddingServiceSettings)
    events: EventSettings = Field(default_factory=EventSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)
    limits: Limits = Field(default_factory=Limits)
