"""Unit tests for ``TenantSessionFactory``'s request-scoped unit of work
(5.1-أ — ``begin(ctx)``, structurally ``framework/ports/unit_of_work.py``'s
``UnitOfWork``).

Hermetic: a fake sessionmaker stands in for SQLAlchemy so these tests never
touch a real database. The point is proving the ``ContextVar``-based
join/lifecycle logic in ``infrastructure/persistence/rls.py`` itself, not
Postgres — ``tests/integration/test_producer_atomicity.py`` proves the same
seam end to end against a real one.
"""

from __future__ import annotations

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.infrastructure.persistence.rls import TenantSessionFactory


class _FakeTransaction:
    """Emulates ``AsyncSessionTransaction`` -- commits on clean exit, rolls
    back on exception, exactly the ``session.begin()`` semantics
    ``TenantSessionFactory`` relies on."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> None:
        if exc_type is None:
            self._session.committed = True
        else:
            self._session.rolled_back = True


class _FakeSession:
    """Emulates the ONE slice of ``AsyncSession`` ``TenantSessionFactory``
    touches: entering/exiting as its own context manager, ``begin()``, and
    the ``execute`` call ``set_tenant`` issues."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.executed: list[object] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> None:
        self.closed = True

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    async def execute(self, statement: object, params: object | None = None) -> None:
        self.executed.append(statement)


class _FakeSessionMaker:
    """Emulates ``async_sessionmaker[AsyncSession]`` -- a fresh ``_FakeSession``
    per call, all remembered so a test can assert exactly how many
    transactions a scenario actually opened."""

    def __init__(self) -> None:
        self.created: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        session = _FakeSession()
        self.created.append(session)
        return session


def _ctx(workspace_id: str = "ws-1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u-1",
        correlation_id="corr-1",
        roles=frozenset({"member"}),
    )


# --------------------------------------------------------------------------- #
# __call__ outside any begin() -- legacy per-call behaviour                    #
# --------------------------------------------------------------------------- #
async def test_call_outside_any_uow_opens_sets_and_commits_its_own_session() -> None:
    """Unchanged from before 5.1-أ: one call, one session, one commit."""
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)

    async with factory(_ctx()) as session:
        assert session.committed is False
        assert len(session.executed) == 1  # set_tenant's SELECT set_config(...)

    assert session.committed is True
    assert session.rolled_back is False
    assert len(maker.created) == 1


async def test_two_calls_outside_any_uow_open_two_independent_sessions() -> None:
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)

    async with factory(_ctx()) as first:
        pass
    async with factory(_ctx()) as second:
        pass

    assert first is not second
    assert len(maker.created) == 2


# --------------------------------------------------------------------------- #
# begin(ctx) -- the request-scoped unit of work                                #
# --------------------------------------------------------------------------- #
async def test_two_calls_inside_begin_share_the_same_session_with_no_inner_commit() -> None:
    """Mutation-battery target #1: if `__call__` ever stopped joining the
    active session, this would open a SECOND transaction here."""
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)
    ctx = _ctx()

    async with factory.begin(ctx):
        async with factory(ctx) as session_a:
            assert session_a.committed is False
        assert session_a.committed is False  # the first __call__'s exit did not commit

        async with factory(ctx) as session_b:
            assert session_b.committed is False

        assert session_a is session_b
        assert len(maker.created) == 1  # begin() opened the only session there is

    assert session_a.committed is True  # only the OUTER begin() commits, on its own exit


async def test_begin_sets_the_tenant_guc_once_and_a_joined_call_does_not_repeat_it() -> None:
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)
    ctx = _ctx()

    async with factory.begin(ctx):
        session = maker.created[0]
        assert len(session.executed) == 1
        async with factory(ctx):
            pass
        assert len(session.executed) == 1


async def test_begin_commits_the_session_on_clean_exit() -> None:
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)

    async with factory.begin(_ctx()):
        pass

    session = maker.created[0]
    assert session.committed is True
    assert session.rolled_back is False


async def test_begin_rolls_back_the_session_on_an_exception_raised_inside() -> None:
    """Mutation-battery target #2: swapping rollback for commit must be
    caught here."""
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)

    with pytest.raises(RuntimeError, match="boom"):
        async with factory.begin(_ctx()):
            raise RuntimeError("boom")

    session = maker.created[0]
    assert session.rolled_back is True
    assert session.committed is False


async def test_a_joined_calls_exception_also_rolls_back_the_shared_session() -> None:
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)
    ctx = _ctx()

    with pytest.raises(RuntimeError, match="boom"):
        async with factory.begin(ctx):
            async with factory(ctx):
                raise RuntimeError("boom")

    session = maker.created[0]
    assert session.rolled_back is True
    assert session.committed is False


# --------------------------------------------------------------------------- #
# ContextVar lifecycle -- always reset, exception or not                       #
# --------------------------------------------------------------------------- #
async def test_context_var_resets_after_a_clean_exit_so_the_next_call_is_fresh() -> None:
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)
    ctx = _ctx()

    async with factory.begin(ctx), factory(ctx) as inside:
        pass

    async with factory(ctx) as after:
        pass

    assert after is not inside
    assert len(maker.created) == 2


async def test_context_var_resets_even_after_an_exception() -> None:
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)
    ctx = _ctx()

    with pytest.raises(RuntimeError):
        async with factory.begin(ctx):
            raise RuntimeError("boom")

    async with factory(ctx) as after:
        pass

    assert after.committed is True  # a fresh, independent, legacy per-call session
    assert len(maker.created) == 2  # the crashed begin() session, then this one


# --------------------------------------------------------------------------- #
# Cross-tenant guard                                                           #
# --------------------------------------------------------------------------- #
async def test_a_call_for_a_different_workspace_inside_a_uow_raises() -> None:
    """A cross-tenant join inside one UoW is a bug in the caller -- it must
    never be silently honoured by handing back the wrong-tenant session."""
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)

    async with factory.begin(_ctx(workspace_id="ws-1")):
        with pytest.raises(AppError) as excinfo:
            async with factory(_ctx(workspace_id="ws-2")):
                pass

    assert excinfo.value.code == "common.internal"


async def test_a_call_for_the_same_workspace_inside_a_uow_does_not_raise() -> None:
    maker = _FakeSessionMaker()
    factory = TenantSessionFactory(maker)
    ctx = _ctx(workspace_id="ws-1")

    async with factory.begin(ctx), factory(_ctx(workspace_id="ws-1")) as session:
        assert session is not None
