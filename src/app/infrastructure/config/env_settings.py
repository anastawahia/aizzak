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
    DatabaseSettings,
    EmbeddingServiceSettings,
    EventSettings,
    FirebaseSettings,
    HealthSettings,
    IntegrationsSettings,
    MetricsSettings,
    MinioSettings,
    OllamaSettings,
    QdrantSettings,
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

    redis_url: str = Field("redis://redis:6379/0", alias="REDIS_URL")

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

    ollama_base_url: str = Field("http://ollama:11434", alias="OLLAMA_BASE_URL")

    # 2.10: only the URL is env-editable (DD-11) -- model/dimensions/batch/
    # timeout are pinned defaults that must match the baked service image
    # (EmbeddingServiceSettings' own docstring explains why an env-editable
    # dimension would be dangerous).
    embedding_service_url: str = Field("http://embedding:8080", alias="EMBEDDING_SERVICE_URL")

    event_stream_prefix: str = Field("stream.", alias="EVENT_STREAM_PREFIX")
    outbox_poll_interval_ms: int = Field(500, alias="OUTBOX_POLL_INTERVAL_MS")
    consumer_block_ms: int = Field(5000, alias="CONSUMER_BLOCK_MS")
    max_retries_before_dlq: int = Field(5, alias="MAX_RETRIES_BEFORE_DLQ")
    outbox_relay_batch_size: int = Field(256, alias="OUTBOX_RELAY_BATCH_SIZE")
    consumer_batch_count: int = Field(16, alias="CONSUMER_BATCH_COUNT")
    # 0 means "no trimming" (7.3) -- `ge=0` here, then mapped to the
    # `int | None` the settings contract actually models. Reading it as 0
    # rather than an empty string keeps the env value a plain integer.
    stream_maxlen: int = Field(100_000, alias="STREAM_MAXLEN", ge=0)

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
        ),
        redis=RedisSettings(url=env.redis_url),
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
        ollama=OllamaSettings(base_url=env.ollama_base_url),
        embedding_service=EmbeddingServiceSettings(url=env.embedding_service_url),
        events=EventSettings(
            stream_prefix=env.event_stream_prefix,
            outbox_poll_interval_ms=env.outbox_poll_interval_ms,
            consumer_block_ms=env.consumer_block_ms,
            max_retries_before_dlq=env.max_retries_before_dlq,
            outbox_relay_batch_size=env.outbox_relay_batch_size,
            consumer_batch_count=env.consumer_batch_count,
            # 0 disables trimming (7.3). The contract models "off" as None
            # rather than 0 so the adapter branches on a real absence, not on
            # a magic number it would have to re-interpret at every call.
            stream_maxlen=env.stream_maxlen or None,
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
