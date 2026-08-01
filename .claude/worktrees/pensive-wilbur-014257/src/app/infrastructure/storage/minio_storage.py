"""MinIO adapter for the ``StorageProvider`` port (02-port-contracts §1.6).

Phase 2.4. Same split as ``cache/redis_cache.py`` (2.3): a factory builds
the technology client, a thin adapter class implements the port over it
(structural Protocol match -- no inheritance).

Credentials are NOT read from ``Settings`` -- deliberately. The settings
contract (05 §2) defines only ``MINIO_ENDPOINT``/``MINIO_BUCKET``; the
access/secret keys live in Vault (05 §3, ``secret/data/minio``). The factory
therefore takes them as explicit parameters. Since 6.1-هـ-1 the Composition
Root sources them for real: its async ``connect_storage`` reads the Vault
secret at startup and binds this adapter into the ``StorageHandle`` built at
``from_env`` time (``framework/di/storage_handle.py``); the live integration
tests keep injecting a dedicated, bucket-scoped service account directly.

minio-py is a SYNCHRONOUS client (urllib3). Every port method offloads the
blocking call through ``asyncio.to_thread`` so the event loop stays free
(ARC "async in all I/O"; the same treatment planned for the sync knowledge
parsers). One thread per in-flight storage call is acceptable at v1's file
sizes/limits (07-nfr §4: ≤25 MB objects).

Error policy -- translate, and keep the one caller-branchable case distinct:
``S3Error(code="NoSuchKey")`` on ``get`` becomes ``NotFoundError``
(``common.not_found``/404) because a missing object is a normal, decidable
outcome for callers (the port's ``get`` returns ``bytes``, never ``None``,
so absence MUST surface as an exception). Everything else (connection
refused, timeouts, AccessDenied, bucket trouble, ...) is an infrastructure
fault folded into ``common.internal`` (the R6 precedent) -- minio/urllib3
exception types never escape this adapter.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from io import BytesIO
from typing import TYPE_CHECKING

import urllib3
from minio import Minio
from minio.error import MinioException, S3Error

from app.framework.errors import AppError, NotFoundError

if TYPE_CHECKING:
    from app.framework.settings.settings import MinioSettings

# Fail fast instead of hanging a request coroutine on a dead object store
# (the 2.3 redis-factory precedent; 07-nfr latency budgets are sub-second).
_CONNECT_TIMEOUT_S = 2.0
_READ_TIMEOUT_S = 10.0
_RETRIES = 1


def _build(endpoint: str, *, secure: bool, access_key: str, secret_key: str, region: str) -> Minio:
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        # Explicit, never inferred: without it minio-py issues a live
        # GetBucketLocation against this client's own endpoint before signing
        # anything -- fatal for the signing client, whose endpoint is the
        # PUBLIC address the server usually cannot reach (MinioSettings.region).
        region=region,
        http_client=urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=_CONNECT_TIMEOUT_S, read=_READ_TIMEOUT_S),
            retries=urllib3.Retry(total=_RETRIES, backoff_factor=0.2),
        ),
    )


def create_minio_client(settings: MinioSettings, *, access_key: str, secret_key: str) -> Minio:
    """Build the shared MinIO client (Composition Root / test harness only).

    ``access_key``/``secret_key`` are explicit parameters -- NOT settings
    fields -- because their source of truth is Vault ``secret/data/minio``
    (05 §3); see the module docstring.
    """
    return _build(
        settings.endpoint,
        secure=settings.secure,
        access_key=access_key,
        secret_key=secret_key,
        region=settings.region,
    )


def create_minio_signing_client(
    settings: MinioSettings, client: Minio, *, access_key: str, secret_key: str
) -> Minio:
    """The client whose endpoint presigned URLs are SIGNED against (7.1).

    Returns ``client`` ITSELF whenever no distinct public address is
    configured -- the single-address deployment stays a single client with a
    single connection pool, exactly as before 7.1. A second client is built
    only for the split-address case (08 §2's Compose topology, where the
    server reaches ``minio:9000`` but the browser must be handed a routable
    host), because SigV4 covers the host header: a presigned URL signed
    against the internal name cannot be repointed afterwards, not by Nginx
    and not by string surgery, without invalidating the signature.
    """
    if not settings.public_endpoint:
        return client
    return _build(
        settings.signing_endpoint,
        secure=settings.signing_secure,
        access_key=access_key,
        secret_key=secret_key,
        region=settings.region,
    )


class MinioStorage:
    """MinIO-backed ``StorageProvider`` (02 §1.6, structural Protocol match).

    One adapter instance serves ONE bucket (``MinioSettings.bucket``): the
    port's keys are already workspace-prefixed by the ``files`` module
    (``StorageKey`` = ``<workspace_id>/...``), so the bucket is a fixed
    deployment concern, not a per-call argument.

    Two clients, one bucket (7.1): ``client`` is the DATA path (put/get/
    delete -- server-to-MinIO, inside the deployment network) and
    ``signing_client`` is the PRESIGN path (URLs handed to an end user, which
    must name an address that user can resolve). They are the same object
    unless ``MinioSettings.public_endpoint`` says otherwise, so every existing
    two-argument construction -- the live test harness included -- keeps its
    pre-7.1 behaviour untouched.
    """

    def __init__(self, client: Minio, bucket: str, signing_client: Minio | None = None) -> None:
        self._client = client
        self._bucket = bucket
        self._signing_client = signing_client if signing_client is not None else client

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        def _put() -> None:
            self._client.put_object(
                self._bucket, key, BytesIO(data), length=len(data), content_type=content_type
            )

        try:
            await asyncio.to_thread(_put)
        except (MinioException, urllib3.exceptions.HTTPError, OSError) as exc:
            raise _translate(exc) from exc

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            response = self._client.get_object(self._bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(_get)
        except (MinioException, urllib3.exceptions.HTTPError, OSError) as exc:
            raise _translate(exc) from exc

    async def delete(self, key: str) -> None:
        # S3 DELETE of a missing object succeeds (204) -- the port's delete
        # is silently idempotent, like cache.delete / access.remove.
        def _delete() -> None:
            self._client.remove_object(self._bucket, key)

        try:
            await asyncio.to_thread(_delete)
        except (MinioException, urllib3.exceptions.HTTPError, OSError) as exc:
            raise _translate(exc) from exc

    async def presign_get(self, key: str, ttl_s: int) -> str:
        def _presign() -> str:
            return self._signing_client.presigned_get_object(
                self._bucket, key, expires=timedelta(seconds=ttl_s)
            )

        try:
            return await asyncio.to_thread(_presign)
        except (MinioException, urllib3.exceptions.HTTPError, OSError, ValueError) as exc:
            raise _translate(exc) from exc

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        # DOCUMENTED LIMITATION: minio-py cannot sign request headers into a
        # presigned PUT, so ``content_type`` is advisory here -- the uploader
        # sends it, MinIO stores it, but the URL does not cryptographically
        # pin it. The design already re-verifies uploads server-side at
        # ``files.CompleteUpload`` (size/sha256/type against the registered
        # metadata), which is the enforcement point that matters.
        del content_type

        def _presign() -> str:
            return self._signing_client.presigned_put_object(
                self._bucket, key, expires=timedelta(seconds=ttl_s)
            )

        try:
            return await asyncio.to_thread(_presign)
        except (MinioException, urllib3.exceptions.HTTPError, OSError, ValueError) as exc:
            raise _translate(exc) from exc


def _translate(exc: Exception) -> AppError:
    """Map a driver-level failure onto the shared framework hierarchy
    (03-api-spec §4). ``NoSuchKey`` is the one caller-branchable case
    (``common.not_found``/404); everything else -- connection refused,
    timeout, AccessDenied, NoSuchBucket, invalid expiry -- folds into the
    500-class ``common.internal`` (R6 precedent)."""
    if isinstance(exc, S3Error) and exc.code == "NoSuchKey":
        return NotFoundError("object does not exist in storage")
    return AppError("storage operation failed", code="common.internal")
