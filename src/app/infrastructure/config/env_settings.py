"""The single reader of environment/``.env`` (DD-11, 10-code-standards §9).

Flat env keys (05-rbac-config-secrets §2) are loaded here and assembled into
the immutable ``Settings`` contract that the rest of the system consumes. No
other module reads ``os.environ`` or ``.env`` directly. Secrets are *not* read
here — they are resolved via ``SecretsProvider``/Vault at runtime.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.framework.settings.settings import (
    AuthSettings,
    DatabaseSettings,
    EmbeddingServiceSettings,
    EventSettings,
    FirebaseSettings,
    HealthSettings,
    IntegrationsSettings,
    MetricsSettings,
    MigrationSettings,
    MinioSettings,
    OllamaSettings,
    QdrantSettings,
    RateLimitSettings,
    RedisSettings,
    Settings,
    UsageSettings,
    VaultSettings,
)


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


class _EnvSettings(BaseSettings):
    """Flat env-var view (aliases are the exact env keys)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_env: str = Field("development", alias="APP_ENV")
    app_host: str = Field("0.0.0.0", alias="APP_HOST")
    app_port: int = Field(8000, alias="APP_PORT")
    api_prefix: str = Field("/api/v1", alias="API_PREFIX")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    database_url: str = Field("postgresql+asyncpg://app@pgbouncer:6432/app", alias="DATABASE_URL")
    db_pool_size: int = Field(10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(20, alias="DB_MAX_OVERFLOW")

    # capacity-plan 2.6 -- the REQUEST path's four connection timeouts. The
    # contract's own defaults are deliberately looser than these (`0` for both
    # server-side budgets, so `app.ops.*` keep running statements for minutes
    # -- see `DatabaseSettings`); what an API/worker process gets is decided
    # HERE, and only background processes override it, from their own
    # constants in `workers/bootstrap.py`.
    #
    # `ge=0` and not `ge=1`: `0` is Postgres's own spelling of "no limit" on
    # both server-side GUCs and the value that switches the `begin` listener
    # off entirely, so it has to remain expressible. The pool bounds get
    # `gt=0` instead -- a zero-second checkout wait is not a policy, it is a
    # pool that refuses every request the moment two arrive together.
    db_pool_timeout_s: float = Field(5.0, alias="DB_POOL_TIMEOUT_S", gt=0)
    db_pool_recycle_s: int = Field(900, alias="DB_POOL_RECYCLE_S", gt=0)
    db_statement_timeout_ms: int = Field(5_000, alias="DB_STATEMENT_TIMEOUT_MS", ge=0)
    db_idle_in_transaction_timeout_ms: int = Field(
        10_000, alias="DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", ge=0
    )

    # capacity-plan 2.9 -- the DEPLOY's two waits, not a fifth and sixth
    # connection timeout; `MigrationSettings` carries the measurement that
    # says why they must not share a number with each other or with the
    # request path. `gt=0` on both: `0` here would mean "wait forever", which
    # is the behaviour `ح-18` exists to remove, so it stays inexpressible.
    migration_lock_timeout_ms: int = Field(3_000, alias="MIGRATION_LOCK_TIMEOUT_MS", gt=0)
    provision_lock_wait_ms: int = Field(900_000, alias="PROVISION_LOCK_WAIT_MS", gt=0)

    redis_url: str = Field("redis://redis-stream:6379/0", alias="REDIS_URL")

    # capacity 5.2 (ح-10 · ق-4) -- the `allkeys-lru` server, and the ONLY
    # setting in this file whose default is "whatever the other one says".
    #
    # Blank is not an oversight and not "unconfigured": it COLLAPSES the split,
    # putting the evictable cache back on the single instance every deployment
    # had before this step. That is the م-8 off switch (2.1's rule: every
    # improvement ships with a way back to the state the baseline is measured
    # in) and it is also the safe default for an environment this repository
    # does not control -- a stale `.env` that names one Redis keeps booting,
    # instead of failing to resolve a hostname it has never heard of.
    #
    # ⚠️ And the fallback direction matters. It collapses onto REDIS_URL --
    # the `noeviction` instance -- never the other way: an unconfigured
    # deployment ends up with a cache that is never evicted (today's
    # behaviour, ح-10 unmitigated but nothing NEW broken), rather than with a
    # session denylist that is.
    cache_redis_url: str = Field("", alias="CACHE_REDIS_URL")

    # P1-3 (docs/p1-hardening-plan.md §3 step 10): the `/metrics` endpoint's
    # OWN role -- see `MetricsSettings`'s docstring for why this cannot be
    # `database_url`/`app_rw` widened. Blank in an env that has not wired the
    # role yet is caught by `MetricsSource`'s own connection failure the
    # first time `/metrics` is scraped, not at boot -- the same "an
    # unconfigured feature 500s only when used" posture `web_search` already
    # follows (composition_root.py's module docstring), since a bare `/health`
    # replica must still boot even before an operator has provisioned the
    # role.
    metrics_database_url: str = Field(
        "postgresql+asyncpg://metrics_reader@pgbouncer:6432/app", alias="METRICS_DATABASE_URL"
    )

    minio_endpoint: str = Field("minio:9000", alias="MINIO_ENDPOINT")
    minio_bucket: str = Field("workspace-files", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    # Address presigned URLs are signed against; empty => same as MINIO_ENDPOINT
    # (MinioSettings.signing_endpoint owns that fallback).
    minio_public_endpoint: str = Field("", alias="MINIO_PUBLIC_ENDPOINT")
    minio_public_secure: bool | None = Field(default=None, alias="MINIO_PUBLIC_SECURE")
    # Presigned-URL lifetimes (3.79). Bounds are NOT repeated here: `MinioSettings`
    # owns them (1s..7d, SigV4's own range), so an out-of-range value fails once,
    # in the contract, with the contract's message -- rather than twice, with two.
    minio_presign_put_ttl_s: int = Field(900, alias="MINIO_PRESIGN_PUT_TTL_S")
    minio_presign_get_ttl_s: int = Field(300, alias="MINIO_PRESIGN_GET_TTL_S")

    qdrant_url: str = Field("http://qdrant:6333", alias="QDRANT_URL")

    vault_addr: str = Field("http://vault:8200", alias="VAULT_ADDR")
    vault_role_id: str | None = Field(default=None, alias="VAULT_ROLE_ID")

    firebase_project_id: str = Field("", alias="FIREBASE_PROJECT_ID")
    firebase_jwks_cache_ttl: int = Field(3600, alias="FIREBASE_JWKS_CACHE_TTL")

    # capacity-plan 1.1. `0` does not mean a zero-second TTL -- it means the
    # Composition Root builds NO principal cache, so a baseline run pays not
    # even a Redis round trip for it. The upper bound is the contract's
    # (`MAX_PRINCIPAL_CACHE_TTL_S`), stated once there and not repeated here.
    auth_principal_cache_ttl_s: int = Field(60, alias="AUTH_PRINCIPAL_CACHE_TTL_S", ge=0)

    # capacity-plan 1.2. `false` builds NO limiter (the `م-8` baseline switch,
    # the same shape as the line above); the two numbers are bounded here
    # rather than in the model because a `0` reaching either one would refuse
    # every request in the platform with a well-formed 429 -- except
    # `MAX_IN_FLIGHT_REQUESTS`, where `0` legitimately means "do not install
    # the burst guard at all" and the middleware is simply not added.
    api_rate_limit_enabled: bool = Field(True, alias="API_RATE_LIMIT_ENABLED")
    workspace_rate_per_min: int = Field(2400, alias="WORKSPACE_RATE_PER_MIN", ge=1)
    max_in_flight_requests: int = Field(64, alias="MAX_IN_FLIGHT_REQUESTS", ge=0)
    # capacity-plan 5.3. `0` builds no queue gate (the line above's shape).
    queue_lag_ceiling_s: int = Field(120, alias="QUEUE_LAG_CEILING_S", ge=0)
    queue_retry_after_s: int = Field(30, alias="QUEUE_RETRY_AFTER_S", ge=1)

    ollama_base_url: str = Field("http://ollama:11434", alias="OLLAMA_BASE_URL")

    # 2.10: only the URL is env-editable (DD-11) -- model/dimensions/batch/
    # timeout are pinned defaults that must match the baked service image
    # (EmbeddingServiceSettings' own docstring explains why an env-editable
    # dimension would be dangerous).
    embedding_service_url: str = Field("http://embedding:8080", alias="EMBEDDING_SERVICE_URL")
    # capacity-plan 4.3. `0` does not mean a zero-second TTL -- it means the
    # Composition Root wraps nothing, so a baseline run pays not even a Redis
    # round trip for it (the `AUTH_PRINCIPAL_CACHE_TTL_S` shape above). The
    # upper bound is the adapter's (`MAX_EMBEDDING_CACHE_TTL_S`), stated once
    # there rather than twice.
    embedding_cache_ttl_s: int = Field(600, alias="EMBEDDING_CACHE_TTL_S", ge=0)

    event_stream_prefix: str = Field("stream.", alias="EVENT_STREAM_PREFIX")
    outbox_poll_interval_ms: int = Field(500, alias="OUTBOX_POLL_INTERVAL_MS")
    consumer_block_ms: int = Field(5000, alias="CONSUMER_BLOCK_MS")
    max_retries_before_dlq: int = Field(5, alias="MAX_RETRIES_BEFORE_DLQ")
    outbox_relay_batch_size: int = Field(256, alias="OUTBOX_RELAY_BATCH_SIZE")
    consumer_batch_count: int = Field(16, alias="CONSUMER_BATCH_COUNT")
    # capacity 5.1 (`ح-6`). `WORKER_CONCURRENCY` and not `CONSUMER_CONCURRENCY`
    # because the thing it bounds is a WORKER process's in-flight work, and the
    # capacity plan names it that; `EventSettings.worker_concurrency` carries
    # the three numbers that move with it. `1` restores the pre-5.1 sequential
    # engine exactly (`م-8`).
    worker_concurrency: int = Field(4, alias="WORKER_CONCURRENCY", ge=1)
    # capacity 5.1 invariant (4). `0` disables the drain (cancel where it
    # stands, the pre-5.1 path); above 0 it must stay under the service's
    # `stop_grace_period`, which is what makes the drain reachable at all.
    worker_drain_timeout_s: float = Field(30.0, alias="WORKER_DRAIN_TIMEOUT_S", ge=0)
    # 0 means "no trimming" (7.3) -- `ge=0` here, then mapped to the
    # `int | None` the settings contract actually models. Reading it as 0
    # rather than an empty string keeps the env value a plain integer.
    stream_maxlen: int = Field(100_000, alias="STREAM_MAXLEN", ge=0)
    # capacity 5.5 (`ح-17`): the relay's trim below each stream's slowest
    # reader. `0` disables it (the `م-8` switch -- MAXLEN alone, as before);
    # the margin is inspection history, not safety (`EventSettings`).
    stream_trim_interval_s: float = Field(60.0, alias="STREAM_TRIM_INTERVAL_S", ge=0)
    stream_trim_margin_s: float = Field(600.0, alias="STREAM_TRIM_MARGIN_S", ge=0)

    # ت-2: the two automatic sweeps' knobs (EventSettings' own docstrings
    # carry the safety relation between `CONSUMER_STALE_IDLE_S` and
    # `CONSUMER_BLOCK_MS`). `0` disables a sweep rather than meaning "always".
    consumer_sweep_interval_s: float = Field(300.0, alias="CONSUMER_SWEEP_INTERVAL_S", ge=0)
    consumer_stale_idle_s: float = Field(900.0, alias="CONSUMER_STALE_IDLE_S", ge=0)
    notify_group_sweep_interval_s: float = Field(900.0, alias="NOTIFY_GROUP_SWEEP_INTERVAL_S", ge=0)

    # ت-6: how often a worker reports a non-empty DLQ (`consumers/dlq_watch.py`).
    # `0` disables the report -- it never disables dead-lettering itself.
    dlq_watch_interval_s: float = Field(300.0, alias="DLQ_WATCH_INTERVAL_S", ge=0)

    # ت-3: where the loop-shaped processes stamp their liveness, and how stale
    # that stamp may get before `app.ops.healthcheck` calls it dead. Empty
    # `HEARTBEAT_DIR` disables the file entirely (HealthSettings' own
    # docstring) -- read as a plain string here, since "" is a MEANINGFUL
    # value and `None` would just be a second spelling of it.
    heartbeat_dir: str = Field("/tmp/aizzak-heartbeat", alias="HEARTBEAT_DIR")
    heartbeat_max_age_s: int = Field(300, alias="HEARTBEAT_MAX_AGE_S", ge=1)

    provider_routing: dict[str, Any] = Field(default_factory=dict, alias="PROVIDER_ROUTING")

    oauth_redirect_base_url: str | None = Field(default=None, alias="OAUTH_REDIRECT_BASE_URL")
    mcp_allowed_transports: str = Field("http,sse", alias="MCP_ALLOWED_TRANSPORTS")
    oauth_refresh_skew_s: int = Field(60, alias="OAUTH_REFRESH_SKEW_S")

    usage_rollup_periods: str = Field("day,month", alias="USAGE_ROLLUP_PERIODS")
    usage_default_limits: dict[str, Any] = Field(default_factory=dict, alias="USAGE_DEFAULT_LIMITS")


def load_settings() -> Settings:
    """Read env/``.env`` and assemble the immutable ``Settings`` contract."""
    env = _EnvSettings()

    usage_kwargs: dict[str, Any] = {"rollup_periods": _split_csv(env.usage_rollup_periods)}
    if env.usage_default_limits:
        usage_kwargs["default_limits"] = env.usage_default_limits

    return Settings(
        app_env=env.app_env,
        app_host=env.app_host,
        app_port=env.app_port,
        api_prefix=env.api_prefix,
        log_level=env.log_level,
        provider_routing=env.provider_routing,
        database=DatabaseSettings(
            url=env.database_url,
            pool_size=env.db_pool_size,
            max_overflow=env.db_max_overflow,
            pool_timeout_s=env.db_pool_timeout_s,
            pool_recycle_s=env.db_pool_recycle_s,
            statement_timeout_ms=env.db_statement_timeout_ms,
            idle_in_transaction_timeout_ms=env.db_idle_in_transaction_timeout_ms,
        ),
        migrations=MigrationSettings(
            lock_timeout_ms=env.migration_lock_timeout_ms,
            provision_lock_wait_ms=env.provision_lock_wait_ms,
        ),
        redis=RedisSettings(url=env.redis_url),
        # `or` and not a `if`: an empty CACHE_REDIS_URL is the documented
        # collapse (see the field above), and an empty Redis URL is not a
        # thing that could be meant literally anyway.
        cache_redis=RedisSettings(url=env.cache_redis_url or env.redis_url),
        metrics=MetricsSettings(database_url=env.metrics_database_url),
        minio=MinioSettings(
            endpoint=env.minio_endpoint,
            bucket=env.minio_bucket,
            secure=env.minio_secure,
            public_endpoint=env.minio_public_endpoint,
            public_secure=env.minio_public_secure,
            presign_put_ttl_s=env.minio_presign_put_ttl_s,
            presign_get_ttl_s=env.minio_presign_get_ttl_s,
        ),
        qdrant=QdrantSettings(url=env.qdrant_url),
        vault=VaultSettings(addr=env.vault_addr, role_id=env.vault_role_id),
        firebase=FirebaseSettings(
            project_id=env.firebase_project_id,
            jwks_cache_ttl=env.firebase_jwks_cache_ttl,
        ),
        auth=AuthSettings(principal_cache_ttl_s=env.auth_principal_cache_ttl_s),
        rate_limit=RateLimitSettings(
            enabled=env.api_rate_limit_enabled,
            workspace_per_min=env.workspace_rate_per_min,
            max_in_flight=env.max_in_flight_requests,
            queue_lag_ceiling_s=env.queue_lag_ceiling_s,
            queue_retry_after_s=env.queue_retry_after_s,
        ),
        ollama=OllamaSettings(base_url=env.ollama_base_url),
        embedding_service=EmbeddingServiceSettings(
            url=env.embedding_service_url, cache_ttl_s=env.embedding_cache_ttl_s
        ),
        events=EventSettings(
            stream_prefix=env.event_stream_prefix,
            outbox_poll_interval_ms=env.outbox_poll_interval_ms,
            consumer_block_ms=env.consumer_block_ms,
            max_retries_before_dlq=env.max_retries_before_dlq,
            outbox_relay_batch_size=env.outbox_relay_batch_size,
            consumer_batch_count=env.consumer_batch_count,
            worker_concurrency=env.worker_concurrency,
            worker_drain_timeout_s=env.worker_drain_timeout_s,
            # 0 disables trimming (7.3). The contract models "off" as None
            # rather than 0 so the adapter branches on a real absence, not on
            # a magic number it would have to re-interpret at every call.
            stream_maxlen=env.stream_maxlen or None,
            stream_trim_interval_s=env.stream_trim_interval_s,
            stream_trim_margin_s=env.stream_trim_margin_s,
            consumer_sweep_interval_s=env.consumer_sweep_interval_s,
            consumer_stale_idle_s=env.consumer_stale_idle_s,
            notify_group_sweep_interval_s=env.notify_group_sweep_interval_s,
            dlq_watch_interval_s=env.dlq_watch_interval_s,
        ),
        health=HealthSettings(
            heartbeat_dir=env.heartbeat_dir,
            heartbeat_max_age_s=env.heartbeat_max_age_s,
        ),
        integrations=IntegrationsSettings(
            oauth_redirect_base_url=env.oauth_redirect_base_url,
            mcp_allowed_transports=_split_csv(env.mcp_allowed_transports),
            oauth_refresh_skew_s=env.oauth_refresh_skew_s,
        ),
        usage=UsageSettings(**usage_kwargs),
    )
