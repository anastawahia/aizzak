"""Credentials DTOs — the wire shapes of ``/api/v1/credentials`` (03-api-spec
§2, Phase 6.1-و-2).

**The secret is never returned** (03 §2, verbatim: «لا يُعاد السرّ أبداً»).
``CredentialOut`` has no field that could carry one, and there is nothing to
strip on the way out either: the aggregate itself stores a ``CipherRef``, not
a plaintext key (INV-C2). The omission here and the encryption down there say
the same thing twice, deliberately — a redaction that lives only in a mapper
is one careless field away from leaking.

``secret`` is the raw provider key going IN, and it is the only place in the
platform where one exists in the clear: ``AddUserCredential`` encrypts it
through Vault Transit before anything is persisted, so it never reaches a
row, a log line, or a response.

``scope`` keeps the spec's ``Literal['platform','user']`` rather than
narrowing to ``'user'``. Platform scope is a real, valid scope — it is simply
not a tenant's to create (INV-C1: a platform row has ``workspace_id IS NULL``,
and no RLS-subject path inserts one). The router refuses it as 403, which is
the truth; a DTO-level ``Literal['user']`` would answer 422 «not a valid
scope» instead, which is a lie about the vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CredentialCreateIn(BaseModel):
    provider: str
    scope: Literal["platform", "user"]
    label: str | None = None
    # A key with nothing in it is not a credential. The domain refuses it too
    # (`AddUserCredential`'s empty check) — two guards, one number.
    secret: str = Field(min_length=1)


class CredentialOut(BaseModel):
    """Stored-credential metadata. No secret, by construction."""

    id: str
    provider: str
    scope: str
    label: str | None
    status: str
    created_at: datetime
