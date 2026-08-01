"""Unit tests for ``framework/di/storage_binding.py::bind_minio`` (step 15 of
``deferred-adapters-plan.md`` -- extracted out of
``CompositionRoot.connect_storage`` so a SECOND caller, the knowledge
worker's own composition root, can bind the identical adapter without
importing the Composition Root's entire module/agent graph).

``tests/unit/test_composition_root.py``'s ``test_connect_storage_*`` tests
cover the SAME behaviour through the API-side caller and are left unchanged
(``connect_storage`` is now a two-line delegator); these tests exercise
``bind_minio`` directly, the way ``build_knowledge_worker_from_env`` -- its
second caller -- does.
"""

from __future__ import annotations

import pytest

from app.framework.di.storage_binding import bind_minio
from app.framework.di.storage_handle import StorageHandle
from app.framework.errors import ValidationError
from app.framework.settings import MinioSettings
from app.infrastructure.storage.minio_storage import MinioStorage


class _FakeSecrets:
    """A ``SecretsProvider`` fake that serves 05 §3's minio entry and counts
    reads -- the ``test_composition_root.py`` precedent, reused here so both
    callers of ``bind_minio`` are proven against the identical fake shape."""

    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload: dict[str, object] = (
            {"access_key": "minio-ak", "secret_key": "minio-sk"} if payload is None else payload
        )
        self.reads: list[str] = []

    async def get_secret(self, path: str) -> dict[str, object]:
        self.reads.append(path)
        return self.payload

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        raise AssertionError("not exercised")

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        raise AssertionError("not exercised")


async def test_bind_minio_binds_from_a_well_formed_secret() -> None:
    storage = StorageHandle()
    secrets = _FakeSecrets()

    await bind_minio(storage, secrets, MinioSettings())

    assert secrets.reads == ["secret/data/minio"]
    assert storage.is_bound is True
    bound = storage._provider
    assert isinstance(bound, MinioStorage)
    assert bound._bucket == MinioSettings().bucket


async def test_bind_minio_is_idempotent_across_reentries() -> None:
    storage = StorageHandle()
    secrets = _FakeSecrets()

    await bind_minio(storage, secrets, MinioSettings())
    first = storage._provider
    await bind_minio(storage, secrets, MinioSettings())

    assert secrets.reads == ["secret/data/minio"]  # ONE read, not two
    assert storage._provider is first


@pytest.mark.parametrize(
    "payload",
    [
        {"secret_key": "sk"},  # access_key absent
        {"access_key": "", "secret_key": "sk"},  # access_key empty
    ],
)
async def test_bind_minio_rejects_a_malformed_access_key(payload: dict[str, object]) -> None:
    storage = StorageHandle()

    with pytest.raises(ValidationError, match="access_key"):
        await bind_minio(storage, _FakeSecrets(payload), MinioSettings())
    assert storage.is_bound is False


@pytest.mark.parametrize(
    "payload",
    [
        {"access_key": "ak"},  # secret_key absent
        {"access_key": "ak", "secret_key": ""},  # secret_key empty
    ],
)
async def test_bind_minio_rejects_a_malformed_secret_key(payload: dict[str, object]) -> None:
    storage = StorageHandle()

    with pytest.raises(ValidationError, match="secret_key"):
        await bind_minio(storage, _FakeSecrets(payload), MinioSettings())
    assert storage.is_bound is False
