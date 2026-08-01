"""Credentials value objects (pure — 06-domain-models §3).

Frozen, self-validating primitives. ``ProviderRef`` is the provider identity (a
known base provider, or a ``<modality>:<name>`` key); ``CipherRef`` is the opaque
Vault Transit reference (ciphertext + Transit key id) and never holds a raw
secret (INV-C2). ``CredentialScope``/``CredentialStatus`` are closed
vocabularies. Identifiers stay plain UUIDv7 text; the domain imports no framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.credentials.domain.errors import InvalidCredentialInput

# Base LLM providers (02 §3.5) plus namespaced modality providers embedding:* /
# image:* / video:* (06 §3). Deep provider validation is the resolver's concern.
_BASE_PROVIDERS = frozenset({"openai", "gemini", "claude", "ollama", "openrouter"})
_MODALITY_RE = re.compile(r"^(embedding|image|video):[a-z0-9][a-z0-9_-]*$")


class CredentialScope(StrEnum):
    """Whether a credential belongs to a workspace user or the whole platform."""

    PLATFORM = "platform"
    USER = "user"


class CredentialStatus(StrEnum):
    """Lifecycle of a stored credential."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ProviderRef:
    """A provider identity: a known base provider or a ``<modality>:<name>`` key."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if normalized not in _BASE_PROVIDERS and not _MODALITY_RE.match(normalized):
            raise InvalidCredentialInput(f"unknown provider: {self.value!r}")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class CipherRef:
    """Opaque Vault Transit reference: ciphertext + Transit key id (never a raw secret)."""

    ciphertext: str
    key_name: str

    def __post_init__(self) -> None:
        if not self.ciphertext:
            raise InvalidCredentialInput("cipher reference must not be empty")
        if not self.key_name:
            raise InvalidCredentialInput("cipher key name must not be empty")
