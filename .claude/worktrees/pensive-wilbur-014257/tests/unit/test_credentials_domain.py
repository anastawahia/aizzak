"""Unit tests for the credentials domain — provider/cipher VOs and INV-C1.
Pure: no ports, no infrastructure."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.errors import (
    CredentialScopeMismatch,
    InvalidCredentialInput,
)
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)

_TS = datetime(2026, 7, 10, tzinfo=UTC)


def _cred(scope: CredentialScope, workspace_id: str | None) -> Credential:
    return Credential(
        id="c1",
        workspace_id=workspace_id,
        provider=ProviderRef("openai"),
        scope=scope,
        label="****abcd",
        ciphertext_ref=CipherRef(ciphertext="vault:v1:xx", key_name="tenant-secrets"),
        status=CredentialStatus.ACTIVE,
        created_by="u1",
        created_at=_TS,
        updated_at=_TS,
        version=1,
    )


@pytest.mark.parametrize(
    "value",
    [
        "openai",
        "OpenAI ",
        "claude",
        "gemini",
        "ollama",
        "openrouter",
        "embedding:openai",
        "image:stability",
        "video:runway",
    ],
)
def test_provider_ref_accepts_known(value: str) -> None:
    assert ProviderRef(value).value == value.strip().lower()


@pytest.mark.parametrize(
    "value",
    ["", "  ", "gpt-4", "foobar", "embedding:", "sql:injection", "openai:extra"],
)
def test_provider_ref_rejects_unknown(value: str) -> None:
    with pytest.raises(InvalidCredentialInput):
        ProviderRef(value)


def test_cipher_ref_rejects_empty() -> None:
    with pytest.raises(InvalidCredentialInput):
        CipherRef(ciphertext="", key_name="tenant-secrets")
    with pytest.raises(InvalidCredentialInput):
        CipherRef(ciphertext="x", key_name="")


def test_user_credential_requires_workspace() -> None:
    with pytest.raises(CredentialScopeMismatch):
        _cred(CredentialScope.USER, None)


def test_platform_credential_forbids_workspace() -> None:
    with pytest.raises(CredentialScopeMismatch):
        _cred(CredentialScope.PLATFORM, "w1")


def test_valid_scopes_construct() -> None:
    assert _cred(CredentialScope.USER, "w1").scope is CredentialScope.USER
    assert _cred(CredentialScope.PLATFORM, None).scope is CredentialScope.PLATFORM


def test_revoke_is_idempotent() -> None:
    cred = _cred(CredentialScope.USER, "w1")
    first = datetime(2026, 7, 11, tzinfo=UTC)
    cred.revoke(first)
    assert cred.status is CredentialStatus.REVOKED
    assert cred.updated_at == first
    cred.revoke(datetime(2026, 7, 12, tzinfo=UTC))  # no-op: timestamp unchanged
    assert cred.updated_at == first
