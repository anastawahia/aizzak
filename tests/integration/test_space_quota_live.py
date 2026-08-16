"""Live-Postgres proof of the per-space quota (``docs/spaces-backend-plan.md``
step 5, §3.3; 09-testing-strategy §3).

The unit file next door proves the SEQUENCE. Three of this step's claims are
not provable there at all, because they are claims about PostgreSQL:

* ``SELECT ... FOR UPDATE`` runs as ``app_rw`` — a role that is neither
  superuser nor ``BYPASSRLS``, against a table with ``FORCE ROW LEVEL
  SECURITY``, and ``FOR UPDATE`` needs the UPDATE privilege;
* the lock actually BLOCKS a second registration into the same space until the
  first transaction ends, which is the entire answer to plan risk 2 — and it
  only does so because ``UnitOfWork.begin`` holds one transaction open across
  the whole service call;
* ``SUM(size_bytes)`` returns an ``int``. PostgreSQL types ``sum(bigint)`` as
  ``numeric``, so without the adapter's cast this port hands back a
  ``decimal.Decimal`` while type-checking green.

The registrar is a fake here, and deliberately: this file is about the gate,
not about what happens after it opens, and a real ``FileTransferService``
would drag MinIO into a database test. Rows are seeded through raw SQL with
``space_id`` filled in, because no writer fills it yet — that is plan step 6,
and the quota has to be correct before the column has writers, not after.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.space_quota import SpaceQuotaService
from app.framework.errors import ConflictError, NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName

pytestmark = [pytest.mark.live_db]


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _space(workspace_id: str, name: str = "Research") -> Space:
    at = utc_now()
    return Space(
        id=new_uuid7(),
        workspace_id=workspace_id,
        name=SpaceName(name),
        created_by=new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=None,
        version=1,
    )


async def _seed_file(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    workspace_id: str,
    space_id: str | None,
    size_bytes: int,
    deleted: bool = False,
) -> str:
    """One ``files.files`` row with ``space_id`` ALREADY SET — the state plan
    step 6 will produce. ``SqlFileRepository.add`` cannot make it: the
    aggregate has no ``space_id`` yet and the INSERT does not name the column.
    """
    file_id = new_uuid7()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
        )
        await session.execute(
            text(
                "INSERT INTO files.files (id, workspace_id, name, content_type, size_bytes, "
                "storage_key, status, space_id, deleted_at) VALUES (:id, :ws, :name, "
                "'application/pdf', :size, :key, 'uploaded', :space, "
                "CASE WHEN :deleted THEN now() ELSE NULL END)"
            ),
            {
                "id": file_id,
                "ws": workspace_id,
                "name": f"{file_id}.pdf",
                "size": size_bytes,
                "key": f"{workspace_id}/{file_id}",
                "space": space_id,
                "deleted": deleted,
            },
        )
    return file_id


class _FakeRegistrar:
    """``FileTransferService.register``'s shape, with a settable delay so the
    serialisation test can hold the lock open while a second caller asks."""

    def __init__(self, *, hold_s: float = 0.0, trace: list[str] | None = None) -> None:
        self._hold_s = hold_s
        self.trace = trace if trace is not None else []
        self.calls = 0

    async def register(
        self, ctx: ExecutionContext, *, name: str, content_type: str, size_bytes: int
    ) -> str:
        self.calls += 1
        self.trace.append(f"registering {name}")
        if self._hold_s:
            await asyncio.sleep(self._hold_s)
        self.trace.append(f"registered {name}")
        return name


def _service(
    tenant_session: TenantSessionFactory,
    registrar: _FakeRegistrar,
    limits: Limits,
) -> SpaceQuotaService[str]:
    return SpaceQuotaService(
        registrar,
        SqlSpaceRepository(tenant_session),
        SqlFileRepository(tenant_session),
        tenant_session,
        limits,
    )


# --------------------------------------------------------------------------- #
# (1) the lock anchor itself                                                  #
# --------------------------------------------------------------------------- #
async def test_an_active_space_can_be_locked_by_the_application_role(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """``FOR UPDATE`` needs the UPDATE privilege, and ``app_rw`` only has it
    because ``spaces.spaces`` is in ``_TENANT_TABLES`` (``ops/provision.py``).
    Dropping it there would not fail any other test in this suite."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)

    assert await repo_spaces.lock(ctx, space.id) is True


async def test_a_soft_deleted_space_cannot_be_locked_although_get_still_finds_it(
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The one place ``lock`` deviates from ``get`` on purpose: ``get`` keeps
    answering after a soft delete so deletion stays idempotent, but holding a
    deleted space still would serialise writes into a space that must receive
    none."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)
    space.soft_delete(utc_now())
    await repo_spaces.save(ctx, space)

    assert await repo_spaces.get(ctx, space.id) is not None
    assert await repo_spaces.lock(ctx, space.id) is False


async def test_another_tenants_space_and_an_unknown_id_are_both_unlockable(
    repo_spaces: SqlSpaceRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    space = _space(ws_a)
    await repo_spaces.add(_ctx(ws_a), space)

    assert await repo_spaces.lock(_ctx(ws_b), space.id) is False
    assert await repo_spaces.lock(_ctx(ws_a), new_uuid7()) is False


# --------------------------------------------------------------------------- #
# (2) the sum                                                                 #
# --------------------------------------------------------------------------- #
async def test_the_total_covers_this_spaces_active_files_and_nothing_else(
    repo_files: SqlFileRepository,
    repo_spaces: SqlSpaceRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ws, other_ws = new_uuid7(), new_uuid7()
    ctx = _ctx(ws)
    mine, sibling = _space(ws, "Mine"), _space(ws, "Sibling")
    await repo_spaces.add(ctx, mine)
    await repo_spaces.add(ctx, sibling)

    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=mine.id, size_bytes=1000)
    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=mine.id, size_bytes=234)
    # Each of these must be invisible to the total, for a different reason.
    await _seed_file(
        sessionmaker_app, workspace_id=ws, space_id=mine.id, size_bytes=9_000, deleted=True
    )
    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=sibling.id, size_bytes=9_000)
    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=None, size_bytes=9_000)
    await _seed_file(sessionmaker_app, workspace_id=other_ws, space_id=mine.id, size_bytes=9_000)

    assert await repo_files.bytes_in_space(ctx, mine.id) == 1234


async def test_the_total_is_a_plain_int_whether_the_space_is_empty_or_not(
    repo_files: SqlFileRepository,
    repo_spaces: SqlSpaceRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """``COALESCE`` covers the empty space; the CAST covers the type. Without
    it PostgreSQL answers ``numeric`` (``sum(bigint) -> numeric``) and asyncpg
    hands back a ``Decimal`` that satisfies the port's ``int`` annotation at
    type-check time and nothing at runtime — so both branches are asserted,
    because only the populated one goes anywhere near ``sum``."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)

    empty = await repo_files.bytes_in_space(ctx, space.id)
    assert empty == 0
    assert type(empty) is int

    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=space.id, size_bytes=7)
    total = await repo_files.bytes_in_space(ctx, space.id)
    assert total == 7
    assert type(total) is int


# --------------------------------------------------------------------------- #
# (3) the service, end to end against real rows                               #
# --------------------------------------------------------------------------- #
async def test_a_registration_that_fits_the_real_total_goes_through(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)
    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=space.id, size_bytes=500)

    registrar = _FakeRegistrar()
    service = _service(tenant_session, registrar, Limits(max_space_bytes=1000))

    result = await service.register(
        ctx, space_id=space.id, name="fits.pdf", content_type="application/pdf", size_bytes=500
    )

    assert result == "fits.pdf"
    assert registrar.calls == 1


async def test_a_registration_past_the_real_total_is_refused_with_the_named_code(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)
    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=space.id, size_bytes=500)

    registrar = _FakeRegistrar()
    service = _service(tenant_session, registrar, Limits(max_space_bytes=1000))

    with pytest.raises(ConflictError) as exc_info:
        await service.register(
            ctx, space_id=space.id, name="over.pdf", content_type="application/pdf", size_bytes=501
        )

    assert exc_info.value.code == "spaces.quota_exceeded"
    assert registrar.calls == 0


async def test_registering_into_a_space_that_is_not_there_is_a_404(
    tenant_session: TenantSessionFactory,
) -> None:
    registrar = _FakeRegistrar()
    service = _service(tenant_session, registrar, Limits())

    with pytest.raises(NotFoundError):
        await service.register(
            _ctx(new_uuid7()),
            space_id=new_uuid7(),
            name="x.pdf",
            content_type="application/pdf",
            size_bytes=1,
        )

    assert registrar.calls == 0


# --------------------------------------------------------------------------- #
# (4) ⭐ the race the lock exists for (plan risk 2)                            #
# --------------------------------------------------------------------------- #
async def test_two_registrations_into_one_space_serialise_on_its_row(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The failure this prevents is silent: two 600 MiB uploads read the same
    total, both find room, and the space ends at 1.2 GiB with no error
    anywhere. So the assertion is on ORDER — the second caller must not even
    reach its own registration until the first transaction has ended.

    The first caller holds the lock for the length of its registrar call,
    which is exactly the window the service's docstring documents.
    """
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)

    trace: list[str] = []
    slow = _service(tenant_session, _FakeRegistrar(hold_s=0.75, trace=trace), Limits())
    quick = _service(tenant_session, _FakeRegistrar(trace=trace), Limits())

    async def first() -> None:
        await slow.register(
            ctx, space_id=space.id, name="first", content_type="application/pdf", size_bytes=1
        )

    async def second() -> None:
        await asyncio.sleep(0.2)
        trace.append("second asking")
        await quick.register(
            ctx, space_id=space.id, name="second", content_type="application/pdf", size_bytes=1
        )

    await asyncio.gather(first(), second())

    assert trace == [
        "registering first",
        "second asking",
        "registered first",
        "registering second",
        "registered second",
    ]


async def test_registrations_into_two_different_spaces_do_not_block_each_other(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
) -> None:
    """The counterpart, and the reason the anchor is the SPACE's row rather
    than anything coarser: a quota that serialises a whole workspace would be
    correct and unusable."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    one, two = _space(ws, "One"), _space(ws, "Two")
    await repo_spaces.add(ctx, one)
    await repo_spaces.add(ctx, two)

    trace: list[str] = []
    slow = _service(tenant_session, _FakeRegistrar(hold_s=0.75, trace=trace), Limits())
    quick = _service(tenant_session, _FakeRegistrar(trace=trace), Limits())

    async def first() -> None:
        await slow.register(
            ctx, space_id=one.id, name="first", content_type="application/pdf", size_bytes=1
        )

    async def second() -> None:
        await asyncio.sleep(0.2)
        await quick.register(
            ctx, space_id=two.id, name="second", content_type="application/pdf", size_bytes=1
        )

    await asyncio.gather(first(), second())

    # "second" finishes while "first" is still holding its own space's row.
    assert trace == [
        "registering first",
        "registering second",
        "registered second",
        "registered first",
    ]


async def test_the_lock_is_released_when_the_quota_rejects(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    """A rejection leaves the unit of work through its ``finally``, so the
    transaction rolls back and the row is free. Without that, one over-quota
    request would wedge every later registration into the same space."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)
    await _seed_file(sessionmaker_app, workspace_id=ws, space_id=space.id, size_bytes=1000)

    registrar = _FakeRegistrar()
    service = _service(tenant_session, registrar, Limits(max_space_bytes=1000))

    with pytest.raises(ConflictError):
        await service.register(
            ctx, space_id=space.id, name="over.pdf", content_type="application/pdf", size_bytes=1
        )

    # The next caller must not hang; `asyncio.timeout` turns a lock still held
    # into a failing test instead of a suite that never finishes.
    async with asyncio.timeout(5):
        assert await repo_spaces.lock(ctx, space.id) is True
