"""Unit tests for the presigned-URL TTL configuration (3.79,
``MinioSettings.presign_put_ttl_s`` / ``presign_get_ttl_s``).

These were module constants inside the files use-case until 3.79. Making them
operator-editable is only safe BECAUSE the bounds are enforced: SigV4 signs a
lifetime of 1s..7d and nothing else, so a typo'd ``MINIO_PRESIGN_PUT_TTL_S``
would otherwise turn every ``POST /files`` into a 500 at the first presign call
— a value that is accepted at boot and then unusable for the whole life of the
process. The bound test is the point of this module; the mapping tests exist so
the two keys cannot silently swap (900/300 look interchangeable at a glance,
and a swap would be invisible until an upload of any size timed out).

``env_file`` is neutralised for every test here for the ``STREAM_MAXLEN``
suite's reason: this repository HAS a real (gitignored) ``.env``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.framework.settings.settings import MinioSettings
from app.infrastructure.config import env_settings
from app.infrastructure.config.env_settings import load_settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(env_settings._EnvSettings.model_config, "env_file", None)
    monkeypatch.delenv("MINIO_PRESIGN_PUT_TTL_S", raising=False)
    monkeypatch.delenv("MINIO_PRESIGN_GET_TTL_S", raising=False)


def test_defaults_are_the_values_the_module_constants_carried() -> None:
    """Moving a constant into configuration must not change behaviour for an
    operator who sets nothing."""
    settings = load_settings()

    assert settings.minio.presign_put_ttl_s == 900
    assert settings.minio.presign_get_ttl_s == 300


def test_each_key_maps_to_its_own_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIO_PRESIGN_PUT_TTL_S", "1800")
    monkeypatch.setenv("MINIO_PRESIGN_GET_TTL_S", "60")

    settings = load_settings()

    assert settings.minio.presign_put_ttl_s == 1800
    assert settings.minio.presign_get_ttl_s == 60


@pytest.mark.parametrize("value", [0, -1])
def test_a_non_positive_ttl_fails_at_boot(value: int) -> None:
    """Zero is not "no expiry" to a signer — it is a URL that is already dead
    when it is handed to the client."""
    with pytest.raises(ValidationError):
        MinioSettings(presign_put_ttl_s=value)
    with pytest.raises(ValidationError):
        MinioSettings(presign_get_ttl_s=value)


def test_a_ttl_beyond_sigv4s_ceiling_fails_at_boot() -> None:
    """7 days + 1s is the first value minio-py refuses to sign. Accepting it
    here would mean a process that boots green and then cannot register a
    single upload."""
    with pytest.raises(ValidationError):
        MinioSettings(presign_put_ttl_s=604_801)

    assert MinioSettings(presign_put_ttl_s=604_800).presign_put_ttl_s == 604_800


def test_an_out_of_range_env_value_aborts_the_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env layer deliberately repeats no bound of its own — the failure
    must still happen, and it must happen while loading, not later."""
    monkeypatch.setenv("MINIO_PRESIGN_GET_TTL_S", "0")

    with pytest.raises(ValidationError):
        load_settings()
