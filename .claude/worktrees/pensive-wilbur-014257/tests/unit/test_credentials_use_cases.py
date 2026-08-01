"""Unit tests for credentials use-cases over in-memory fakes.
Pure: the repository and SecretsProvider are faked. Covers encryption-at-rest,
the duplicate-active rule, and the user→platform resolution order (D-16)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.types import Json
from app.modules.credentials.application.use_cases import (
    AddUserCredential,
    ResolveCredential,
    RevokeCredential,
)
from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.events import CredentialAdded
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)

_TS = datetime(2026, 7, 10, tzinfo=UTC)


def _ciphertext(raw: str) -> str:
    """Match ``_FakeSecrets`` so platform seeds decrypt back to ``raw``."""
    return f"vault:v1:{base64.b64encode(raw.encode('utf-8')).decode('ascii')}"


class _FakeSecrets:
    """In-memory Transit: a base64 round-trip standing in for Vault encrypt/decrypt."""

    async def get_secret(self, path: str) -> Json:
        return {}

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        return f"vault:v1:{base64.b64encode(plaintext).decode('ascii')}"

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        return base64.b64decode(ciphertext.split(":", 2)[2])


class _FakeCredentials:
    """In-memory ``CredentialRepository``; ``find_active`` respects scope + tenant."""

    def __init__(self) -> None:
        self.rows: dict[str, Credential] = {}

    async def add(self, ctx: ExecutionContext, credential: Credential) -> None:
        self.rows[credential.id] = credential

    async def get(self, ctx: ExecutionContext, credential_id: str) -> Credential | None:
        row = self.rows.get(credential_id)
        if row is None or (row.workspace_id is not None and row.workspace_id != ctx.workspace_id):
            return None
        return row

    async def find_active(
        self, ctx: ExecutionContext, provider: ProviderRef, scope: CredentialScope
    ) -> Credential | None:
        for row in self.rows.values():
            if row.status is not CredentialStatus.ACTIVE:
                continue
            if row.provider.value != provider.value or row.scope is not scope:
                continue
            if scope is CredentialScope.USER and row.workspace_id != ctx.workspace_id:
                continue
            return row
        return None

    async def save(self, ctx: ExecutionContext, credential: Credential) -> None:
        self.rows[credential.id] = credential


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"owner"}),
    )


def _platform_key(provider: str, raw: str) -> Credential:
    return Credential(
        id=f"plat-{provider}",
        workspace_id=None,
        provider=ProviderRef(provider),
        scope=CredentialScope.PLATFORM,
        label="****seed",
        ciphertext_ref=CipherRef(ciphertext=_ciphertext(raw), key_name="tenant-secrets"),
        status=CredentialStatus.ACTIVE,
        created_by="platform",
        created_at=_TS,
        updated_at=_TS,
        version=1,
    )


# --------------------------------------------------------------------------- #
# AddUserCredential                                                           #
# --------------------------------------------------------------------------- #
async def test_add_encrypts_and_persists() -> None:
    repo = _FakeCredentials()
    cred, events = await AddUserCredential(repo, _FakeSecrets()).execute(
        _ctx(), provider="openai", raw_key="sk-secret-1234"
    )
    assert cred.scope is CredentialScope.USER
    assert cred.workspace_id == "w1"
    assert cred.status is CredentialStatus.ACTIVE
    assert cred.label == "****1234"  # masked — never the raw key
    assert "sk-secret-1234" not in cred.ciphertext_ref.ciphertext
    assert cred.id in repo.rows
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, CredentialAdded)
    assert event.provider == "openai"
    assert event.scope == "user"


async def test_add_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        await AddUserCredential(_FakeCredentials(), _FakeSecrets()).execute(
            _ctx(), provider="gpt-9", raw_key="x"
        )


async def test_add_rejects_empty_key() -> None:
    with pytest.raises(ValidationError):
        await AddUserCredential(_FakeCredentials(), _FakeSecrets()).execute(
            _ctx(), provider="openai", raw_key="   "
        )


async def test_add_rejects_duplicate_active() -> None:
    repo = _FakeCredentials()
    add = AddUserCredential(repo, _FakeSecrets())
    await add.execute(_ctx(), provider="openai", raw_key="k1")
    with pytest.raises(ConflictError):
        await add.execute(_ctx(), provider="openai", raw_key="k2")


# --------------------------------------------------------------------------- #
# RevokeCredential                                                            #
# --------------------------------------------------------------------------- #
async def test_revoke_flips_status_and_emits_event() -> None:
    repo = _FakeCredentials()
    cred, _ = await AddUserCredential(repo, _FakeSecrets()).execute(
        _ctx(), provider="claude", raw_key="k"
    )
    revoked, events = await RevokeCredential(repo).execute(_ctx(), credential_id=cred.id)
    assert revoked.status is CredentialStatus.REVOKED
    assert len(events) == 1


async def test_revoke_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await RevokeCredential(_FakeCredentials()).execute(_ctx(), credential_id="nope")


async def test_revoke_is_idempotent() -> None:
    repo = _FakeCredentials()
    cred, _ = await AddUserCredential(repo, _FakeSecrets()).execute(
        _ctx(), provider="claude", raw_key="k"
    )
    revoke = RevokeCredential(repo)
    await revoke.execute(_ctx(), credential_id=cred.id)
    _, events = await revoke.execute(_ctx(), credential_id=cred.id)
    assert events == ()


# --------------------------------------------------------------------------- #
# ResolveCredential (user → platform, no cross-provider fallback)             #
# --------------------------------------------------------------------------- #
async def test_resolve_prefers_user_key() -> None:
    repo = _FakeCredentials()
    repo.rows["plat"] = _platform_key("openai", "PLATFORM-KEY")
    await AddUserCredential(repo, _FakeSecrets()).execute(
        _ctx(), provider="openai", raw_key="USER-KEY"
    )
    resolved = await ResolveCredential(repo, _FakeSecrets()).resolve(_ctx(), "openai")
    assert resolved.api_key == "USER-KEY"
    assert resolved.scope == "user"


async def test_resolve_falls_back_to_platform() -> None:
    repo = _FakeCredentials()
    repo.rows["plat"] = _platform_key("openai", "PLATFORM-KEY")
    resolved = await ResolveCredential(repo, _FakeSecrets()).resolve(_ctx(), "openai")
    assert resolved.api_key == "PLATFORM-KEY"
    assert resolved.scope == "platform"


async def test_resolve_with_no_key_is_credentials_none_available() -> None:
    """6.2 resolved the §3.63 tension: 03 §4's `credentials.none_available`
    /409, not the inherited 404. The provider exists and so does the
    workspace — what is missing is a key the caller can go and add."""
    with pytest.raises(ConflictError) as exc_info:
        await ResolveCredential(_FakeCredentials(), _FakeSecrets()).resolve(_ctx(), "openai")
    assert exc_info.value.code == "credentials.none_available"
    assert exc_info.value.status == 409


async def test_resolve_no_cross_provider_fallback() -> None:
    repo = _FakeCredentials()
    await AddUserCredential(repo, _FakeSecrets()).execute(
        _ctx(), provider="openai", raw_key="USER-KEY"
    )
    with pytest.raises(ConflictError) as exc_info:
        await ResolveCredential(repo, _FakeSecrets()).resolve(_ctx(), "claude")
    assert exc_info.value.code == "credentials.none_available"


async def test_resolve_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        await ResolveCredential(_FakeCredentials(), _FakeSecrets()).resolve(_ctx(), "bogus")
