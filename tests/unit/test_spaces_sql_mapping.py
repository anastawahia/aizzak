"""Unit tests for the pure parts of the spaces SQL adapter — driver-error
translation, row hydration, and the adapter's structural fit to its port.

No database: everything here is a function over values. The adapter's
behaviour AGAINST a real table (RLS isolation, the optimistic lock, the
uniqueness index that produces the ``23505`` translated below) arrives with
the migration in step 2, as ``tests/integration/test_spaces_repository_rls.py``
— the same order every other module followed.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.framework.clock import utc_now
from app.framework.errors import AppError, ConflictError
from app.modules.spaces.adapters.sql_repository import (
    SqlSpaceRepository,
    _hydrate,
    _translate,
)
from app.modules.spaces.ports.repository import SpaceRepository


class _Orig(Exception):
    """The driver exception SQLAlchemy wraps — only ``sqlstate`` is read."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _dbapi_error(sqlstate: str | None) -> DBAPIError:
    return DBAPIError("INSERT INTO spaces.spaces", {}, _Orig(sqlstate))


# --------------------------------------------------------------------------- #
# _translate                                                                   #
# --------------------------------------------------------------------------- #
def test_a_unique_violation_names_the_duplicate_name() -> None:
    # The one deviation from the `files` translator: this table's only
    # collidable unique constraint is `ux_spaces_ws_name`, so the server KNOWS
    # which conflict this is and says so instead of `common.conflict`.
    error = _translate(_dbapi_error("23505"))
    assert isinstance(error, ConflictError)
    assert error.code == "spaces.duplicate_name"
    assert error.status == 409


def test_an_rls_rejection_is_internal_not_a_client_error() -> None:
    # A forged cross-tenant `workspace_id` caught by `WITH CHECK`: a
    # well-behaved caller can never reach it, so it is not a 4xx.
    error = _translate(_dbapi_error("42501"))
    assert error.code == "common.internal"
    assert error.status == 500


@pytest.mark.parametrize("sqlstate", ["23503", "08006", None])
def test_any_other_driver_failure_folds_into_internal(sqlstate: str | None) -> None:
    error = _translate(_dbapi_error(sqlstate))
    assert type(error) is AppError
    assert error.code == "common.internal"


def test_the_driver_exception_never_escapes() -> None:
    # R6: `sqlalchemy`/`asyncpg` types stop at the adapter boundary.
    assert not isinstance(_translate(_dbapi_error("23505")), DBAPIError)


# --------------------------------------------------------------------------- #
# _hydrate                                                                     #
# --------------------------------------------------------------------------- #
def test_hydrate_rebuilds_the_aggregate_through_its_value_object() -> None:
    now = utc_now()
    row = {
        "id": "s1",
        "workspace_id": "w1",
        "name": "Research",
        "created_by": "u1",
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "version": 3,
    }
    space = _hydrate(row)  # type: ignore[arg-type]
    assert space.name.value == "Research"
    assert space.version == 3
    assert space.is_active is True


def test_hydrate_carries_the_deletion_stamp() -> None:
    now = utc_now()
    row = {
        "id": "s1",
        "workspace_id": "w1",
        "name": "Research",
        "created_by": None,
        "created_at": now,
        "updated_at": now,
        "deleted_at": now,
        "version": 1,
    }
    assert _hydrate(row).is_active is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Structural fit                                                               #
# --------------------------------------------------------------------------- #
def test_the_adapter_implements_every_method_its_port_declares() -> None:
    """The ports here are structural Protocols matched at the wiring site, so
    nothing checks the adapter against them until the Composition Root does
    (step 13). Until then this is the check: a method added to the port or
    renamed on the adapter fails here rather than at boot."""
    declared = {
        name
        for name, value in vars(SpaceRepository).items()
        if not name.startswith("_") and callable(value)
    }
    # `lock` joined in plan step 5 (§3.143) — the quota's serialisation
    # anchor, and the one method on this port whose caller is not a use-case.
    assert declared == {"get", "lock", "add", "save", "list"}
    assert declared <= {name for name in dir(SqlSpaceRepository) if not name.startswith("_")}
