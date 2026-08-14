"""Platform provider administration (BE-ADM-010 · BE-ADM-011 · BE-ADM-012).

**Enablement is the key, not a flag.** Alpha's panel carried a per-provider
enable/disable switch, and reproducing it here would have meant inventing a
runtime toggle the platform does not consult: ``D-16`` puts the provider/model
choice in configuration, parsed once at boot, and a provider marked "disabled"
in a table would keep answering every ``resolve_llm`` that routes to it. What
an operator can genuinely change while the process runs is whether the platform
SUPPLIES a key — so storing one is the enable and revoking it is the disable,
and both are real state with a real effect.

Two consequences are stated rather than hidden. First, disabling costs the key:
revocation is terminal by design (``RevokeCredential``'s contract, and no
un-revoke exists), so re-enabling means pasting the credential again. That is
the honest price of not keeping a decryptable secret at rest for a provider
nobody is watching. Second, revoking the platform key does NOT cut every
workspace off: resolution prefers a workspace's own key and falls back to the
platform's (``ResolveCredential``, D-16), so a tenant holding its own key keeps
working. The listing therefore describes what the PLATFORM supplies, never what
every tenant can reach — a stronger claim it has no way to verify.

**The probe decrypts, and the shape is what keeps that safe.** ``ProbePlatform
Provider`` holds a ``SecretsProvider`` and can turn a stored ``CipherRef`` back
into a usable key, which is precisely the face ``CredentialUseCases`` refuses
to carry. It is safe here for the reason ``ProviderResolver`` is safe: the only
value this use case returns is a ``ProbeOutcome`` — ok, latency, a reason —
so there is no shape through which a secret reaches the API layer. INV-C2 is
kept by what the type can express, not by a filter someone must remember.

**Rate limiting is shared, not per process.** The counter lives in the cache
because a limit enforced in memory would be multiplied by the number of
gunicorn workers, which is the same as no limit at all on the only deployment
that matters.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import (
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.framework.ports.cache_provider import CacheProvider
from app.framework.ports.secrets_provider import SecretsProvider
from app.framework.providers.inventory import (
    ConfiguredProvider,
    ProbeOutcome,
    ProviderInventory,
    ProviderProbe,
    ProviderRoute,
)
from app.modules.admin.ports.providers import (
    PlatformCredentialStore,
    PlatformKeyChange,
    PlatformProviderKey,
)

_MIN_REASON_LENGTH = 3
_MAX_REASON_LENGTH = 500
_MAX_LABEL_LENGTH = 120

# SEC-07 / 05 §3.2 unify credentials and integrations on ONE Transit key, and
# `credentials.application.use_cases` spells the same literal. It is repeated
# rather than imported because import-linter contract 4 forbids one module
# importing another, and the two MUST agree: a platform key encrypted under a
# different Transit key would store and list perfectly and then fail to
# decrypt in `ResolveCredential`, i.e. break at the only moment that matters.
# `tests/unit/test_platform_providers.py` asserts the two literals are equal,
# so the coupling is enforced rather than merely commented.
_TENANT_SECRETS_KEY = "tenant-secrets"

# 03 §4's codes, reused rather than minted: an unroutable provider is the same
# "we do not know that provider" the credentials module already names, and a
# provider with no stored key is the same "no credential to use" a resolution
# would report.
_PROVIDER_UNKNOWN = "credentials.provider_unknown"
_NONE_AVAILABLE = "credentials.none_available"

# One probe per provider per window, platform-wide. Small on purpose: a probe
# is an authenticated outbound call to a vendor, and a control an operator can
# hold down is a way to burn quota and to look like an attack from our egress
# address. Six is comfortable for a person and useless for a loop.
_PROBE_WINDOW_S = 60
_PROBE_MAX_PER_WINDOW = 6


@dataclass(frozen=True, slots=True)
class PlatformProviderView:
    """One provider as the administration surface sees it: what the
    configuration routes to it, and what key the platform supplies for it.

    Two sources, joined here rather than in either of them — the routing table
    is boot-time configuration that performs no I/O, and the key is a row in
    Postgres. Keeping the join in the application layer is what lets the
    inventory stay synchronous and keyless.
    """

    provider: str
    keyless: bool
    probeable: bool
    routes: tuple[ProviderRoute, ...]
    key: PlatformProviderKey | None


class ListPlatformProviders:
    """Every routable provider, with the platform key it currently has."""

    def __init__(self, inventory: ProviderInventory, store: PlatformCredentialStore) -> None:
        self._inventory = inventory
        self._store = store

    async def execute(self) -> tuple[PlatformProviderView, ...]:
        """Join configuration to credentials, configuration leading.

        A stored key for a provider the table no longer routes is deliberately
        NOT listed: it is dead weight nothing can spend, and surfacing it as a
        provider would suggest this deployment can reach something it cannot.
        The row is still there, and revoking it is still possible by restoring
        the route — an outcome an operator can reason about, unlike a phantom
        provider in a list of live ones.
        """
        keys = {key.provider: key for key in await self._store.active_keys()}
        return tuple(
            PlatformProviderView(
                provider=configured.provider,
                keyless=configured.keyless,
                probeable=configured.probeable,
                routes=configured.routes,
                key=keys.get(configured.provider),
            )
            for configured in self._inventory.configured_providers()
        )


class SetPlatformProviderKey:
    """Store or rotate the platform key for one configured provider."""

    def __init__(
        self,
        inventory: ProviderInventory,
        store: PlatformCredentialStore,
        secrets: SecretsProvider,
    ) -> None:
        self._inventory = inventory
        self._store = store
        self._secrets = secrets

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        provider: str,
        secret: str,
        label: str | None,
        reason: str,
    ) -> PlatformKeyChange:
        """Encrypt inside the platform boundary, then write one audited row.

        The raw key exists in this process only between the request body and
        ``encrypt``; nothing below this line ever sees it, which is why the
        label defaults to a masked hint rather than anything derived from the
        value at a later layer.
        """
        actor = _actor(ctx)
        configured = _configured(self._inventory, provider)
        if configured.keyless:
            raise ValidationError(
                f"provider {configured.provider!r} takes no credential; "
                f"a key stored for it would never be read",
                code=_PROVIDER_UNKNOWN,
            )
        cleaned_secret = secret.strip()
        if not cleaned_secret:
            raise ValidationError("credential value must not be empty")
        ciphertext = await self._secrets.encrypt(
            _TENANT_SECRETS_KEY, cleaned_secret.encode("utf-8")
        )
        return await self._store.store(
            provider=configured.provider,
            ciphertext=ciphertext,
            key_name=_TENANT_SECRETS_KEY,
            label=_label_for(label, cleaned_secret),
            actor_user_id=actor,
            reason=_reason(reason),
        )


class RevokePlatformProviderKey:
    """Withdraw the platform key for one provider. Idempotent."""

    def __init__(self, inventory: ProviderInventory, store: PlatformCredentialStore) -> None:
        self._inventory = inventory
        self._store = store

    async def execute(
        self, ctx: ExecutionContext, *, provider: str, reason: str
    ) -> PlatformKeyChange:
        actor = _actor(ctx)
        configured = _configured(self._inventory, provider)
        return await self._store.revoke(
            provider=configured.provider, actor_user_id=actor, reason=_reason(reason)
        )


class ProbePlatformProvider:
    """Spend one minimal call to find out whether a key actually works."""

    def __init__(
        self,
        inventory: ProviderInventory,
        store: PlatformCredentialStore,
        secrets: SecretsProvider,
        probe: ProviderProbe,
        cache: CacheProvider,
    ) -> None:
        self._inventory = inventory
        self._store = store
        self._secrets = secrets
        self._probe = probe
        self._cache = cache

    async def execute(
        self, ctx: ExecutionContext, *, provider: str, secret: str | None = None
    ) -> ProbeOutcome:
        """Probe a candidate key, or the stored one when none is supplied.

        Both modes exist because they answer different questions. A candidate
        is what an operator holds BEFORE committing — checking it first is the
        only way to avoid rotating a live provider onto a key that turns out
        to be wrong, and nothing about the candidate is written down. The
        stored key is what answers "why is this provider failing right now",
        which no pre-save check can ever tell you.
        """
        _actor(ctx)
        configured = _configured(self._inventory, provider)
        if not configured.probeable:
            raise ValidationError(
                f"provider {configured.provider!r} has no route this platform can probe "
                f"without generating billable output"
            )
        api_key = await self._api_key_for(configured, secret)
        await self._consume_probe_budget(configured.provider)
        return await self._probe.probe(configured.provider, api_key)

    async def _api_key_for(self, configured: ConfiguredProvider, secret: str | None) -> str:
        """The candidate, the stored key, or "" for a keyless provider."""
        if configured.keyless:
            if secret is not None:
                raise ValidationError(
                    f"provider {configured.provider!r} takes no credential; "
                    f"probe it without one to check that it answers",
                    code=_PROVIDER_UNKNOWN,
                )
            return ""
        if secret is not None:
            cleaned = secret.strip()
            if not cleaned:
                raise ValidationError("credential value must not be empty")
            return cleaned
        cipher = await self._store.active_cipher(configured.provider)
        if cipher is None:
            raise NotFoundError(
                f"no active platform credential for provider {configured.provider}",
                code=_NONE_AVAILABLE,
            )
        plaintext = await self._secrets.decrypt(cipher.key_name, cipher.ciphertext)
        return plaintext.decode("utf-8")

    async def _consume_probe_budget(self, provider: str) -> None:
        """A fixed window per provider, counted where every worker can see it.

        The TTL is set only on the first increment of a window: refreshing it
        on every call would turn a fixed window into a sliding ban that never
        expires while anyone keeps trying.
        """
        key = f"admin:provider-probe:{provider}"
        used = await self._cache.incr(key)
        if used == 1:
            await self._cache.expire(key, _PROBE_WINDOW_S)
        if used > _PROBE_MAX_PER_WINDOW:
            raise RateLimitedError(
                f"provider {provider} may be probed at most "
                f"{_PROBE_MAX_PER_WINDOW} times per {_PROBE_WINDOW_S}s",
                retry_after_s=_PROBE_WINDOW_S,
            )


@dataclass(frozen=True, slots=True)
class PlatformProviderUseCases:
    """The provider administration face the HTTP API holds.

    Like every other bundle in this codebase, what it OMITS is the point:
    there is no face here that returns a credential, decrypted or otherwise.
    """

    list: ListPlatformProviders
    set_key: SetPlatformProviderKey
    revoke_key: RevokePlatformProviderKey
    probe: ProbePlatformProvider


def _actor(ctx: ExecutionContext) -> str:
    if ctx.user_id is None:
        raise ForbiddenError("authenticated user is required")
    return ctx.user_id


def _configured(inventory: ProviderInventory, provider: str) -> ConfiguredProvider:
    """Resolve a provider name against the routing table, case-insensitively.

    Unknown here means "this deployment routes nothing to it", which is a
    stricter and more useful test than ``ProviderRef``'s vocabulary check: a
    perfectly spellable provider with no route is one whose key nothing would
    ever spend.
    """
    wanted = provider.strip().lower()
    for configured in inventory.configured_providers():
        if configured.provider.lower() == wanted:
            return configured
    raise ValidationError(
        f"provider {provider!r} is not configured on this deployment",
        code=_PROVIDER_UNKNOWN,
    )


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not _MIN_REASON_LENGTH <= len(cleaned) <= _MAX_REASON_LENGTH:
        raise ValidationError(
            f"reason must be between {_MIN_REASON_LENGTH} and {_MAX_REASON_LENGTH} characters"
        )
    return cleaned


def _label_for(label: str | None, raw_key: str) -> str:
    """An operator's name for the key, else the masked last four characters —
    the same non-secret display hint ``AddUserCredential`` mints, so a rotated
    key stays distinguishable from the one it replaced without being readable."""
    cleaned = (label or "").strip()
    if not cleaned:
        return f"****{raw_key[-4:]}"
    if len(cleaned) > _MAX_LABEL_LENGTH:
        raise ValidationError(f"label must be at most {_MAX_LABEL_LENGTH} characters")
    return cleaned
