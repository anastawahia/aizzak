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

from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import AgentOrchestrator, OrchestratorDependencies
from app.api.main import create_app
from app.api.middleware.auth import ApiAuthenticator
from app.api.v1.dependencies import ApiServices, Context
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.registry import InMemoryAgentRegistry
from app.framework.auth.revocation import SessionRevocationList
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ForbiddenError, UnauthorizedError
from app.framework.ports.auth_provider import Identity
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers.resolver import ResolvedProvider
from app.framework.settings import Settings
from app.framework.streaming import ConnectionHub
from app.framework.types import Json, Uuid
from app.framework.workflows import InMemoryWorkflowRegistry
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
) -> tuple[ApiAuthenticator, _FakeProvisioning, _FakeAccess]:
    verifier = _FakeVerifier({"t": identity or _identity()}, failure)
    provisioning = _FakeProvisioning(active=active)
    access = _FakeAccess(roles)
    authenticator = ApiAuthenticator(
        verifier,
        provisioning,
        access,
        access,
        revocations or SessionRevocationList(DictCache()),
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


def _make_app(authenticator: ApiAuthenticator) -> FastAPI:
    """The real app, wired with the REAL authenticator over fake ports — so
    the header handling under test is FastAPI's own, not a stand-in's."""
    registry = InMemoryAgentRegistry()
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
