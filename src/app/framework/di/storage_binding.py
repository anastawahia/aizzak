"""MinIO ``StorageProvider`` binding, shared by TWO composition roots (the
API's ``CompositionRoot.connect_storage`` and the knowledge worker's
``build_knowledge_worker_from_env``, deferred-adapters-plan.md step 15).

Fetching MinIO's access/secret keys is an ASYNC Vault read (``await
secrets.get_secret("secret/data/minio")``, 05 §3) -- the one piece of MinIO's
wiring that cannot happen inside a plain synchronous factory
(``CompositionRoot.from_env``'s own docstring has carried this debt since
2.4). This module holds the ONE place that validates the secret's shape and
builds the two 2.4 ``MinioStorage`` clients (the plain one and 7.1's
signing one, split only when a public endpoint differs), so that shape check
has a single home instead of two copies free to drift the moment a second
caller needed it -- which is exactly what the knowledge worker is.

Both callers share the SAME idempotence guard (``storage.is_bound``): a
lifespan re-entered in tests, or a second worker construction in the same
process, must not re-read Vault or swap a live adapter out from under
callers already holding the handle.
"""

from __future__ import annotations

from app.framework.di.storage_handle import StorageHandle
from app.framework.errors import ValidationError
from app.framework.observability import get_logger
from app.framework.ports import SecretsProvider
from app.framework.settings import MinioSettings
from app.infrastructure.storage.minio_storage import (
    MinioStorage,
    create_minio_client,
    create_minio_signing_client,
)

_logger = get_logger(__name__)

# 05 §3's KV entry for the object store's service account:
# `{"access_key": "...", "secret_key": "..."}`. Read at STARTUP, not boot --
# both callers' own docstrings explain the split.
_MINIO_SECRET_PATH = "secret/data/minio"


async def read_minio_credentials(secrets: SecretsProvider) -> tuple[str, str]:
    """Read + shape-validate the MinIO service account out of Vault
    (``(access_key, secret_key)``).

    Extracted out of ``bind_minio`` (BE-ADM-014) so a THIRD caller --
    ``app.ops.purge``'s own composition, which needs a raw ``Minio`` client
    but has no ``StorageHandle`` to bind into -- shares this exact shape
    check instead of copying it. Raises ``ValidationError`` (never a driver
    exception) on a missing/malformed secret, so every caller's own
    fail-fast boot policy applies unchanged.
    """
    secret = await secrets.get_secret(_MINIO_SECRET_PATH)
    access_key = secret.get("access_key")
    secret_key = secret.get("secret_key")
    if not (isinstance(access_key, str) and access_key):
        raise ValidationError(f"{_MINIO_SECRET_PATH} is missing a non-empty 'access_key'")
    if not (isinstance(secret_key, str) and secret_key):
        raise ValidationError(f"{_MINIO_SECRET_PATH} is missing a non-empty 'secret_key'")
    return access_key, secret_key


async def bind_minio(
    storage: StorageHandle, secrets: SecretsProvider, settings: MinioSettings
) -> None:
    """Bind the MinIO ``StorageProvider`` into ``storage`` (6.1-هـ-1, closing
    debt (ز); deferred-adapters-plan.md step 15 makes this the SECOND
    caller, the knowledge worker, alongside the API's
    ``CompositionRoot.connect_storage``).

    Failure policy is fail-fast, the ``FIREBASE_PROJECT_ID``/D5 boot
    precedent: Vault unreachable or a malformed secret raises out of startup
    and the process refuses to boot, rather than booting a replica whose
    every file operation would 500. Idempotent: a lifespan re-entered in
    tests re-runs its hooks, and re-reading the secret to rebuild an adapter
    that is already live would be pure churn.
    """
    if storage.is_bound:
        return
    access_key, secret_key = await read_minio_credentials(secrets)
    client = create_minio_client(settings, access_key=access_key, secret_key=secret_key)
    # 7.1: presigned URLs must be signed against the address the CLIENT
    # will use, which in the Compose topology is not ``minio:9000``.
    # Same object as ``client`` unless a public endpoint is configured.
    signing_client = create_minio_signing_client(
        settings, client, access_key=access_key, secret_key=secret_key
    )
    storage.bind(MinioStorage(client, settings.bucket, signing_client))
    _logger.info(
        "storage_binding.minio_bound",
        extra={
            "endpoint": settings.endpoint,
            "signing_endpoint": settings.signing_endpoint,
            "bucket": settings.bucket,
        },
    )
