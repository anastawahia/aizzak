"""``app.ops.role_guard`` -- a cross-tenant sweep refuses any role but its
own (capacity 5.7). The refusal against a real Postgres, through the real
tool, is in ``tests/integration/test_ops_scheduler_live.py``; this checks the
check itself over a stub engine, and that each of the three tools asks for
the role its RLS carve-outs name.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.ops import purge, retention, rotate_transit
from app.ops.provision import PURGE_ROLE, RETENTION_ROLE, TRANSIT_ROTATOR_ROLE
from app.ops.role_guard import RoleMismatchError, require_role


class _Result:
    def __init__(self, value: str) -> None:
        self._value = value

    def scalar_one(self) -> str:
        return self._value


class _Conn:
    def __init__(self, role: str) -> None:
        self.role = role
        self.sql: list[str] = []

    async def execute(self, stmt: Any) -> _Result:
        self.sql.append(str(stmt))
        return _Result(self.role)

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Engine:
    def __init__(self, role: str) -> None:
        self.conn = _Conn(role)

    def connect(self) -> _Conn:
        return self.conn


async def test_the_right_role_passes_with_one_question() -> None:
    engine = _Engine("retention_sweeper")
    await require_role(engine, tool="t", expected="retention_sweeper")  # type: ignore[arg-type]
    assert engine.conn.sql == ["SELECT current_user"]


async def test_any_other_role_is_refused_with_the_reason_rls_gives_no_error() -> None:
    """The measured case: the table OWNER, whose dry run printed `affected: 0`
    for 6,943 rows past the window."""
    with pytest.raises(RoleMismatchError) as caught:
        await require_role(
            _Engine("aizzak_owner"), tool="app.ops.retention", expected="retention_sweeper"
        )  # type: ignore[arg-type]
    message = str(caught.value)
    assert "'aizzak_owner'" in message and "'retention_sweeper'" in message
    assert "zero rows rather than an error" in message


@pytest.mark.parametrize(
    ("module", "role"),
    [(retention, RETENTION_ROLE), (purge, PURGE_ROLE), (rotate_transit, TRANSIT_ROTATOR_ROLE)],
)
def test_each_cross_tenant_tool_checks_its_own_role_before_it_reads(module: Any, role: str) -> None:
    source = inspect.getsource(module._run_cli)
    constant = {
        RETENTION_ROLE: "RETENTION_ROLE",
        PURGE_ROLE: "PURGE_ROLE",
        TRANSIT_ROTATOR_ROLE: "TRANSIT_ROTATOR_ROLE",
    }[role]
    assert f"expected={constant}" in source
    # Before the first statement that reads a tenant table.
    first_read = min(
        source.find(marker)
        for marker in ("sweep_all", "find_candidates", "rewrap_all", "_SWEEPERS")
        if marker in source
    )
    assert source.find("require_role") < first_read
