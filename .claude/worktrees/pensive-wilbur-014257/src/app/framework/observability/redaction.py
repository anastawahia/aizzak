"""Secret redaction for logs and error surfaces (10-code-standards §9,§10).

Never log tokens, secrets, API keys, or authorization material. ``redact``
walks a structure and masks any mapping key whose name signals a secret. It is
defence-in-depth: the primary rule is simply to not pass secrets to the logger.
"""

from __future__ import annotations

from typing import Any

REDACTED = "***"
_MAX_DEPTH = 6

# Substrings that mark a mapping key as secret-bearing. Matched case-insensitively.
_SECRET_MARKERS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
    "access_key",
    "client_secret",
    "ciphertext",
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a deep copy of ``value`` with secret-bearing fields masked.

    Recursion is bounded to avoid pathological/cyclic structures overflowing.
    """
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_key(key):
                out[key] = REDACTED
            else:
                out[key] = redact(item, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item, _depth=_depth + 1) for item in value)
    return value
