"""The presigned-URL address split (7.1, 08-local-runbook §2).

``MINIO_ENDPOINT`` is where the SERVER reaches MinIO; inside the Compose
topology that is the service name ``minio:9000``, which no end-user browser
resolves. SigV4 signs the host, so a presigned URL cannot be repointed after
signing -- it must be signed against the address the client will use. These
tests pin both halves: the settings-level fallback, and the fact that the
DATA path and the PRESIGN path use different clients only when they should.

No live MinIO: ``Minio(...)`` opens no connection at construction, and the
adapter's presign methods are exercised against stand-ins.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.framework.settings.settings import MinioSettings
from app.infrastructure.storage.minio_storage import (
    MinioStorage,
    create_minio_client,
    create_minio_signing_client,
)

_KEYS: dict[str, str] = {"access_key": "ak", "secret_key": "sk"}


class _SpyClient:
    """Records which client a presign call landed on."""

    def __init__(self, name: str) -> None:
        self.name = name

    def presigned_get_object(self, bucket: str, key: str, expires: Any) -> str:
        return f"{self.name}:get:{bucket}/{key}"

    def presigned_put_object(self, bucket: str, key: str, expires: Any) -> str:
        return f"{self.name}:put:{bucket}/{key}"


# --------------------------------------------------------------------------
# Settings-level fallback -- the ONE place public/internal is decided.
# --------------------------------------------------------------------------


def test_signing_endpoint_falls_back_to_endpoint_when_no_public_address() -> None:
    settings = MinioSettings(endpoint="minio:9000")
    assert settings.signing_endpoint == "minio:9000"


def test_signing_endpoint_prefers_the_public_address() -> None:
    settings = MinioSettings(endpoint="minio:9000", public_endpoint="localhost:19000")
    assert settings.signing_endpoint == "localhost:19000"


def test_signing_secure_inherits_secure_when_unset() -> None:
    assert MinioSettings(secure=True).signing_secure is True
    assert MinioSettings(secure=False).signing_secure is False


@pytest.mark.parametrize("public_secure", [True, False])
def test_signing_secure_overrides_secure_when_set(public_secure: bool) -> None:
    """The public hop is exactly where TLS commonly differs from the internal
    one -- plaintext inside the network, https at the edge."""
    settings = MinioSettings(secure=False, public_secure=public_secure)
    assert settings.signing_secure is public_secure


# --------------------------------------------------------------------------
# Factory -- one client unless a public address genuinely differs.
# --------------------------------------------------------------------------


def test_signing_client_is_the_same_object_without_a_public_endpoint() -> None:
    """The single-address deployment keeps ONE connection pool, exactly as
    before 7.1 -- an identity check, not an equality check."""
    settings = MinioSettings(endpoint="minio:9000")
    client = create_minio_client(settings, **_KEYS)
    assert create_minio_signing_client(settings, client, **_KEYS) is client


def test_signing_client_is_a_distinct_client_bound_to_the_public_endpoint() -> None:
    settings = MinioSettings(endpoint="minio:9000", public_endpoint="files.example.com")
    client = create_minio_client(settings, **_KEYS)
    signing = create_minio_signing_client(settings, client, **_KEYS)

    assert signing is not client
    assert "files.example.com" in signing._base_url.host
    assert "minio:9000" in client._base_url.host


def test_both_clients_carry_an_explicit_region() -> None:
    """Without a pinned region minio-py resolves it with a live
    GetBucketLocation call against the CLIENT'S OWN endpoint -- which for the
    signing client is the public address the server usually cannot reach, so
    presigning died on a connection error before signing anything. Found live
    on the first containerised boot."""
    settings = MinioSettings(endpoint="minio:9000", public_endpoint="files.example.com")
    client = create_minio_client(settings, **_KEYS)
    signing = create_minio_signing_client(settings, client, **_KEYS)

    assert client._base_url.region == settings.region
    assert signing._base_url.region == settings.region
    assert settings.region, "an empty region would restore the network lookup"


def test_signing_client_honours_the_public_tls_setting() -> None:
    """Signed URLs must carry the scheme the CLIENT will use; signing an
    https edge with an http-derived signature yields a URL that fails at the
    edge, not at MinIO."""
    settings = MinioSettings(
        endpoint="minio:9000", secure=False, public_endpoint="files.example.com", public_secure=True
    )
    signing = create_minio_signing_client(settings, create_minio_client(settings, **_KEYS), **_KEYS)
    assert signing._base_url.is_https is True


# --------------------------------------------------------------------------
# Adapter -- data path and presign path go to the right client.
# --------------------------------------------------------------------------


async def test_presign_get_uses_the_signing_client() -> None:
    data, signing = _SpyClient("data"), _SpyClient("signing")
    storage = MinioStorage(data, "bucket", signing)  # type: ignore[arg-type]
    assert await storage.presign_get("k", 60) == "signing:get:bucket/k"


async def test_presign_put_uses_the_signing_client() -> None:
    data, signing = _SpyClient("data"), _SpyClient("signing")
    storage = MinioStorage(data, "bucket", signing)  # type: ignore[arg-type]
    assert await storage.presign_put("k", 60, "text/plain") == "signing:put:bucket/k"


async def test_omitted_signing_client_leaves_presign_on_the_data_client() -> None:
    """Every pre-7.1 two-argument construction -- the live test harness
    included -- keeps its old behaviour."""
    data = _SpyClient("data")
    storage = MinioStorage(data, "bucket")  # type: ignore[arg-type]
    assert await storage.presign_get("k", 60) == "data:get:bucket/k"
    assert await storage.presign_put("k", 60, "text/plain") == "data:put:bucket/k"
