"""Unit tests for the Vault adapter's pure helpers and its exact hvac call
surface (``infrastructure/secrets/vault_secrets.py``, Phase 2.6). No marker,
no Docker, no network: plain functions plus a hand-rolled fake standing in
for ``hvac.Client``'s nested ``secrets.kv.v2``/``secrets.transit`` attribute
shape (mirrors ``test_qdrant_mapping.py``'s no-network unit style).
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from hvac.exceptions import Forbidden, InvalidPath, Unauthorized, VaultError

from app.framework.errors import AppError, NotFoundError, ValidationError
from app.framework.settings.settings import VaultSettings
from app.framework.types import Json
from app.infrastructure.secrets.vault_secrets import (
    VaultSecrets,
    _guard_key_name,
    _parse_kv_path,
    _translate,
    _translate_kv,
    create_approle_relogin,
)


# --------------------------------------------------------------------------- #
# U1 -- _parse_kv_path                                                        #
# --------------------------------------------------------------------------- #
def test_parse_kv_path_accepts_the_catalog_literal_shape() -> None:
    assert _parse_kv_path("secret/data/minio") == ("secret", "minio")
    assert _parse_kv_path("secret/data/providers/platform") == ("secret", "providers/platform")


@pytest.mark.parametrize(
    "path",
    [
        "secret/metadata/x",  # the metadata/ endpoint, not the catalog's data/ shape
        "minio",  # bare KV v2 path -- no mount, no /data/ infix
        "secret/data/",  # empty relative path
        "/data/x",  # empty mount
        "secret/data/../etc",  # a ".." path segment
    ],
)
def test_parse_kv_path_rejects_every_non_catalog_shape(path: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _parse_kv_path(path)

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# U2 -- _guard_key_name                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key_name", ["tenant-secrets", "aizzak-test-018f1234abcd7000"])
def test_guard_key_name_accepts_bare_tokens(key_name: str) -> None:
    _guard_key_name(key_name)  # must not raise


@pytest.mark.parametrize("key_name", ["../evil", "a/b", "a b", "", "k.v"])
def test_guard_key_name_rejects_everything_else(key_name: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _guard_key_name(key_name)

    assert excinfo.value.code == "common.validation_error"
    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# U3 -- _translate                                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exc", [VaultError("boom"), OSError("connection refused")])
def test_translate_always_maps_to_common_internal(exc: Exception) -> None:
    result = _translate(exc)

    assert isinstance(result, AppError)
    assert result.code == "common.internal"
    assert result.status == 500


# --------------------------------------------------------------------------- #
# U4 -- _translate_kv                                                         #
# --------------------------------------------------------------------------- #
def test_translate_kv_maps_invalid_path_to_not_found() -> None:
    result = _translate_kv(InvalidPath("no such path"))

    assert isinstance(result, NotFoundError)
    assert result.code == "common.not_found"
    assert result.status == 404


@pytest.mark.parametrize("exc", [Forbidden("permission denied"), OSError("connection refused")])
def test_translate_kv_maps_everything_else_to_common_internal(exc: Exception) -> None:
    result = _translate_kv(exc)

    assert isinstance(result, AppError)
    assert not isinstance(result, NotFoundError)
    assert result.code == "common.internal"
    assert result.status == 500


# --------------------------------------------------------------------------- #
# U5 -- VaultSecrets over a fake hvac.Client (structural, no network)         #
# --------------------------------------------------------------------------- #
class _FakeKvV2:
    """Stands in for ``hvac.Client().secrets.kv.v2`` -- records every call so
    tests can assert the adapter's exact keyword-call surface."""

    def __init__(self, inner_data: Json) -> None:
        self._inner_data = inner_data
        self.calls: list[dict[str, Any]] = []

    def read_secret_version(
        self, *, path: str, mount_point: str, raise_on_deleted_version: bool
    ) -> Json:
        self.calls.append(
            {
                "path": path,
                "mount_point": mount_point,
                "raise_on_deleted_version": raise_on_deleted_version,
            }
        )
        # A realistic KV v2 envelope -- ``metadata`` must NEVER leak through
        # the port, only ``data.data``.
        return {"data": {"data": self._inner_data, "metadata": {"version": 1}}}


class _FakeTransit:
    """Stands in for ``hvac.Client().secrets.transit`` -- a genuine base64
    round-trip (not just an echo) so non-UTF-8 bytes are truly exercised,
    while still recording the exact keyword-call surface."""

    def __init__(self) -> None:
        self.encrypt_calls: list[dict[str, Any]] = []
        self.decrypt_calls: list[dict[str, Any]] = []

    def encrypt_data(self, *, name: str, plaintext: str, mount_point: str) -> Json:
        self.encrypt_calls.append(
            {"name": name, "plaintext": plaintext, "mount_point": mount_point}
        )
        return {"data": {"ciphertext": f"vault:v1:{plaintext}", "key_version": 1}}

    def decrypt_data(self, *, name: str, ciphertext: str, mount_point: str) -> Json:
        self.decrypt_calls.append(
            {"name": name, "ciphertext": ciphertext, "mount_point": mount_point}
        )
        return {"data": {"plaintext": ciphertext.removeprefix("vault:v1:")}}


class _FakeSecretsKv:
    def __init__(self, v2: _FakeKvV2) -> None:
        self.v2 = v2


class _FakeSecrets:
    def __init__(self, kv: _FakeKvV2, transit: _FakeTransit) -> None:
        self.kv = _FakeSecretsKv(kv)
        self.transit = transit


class _FakeVaultClient:
    """Structural stand-in for ``hvac.Client`` -- only the attribute chain
    ``VaultSecrets`` actually walks (``.secrets.kv.v2``, ``.secrets.transit``).
    """

    def __init__(self, kv: _FakeKvV2, transit: _FakeTransit) -> None:
        self.secrets = _FakeSecrets(kv, transit)


async def test_get_secret_returns_only_the_inner_data_with_the_exact_call_surface() -> None:
    kv = _FakeKvV2({"access_key": "AKIAEXAMPLE", "secret_key": "shh"})
    secrets = VaultSecrets(_FakeVaultClient(kv, _FakeTransit()))

    result = await secrets.get_secret("secret/data/minio")

    assert result == {"access_key": "AKIAEXAMPLE", "secret_key": "shh"}
    assert kv.calls == [
        {"path": "minio", "mount_point": "secret", "raise_on_deleted_version": True}
    ]


# --------------------------------------------------------------------------- #
# U6 -- off-contract 200 bodies translate, never escape raw (verifier fix)    #
# --------------------------------------------------------------------------- #
class _BrokenKvV2:
    """A 200-shaped KV response that is off the KV-v2 contract: no inner
    ``data.data`` key -- unwrapping it must translate, never raise a raw
    ``KeyError`` (2.6 verifier follow-up, R6)."""

    def read_secret_version(self, **_: Any) -> Json:
        return {"data": {"metadata": {"version": 1}}}


class _BrokenTransit:
    """200-shaped Transit responses that are off-contract: ``encrypt_data``
    drops ``ciphertext`` (KeyError path) and ``decrypt_data`` returns a
    non-base64 ``plaintext`` body (``binascii.Error``/``ValueError`` path --
    ``validate=True`` makes corruption raise instead of silently decoding to
    wrong secret bytes)."""

    def encrypt_data(self, **_: Any) -> Json:
        return {"data": {"key_version": 1}}

    def decrypt_data(self, **_: Any) -> Json:
        return {"data": {"plaintext": "!!! not base64 !!!"}}


async def test_off_contract_kv_body_translates_to_common_internal() -> None:
    secrets = VaultSecrets(_FakeVaultClient(_BrokenKvV2(), _FakeTransit()))

    with pytest.raises(AppError) as excinfo:
        await secrets.get_secret("secret/data/minio")

    assert not isinstance(excinfo.value, NotFoundError)  # a malformed 200 is NOT "absent"
    assert excinfo.value.code == "common.internal"
    assert excinfo.value.status == 500


async def test_off_contract_transit_bodies_translate_to_common_internal() -> None:
    secrets = VaultSecrets(_FakeVaultClient(_FakeKvV2({}), _BrokenTransit()))

    with pytest.raises(AppError) as encinfo:
        await secrets.encrypt("tenant-secrets", b"x")
    assert encinfo.value.code == "common.internal"

    with pytest.raises(AppError) as decinfo:
        await secrets.decrypt("tenant-secrets", "vault:v1:whatever")
    assert decinfo.value.code == "common.internal"


async def test_encrypt_decrypt_round_trip_preserves_non_utf8_bytes_and_call_surface() -> None:
    transit = _FakeTransit()
    secrets = VaultSecrets(_FakeVaultClient(_FakeKvV2({}), transit))
    payload = b"\x00\xff\xfe not-utf8 \xd8\x00"

    ciphertext = await secrets.encrypt("tenant-secrets", payload)
    recovered = await secrets.decrypt("tenant-secrets", ciphertext)

    assert recovered == payload
    assert transit.encrypt_calls == [
        {
            "name": "tenant-secrets",
            "plaintext": base64.b64encode(payload).decode("ascii"),
            "mount_point": "transit",
        }
    ]
    assert transit.decrypt_calls == [
        {"name": "tenant-secrets", "ciphertext": ciphertext, "mount_point": "transit"}
    ]


# --------------------------------------------------------------------------- #
# U6 -- AppRole re-login on an EXPIRED token (7.3)                            #
# --------------------------------------------------------------------------- #
# 08 §3.1 fixes token_ttl=1h and ``create_vault_client`` logs in exactly once,
# so an AppRole deployment loses Vault an hour after boot unless the adapter
# re-authenticates. Measured live on a throwaway 8-second role before any of
# this was written: KV *and* Transit both start failing the moment the token
# lapses, while /health/ready stays green (it probes no dependency).
class _ExpiringKvV2(_FakeKvV2):
    """A KV fake that rejects the first ``fail_times`` calls the way Vault
    rejects an expired token -- 403 Forbidden, which is byte-identical to a
    genuine policy denial and is exactly why the retry can only be spent once.
    """

    def __init__(self, inner_data: Json, *, fail_times: int) -> None:
        super().__init__(inner_data)
        self._remaining = fail_times
        self.attempts = 0

    def read_secret_version(
        self, *, path: str, mount_point: str, raise_on_deleted_version: bool
    ) -> Json:
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise Forbidden("permission denied")
        return super().read_secret_version(
            path=path,
            mount_point=mount_point,
            raise_on_deleted_version=raise_on_deleted_version,
        )


class _Relogin:
    """Records how often the AppRole login was re-run."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


async def test_expired_token_is_re_authenticated_once_and_the_call_succeeds() -> None:
    kv = _ExpiringKvV2({"access_key": "AKIAEXAMPLE"}, fail_times=1)
    relogin = _Relogin()
    secrets = VaultSecrets(_FakeVaultClient(kv, _FakeTransit()), relogin=relogin)

    assert await secrets.get_secret("secret/data/minio") == {"access_key": "AKIAEXAMPLE"}
    assert relogin.count == 1
    assert kv.attempts == 2  # rejected, re-authenticated, retried


async def test_without_a_relogin_an_expired_token_is_not_retried() -> None:
    """Token auth (local dev) keeps its exact previous behaviour: one attempt,
    a translated error, and no re-authentication seam at all."""
    kv = _ExpiringKvV2({}, fail_times=1)
    secrets = VaultSecrets(_FakeVaultClient(kv, _FakeTransit()))

    with pytest.raises(AppError) as excinfo:
        await secrets.get_secret("secret/data/minio")

    assert excinfo.value.code == "common.internal"
    assert kv.attempts == 1


async def test_a_genuine_denial_costs_exactly_one_retry_and_never_loops() -> None:
    """A wrong secret_id looks the same on the wire as a stale one. The retry
    budget is therefore one: a second rejection means the credentials are
    wrong, and a loop would turn a misconfiguration into a login storm."""
    kv = _ExpiringKvV2({}, fail_times=99)
    relogin = _Relogin()
    secrets = VaultSecrets(_FakeVaultClient(kv, _FakeTransit()), relogin=relogin)

    with pytest.raises(AppError) as excinfo:
        await secrets.get_secret("secret/data/minio")

    assert excinfo.value.code == "common.internal"
    assert relogin.count == 1
    assert kv.attempts == 2


class _AuthFailingTransit(_FakeTransit):
    def __init__(self, exc: Exception, *, fail_times: int) -> None:
        super().__init__()
        self._exc = exc
        self._remaining = fail_times
        self.attempts = 0

    def _maybe_fail(self) -> None:
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._exc

    def encrypt_data(self, *, name: str, plaintext: str, mount_point: str) -> Json:
        self._maybe_fail()
        return super().encrypt_data(name=name, plaintext=plaintext, mount_point=mount_point)

    def decrypt_data(self, *, name: str, ciphertext: str, mount_point: str) -> Json:
        self._maybe_fail()
        return super().decrypt_data(name=name, ciphertext=ciphertext, mount_point=mount_point)


@pytest.mark.parametrize("exc", [Forbidden("denied"), Unauthorized("no token")])
async def test_transit_re_authenticates_on_either_auth_rejection(exc: Exception) -> None:
    """Transit is the RUNTIME path -- every credentials/integrations operation
    goes through it long after startup -- so it needs the seam at least as much
    as the boot-time KV read does. Vault answers a lapsed token 403 or 401
    depending on the endpoint, so both have to trigger it."""
    transit = _AuthFailingTransit(exc, fail_times=1)
    relogin = _Relogin()
    secrets = VaultSecrets(_FakeVaultClient(_FakeKvV2({}), transit), relogin=relogin)

    assert (await secrets.encrypt("tenant-secrets", b"x")).startswith("vault:v1:")
    assert relogin.count == 1
    assert transit.attempts == 2


async def test_decrypt_re_authenticates_too() -> None:
    transit = _AuthFailingTransit(Forbidden("denied"), fail_times=1)
    relogin = _Relogin()
    secrets = VaultSecrets(_FakeVaultClient(_FakeKvV2({}), transit), relogin=relogin)

    assert await secrets.decrypt("tenant-secrets", "vault:v1:eA==") == b"x"
    assert relogin.count == 1


@pytest.mark.parametrize("exc", [VaultError("sealed"), OSError("connection refused")])
async def test_a_non_auth_failure_never_re_authenticates(exc: Exception) -> None:
    """The seam re-establishes an EXPIRED SESSION; it is not a general retry.
    A sealed Vault or a refused connection must fail on the first attempt --
    otherwise this quietly becomes the internal retry policy the messaging
    adapters explicitly refuse to own."""
    transit = _AuthFailingTransit(exc, fail_times=1)
    relogin = _Relogin()
    secrets = VaultSecrets(_FakeVaultClient(_FakeKvV2({}), transit), relogin=relogin)

    with pytest.raises(AppError) as excinfo:
        await secrets.encrypt("tenant-secrets", b"x")

    assert excinfo.value.code == "common.internal"
    assert relogin.count == 0
    assert transit.attempts == 1


class _AbsentKvV2(_FakeKvV2):
    """InvalidPath is Vault's 404, not an auth rejection."""

    def read_secret_version(
        self, *, path: str, mount_point: str, raise_on_deleted_version: bool
    ) -> Json:
        raise InvalidPath("no version")


async def test_an_absent_secret_still_reads_as_not_found_through_the_seam() -> None:
    """The one caller-branchable KV case must survive the retry wrapper
    untouched -- 2.6's contract, and the reason the unwrapping deliberately
    stayed inside each method's own ``try``."""
    secrets = VaultSecrets(_FakeVaultClient(_AbsentKvV2({}), _FakeTransit()), relogin=_Relogin())

    with pytest.raises(NotFoundError):
        await secrets.get_secret("secret/data/nope")


# --------------------------------------------------------------------------- #
# U7 -- create_approle_relogin: which deployments get the seam at all         #
# --------------------------------------------------------------------------- #
class _RecordingApprole:
    def __init__(self) -> None:
        self.logins: list[dict[str, Any]] = []

    def login(self, *, role_id: str, secret_id: str) -> None:
        self.logins.append({"role_id": role_id, "secret_id": secret_id})


class _RecordingAuthClient:
    def __init__(self) -> None:
        self.approle = _RecordingApprole()


class _LoginableClient:
    def __init__(self) -> None:
        self.auth = _RecordingAuthClient()


@pytest.mark.parametrize(
    ("role_id", "secret_id"),
    [(None, "s"), ("r", None), (None, None), ("", "s"), ("r", "")],
)
def test_no_relogin_without_a_complete_approle_pair(
    role_id: str | None, secret_id: str | None
) -> None:
    """Half a credential is not a credential. Token-auth deployments and
    misconfigured ones both get ``None`` -- the seam is inert, never partial."""
    assert (
        create_approle_relogin(
            VaultSettings(addr="http://vault:8200", role_id=role_id),
            _LoginableClient(),  # type: ignore[arg-type]
            secret_id=secret_id,
        )
        is None
    )


def test_relogin_replays_the_exact_approle_login() -> None:
    client = _LoginableClient()
    relogin = create_approle_relogin(
        VaultSettings(addr="http://vault:8200", role_id="the-role"),
        client,  # type: ignore[arg-type]
        secret_id="the-secret",
    )
    assert relogin is not None

    relogin()
    relogin()

    assert client.auth.approle.logins == [
        {"role_id": "the-role", "secret_id": "the-secret"},
        {"role_id": "the-role", "secret_id": "the-secret"},
    ]
