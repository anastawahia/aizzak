"""Unit tests for ``SpaceQuotaService`` (``docs/spaces-backend-plan.md`` step
5, §3.3). Pure: all three collaborators are fakes, so nothing here touches a
database — what the live file next door proves is that the lock is a real
lock; what THIS file proves is that the service asks for it, in the right
order, and refuses at the right number.

Every fake appends to ONE shared trace list, because most of this service's
correctness is ordering, not arithmetic: a sum read before the lock is taken,
or an insert issued after the unit of work closed, is a quota that passes
every value-based assertion and still lets two concurrent uploads through.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.di.space_quota import SpaceQuotaService
from app.framework.errors import ConflictError, NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits

_GIB = 1_073_741_824


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


class _RecordingUnitOfWork:
    """A no-op transaction boundary that says WHEN it opened and closed.
    ``test_unit_of_work.py`` covers the real ``TenantSessionFactory.begin``."""

    def __init__(self, trace: list[str]) -> None:
        self._trace = trace

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        self._trace.append("uow:enter")
        try:
            yield
        finally:
            self._trace.append("uow:exit")


class _FakeSpaces:
    def __init__(self, trace: list[str], *, lockable: bool = True) -> None:
        self._trace = trace
        self._lockable = lockable

    async def lock(self, ctx: ExecutionContext, space_id: str) -> bool:
        self._trace.append("lock")
        return self._lockable


class _FakeFiles:
    def __init__(self, trace: list[str], *, used: int = 0) -> None:
        self._trace = trace
        self._used = used

    async def bytes_in_space(self, ctx: ExecutionContext, space_id: str) -> int:
        self._trace.append("sum")
        return self._used


class _FakeRegistrar:
    """Stands in for ``FileTransferService.register`` — the validate + mint +
    presign face. Returns a sentinel string so the test can prove the caller
    gets the registrar's OWN result back, untouched."""

    def __init__(self, trace: list[str]) -> None:
        self._trace = trace
        self.calls: list[tuple[str, str, int]] = []

    async def register(
        self, ctx: ExecutionContext, *, name: str, content_type: str, size_bytes: int
    ) -> str:
        self._trace.append("register")
        self.calls.append((name, content_type, size_bytes))
        return f"registered:{name}"


def _build(
    *, used: int = 0, lockable: bool = True, limits: Limits | None = None
) -> tuple[SpaceQuotaService[str], _FakeRegistrar, list[str]]:
    trace: list[str] = []
    registrar = _FakeRegistrar(trace)
    service = SpaceQuotaService(
        registrar,
        _FakeSpaces(trace, lockable=lockable),
        _FakeFiles(trace, used=used),
        _RecordingUnitOfWork(trace),
        limits or Limits(),
    )
    return service, registrar, trace


# --------------------------------------------------------------------------- #
# (0) the number itself                                                       #
# --------------------------------------------------------------------------- #
def test_the_default_quota_is_the_one_gibibyte_the_plan_decided() -> None:
    """Plan decision 4 is "1 GiB per space, by SIZE not by count", and it is a
    decision — not a value to be nudged. Every other test here passes an
    explicit ceiling, so without this line the shipped default is the one
    number in this step that nothing checks."""
    assert Limits().max_space_bytes == _GIB


# --------------------------------------------------------------------------- #
# (1) the happy path, and the ORDER that makes it safe                        #
# --------------------------------------------------------------------------- #
async def test_an_upload_that_fits_is_registered_under_the_lock_in_one_unit_of_work() -> None:
    """The order is the mechanism (plan risk 2). Reading the total before
    taking the lock, or registering after the unit of work closed, both leave
    two concurrent 600 MiB uploads reading the same total and both passing."""
    service, registrar, trace = _build(used=100)

    result = await service.register(
        _ctx(),
        space_id=new_uuid7(),
        name="report.pdf",
        content_type="application/pdf",
        size_bytes=2048,
    )

    assert result == "registered:report.pdf"
    assert trace == ["uow:enter", "lock", "sum", "register", "uow:exit"]
    assert registrar.calls == [("report.pdf", "application/pdf", 2048)]


# --------------------------------------------------------------------------- #
# (2) the ceiling                                                             #
# --------------------------------------------------------------------------- #
async def test_an_upload_that_would_cross_the_ceiling_is_refused_before_it_is_registered() -> None:
    service, registrar, trace = _build(used=_GIB - 1024)

    with pytest.raises(ConflictError) as exc_info:
        await service.register(
            _ctx(),
            space_id=new_uuid7(),
            name="big.pdf",
            content_type="application/pdf",
            size_bytes=2048,
        )

    assert exc_info.value.code == "spaces.quota_exceeded"
    assert exc_info.value.status == 409
    # Refused, not merely reported: nothing was minted, nothing was presigned,
    # and the unit of work still closed (its rollback is what makes the lock
    # a lock rather than a leak).
    assert registrar.calls == []
    assert trace == ["uow:enter", "lock", "sum", "uow:exit"]


async def test_filling_the_space_exactly_to_the_ceiling_is_allowed() -> None:
    """``>`` and not ``>=``: a 1 GiB quota that refuses the byte that makes the
    space exactly 1 GiB is a 1 GiB-minus-one quota, and the off-by-one would
    only ever be found by the one user who hit it."""
    service, registrar, _ = _build(used=_GIB - 2048)

    await service.register(
        _ctx(),
        space_id=new_uuid7(),
        name="last.pdf",
        content_type="application/pdf",
        size_bytes=2048,
    )

    assert registrar.calls == [("last.pdf", "application/pdf", 2048)]


async def test_one_byte_past_the_ceiling_is_refused() -> None:
    service, registrar, _ = _build(used=_GIB - 2047)

    with pytest.raises(ConflictError):
        await service.register(
            _ctx(),
            space_id=new_uuid7(),
            name="last.pdf",
            content_type="application/pdf",
            size_bytes=2048,
        )

    assert registrar.calls == []


async def test_the_ceiling_is_the_configured_one_not_a_hardcoded_gibibyte() -> None:
    service, registrar, _ = _build(used=4096, limits=Limits(max_space_bytes=4096))

    with pytest.raises(ConflictError) as exc_info:
        await service.register(
            _ctx(), space_id=new_uuid7(), name="x.pdf", content_type="application/pdf", size_bytes=1
        )

    # The message carries the numbers a client needs to act, not just a code.
    assert "4096" in str(exc_info.value)
    assert registrar.calls == []


# --------------------------------------------------------------------------- #
# (3) the space itself                                                        #
# --------------------------------------------------------------------------- #
async def test_a_space_that_cannot_be_locked_is_a_404_and_nothing_else_happens() -> None:
    """Missing, another tenant's, or soft-deleted — one answer for all three,
    and the total is never even read: there is no space whose room to measure."""
    service, registrar, trace = _build(lockable=False)

    with pytest.raises(NotFoundError):
        await service.register(
            _ctx(),
            space_id=new_uuid7(),
            name="report.pdf",
            content_type="application/pdf",
            size_bytes=1,
        )

    assert registrar.calls == []
    assert trace == ["uow:enter", "lock", "uow:exit"]


# --------------------------------------------------------------------------- #
# (4) the deferral to the per-file cap                                        #
# --------------------------------------------------------------------------- #
async def test_a_file_larger_than_the_per_file_cap_is_left_to_the_registrar() -> None:
    """A 2 GiB upload into an EMPTY 1 GiB space must not be answered "the space
    is full" — that is true and useless. ``RegisterUpload`` owns
    ``files.too_large`` (413) and answers with the number the client can act
    on, so this service steps aside and lets the call through to it."""
    service, registrar, trace = _build(
        used=0, limits=Limits(max_upload_bytes=50, max_space_bytes=100)
    )

    await service.register(
        _ctx(),
        space_id=new_uuid7(),
        name="huge.pdf",
        content_type="application/pdf",
        size_bytes=5000,
    )

    assert registrar.calls == [("huge.pdf", "application/pdf", 5000)]
    assert trace == ["uow:enter", "lock", "sum", "register", "uow:exit"]


async def test_a_file_at_the_per_file_cap_is_still_judged_against_the_space() -> None:
    """The deferral above is bounded by ``<=``: a file the per-file cap ACCEPTS
    gets no free pass, or the quota would be unenforceable for anyone
    uploading at exactly the maximum size."""
    service, registrar, _ = _build(used=60, limits=Limits(max_upload_bytes=50, max_space_bytes=100))

    with pytest.raises(ConflictError) as exc_info:
        await service.register(
            _ctx(),
            space_id=new_uuid7(),
            name="x.pdf",
            content_type="application/pdf",
            size_bytes=50,
        )

    assert exc_info.value.code == "spaces.quota_exceeded"
    assert registrar.calls == []
