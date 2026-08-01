"""The authentication path — a verified Firebase token becomes a ``Principal``
(05-rbac-config-secrets §1.4/§1.5 · architecture §11 · SEC-01 · D-25) — Phase
6.4-أ.

This fills the seam 6.1-a and 5.3-ج both left open, and it is the ONLY place in
the system where an external identity turns into a tenant scope. Four steps, in
this order, on every single request:

1. **Verify the token** — delegated whole to the ``AuthProvider`` port (the 2.7
   Firebase adapter): local JWKS verification, no round trip per request
   (D-25). Nothing about JWTs is re-implemented here; this module could not
   name a claim if it wanted to, beyond the three ``Identity`` carries.
1b. **Refuse a revoked subject** (3.79) — the ``auth:revoked:<sub>`` denylist
   (``framework/auth/revocation.py``). The 2.7 adapter specified this control
   and named THIS module as its home, precisely so ``verify_token`` stays a
   pure, zero-extra-I/O function; it closes the residual risk that adapter's
   D7 paragraph records — a token stolen before revocation staying usable
   until ``exp``. It runs SECOND, immediately after verification and before
   provisioning, because a refused request must cost nothing (the same
   ordering argument step 3 already makes) and because the ``sub`` is the only
   thing it needs, which verification has just produced.
2. **Mirror the identity** — ``UserProvisioning.provision_on_login`` (05 §1.5),
   idempotent, which on a FIRST login mints the workspace and the user.
3. **Refuse a disabled account** — the compensating control for v1's deliberate
   "no revocation check" decision (``firebase_auth.py`` D7): the account's state
   is read from the database on EVERY request with no cache whatsoever, so a
   disabled account is locked out at once regardless of how much life its token
   has left. This is the one alpha behaviour explicitly carried over
   (``refs/auth-firebase.md`` §9: "يُعاد استخدامه — ضابط تعويضي يبقى صالحاً
   مهما كوشِفت المفاتيح").
4. **Resolve roles** — ``AuthorizationService.roles_of``, read fresh for the
   same reason, and seeded with ``owner`` on the login that created the
   workspace (``RoleSeeding``, 05 §1.5). Those roles ride the ``Principal`` into
   ``ExecutionContext.roles``, which is what 6.4-ب's guards decide on.

**401 versus 403 is the whole error contract here**, inherited deliberately from
alpha (``refs/auth-firebase.md`` §4): 401 is "we do not know who you are" — the
token layer — and 403 is "we know you, and the answer is no". So a bad token is
``auth.invalid_token``/401 (raised by the adapter, never re-dressed here) and a
disabled account is ``authz.forbidden``/403. What alpha got WRONG and is not
carried over: its bare ``except Exception`` folded a Google outage into the same
401 as a garbage token. Nothing here catches broadly — an infrastructure failure
surfaces as the adapter's own ``common.internal``/500, and the distinction the
2.7 adapter paid for stays intact all the way to the wire.

**A revoked subject is a 401, not a 403** (3.79), and the split is the same
one: revocation is a statement about the CREDENTIAL — "this token is no longer
one we accept" — not about what a known caller may do. It therefore carries the
same opaque ``auth.invalid_token`` and the same fixed literal detail as every
other 401 here, so a client cannot tell a revoked session from an expired or
forged one. That indistinguishability is deliberate: the alternative tells an
attacker holding a stolen token that the theft was noticed.

**The denylist's outage posture is this module's existing one, not a new one.**
A cache MISS means "not revoked" — that is the normal path. A cache OUTAGE is
not a miss: the ``CacheProvider`` adapter raises, nothing here catches it, and
the request dies as ``common.internal``/500 exactly as a Postgres outage in
step 2 already does. Fail-closed for access, and it refuses the one thing a
denylist must never do, which is quietly stop denying while the store is down.

**A token with no usable ``email`` claim is refused with the same opaque
``auth.invalid_token``/401.** The platform's ``User`` carries a non-optional
``Email`` (06 §1) and 05 §1.5's JIT rule needs one to mint a workspace, so an
anonymous/phone Firebase token is simply not an identity this deployment can
seat — a truthful refusal, not a validation error about a body the caller did
not send. The 401 detail stays the fixed literal the 2.7 adapter uses; the
reason goes to the log, and the log carries the ``firebase_uid`` only — never
the token, never a claim (10 §10). The check is ABSENCE only (missing or
blank), never shape: an email's syntax belongs to ``Email`` in the workspace
domain, and re-stating that regex here would be a second definition of valid
free to drift from the first. A present-but-malformed claim therefore still
surfaces as provisioning's own 422 — an unreachable case in practice, since
the claim is minted by Google's signer, and one we would rather leave to the
one place that owns the rule than guess at here.

**What this module cannot do, by construction.** It holds two one-method inbound
ports (``UserProvisioning``, ``RoleSeeding``) and one read port
(``AuthorizationService``). It therefore cannot rename a workspace, cannot grant
a role other than the first owner of a workspace it just created, and cannot
read a row belonging to anyone else. The narrowness is the security property:
the authentication path runs BEFORE any guard, so anything reachable from here
is reachable un-guarded.
"""

from __future__ import annotations

from app.api.v1.dependencies import Principal
from app.framework.auth.revocation import SessionRevocationList
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ForbiddenError, UnauthorizedError
from app.framework.identifiers import new_uuid7
from app.framework.observability import get_logger
from app.framework.ports.auth_provider import AuthProvider
from app.framework.types import Json
from app.modules.access.ports.inbound import AuthorizationService, RoleSeeding
from app.modules.workspace.ports.inbound import UserProvisioning

_logger = get_logger(__name__)

# The one fixed literal every 401 on this path carries — the 2.7 adapter's own
# rule (`_invalid_token`), repeated here so the two refusals a client can meet
# are indistinguishable: never the reason, never a claim, never the token.
_INVALID_TOKEN_DETAIL = "invalid token"


class ApiAuthenticator:
    """Bearer token → ``Principal``. Satisfies ``HttpAuthenticator`` AND
    ``WsAuthenticator`` (one structural Protocol since 6.4 unified
    ``WsPrincipal`` onto ``Principal``), so the Composition Root builds ONE
    verifier and hands the same instance to both transports.

    That single instance matters beyond tidiness: the 2.7 adapter's JWKS cache
    is instance-level (D3), so two authenticators would mean two key caches,
    two refresh budgets, and a socket authenticating against a key set the HTTP
    path had already retired.
    """

    def __init__(
        self,
        verifier: AuthProvider,
        provisioning: UserProvisioning,
        seeding: RoleSeeding,
        authorization: AuthorizationService,
        revocations: SessionRevocationList,
    ) -> None:
        self._verifier = verifier
        self._provisioning = provisioning
        self._seeding = seeding
        self._authorization = authorization
        # REQUIRED, not optional-with-a-default: a security control that a
        # caller can forget to wire is a control that is off in whichever
        # deployment forgot. The Composition Root builds it from the same
        # process-wide `CacheProvider` everything else uses.
        self._revocations = revocations

    async def authenticate(self, token: str) -> Principal:
        identity = await self._verifier.verify_token(token)
        if await self._revocations.is_revoked(identity.firebase_uid):
            # The uid alone, never the token (10 §10) — the same discipline the
            # missing-email branch below already follows, and what lets an
            # operator confirm their revocation is biting.
            _logger.warning("auth.revoked_subject", extra={"uid": identity.firebase_uid})
            raise UnauthorizedError(_INVALID_TOKEN_DETAIL, code="auth.invalid_token")

        email = (identity.email or "").strip()
        if not email:
            # Logged with the uid alone (an opaque identifier — alpha's own
            # discipline, refs §7) so an operator can tell "this Firebase
            # project issues tokens we cannot seat" apart from "someone is
            # sending garbage", while the caller learns neither.
            _logger.warning("auth.identity_without_email", extra={"uid": identity.firebase_uid})
            raise UnauthorizedError(_INVALID_TOKEN_DETAIL, code="auth.invalid_token")

        # Minted, not threaded through: `authenticate` is transport-agnostic and
        # the WebSocket handshake has no correlation id at all at this point
        # (the endpoint mints one per invoke, 5.3-ج). Widening the seam's
        # signature would buy a trace link on one transport and a placeholder on
        # the other.
        correlation_id = new_uuid7()
        provisioned = await self._provisioning.provision_on_login(
            firebase_uid=identity.firebase_uid,
            email=email,
            display_name=_display_name(identity.claims),
            correlation_id=correlation_id,
        )

        # The tenant scope every read below runs under — including the RLS GUC
        # the role lookup needs (DD-04). `roles` is empty here BY CONSTRUCTION:
        # this context exists to FIND the roles, and RLS keys on `workspace_id`
        # alone, so an empty set costs nothing and states plainly that no
        # permission has been established yet.
        ctx = ExecutionContext(
            workspace_id=provisioned.workspace_id,
            user_id=provisioned.user_id,
            correlation_id=correlation_id,
            roles=frozenset(),
        )
        if not provisioned.active:
            # FIRST, before a single further write or read: a refused account
            # must cost nothing (FR-132's shape, applied to identity), and
            # ordering it ahead of seeding is what makes this fail CLOSED —
            # today a freshly created user is always active, and this line does
            # not depend on that staying true.
            _logger.warning(
                "auth.disabled_account",
                extra={"user_id": provisioned.user_id, "correlation_id": correlation_id},
            )
            raise ForbiddenError("account is disabled")

        if provisioned.created:
            # 05 §1.5, and the reason a first login is not immediately a 403 on
            # everything: the workspace was minted a moment ago, so its owner
            # does not exist until this line runs.
            await self._seeding.seed_owner(ctx, provisioned.user_id)

        roles = await self._authorization.roles_of(ctx, provisioned.user_id)
        return Principal(
            workspace_id=provisioned.workspace_id,
            user_id=provisioned.user_id,
            roles=roles,
        )


def _display_name(claims: Json) -> str | None:
    """Firebase's ``name`` claim, when it is a usable string.

    Best-effort by design: the name only seeds a default workspace title, and
    ``ProvisionUserOnFirstLogin`` already falls back to the email's local part.
    A malformed claim therefore degrades to ``None`` rather than refusing a
    login — the ``_to_identity`` rule for ``email`` (2.7), applied to a field
    that matters even less.
    """
    name = claims.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return None
