"""Unit tests for the revocation command (``app.ops.revoke``, 3.79).

Hermetic: ``revoke_subjects`` is exercised over a stubbed client factory, so
nothing here needs a Redis. What matters is the argument handling and the
teardown — the denylist's own behaviour is pinned in
``test_auth_revocation.py`` and against a real server in
``tests/integration/test_auth_revocation_live.py``.

The CLI branch is worth a test of its own for one reason: this command is
reached by an operator during an incident, and a no-argument invocation that
silently succeeded (revoking nothing, exiting 0) would read as "done" at
exactly the wrong moment.
"""

from __future__ import annotations

import pytest

from app.framework.errors import ValidationError
from app.framework.settings.settings import RedisSettings
from app.ops import revoke as revoke_module


class _StubClient:
    def __init__(self) -> None:
        self.closed = False
        self.values: dict[str, bytes] = {}

    async def aclose(self) -> None:
        self.closed = True


class _StubCache:
    """Stands in for ``RedisCache`` over the stub client."""

    def __init__(self, client: _StubClient) -> None:
        self._client = client

    async def get(self, key: str) -> bytes | None:
        return self._client.values.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        self._client.values[key] = value

    async def delete(self, key: str) -> None:
        self._client.values.pop(key, None)

    async def incr(self, key: str, amount: int = 1) -> int:
        return amount

    async def expire(self, key: str, ttl_s: int) -> None:
        return None


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> _StubClient:
    client = _StubClient()
    monkeypatch.setattr(revoke_module, "create_redis_client", lambda settings: client)
    monkeypatch.setattr(revoke_module, "RedisCache", _StubCache)
    return client


async def test_every_named_subject_is_denied_under_the_specified_key(stub: _StubClient) -> None:
    await revoke_module.revoke_subjects(RedisSettings(), ("uid-a", "uid-b"))

    assert set(stub.values) == {"auth:revoked:uid-a", "auth:revoked:uid-b"}


async def test_the_client_is_closed_even_when_a_revocation_fails(stub: _StubClient) -> None:
    """A one-shot process leaving a connection open is a connection the server
    holds until its own timeout — and an operator runs this in a loop."""
    with pytest.raises(ValidationError):
        await revoke_module.revoke_subjects(RedisSettings(), ("uid-a", "  "))

    assert stub.closed is True


def test_no_arguments_exits_loudly_rather_than_revoking_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.revoke"])

    with pytest.raises(SystemExit) as raised:
        revoke_module.main()

    assert "usage:" in str(raised.value)
