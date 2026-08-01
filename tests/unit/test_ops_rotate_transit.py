"""Hermetic tests for the Transit key-rotation sweep (``app.ops.rotate_transit``,
P1-9, ``docs/p1-hardening-plan.md`` §3 step 12).

Everything here runs over ``_FakeEngine``/``_FakeConnection`` (the
``test_ops_retention.py``/``test_ops_dlq.py`` ``_Fake*``/``_StubRedis``
precedent) and a ``_FakeSecrets`` stand-in for ``VaultSecrets`` -- no real
Vault, no real Postgres. What this file proves: which SQL each step issues,
that ``_key_version`` parses Vault's own ciphertext prefix rather than
comparing raw bytes (measured live to change on every rewrap regardless of
whether the key version does -- see ``rotate_transit``'s own module
docstring), that a row already at the current version is skipped (never
written), that a genuinely older one is persisted with the concurrency
guard, that a concurrent write racing the sweep is not miscounted, and the
CLI's own argument shape. The live round trips this hermetic layer cannot
prove -- the RLS cross-tenant reach, and the honest "old ciphertext stops
decrypting, rewrapped one survives" proof -- live in
``tests/integration/test_rotate_transit_ops_live.py`` and the extended
``tests/integration/test_vault_secrets.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.ops import rotate_transit as rotate_transit_module
from app.ops.rotate_transit import (
    _TABLE_SPECS,
    RewrapResult,
    _key_version,
    rewrap_all,
    rewrap_table,
)


class _FakeResult:
    def __init__(self, *, rows: list[tuple[Any, ...]] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, engine: _FakeEngine) -> None:
        self._engine = engine

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(stmt)
        self._engine.calls.append((sql, dict(params or {})))
        if sql.strip().upper().startswith("SELECT"):
            return _FakeResult(rows=self._engine.select_rows)
        return _FakeResult(rowcount=self._engine.update_rowcount)


class _FakeConnCtx:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeEngine:
    """``connect()`` (read-only) and ``begin()`` (a committing write) both
    hand back a connection recording every call on the SAME engine -- the
    real distinction (``rewrap_table``'s docstring: no Vault I/O held open
    inside a transaction) does not matter for asserting SQL shape."""

    def __init__(
        self, select_rows: list[tuple[Any, ...]] | None = None, update_rowcount: int = 1
    ) -> None:
        self.select_rows = select_rows or []
        self.update_rowcount = update_rowcount
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def connect(self) -> _FakeConnCtx:
        return _FakeConnCtx(_FakeConnection(self))

    def begin(self) -> _FakeConnCtx:
        return _FakeConnCtx(_FakeConnection(self))


class _FakeSecrets:
    """Stands in for ``VaultSecrets`` -- only ``rewrap`` is ever called by
    this module."""

    def __init__(self, rewrap_fn: Callable[[str, str], str]) -> None:
        self._rewrap_fn = rewrap_fn
        self.calls: list[tuple[str, str]] = []

    async def rewrap(self, key_name: str, ciphertext: str) -> str:
        self.calls.append((key_name, ciphertext))
        return self._rewrap_fn(key_name, ciphertext)


def _unchanged(_key_name: str, ciphertext: str) -> str:
    return ciphertext


def _bump_version(_key_name: str, ciphertext: str) -> str:
    return ciphertext.replace(":v1:", ":v2:")


def test_key_version_parses_vaults_own_wire_prefix() -> None:
    assert _key_version("vault:v1:aaaa") == 1
    assert _key_version("vault:v12:bbbb") == 12


def test_key_version_rejects_a_string_that_is_not_vault_transit_ciphertext() -> None:
    """A data-integrity fault worth crashing loudly on (module docstring) --
    never silently miscounted as version 0 or 1."""
    with pytest.raises(ValueError, match="not a Vault Transit ciphertext"):
        _key_version("not-a-vault-ciphertext")


def test_the_three_table_specs_name_exactly_the_confirmed_ciphertext_columns() -> None:
    """Guards the module docstring's own claim (grepped from the encrypt call
    sites): credentials.credentials.ciphertext_ref,
    integrations.connections.token_ref, integrations.mcp_servers.auth_ref --
    no fourth site, no silent rename."""
    found = {(spec.table, spec.cipher_col) for spec in _TABLE_SPECS}
    assert found == {
        ("credentials.credentials", "ciphertext_ref"),
        ("integrations.connections", "token_ref"),
        ("integrations.mcp_servers", "auth_ref"),
    }
    assert all(spec.key_col == "key_id" for spec in _TABLE_SPECS)


async def test_rewrap_table_selects_only_rows_with_a_non_null_ciphertext() -> None:
    engine = _FakeEngine(select_rows=[])
    secrets = _FakeSecrets(_unchanged)
    spec = _TABLE_SPECS[1]  # integrations.connections -- nullable token_ref

    result = await rewrap_table(engine, secrets, spec)

    assert result.table == "integrations.connections"
    assert result.scanned == 0
    [(sql, _)] = engine.calls
    assert sql.startswith("SELECT id, token_ref, key_id FROM integrations.connections")
    assert "token_ref IS NOT NULL" in sql


async def test_a_ciphertext_already_at_the_current_key_version_is_skipped_not_written() -> None:
    """The idempotency mechanism the module docstring names: the rewrapped
    ciphertext's embedded key VERSION (never the raw bytes -- Vault mints a
    fresh nonce on every rewrap regardless) is compared to the stored one's,
    and an equal version means nothing is written and the row is not
    counted as rewrapped."""
    engine = _FakeEngine(select_rows=[("id-1", "vault:v1:aaa", "tenant-secrets")])
    secrets = _FakeSecrets(_unchanged)

    result = await rewrap_table(engine, secrets, _TABLE_SPECS[0])

    assert result.scanned == 1
    assert result.rewrapped == 0
    assert secrets.calls == [("tenant-secrets", "vault:v1:aaa")]
    # Only the SELECT was issued -- no UPDATE for an unchanged ciphertext.
    assert len(engine.calls) == 1


async def test_a_changed_ciphertext_is_persisted_with_the_concurrency_guard() -> None:
    engine = _FakeEngine(select_rows=[("id-1", "vault:v1:aaa", "tenant-secrets")])
    secrets = _FakeSecrets(_bump_version)

    result = await rewrap_table(engine, secrets, _TABLE_SPECS[0])

    assert result.rewrapped == 1
    assert result.dry_run is False
    [_, (update_sql, params)] = engine.calls
    assert update_sql.startswith("UPDATE credentials.credentials SET ciphertext_ref = :new")
    assert "id = :id AND ciphertext_ref = :old" in update_sql
    assert params == {"new": "vault:v2:aaa", "id": "id-1", "old": "vault:v1:aaa"}


async def test_dry_run_calls_vault_for_real_but_writes_nothing() -> None:
    engine = _FakeEngine(select_rows=[("id-1", "vault:v1:aaa", "tenant-secrets")])
    secrets = _FakeSecrets(_bump_version)

    result = await rewrap_table(engine, secrets, _TABLE_SPECS[0], dry_run=True)

    assert result.dry_run is True
    assert result.rewrapped == 1
    assert secrets.calls == [("tenant-secrets", "vault:v1:aaa")]  # Vault WAS called
    assert len(engine.calls) == 1  # but only the SELECT -- no UPDATE


async def test_a_row_changed_concurrently_is_not_counted_as_rewrapped() -> None:
    """The optimistic guard's other half: the UPDATE's WHERE clause matches
    zero rows when a concurrent write already changed the ciphertext, and
    that must not be miscounted as this sweep's own success."""
    engine = _FakeEngine(
        select_rows=[("id-1", "vault:v1:aaa", "tenant-secrets")], update_rowcount=0
    )
    secrets = _FakeSecrets(_bump_version)

    result = await rewrap_table(engine, secrets, _TABLE_SPECS[0])

    assert result.scanned == 1
    assert result.rewrapped == 0


async def test_rewrap_all_sweeps_every_table_in_the_documented_order() -> None:
    engine = _FakeEngine(select_rows=[])
    secrets = _FakeSecrets(_unchanged)

    results = await rewrap_all(engine, secrets)

    assert [r.table for r in results] == [
        "credentials.credentials",
        "integrations.connections",
        "integrations.mcp_servers",
    ]
    assert all(isinstance(r, RewrapResult) for r in results)


def test_cli_parses_dry_run() -> None:
    parser = rotate_transit_module._build_parser()
    args = parser.parse_args(["sweep", "--dry-run"])
    assert args.action == "sweep"
    assert args.dry_run is True


def test_cli_defaults_dry_run_to_false() -> None:
    parser = rotate_transit_module._build_parser()
    args = parser.parse_args(["sweep"])
    assert args.dry_run is False


def test_cli_requires_a_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.rotate_transit"])
    with pytest.raises(SystemExit):
        rotate_transit_module.main()
