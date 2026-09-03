"""The authentication path — ``api/middleware/auth.py`` (Phase 6.4-أ).

Hermetic: the ``AuthProvider``, the two inbound ports and the role reader are
all fakes, so nothing here needs a Firebase project or a database. That is the
point of the ports — the 2.7 adapter's own verification is tested exhaustively
in ``test_firebase_auth.py``, and this module tests the four things that happen
AROUND it and nowhere else:

* an identity becomes a tenant scope (JIT provisioning, 05 §1.5) and the first
  login gets ``owner`` — without which every brand-new user would meet a 403 on
  every route;
* an account's state is read FRESH on every request, so a disabled account is
  locked out at once even holding a token that verifies perfectly (the
  compensating control for v1's no-revocation decision, ``firebase_auth.py``
  D7);
* the 401/403 split (``refs/auth-firebase.md`` §4) — and, the half alpha got
  wrong, that an INFRASTRUCTURE failure is neither of them;
* the ASGI surface: which shapes of ``Authorization`` header are "missing"
  rather than "invalid", and that the published document declares the bearer
  scheme alpha's ``Header(default=None)`` never did (§9, an AC-07 defect).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import create_app
from app.api.middleware.auth import ApiAuthenticator
from app.api.v1.dependencies import ApiServices, Context
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.auth.principal_cache import CachedPrincipal, PrincipalCache
from app.framework.auth.revocation import SessionRevocationList
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ForbiddenError, UnauthorizedError
from app.framework.ports.auth_provider import Identity
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.ports.system_stats import CpuStats, MemoryStats, SystemStats
from app.framework.providers.inventory import (
    ConfiguredProvider,
    ProbeOutcome,
    ProviderRoute,
)
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.types import Json, Uuid
from app.framework.workflows import InMemoryWorkflowRegistry
from app.modules.access.domain.value_objects import Permission
from app.modules.admin.application.providers import (
    ListPlatformProviders,
    PlatformProviderUseCases,
    ProbePlatformProvider,
    RevokePlatformProviderKey,
    SetPlatformProviderKey,
)
from app.modules.admin.application.users import (
    DeletePlatformUser,
    ListPlatformUsers,
    PlatformAdminUseCases,
    SetPlatformAccountStatus,
    SetPlatformAdminRole,
    SetPlatformWorkspaceRole,
)
from app.modules.admin.ports.accounts import (
    AccountStatus,
    PlatformAccountDeletion,
    PlatformAccountStatusChange,
)
from app.modules.admin.ports.directory import PlatformUser, PlatformUserPage
from app.modules.admin.ports.providers import (
    PlatformKeyChange,
    PlatformProviderKey,
    StoredCipher,
)
from app.modules.admin.ports.roles import PlatformRoleChange, WorkspaceRole
from app.modules.workspace.ports.inbound import ProvisionedUser
from tests.unit.support_access import build_authorization
from tests.unit.support_conversations import build_conversations
from tests.unit.support_credentials import build_credentials
from tests.unit.support_files_media import build_files_media
from tests.unit.support_idempotency import InMemoryIdempotencyStore
from tests.unit.support_integrations import DictCache, build_integrations
from tests.unit.support_knowledge import build_knowledge
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.support_workspace_usage import build_workspace_usage

_UID = "firebase-uid-1"
# The uid of the account the platform-admin routes act ON — never the
# caller's own, which those routes refuse by name.
_TARGET_UID = "target-firebase-uid"
_W1 = "018f0000-0000-7000-8000-0000000000w1"
_U1 = "018f0000-0000-7000-8000-0000000000u1"
_EMAIL = "someone@example.com"


# --------------------------------------------------------------------------- #
# Fakes — one per port the authenticator holds                                #
# --------------------------------------------------------------------------- #
class _FakeVerifier:
    """An ``AuthProvider`` keyed by token, or a raised error."""

    def __init__(self, identities: dict[str, Identity], failure: Exception | None = None) -> None:
        self._identities = identities
        self._failure = failure
        self.calls = 0

    async def verify_token(self, id_token: str) -> Identity:
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        identity = self._identities.get(id_token)
        if identity is None:
            raise UnauthorizedError("invalid token", code="auth.invalid_token")
        return identity


class _FakeProvisioning:
    """``UserProvisioning`` over a dict — first call for a uid creates."""

    def __init__(self, *, active: bool = True) -> None:
        self._seen: set[str] = set()
        self._active = active
        self.display_names: list[str | None] = []
        self.calls = 0

    async def provision_on_login(
        self,
        *,
        firebase_uid: str,
        email: str,
        display_name: str | None,
        correlation_id: Uuid,
    ) -> ProvisionedUser:
        self.calls += 1
        self.display_names.append(display_name)
        created = firebase_uid not in self._seen
        self._seen.add(firebase_uid)
        return ProvisionedUser(workspace_id=_W1, user_id=_U1, active=self._active, created=created)


class _FakeAccess:
    """``RoleSeeding`` + ``AuthorizationService`` over one in-memory set.

    Deliberately ONE object: the production wiring builds both faces over the
    same repository, so a fake that seeds into one store and reads from another
    would make the "a first login can act" claim untestable.
    """

    def __init__(self, roles: frozenset[str] = frozenset()) -> None:
        self.roles = roles
        self.seeded: list[tuple[str, str]] = []
        self.reads: list[str] = []

    async def seed_owner(self, ctx: ExecutionContext, user_id: Uuid) -> None:
        self.seeded.append((ctx.workspace_id, user_id))
        self.roles = self.roles | {"owner"}

    async def roles_of(self, ctx: ExecutionContext, user_id: Uuid) -> frozenset[str]:
        self.reads.append(user_id)
        return self.roles

    def is_allowed(self, roles: frozenset[str], permission: str) -> bool:
        raise AssertionError("the authentication path never decides a permission")


class _FakePlatformDirectory:
    """A single deterministic directory row for the platform-admin route."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str | None]] = []

    async def list_users(
        self, *, limit: int, offset: int, search: str | None = None
    ) -> PlatformUserPage:
        self.calls.append((limit, offset, search))
        return PlatformUserPage(
            items=(
                PlatformUser(
                    id=_U1,
                    workspace_id=_W1,
                    email=_EMAIL,
                    display_name="Someone",
                    status="active",
                    roles=("platform_admin",),
                    last_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
                    online=True,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ),
            total=1,
            active=1,
            disabled=0,
            online=1,
        )


class _FakePresence:
    async def execute(self, ctx: ExecutionContext) -> datetime:
        assert (ctx.workspace_id, ctx.user_id) == (_W1, _U1)
        return datetime(2026, 1, 2, tzinfo=UTC)


class _FakeSystemStats:
    """A host whose GPU did not answer while its CPU and memory did — the
    partial reading the route must still serve as 200."""

    async def read(self) -> SystemStats:
        return SystemStats(
            host="gpu-node-1",
            sampled_at=datetime(2026, 1, 4, tzinfo=UTC),
            cpu=CpuStats(
                usage_percent=42.5,
                cores=8,
                interval_seconds=5.0,
                load_average=(1.5, 1.1, 0.9),
            ),
            cpu_error=None,
            memory=MemoryStats(
                total_gb=16.0,
                used_gb=8.0,
                available_gb=8.0,
                cached_gb=4.0,
                used_percent=50.0,
                limit_gb=None,
            ),
            memory_error=None,
            gpus=(),
            gpu_error="nvidia-smi is not available on this host",
        )


class _FakePlatformAccounts:
    def __init__(self) -> None:
        self.status: AccountStatus = "active"
        self.calls: list[tuple[str, str, AccountStatus, str]] = []
        self.deletions: list[tuple[str, str, str]] = []
        self.deleted_at: datetime | None = None

    async def set_status(
        self,
        *,
        actor_user_id: str,
        target_user_id: str,
        status: AccountStatus,
        reason: str,
    ) -> PlatformAccountStatusChange:
        self.calls.append((actor_user_id, target_user_id, status, reason))
        previous = self.status
        self.status = status
        changed = previous != status
        return PlatformAccountStatusChange(
            user_id=target_user_id,
            workspace_id=_W1,
            firebase_uid="target-firebase-uid",
            previous_status=previous,
            status=status,
            changed=changed,
            audit_id="018f0000-0000-7000-8000-0000000000a1" if changed else None,
            changed_at=datetime(2026, 1, 3, tzinfo=UTC) if changed else None,
        )

    async def delete(
        self, *, actor_user_id: str, target_user_id: str, reason: str
    ) -> PlatformAccountDeletion:
        self.deletions.append((actor_user_id, target_user_id, reason))
        first = self.deleted_at is None
        if first:
            self.deleted_at = datetime(2026, 1, 4, tzinfo=UTC)
        return PlatformAccountDeletion(
            user_id=target_user_id,
            workspace_id=_W1,
            firebase_uid="target-firebase-uid",
            deleted=first,
            roles_revoked=("owner",) if first else (),
            audit_id="018f0000-0000-7000-8000-0000000000a3" if first else None,
            deleted_at=self.deleted_at,
        )


class _FakePlatformRoles:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, bool, str]] = []
        self.enabled: set[str] = set()

    async def set_workspace_role(
        self,
        *,
        actor_user_id: str,
        target_user_id: str,
        role: WorkspaceRole,
        enabled: bool,
        reason: str,
    ) -> PlatformRoleChange:
        return self._change(actor_user_id, target_user_id, role, enabled, reason)

    async def set_platform_admin(
        self,
        *,
        actor_user_id: str,
        target_user_id: str,
        enabled: bool,
        reason: str,
    ) -> PlatformRoleChange:
        return self._change(actor_user_id, target_user_id, "platform_admin", enabled, reason)

    def _change(
        self, actor: str, target: str, role: str, enabled: bool, reason: str
    ) -> PlatformRoleChange:
        self.calls.append((actor, target, role, enabled, reason))
        changed = (role in self.enabled) != enabled
        if enabled:
            self.enabled.add(role)
        else:
            self.enabled.discard(role)
        return PlatformRoleChange(
            user_id=target,
            workspace_id=_W1,
            firebase_uid=_TARGET_UID,
            role=role,
            enabled=enabled,
            changed=changed,
            audit_id="018f0000-0000-7000-8000-0000000000a2" if changed else None,
            changed_at=datetime(2026, 1, 3, tzinfo=UTC) if changed else None,
        )


def _identity(*, email: str | None = _EMAIL, claims: Json | None = None) -> Identity:
    return Identity(
        firebase_uid=_UID,
        email=email,
        email_verified=True,
        claims=claims if claims is not None else {"sub": _UID},
    )


def _build(
    *,
    identity: Identity | None = None,
    failure: Exception | None = None,
    active: bool = True,
    roles: frozenset[str] = frozenset(),
    revocations: SessionRevocationList | None = None,
    principals: PrincipalCache | None = None,
    identities: dict[str, Identity] | None = None,
) -> tuple[ApiAuthenticator, _FakeProvisioning, _FakeAccess]:
    verifier = _FakeVerifier(identities or {"t": identity or _identity()}, failure)
    provisioning = _FakeProvisioning(active=active)
    access = _FakeAccess(roles)
    authenticator = ApiAuthenticator(
        verifier,
        provisioning,
        access,
        access,
        revocations or SessionRevocationList(DictCache()),
        # Default OFF, exactly as the constructor's default is: every test
        # written before capacity-plan 1.1 asserts the uncached behaviour,
        # and those assertions must keep meaning what they meant.
        principals,
    )
    return authenticator, provisioning, access


# --------------------------------------------------------------------------- #
# JIT provisioning + the first owner (05 §1.5)                                #
# --------------------------------------------------------------------------- #
async def test_a_first_login_is_seeded_as_the_owner_of_its_new_workspace() -> None:
    """Without this line the whole platform is unusable to a new user.

    ``ProvisionUserOnFirstLogin`` mints the workspace and the user and stops
    there — it CANNOT assign a role, because ``workspace`` may not import
    ``access`` (import-linter contract 4). So the owner grant has to happen on
    the one path that legitimately composes two modules, and if it did not, a
    brand-new user would authenticate successfully and then be refused by every
    single guard 6.4-ب installs.
    """
    authenticator, _provisioning, access = _build()
    principal = await authenticator.authenticate("t")
    assert access.seeded == [(_W1, _U1)]
    assert principal.roles == frozenset({"owner"})


async def test_the_seeding_scope_is_the_minted_workspace_not_a_claimed_one() -> None:
    """The tenant scope is DERIVED, never accepted (03 §0). The context the
    grant runs under is built from what provisioning returned, so a token
    cannot name the workspace it becomes an owner of."""
    authenticator, _provisioning, access = _build(
        identity=_identity(claims={"sub": _UID, "workspace_id": "018f-somebody-elses"})
    )
    await authenticator.authenticate("t")
    assert access.seeded == [(_W1, _U1)]


async def test_a_repeat_login_seeds_nothing_and_reads_the_stored_roles() -> None:
    """Idempotence with teeth: re-seeding on every request would silently
    restore ``owner`` to a user an admin had just demoted."""
    authenticator, provisioning, access = _build()
    first = await authenticator.authenticate("t")
    assert first.roles == frozenset({"owner"})

    access.seeded.clear()
    access.roles = frozenset({"viewer"})  # an admin demotes them between requests
    second = await authenticator.authenticate("t")

    assert provisioning.calls == 2
    assert access.seeded == []
    assert second.roles == frozenset({"viewer"})
    assert first.workspace_id == second.workspace_id == _W1


async def test_the_principal_carries_the_workspace_and_user_provisioning_returned() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"member"}))
    principal = await authenticator.authenticate("t")
    assert (principal.workspace_id, principal.user_id) == (_W1, _U1)


# --------------------------------------------------------------------------- #
# The disabled account — the fresh read (D7)                                  #
# --------------------------------------------------------------------------- #
async def test_a_disabled_account_is_refused_with_403_not_401() -> None:
    """``refs/auth-firebase.md`` §4's split, carried over verbatim: 401 is "we
    do not know who you are", 403 is "we know you, and no". The token here is
    perfectly valid — calling it invalid would be a lie the client would waste
    a refresh cycle on."""
    authenticator, _provisioning, _access = _build(active=False)
    with pytest.raises(ForbiddenError) as raised:
        await authenticator.authenticate("t")
    assert raised.value.code == "authz.forbidden"
    assert raised.value.status == 403


async def test_a_disabled_account_costs_no_role_read() -> None:
    """The refusal is the same whatever roles it holds, and ordering the check
    ahead of both the seeding write and the role read is what makes a disabled
    OWNER cheap to refuse rather than expensive."""
    authenticator, _provisioning, access = _build(active=False)
    with pytest.raises(ForbiddenError):
        await authenticator.authenticate("t")
    assert access.reads == []
    assert access.seeded == []


async def test_the_account_state_is_re_read_on_every_single_request() -> None:
    """The whole compensating control for "no revocation check" (D7): the
    token keeps verifying for up to an hour after an account is disabled, so
    the ONLY thing that can lock it out promptly is that nothing on this path
    is cached. alpha cached the verification RESULT for 300s and inherited a
    revocation window; this asserts we did not."""
    verifier = _FakeVerifier({"t": _identity()})
    provisioning = _FakeProvisioning()
    access = _FakeAccess()
    authenticator = ApiAuthenticator(
        verifier, provisioning, access, access, SessionRevocationList(DictCache())
    )

    assert (await authenticator.authenticate("t")).roles == frozenset({"owner"})
    provisioning._active = False  # the account is disabled between requests

    with pytest.raises(ForbiddenError):
        await authenticator.authenticate("t")
    assert verifier.calls == 2  # and the signature was re-verified both times


# --------------------------------------------------------------------------- #
# What the token has to carry                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("email", [None, "", "   "])
async def test_an_identity_without_a_usable_email_is_refused_as_an_invalid_token(
    email: str | None,
) -> None:
    """An anonymous/phone Firebase token verifies fine and still cannot become
    a platform identity: ``User`` carries a non-optional ``Email`` (06 §1) and
    05 §1.5 needs one to mint a workspace. Refusing at the door beats letting
    ``Email('')`` raise a 422 about a field the caller never sent."""
    authenticator, provisioning, _access = _build(identity=_identity(email=email))
    with pytest.raises(UnauthorizedError) as raised:
        await authenticator.authenticate("t")
    assert raised.value.code == "auth.invalid_token"
    assert provisioning.calls == 0


# --------------------------------------------------------------------------- #
# The auth:revoked:<sub> denylist (3.79)                                       #
# --------------------------------------------------------------------------- #
async def test_a_revoked_subject_is_refused_as_an_invalid_token() -> None:
    """The control the 2.7 adapter specified and left unbuilt (D7): a token
    stolen before revocation used to stay usable until ``exp``. Revocation is
    a statement about the CREDENTIAL, so it is 401 ``auth.invalid_token`` —
    not the 403 a disabled ACCOUNT gets — and it carries the same fixed
    literal every other 401 here does, so a client cannot tell a revoked
    session from an expired or forged one."""
    cache = DictCache()
    revocations = SessionRevocationList(cache)
    await revocations.revoke(_UID)
    authenticator, provisioning, access = _build(revocations=revocations)

    with pytest.raises(UnauthorizedError) as raised:
        await authenticator.authenticate("t")

    assert raised.value.code == "auth.invalid_token"
    assert raised.value.status == 401
    assert raised.value.args[0] == "invalid token"
    # Refused before anything is spent: no provisioning write, no role read.
    assert provisioning.calls == 0
    assert access.reads == []
    assert access.seeded == []


async def test_an_unrevoked_subject_is_unaffected() -> None:
    """An entry for SOMEONE ELSE must not deny this caller — the key is the
    ``sub``, and the check is a membership question about that one value."""
    revocations = SessionRevocationList(DictCache())
    await revocations.revoke("some-other-uid")
    authenticator, _provisioning, _access = _build(revocations=revocations)

    assert (await authenticator.authenticate("t")).workspace_id == _W1


async def test_the_denylist_is_consulted_on_every_request_not_once() -> None:
    """The fresh-read discipline the disabled-account check already follows,
    applied to revocation: an operator revoking mid-session must bite on the
    NEXT request, not after some cache expires."""
    revocations = SessionRevocationList(DictCache())
    authenticator, _provisioning, _access = _build(revocations=revocations)

    assert (await authenticator.authenticate("t")).workspace_id == _W1
    await revocations.revoke(_UID)  # revoked between requests

    with pytest.raises(UnauthorizedError):
        await authenticator.authenticate("t")


async def test_a_denylist_outage_refuses_the_request_rather_than_ignoring_it() -> None:
    """The posture this module already states for infrastructure failures, and
    the one thing a denylist must never do: quietly stop denying while its
    store is down. The cache adapter's ``common.internal`` reaches the handler
    intact — a 500, which is fail-closed for access."""

    class _BrokenCache(DictCache):
        async def get(self, key: str) -> bytes | None:
            raise AppError("redis is unreachable", code="common.internal")

    authenticator, provisioning, _access = _build(revocations=SessionRevocationList(_BrokenCache()))

    with pytest.raises(AppError) as raised:
        await authenticator.authenticate("t")

    assert raised.value.code == "common.internal"
    assert not isinstance(raised.value, UnauthorizedError)
    assert provisioning.calls == 0


async def test_the_401_detail_never_names_the_reason() -> None:
    """10 §10 and the 2.7 adapter's own rule: the two ways to be refused have
    to look identical from outside. "your token has no email claim" tells an
    attacker which Firebase sign-in methods this deployment seats."""
    authenticator, _provisioning, _access = _build(identity=_identity(email=None))
    with pytest.raises(UnauthorizedError) as raised:
        await authenticator.authenticate("t")
    assert raised.value.args[0] == "invalid token"
    assert "email" not in str(raised.value.args[0])


async def test_an_infrastructure_failure_is_not_dressed_up_as_a_bad_token() -> None:
    """alpha's §7 bug, and the reason 2.7 drew the line at the exception tree:
    a bare ``except Exception`` folded a Google JWKS outage into the same 401
    as "your token is garbage", so an outage looked to every client like a
    credential problem. Nothing on this path catches broadly, so the adapter's
    ``common.internal`` reaches the handler intact."""
    failure = AppError("firebase key set unavailable", code="common.internal")
    authenticator, provisioning, _access = _build(failure=failure)
    with pytest.raises(AppError) as raised:
        await authenticator.authenticate("t")
    assert raised.value.code == "common.internal"
    assert raised.value.status == 500
    assert provisioning.calls == 0


# --------------------------------------------------------------------------- #
# The display name — best effort, never a refusal                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"sub": _UID, "name": "Ada Lovelace"}, "Ada Lovelace"),
        ({"sub": _UID, "name": "   "}, None),
        ({"sub": _UID, "name": 42}, None),
        ({"sub": _UID}, None),
    ],
)
async def test_the_display_name_degrades_instead_of_refusing(
    claims: Json, expected: str | None
) -> None:
    """It only seeds a default workspace title, and provisioning already falls
    back to the email's local part — the ``email`` rule of ``_to_identity``
    (2.7), applied to a field that matters even less."""
    authenticator, provisioning, _access = _build(identity=_identity(claims=claims))
    await authenticator.authenticate("t")
    assert provisioning.display_names == [expected]


# --------------------------------------------------------------------------- #
# The ASGI surface                                                            #
# --------------------------------------------------------------------------- #
class _FakeLLM:
    provider = "fake"

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        raise AssertionError("not exercised")

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        raise AssertionError("not exercised")

    def supports(self, capability: str) -> bool:
        return True


class _FakeResolver:
    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[_FakeLLM, ResolvedProvider]:
        return _FakeLLM(), ResolvedProvider(provider="fake", model="fake-model", api_key="k")

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        raise AssertionError("not exercised")


class _FakeProviderInventory:
    """One keyed provider with one LLM route — enough for the routes' shape."""

    def configured_providers(self) -> tuple[ConfiguredProvider, ...]:
        return (
            ConfiguredProvider(
                provider="openai",
                keyless=False,
                probeable=True,
                routes=(ProviderRoute(namespace="llm", capability="default", model="gpt-4o"),),
            ),
        )


class _FakeProviderStore:
    """A platform key store holding one live key for ``openai``."""

    def __init__(self) -> None:
        moment = datetime(2026, 1, 4, tzinfo=UTC)
        self.key = PlatformProviderKey(
            id="00000000-0000-7000-8000-0000000000aa",
            provider="openai",
            label="****1234",
            status="active",
            created_by="00000000-0000-7000-8000-0000000000bb",
            created_at=moment,
            updated_at=moment,
        )
        self.stored: list[str] = []

    async def active_keys(self) -> tuple[PlatformProviderKey, ...]:
        return (self.key,)

    async def active_cipher(self, provider: str) -> StoredCipher | None:
        return StoredCipher(ciphertext="vault:sk-stored", key_name="tenant-secrets")

    async def store(
        self,
        *,
        provider: str,
        ciphertext: str,
        key_name: str,
        label: str,
        actor_user_id: str,
        reason: str,
    ) -> PlatformKeyChange:
        self.stored.append(ciphertext)
        return PlatformKeyChange(
            provider=provider,
            key=self.key,
            previous_status="active",
            changed=True,
            audit_id="00000000-0000-7000-8000-0000000000cc",
        )

    async def revoke(self, *, provider: str, actor_user_id: str, reason: str) -> PlatformKeyChange:
        return PlatformKeyChange(
            provider=provider,
            key=None,
            previous_status="absent",
            changed=False,
            audit_id=None,
        )


class _FakeProviderSecrets:
    async def get_secret(self, path: str) -> Json:
        raise AssertionError("not exercised")

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        return f"vault:{plaintext.decode()}"

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        return ciphertext.removeprefix("vault:").encode()


class _RejectingProbe:
    """A vendor that answers, and says the key is no good."""

    async def probe(self, provider: str, api_key: str) -> ProbeOutcome:
        return ProbeOutcome(ok=False, latency_ms=87, detail="openai rejected the api key")


class _UnlimitedCache:
    async def get(self, key: str) -> bytes | None:
        raise AssertionError("not exercised")

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        raise AssertionError("not exercised")

    async def delete(self, key: str) -> None:
        raise AssertionError("not exercised")

    async def incr(self, key: str, amount: int = 1) -> int:
        return 1

    async def expire(self, key: str, ttl_s: int) -> None:
        return None


def _make_providers(store: _FakeProviderStore) -> PlatformProviderUseCases:
    inventory, secrets, cache = _FakeProviderInventory(), _FakeProviderSecrets(), _UnlimitedCache()
    return PlatformProviderUseCases(
        list=ListPlatformProviders(inventory, store),
        set_key=SetPlatformProviderKey(inventory, store, secrets),
        revoke_key=RevokePlatformProviderKey(inventory, store),
        probe=ProbePlatformProvider(inventory, store, secrets, _RejectingProbe(), cache),
    )


def _make_app(
    authenticator: ApiAuthenticator,
    accounts: _FakePlatformAccounts | None = None,
    roles: _FakePlatformRoles | None = None,
    directory: _FakePlatformDirectory | None = None,
    system_stats: _FakeSystemStats | None = None,
    providers: PlatformProviderUseCases | None = None,
) -> FastAPI:
    """The real app, wired with the REAL authenticator over fake ports — so
    the header handling under test is FastAPI's own, not a stand-in's."""
    registry = InMemoryAgentRegistry()
    # One instance behind both use cases: status changes and deletions are the
    # same account manager, and a second fake would let a test watch a port
    # nobody called.
    platform_accounts = accounts or _FakePlatformAccounts()
    conversations = build_conversations()
    files_media = build_files_media()
    workspace_usage = build_workspace_usage()
    app = create_app(
        ApiServices(
            settings=Settings(),
            orchestrator=AgentOrchestrator(
                OrchestratorDependencies(
                    agents=registry,
                    executor=AgentLifecycleExecutor(),
                    providers=_FakeResolver(),
                    conversations=conversations.service,
                    authorization=build_authorization(),
                )
            ),
            hub=ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry()),
            agents=registry,
            conversations=conversations.use_cases,
            workflows=InMemoryWorkflowRegistry(),
            files=files_media.files,
            media=files_media.media,
            workspace=workspace_usage.workspace,
            usage=workspace_usage.usage,
            credentials=build_credentials().credentials,
            knowledge=build_knowledge().knowledge,
            integrations=build_integrations().integrations,
            authorization=build_authorization(),
            idempotency=InMemoryIdempotencyStore(),
            admin=PlatformAdminUseCases(
                users=ListPlatformUsers(directory or _FakePlatformDirectory()),
                accounts=SetPlatformAccountStatus(platform_accounts),
                deletions=DeletePlatformUser(platform_accounts),
                workspace_roles=SetPlatformWorkspaceRole(roles or _FakePlatformRoles()),
                platform_roles=SetPlatformAdminRole(roles or _FakePlatformRoles()),
            ),
            presence=_FakePresence(),
            session_revocations=authenticator.revocations,
            # The SAME object the authenticator reads, so a route's
            # invalidation and the next authentication meet in one store —
            # which is the only wiring in which 1.1's security criterion
            # can be observed at all.
            principal_cache=authenticator.principals,
            # Left unwired by default so the fail-closed branch is the one a
            # test has to opt OUT of, not the one it has to remember.
            system_stats=system_stats,
            # Unwired by default for the same reason: the fail-closed branch is
            # opt-out, never something a test has to remember to exercise.
            providers=providers,
        ),
        http_authenticator=authenticator,
        ws_authenticator=authenticator,
        revocations=authenticator.revocations,
    )

    @app.get("/api/v1/_ctx")
    async def _ctx(ctx: Context) -> dict[str, object]:
        return {"workspace_id": ctx.workspace_id, "roles": sorted(ctx.roles)}

    return app


@pytest.mark.parametrize(
    "header",
    [
        None,  # no header at all
        "",  # present and empty
        "Bearer",  # the scheme with no credential
        "Bearer    ",  # whitespace where a credential should be
        "Basic dXNlcjpwYXNz",  # a scheme we do not speak
        "t",  # the raw token, no scheme
    ],
)
def test_every_shape_of_absent_credential_is_missing_token_not_invalid_token(
    header: str | None,
) -> None:
    """03 §4 draws the line between "you sent no credential" and "the one you
    sent is bad", and the distinction is worth keeping precise: the first is a
    client that forgot to log in, the second is a token to refresh. An empty
    ``Bearer`` belongs on the missing side — there is nothing to verify."""
    authenticator, _provisioning, _access = _build()
    client = TestClient(_make_app(authenticator))
    headers = {} if header is None else {"Authorization": header}
    response = client.get("/api/v1/_ctx", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.missing_token"


def test_the_bearer_scheme_is_matched_case_insensitively() -> None:
    """RFC 7235 makes the scheme case-insensitive, and a client that sends
    ``bearer`` is not sending a broken request."""
    authenticator, _provisioning, _access = _build()
    client = TestClient(_make_app(authenticator))
    assert client.get("/api/v1/_ctx", headers={"Authorization": "bearer t"}).status_code == 200


def test_the_resolved_principal_becomes_the_request_context() -> None:
    """The whole point of the path: ``workspace_id`` and ``roles`` on the
    context that every delegate call carries are the ones the authenticator
    resolved — the tenant scope RLS keys on (DD-04) and the roles 6.4-ب's
    guards decide with."""
    authenticator, _provisioning, _access = _build(roles=frozenset({"member"}))
    client = TestClient(_make_app(authenticator))
    body = client.get("/api/v1/_ctx", headers={"Authorization": "Bearer t"}).json()
    assert body == {"workspace_id": _W1, "roles": ["member", "owner"]}


def test_me_context_returns_the_server_resolved_identity_and_permissions() -> None:
    """The frontend gate consumes this endpoint instead of deriving admin
    access from a Firebase custom claim or a client-side database mirror."""
    authenticator, _provisioning, _access = _build(roles=frozenset({"member"}))
    client = TestClient(_make_app(authenticator))

    response = client.get("/api/v1/me/context", headers={"Authorization": "Bearer t"})

    assert response.status_code == 200
    assert response.json() == {
        "user": {"id": _U1},
        "workspace": {"id": _W1},
        "roles": ["member", "owner"],
        "permissions": sorted(
            permission.value for permission in Permission if permission != Permission.PLATFORM_ADMIN
        ),
    }


def test_platform_admin_can_read_the_server_backed_user_directory() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    client = TestClient(_make_app(authenticator))

    response = client.get("/api/v1/admin/users", headers={"Authorization": "Bearer t"})

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "id": _U1,
                "workspace_id": _W1,
                "email": _EMAIL,
                "display_name": "Someone",
                "status": "active",
                "roles": ["platform_admin"],
                "last_seen_at": "2026-01-01T00:00:00Z",
                "online": True,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
        "stats": {"total": 1, "active": 1, "disabled": 0, "online": 1},
        "limit": 20,
        "offset": 0,
        "next_offset": None,
    }


def test_the_directory_page_carries_its_bounds_and_search_to_the_port() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    directory = _FakePlatformDirectory()
    client = TestClient(_make_app(authenticator, directory=directory))

    response = client.get(
        "/api/v1/admin/users",
        params={"limit": 5, "offset": 10, "q": "  some  "},
        headers={"Authorization": "Bearer t"},
    )

    assert response.status_code == 200
    # Surrounding whitespace is trimmed, but inner text is the operator's own.
    assert directory.calls == [(5, 10, "some")]
    assert (response.json()["limit"], response.json()["offset"]) == (5, 10)


def test_a_cleared_search_box_reads_the_unfiltered_directory() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    directory = _FakePlatformDirectory()
    client = TestClient(_make_app(authenticator, directory=directory))

    blank = client.get(
        "/api/v1/admin/users", params={"q": "   "}, headers={"Authorization": "Bearer t"}
    )
    absent = client.get("/api/v1/admin/users", headers={"Authorization": "Bearer t"})

    assert (blank.status_code, absent.status_code) == (200, 200)
    # An emptied box is the whole directory, not a search for the empty string.
    assert directory.calls == [(20, 0, None), (20, 0, None)]


def test_tenant_owner_cannot_read_the_platform_user_directory() -> None:
    authenticator, _provisioning, _access = _build()
    client = TestClient(_make_app(authenticator))

    response = client.get("/api/v1/admin/users", headers={"Authorization": "Bearer t"})

    assert response.status_code == 403
    assert response.json()["code"] == "authz.forbidden"


def test_platform_admin_disables_an_account_with_audited_reason_and_revokes_it() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    accounts = _FakePlatformAccounts()
    client = TestClient(_make_app(authenticator, accounts))
    target = "018f0000-0000-7000-8000-0000000000a1"

    response = client.patch(
        f"/api/v1/admin/users/{target}/status",
        headers={"Authorization": "Bearer t"},
        json={"status": "disabled", "reason": "Confirmed security incident"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": target,
        "workspace_id": _W1,
        "status": "disabled",
        "changed": True,
        "audit_id": "018f0000-0000-7000-8000-0000000000a1",
        "changed_at": "2026-01-03T00:00:00Z",
    }
    assert accounts.calls == [(_U1, target, "disabled", "Confirmed security incident")]
    assert asyncio.run(authenticator.revocations.is_revoked("target-firebase-uid"))


def test_platform_admin_reenables_an_account_and_clears_its_temporary_revocation() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    accounts = _FakePlatformAccounts()
    client = TestClient(_make_app(authenticator, accounts))
    target = "018f0000-0000-7000-8000-0000000000a1"

    disabled = client.patch(
        f"/api/v1/admin/users/{target}/status",
        headers={"Authorization": "Bearer t"},
        json={"status": "disabled", "reason": "Confirmed security incident"},
    )
    enabled = client.patch(
        f"/api/v1/admin/users/{target}/status",
        headers={"Authorization": "Bearer t"},
        json={"status": "active", "reason": "Issue resolved"},
    )

    assert disabled.status_code == enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    assert not asyncio.run(authenticator.revocations.is_revoked("target-firebase-uid"))


def test_tenant_owner_cannot_change_a_platform_account_status() -> None:
    authenticator, _provisioning, _access = _build()
    accounts = _FakePlatformAccounts()
    client = TestClient(_make_app(authenticator, accounts))

    response = client.patch(
        "/api/v1/admin/users/018f0000-0000-7000-8000-0000000000a1/status",
        headers={"Authorization": "Bearer t"},
        json={"status": "disabled", "reason": "Confirmed security incident"},
    )

    assert response.status_code == 403
    assert accounts.calls == []


def test_platform_admin_deletes_an_account_and_cuts_off_the_tokens_it_holds() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    accounts = _FakePlatformAccounts()
    client = TestClient(_make_app(authenticator, accounts))
    target = "018f0000-0000-7000-8000-0000000000a1"

    response = client.request(
        "DELETE",
        f"/api/v1/admin/users/{target}",
        headers={"Authorization": "Bearer t"},
        json={"reason": "Left the company"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": target,
        "workspace_id": _W1,
        "deleted": True,
        "roles_revoked": ["owner"],
        "audit_id": "018f0000-0000-7000-8000-0000000000a3",
        "deleted_at": "2026-01-04T00:00:00Z",
    }
    assert accounts.deletions == [(_U1, target, "Left the company")]
    # The tombstone refuses the next sign-in on its own; this is what stops the
    # token already in the deleted user's hands.
    assert asyncio.run(authenticator.revocations.is_revoked("target-firebase-uid"))


def test_a_repeated_deletion_reports_the_original_date_and_no_new_audit() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    accounts = _FakePlatformAccounts()
    client = TestClient(_make_app(authenticator, accounts))
    target = "018f0000-0000-7000-8000-0000000000a1"

    first = client.request(
        "DELETE",
        f"/api/v1/admin/users/{target}",
        headers={"Authorization": "Bearer t"},
        json={"reason": "Left the company"},
    )
    again = client.request(
        "DELETE",
        f"/api/v1/admin/users/{target}",
        headers={"Authorization": "Bearer t"},
        json={"reason": "Left the company"},
    )

    assert first.status_code == again.status_code == 200
    assert again.json()["deleted"] is False
    assert again.json()["audit_id"] is None
    assert again.json()["deleted_at"] == first.json()["deleted_at"]


def test_tenant_owner_cannot_delete_a_platform_user() -> None:
    authenticator, _provisioning, _access = _build()
    accounts = _FakePlatformAccounts()
    client = TestClient(_make_app(authenticator, accounts))

    response = client.request(
        "DELETE",
        "/api/v1/admin/users/018f0000-0000-7000-8000-0000000000a1",
        headers={"Authorization": "Bearer t"},
        json={"reason": "Attempted cleanup"},
    )

    assert response.status_code == 403
    assert accounts.deletions == []


def test_a_partial_system_reading_is_still_a_200_with_its_reason_attached() -> None:
    """A monitoring page that fails whole goes blind exactly when it is being
    read, so a dead GPU degrades to a message beside live CPU and memory."""
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    client = TestClient(_make_app(authenticator, system_stats=_FakeSystemStats()))

    response = client.get("/api/v1/admin/system/stats", headers={"Authorization": "Bearer t"})

    assert response.status_code == 200
    body = response.json()
    # The machine that answered is part of the reading: every other route here
    # answers the same from any replica, and this one cannot.
    assert body["host"] == "gpu-node-1"
    assert body["sampled_at"] == "2026-01-04T00:00:00Z"
    assert body["cpu"] == {
        "usage_percent": 42.5,
        "cores": 8,
        "interval_seconds": 5.0,
        "load_average": [1.5, 1.1, 0.9],
    }
    assert body["cpu_error"] is None
    assert body["memory"]["used_percent"] == 50.0
    assert body["gpus"] == []
    assert body["gpu_error"] == "nvidia-smi is not available on this host"


def test_tenant_owner_cannot_read_the_hosts_system_stats() -> None:
    authenticator, _provisioning, _access = _build()
    client = TestClient(_make_app(authenticator, system_stats=_FakeSystemStats()))

    response = client.get("/api/v1/admin/system/stats", headers={"Authorization": "Bearer t"})

    assert response.status_code == 403
    assert response.json()["code"] == "authz.forbidden"


def test_an_unwired_system_monitor_fails_closed_rather_than_inventing_a_reading() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    client = TestClient(_make_app(authenticator))

    response = client.get("/api/v1/admin/system/stats", headers={"Authorization": "Bearer t"})

    assert response.status_code == 500
    assert response.json()["code"] == "common.internal"


def test_the_provider_listing_carries_the_routes_and_the_key_but_never_a_secret() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    client = TestClient(_make_app(authenticator, providers=_make_providers(_FakeProviderStore())))

    response = client.get("/api/v1/admin/providers", headers={"Authorization": "Bearer t"})

    assert response.status_code == 200
    (entry,) = response.json()["data"]
    assert entry["provider"] == "openai"
    assert entry["routes"] == [{"namespace": "llm", "capability": "default", "model": "gpt-4o"}]
    # The label is the masked hint the credential row already holds; there is
    # no field on this shape that could carry the key itself.
    assert entry["key"]["label"] == "****1234"
    assert "secret" not in entry["key"]


def test_a_tenant_owner_cannot_read_the_platforms_provider_keys() -> None:
    authenticator, _provisioning, _access = _build()
    client = TestClient(_make_app(authenticator, providers=_make_providers(_FakeProviderStore())))

    response = client.get("/api/v1/admin/providers", headers={"Authorization": "Bearer t"})

    assert response.status_code == 403
    assert response.json()["code"] == "authz.forbidden"


def test_an_unwired_provider_surface_fails_closed_rather_than_listing_nothing() -> None:
    """An empty list would read as "this platform routes to no provider",
    which is a different and false statement from "this app has no wiring"."""
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    client = TestClient(_make_app(authenticator))

    response = client.get("/api/v1/admin/providers", headers={"Authorization": "Bearer t"})

    assert response.status_code == 500
    assert response.json()["code"] == "common.internal"


def test_storing_a_platform_key_encrypts_it_and_answers_with_metadata_only() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    store = _FakeProviderStore()
    client = TestClient(_make_app(authenticator, providers=_make_providers(store)))

    response = client.put(
        "/api/v1/admin/providers/openai/key",
        headers={"Authorization": "Bearer t"},
        json={"secret": "sk-live-9999", "reason": "quarterly rotation"},
    )

    assert response.status_code == 200
    assert store.stored == ["vault:sk-live-9999"]
    body = response.json()
    assert body["previous_status"] == "active"
    assert "sk-live-9999" not in response.text


def test_a_rejected_key_is_a_200_saying_so_rather_than_an_error_response() -> None:
    """The request succeeded; its answer is that the credential did not. The
    error channel stays reserved for failures of the probe itself."""
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    client = TestClient(_make_app(authenticator, providers=_make_providers(_FakeProviderStore())))

    response = client.post(
        "/api/v1/admin/providers/openai/probe",
        headers={"Authorization": "Bearer t"},
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["detail"] == "openai rejected the api key"
    assert body["latency_ms"] == 87


def test_platform_admin_changes_workspace_and_platform_roles_on_separate_routes() -> None:
    authenticator, _provisioning, _access = _build(roles=frozenset({"platform_admin"}))
    roles = _FakePlatformRoles()
    client = TestClient(_make_app(authenticator, roles=roles))
    target = "018f0000-0000-7000-8000-0000000000a1"

    workspace = client.patch(
        f"/api/v1/admin/users/{target}/roles/workspace",
        headers={"Authorization": "Bearer t"},
        json={"role": "admin", "enabled": True, "reason": "Support escalation"},
    )
    platform = client.patch(
        f"/api/v1/admin/users/{target}/roles/platform-admin",
        headers={"Authorization": "Bearer t"},
        json={"enabled": True, "reason": "Platform support rotation"},
    )

    assert workspace.status_code == platform.status_code == 200
    assert workspace.json()["role"] == "admin"
    assert platform.json()["role"] == "platform_admin"
    assert roles.calls == [
        (_U1, target, "admin", True, "Support escalation"),
        (_U1, target, "platform_admin", True, "Platform support rotation"),
    ]


def test_tenant_owner_cannot_change_platform_roles() -> None:
    authenticator, _provisioning, _access = _build()
    roles = _FakePlatformRoles()
    client = TestClient(_make_app(authenticator, roles=roles))

    response = client.patch(
        "/api/v1/admin/users/018f0000-0000-7000-8000-0000000000a1/roles/platform-admin",
        headers={"Authorization": "Bearer t"},
        json={"enabled": True, "reason": "Attempted escalation"},
    )

    assert response.status_code == 403
    assert roles.calls == []


def test_heartbeat_records_only_the_authenticated_callers_presence() -> None:
    authenticator, _provisioning, _access = _build()
    client = TestClient(_make_app(authenticator))

    response = client.post("/api/v1/me/heartbeat", headers={"Authorization": "Bearer t"})

    assert response.status_code == 200
    assert response.json() == {"last_seen_at": "2026-01-02T00:00:00Z"}


def test_a_refusal_is_still_an_rfc_9457_problem() -> None:
    authenticator, _provisioning, _access = _build(active=False)
    client = TestClient(_make_app(authenticator))
    response = client.get("/api/v1/_ctx", headers={"Authorization": "Bearer t"})
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "authz.forbidden"


def test_logout_revokes_the_current_subject_before_local_sign_out() -> None:
    authenticator, _provisioning, _access = _build()
    client = TestClient(_make_app(authenticator))

    response = client.post("/api/v1/me/logout", headers={"Authorization": "Bearer t"})

    assert response.status_code == 204
    refused = client.get("/api/v1/_ctx", headers={"Authorization": "Bearer t"})
    assert refused.status_code == 401
    assert refused.json()["code"] == "auth.invalid_token"


# --------------------------------------------------------------------------- #
# The principal cache — capacity-plan wave 1, step 1.1                        #
#                                                                             #
# Everything above this line asserts the UNCACHED path, and keeps doing so:   #
# `_build` leaves the cache off by default exactly as the constructor does,   #
# so those assertions still mean what they meant. What follows is the cached  #
# path, and it is written around the two acceptance criteria the plan states  #
# for this step — the load one (how many database round trips an              #
# authenticated request costs) and the security one (that a role withdrawal   #
# or an account disable still bites on the very next request).                #
# --------------------------------------------------------------------------- #
def _target_identity() -> Identity:
    """A SECOND subject — the account a platform-admin route acts on.

    The routes refuse an administrator acting on their own account by name, so
    every invalidation test needs two identities: the caller, and the one whose
    next request has to see the change.
    """
    return Identity(
        firebase_uid=_TARGET_UID,
        email="target@example.com",
        email_verified=True,
        claims={"sub": _TARGET_UID},
    )


def _cached_build(
    *, roles: frozenset[str] = frozenset(), ttl_s: int = 60
) -> tuple[ApiAuthenticator, _FakeProvisioning, _FakeAccess, PrincipalCache]:
    principals = PrincipalCache(DictCache(), ttl_s=ttl_s)
    authenticator, provisioning, access = _build(
        roles=roles,
        principals=principals,
        identities={"t": _identity(), "u": _target_identity()},
    )
    return authenticator, provisioning, access, principals


# --- the load criterion ----------------------------------------------------- #
async def test_a_repeat_request_costs_no_database_round_trip_at_all() -> None:
    """`ح-2`, and the whole reason this step exists: the two reads below ran on
    EVERY request, before any of the work the caller asked for. Ten
    authentications now cost the one pair the first of them paid — which is the
    plan's "≥2 → ≤0.05" expressed as a count instead of an average."""
    authenticator, provisioning, access, _principals = _cached_build()

    for _ in range(10):
        await authenticator.authenticate("t")

    assert provisioning.calls == 1
    assert len(access.reads) == 1


async def test_a_cache_hit_returns_the_principal_the_database_had_resolved() -> None:
    """A cheaper answer that is a DIFFERENT answer is not a cache."""
    authenticator, _provisioning, _access, _principals = _cached_build(roles=frozenset({"member"}))

    first = await authenticator.authenticate("t")
    second = await authenticator.authenticate("t")

    assert first == second


async def test_the_first_login_still_seeds_its_owner_before_anything_is_cached() -> None:
    """The cache is consulted BEFORE provisioning, so a bug that stored an
    entry too eagerly would skip the owner grant and leave a brand-new user
    refused by every guard. The miss path has to run whole."""
    authenticator, _provisioning, access, _principals = _cached_build()

    principal = await authenticator.authenticate("t")

    assert access.seeded == [(_W1, _U1)]
    assert principal.roles == frozenset({"owner"})


async def test_nothing_is_cached_when_the_deployment_wires_no_cache() -> None:
    """`AUTH_PRINCIPAL_CACHE_TTL_S=0` builds no cache, and the path must then
    cost not even a Redis round trip — a baseline measured with the
    optimisation half-installed answers nothing (`م-8`)."""
    authenticator, provisioning, access = _build()

    await authenticator.authenticate("t")
    await authenticator.authenticate("t")

    assert (provisioning.calls, len(access.reads)) == (2, 2)


# --- the security criterion: what a cache hit does NOT skip ----------------- #
async def test_a_revoked_subject_is_refused_even_with_a_warm_cache() -> None:
    """The ordering that makes every other claim here safe: the denylist runs
    BEFORE the cache is consulted, so `python -m app.ops.revoke` — and the
    admin routes that call it — still cut a session off on its next request,
    whatever is warm in Redis."""
    revocations = SessionRevocationList(DictCache())
    principals = PrincipalCache(DictCache())
    authenticator, _provisioning, _access = _build(revocations=revocations, principals=principals)
    await authenticator.authenticate("t")  # warms the entry

    await revocations.revoke(_UID)

    with pytest.raises(UnauthorizedError) as raised:
        await authenticator.authenticate("t")
    assert raised.value.code == "auth.invalid_token"


async def test_a_cached_inactive_principal_is_still_a_403() -> None:
    """An entry stored by a replica that DID see a disabled account must refuse
    here too — otherwise the cache would be a way to launder a 403 into a 200.
    Written directly into the store, because no code path in THIS version
    produces one: the miss path raises before it caches."""
    authenticator, _provisioning, _access, principals = _cached_build()
    await principals.put(
        _UID,
        CachedPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset(), active=False),
    )

    with pytest.raises(ForbiddenError):
        await authenticator.authenticate("t")


async def test_a_disabled_account_is_never_written_to_the_cache() -> None:
    """The refusal happens before the write, so a 403 leaves nothing behind
    that a later request could read back."""
    principals = PrincipalCache(DictCache())
    authenticator, _provisioning, _access = _build(active=False, principals=principals)

    with pytest.raises(ForbiddenError):
        await authenticator.authenticate("t")

    assert await principals.get(_UID) is None


async def test_one_subject_holds_one_principal_across_two_authentications() -> None:
    """ "لا يعبر بين مساحتَي عمل" — the plan's own words. There is one key per
    subject and it carries the workspace it was resolved with, so a second
    identity cannot be served the first one's tenant."""
    authenticator, _provisioning, _access, _principals = _cached_build()

    caller = await authenticator.authenticate("t")
    target = await authenticator.authenticate("u")

    assert caller.firebase_uid == _UID
    assert target.firebase_uid == _TARGET_UID


# --- the security criterion, through the real routes ------------------------ #
def test_a_role_change_takes_effect_on_the_targets_very_next_request() -> None:
    """1.1's security criterion, end to end and through the REAL route.

    The route is the only place this is enforced: nothing else on the
    role-change path writes anything the authentication path re-reads, so
    without its `invalidate` call a demotion would wait out the TTL. That is
    the difference between a cache and a hole.
    """
    authenticator, _provisioning, access, _principals = _cached_build(
        roles=frozenset({"platform_admin"})
    )
    client = TestClient(_make_app(authenticator, roles=_FakePlatformRoles()))
    target_id = "018f0000-0000-7000-8000-0000000000a1"

    asyncio.run(authenticator.authenticate("t"))  # the administrator, warm
    before = asyncio.run(authenticator.authenticate("u"))
    access.roles = frozenset({"viewer"})  # what the route's own write just did
    response = client.patch(
        f"/api/v1/admin/users/{target_id}/roles/workspace",
        headers={"Authorization": "Bearer t"},
        json={"role": "admin", "enabled": True, "reason": "Support escalation"},
    )
    after = asyncio.run(authenticator.authenticate("u"))

    assert response.status_code == 200
    # The shared role store hands both identities the same set (see
    # `_FakeAccess`); what matters is that the target's SECOND read saw the
    # change and its first did not.
    assert before.roles == frozenset({"owner", "platform_admin"})
    assert after.roles == frozenset({"viewer"})


def test_without_that_invalidation_the_stale_roles_would_have_survived() -> None:
    """The control for the test above. It asserts the cache is REAL — that the
    fresh roles in that test came from the route's invalidation and not from a
    cache that never held anything."""
    authenticator, _provisioning, access, _principals = _cached_build()

    before = asyncio.run(authenticator.authenticate("u"))
    access.roles = frozenset({"viewer"})
    after = asyncio.run(authenticator.authenticate("u"))

    assert before.roles == after.roles == frozenset({"owner"})


def test_disabling_an_account_still_bites_on_its_next_request() -> None:
    """The other half of 1.1's security criterion — and the reason `active`
    may be cached at all. The guarantee is not carried by the freshness of
    that flag; it is carried by the denylist the route writes in the same
    handler, which step 1b checks uncached before the cache is consulted."""
    authenticator, _provisioning, _access, _principals = _cached_build(
        roles=frozenset({"platform_admin"})
    )
    client = TestClient(_make_app(authenticator, accounts=_FakePlatformAccounts()))
    target_id = "018f0000-0000-7000-8000-0000000000a1"

    asyncio.run(authenticator.authenticate("u"))  # a warm entry for the victim
    response = client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers={"Authorization": "Bearer t"},
        json={"status": "disabled", "reason": "Confirmed security incident"},
    )

    assert response.status_code == 200
    with pytest.raises(UnauthorizedError):
        asyncio.run(authenticator.authenticate("u"))


def test_re_enabling_an_account_restores_it_on_its_next_request() -> None:
    """Re-enable is the branch that NEEDS the cache invalidation rather than
    the denylist: `clear` un-blocks the subject, and an entry cached during
    the disabled window would otherwise keep answering for the rest of the
    TTL."""
    authenticator, _provisioning, _access, principals = _cached_build(
        roles=frozenset({"platform_admin"})
    )
    accounts = _FakePlatformAccounts()
    client = TestClient(_make_app(authenticator, accounts=accounts))
    target_id = "018f0000-0000-7000-8000-0000000000a1"
    asyncio.run(
        principals.put(
            _TARGET_UID,
            CachedPrincipal(workspace_id=_W1, user_id=_U1, roles=frozenset(), active=False),
        )
    )

    disabled = client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers={"Authorization": "Bearer t"},
        json={"status": "disabled", "reason": "Confirmed security incident"},
    )
    enabled = client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers={"Authorization": "Bearer t"},
        json={"status": "active", "reason": "Issue resolved"},
    )

    assert disabled.status_code == enabled.status_code == 200
    assert asyncio.run(principals.get(_TARGET_UID)) is None
    # And resolving again succeeds rather than replaying the stored `false`.
    assert asyncio.run(authenticator.authenticate("u")).user_id == _U1


def test_deleting_an_account_leaves_no_principal_behind_in_the_store() -> None:
    """The denylist already refuses this subject, so this changes no outcome —
    it stops a tombstoned user's workspace and roles from sitting in Redis
    with no reader for the rest of the TTL."""
    authenticator, _provisioning, _access, principals = _cached_build(
        roles=frozenset({"platform_admin"})
    )
    client = TestClient(_make_app(authenticator, accounts=_FakePlatformAccounts()))
    target_id = "018f0000-0000-7000-8000-0000000000a1"
    asyncio.run(authenticator.authenticate("u"))

    response = client.request(
        "DELETE",
        f"/api/v1/admin/users/{target_id}",
        headers={"Authorization": "Bearer t"},
        json={"reason": "Account closure requested"},
    )

    assert response.status_code == 200
    assert asyncio.run(principals.get(_TARGET_UID)) is None
