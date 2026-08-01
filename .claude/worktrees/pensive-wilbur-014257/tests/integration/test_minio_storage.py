"""Live-MinIO tests for ``MinioStorage`` (02-port-contracts §1.6, Phase 2.4).

Runs against a real, local MinIO (no Docker -- see
``tests/integration/conftest.py``); auto-skips via ``live_minio`` when
unreachable. The harness uses a dedicated service account scoped to bucket
``aizzak-test`` ONLY (provisioned once, status-doc §3.19 -- the PG
``aizzak_owner``/``app_rw`` precedent), so the server's root keys never
appear here and other buckets are untouchable by construction. Every test
writes only under its own unique ``aizzak-test/<uuid7>/`` object prefix and
sweeps it afterwards.

Behaviours pinned beyond the plain round-trip: raw non-UTF-8 bytes survive
verbatim; ``content_type`` is persisted server-side; a missing object on
``get`` raises ``NotFoundError`` (``common.not_found``/404 -- the port
returns ``bytes``, never ``None``); ``delete`` is silently idempotent;
presigned GET URLs actually serve the object over plain HTTP to an
UNAUTHENTICATED client and genuinely expire; presigned PUT URLs accept an
unauthenticated upload that then reads back through the port; and every
driver failure surfaces as a framework error, never a raw minio/urllib3
exception.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from collections.abc import AsyncIterator

import pytest
from minio import Minio

from app.framework.errors import AppError, NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import MinioSettings
from app.infrastructure.storage.minio_storage import MinioStorage, create_minio_client
from tests.integration.conftest import LiveMinio

pytestmark = [pytest.mark.live_minio]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
@pytest.fixture
async def object_prefix(minio_client: Minio, live_minio: LiveMinio) -> AsyncIterator[str]:
    """A unique per-test object namespace, swept after the test so the shared
    bucket never accumulates suite leftovers."""
    prefix = f"{new_uuid7()}/"
    try:
        yield prefix
    finally:

        def _sweep() -> None:
            bucket = live_minio.settings.bucket
            for obj in minio_client.list_objects(bucket, prefix=prefix, recursive=True):
                if obj.object_name is not None:
                    minio_client.remove_object(bucket, obj.object_name)

        await asyncio.to_thread(_sweep)


def _http_get(url: str) -> tuple[int, bytes]:
    """Fetch a presigned URL as an UNAUTHENTICATED plain-HTTP client (what a
    browser would do) -- the whole point of presigning."""
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _http_put(url: str, data: bytes, content_type: str) -> int:
    request = urllib.request.Request(
        url, data=data, method="PUT", headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


# --------------------------------------------------------------------------- #
# (1)-(4) put/get round-trip                                                  #
# --------------------------------------------------------------------------- #
async def test_put_then_get_round_trips_raw_bytes(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    key = object_prefix + "blob.bin"
    payload = b"\x00\xff\xfe binary \xd8\x00 not-utf8" * 100

    await minio_storage.put(key, payload, "application/octet-stream")

    assert await minio_storage.get(key) == payload


async def test_get_missing_object_raises_not_found(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        await minio_storage.get(object_prefix + "never-written.bin")

    assert excinfo.value.code == "common.not_found"
    assert excinfo.value.status == 404


async def test_put_overwrites_existing_object(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    key = object_prefix + "doc.txt"
    await minio_storage.put(key, b"first", "text/plain")

    await minio_storage.put(key, b"second", "text/plain")

    assert await minio_storage.get(key) == b"second"


async def test_content_type_is_persisted_server_side(
    minio_storage: MinioStorage, minio_client: Minio, live_minio: LiveMinio, object_prefix: str
) -> None:
    key = object_prefix + "page.html"

    await minio_storage.put(key, b"<html></html>", "text/html")

    stat = await asyncio.to_thread(minio_client.stat_object, live_minio.settings.bucket, key)
    assert stat.content_type == "text/html"


# --------------------------------------------------------------------------- #
# (5)-(6) delete                                                              #
# --------------------------------------------------------------------------- #
async def test_delete_removes_and_is_idempotent(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    key = object_prefix + "gone.bin"
    await minio_storage.put(key, b"x", "application/octet-stream")

    await minio_storage.delete(key)
    with pytest.raises(NotFoundError):
        await minio_storage.get(key)

    await minio_storage.delete(key)  # second delete: silent no-op, no error


async def test_workspace_prefixed_keys_round_trip(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    """Keys shaped exactly like the files module's ``StorageKey``
    (``<workspace_id>/<file_id>/<name>``) -- slashes are object-name chars,
    not directories, and must survive verbatim."""
    key = f"{object_prefix}{new_uuid7()}/{new_uuid7()}/تقرير-نهائي.pdf"
    await minio_storage.put(key, b"%PDF-1.4 fake", "application/pdf")

    assert await minio_storage.get(key) == b"%PDF-1.4 fake"


# --------------------------------------------------------------------------- #
# (7)-(10) presigned URLs                                                     #
# --------------------------------------------------------------------------- #
async def test_presign_get_serves_the_object_to_an_unauthenticated_client(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    key = object_prefix + "shared.txt"
    await minio_storage.put(key, b"presigned payload", "text/plain")

    url = await minio_storage.presign_get(key, ttl_s=120)
    status, body = await asyncio.to_thread(_http_get, url)

    assert status == 200
    assert body == b"presigned payload"


async def test_presign_get_url_actually_expires(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    key = object_prefix + "shortlived.txt"
    await minio_storage.put(key, b"x", "text/plain")
    url = await minio_storage.presign_get(key, ttl_s=1)

    await asyncio.sleep(2.0)
    status, body = await asyncio.to_thread(_http_get, url)

    assert status == 403  # AccessDenied: signature expired
    assert b"expired" in body.lower() or b"accessdenied" in body.lower().replace(b" ", b"")


async def test_presign_get_for_missing_object_signs_but_serving_404s(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    """Presigning is a local signature computation -- it cannot know whether
    the object exists; the 404 surfaces at fetch time (S3 semantics)."""
    url = await minio_storage.presign_get(object_prefix + "ghost.bin", ttl_s=60)

    status, _ = await asyncio.to_thread(_http_get, url)

    assert status == 404


async def test_presign_put_accepts_an_unauthenticated_upload(
    minio_storage: MinioStorage, object_prefix: str
) -> None:
    """The files-module upload flow: presign -> client PUTs bytes -> object
    readable through the port."""
    key = object_prefix + "uploaded-via-url.bin"
    url = await minio_storage.presign_put(key, ttl_s=120, content_type="application/octet-stream")

    status = await asyncio.to_thread(_http_put, url, b"uploaded body", "application/octet-stream")

    assert status == 200
    assert await minio_storage.get(key) == b"uploaded body"


# --------------------------------------------------------------------------- #
# (11)-(12) failure translation                                               #
# --------------------------------------------------------------------------- #
async def test_connection_failure_surfaces_as_common_internal() -> None:
    # A closed local port refuses instantly (ECONNREFUSED) -- no timeout wait.
    dead_client = create_minio_client(
        MinioSettings(endpoint="127.0.0.1:9002", bucket="aizzak-test", secure=False),
        access_key="irrelevant",
        secret_key="irrelevant",
    )
    storage = MinioStorage(dead_client, "aizzak-test")

    with pytest.raises(AppError) as excinfo:
        await storage.get("any-key")

    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


async def test_out_of_scope_bucket_is_denied_not_leaked(
    minio_client: Minio, object_prefix: str
) -> None:
    """The scoped harness account must NOT reach other buckets: AccessDenied
    (or NoSuchBucket) translates to ``common.internal``, and definitely not
    a successful read -- proves the blast radius of the test credentials."""
    foreign = MinioStorage(minio_client, "some-other-bucket")

    with pytest.raises(AppError) as excinfo:
        await foreign.get(object_prefix + "nope.bin")

    assert excinfo.value.code == "common.internal"
