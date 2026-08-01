"""SecretsProvider driven port (02-port-contracts §1.9 · Vault, D-03/22).

The single interface to secrets: Vault KV reads plus Transit encrypt/decrypt.
No module talks to Vault directly (05 §3.3, SEC-07). ``credentials`` and
``integrations`` share the unified ``tenant-secrets`` Transit key through here.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.types import Json


class SecretsProvider(Protocol):
    async def get_secret(self, path: str) -> Json: ...  # Vault KV

    async def encrypt(self, key_name: str, plaintext: bytes) -> str: ...  # Transit -> ciphertext

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes: ...  # Transit
