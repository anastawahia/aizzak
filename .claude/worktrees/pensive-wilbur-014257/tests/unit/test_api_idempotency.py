"""Unit tests for the ``Idempotency-Key`` seam (``api/v1/idempotency.py``,
3.79).

The three routers' end-to-end behaviour is pinned over the wire in
``test_api_files_media_router.py``/``test_api_workflows_router.py``. What is
tested HERE is the part no route exercises fully: the request fingerprint's
stability rules, and the branches of ``idempotent`` that only show up under a
store returning something other than "first attempt".
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.api.v1.idempotency import idempotent, request_hash
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ValidationError
from tests.unit.support_idempotency import InMemoryIdempotencyStore

_W1 = "018f0000-0000-7000-8000-0000000000w1"
_W2 = "018f0000-0000-7000-8000-0000000000w2"


class _In(BaseModel):
    name: str
    size: int = 0


class _Out(BaseModel):
    id: str
    note: str = ""


def _ctx(workspace_id: str = _W1) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=None,
        correlation_id="018f0000-0000-7000-8000-0000000000c1",
        roles=frozenset(),
    )


# --------------------------------------------------------------------------- #
# request_hash                                                                #
# --------------------------------------------------------------------------- #
def test_the_same_body_hashes_the_same_and_a_different_one_does_not() -> None:
    assert request_hash(_In(name="a", size=1)) == request_hash(_In(name="a", size=1))
    assert request_hash(_In(name="a", size=1)) != request_hash(_In(name="a", size=2))


def test_the_hash_does_not_expose_the_body() -> None:
    """The ledger stores a digest, never the request: a prompt or a filename
    is tenant content, and keeping a second copy of it just to answer "same
    request?" is storage the store has no business holding."""
    digest = request_hash(_In(name="a-very-secret-filename.pdf"))

    assert "secret" not in digest
    assert len(digest) == 64  # sha256 hex


# --------------------------------------------------------------------------- #
# idempotent                                                                  #
# --------------------------------------------------------------------------- #
async def test_no_key_runs_the_operation_and_never_touches_the_store() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    async def run() -> _Out:
        nonlocal calls
        calls += 1
        return _Out(id="x")

    for _ in range(2):
        await idempotent(
            store,
            _ctx(),
            endpoint="POST /files",
            key=None,
            body=_In(name="a"),
            model=_Out,
            run=run,
        )

    assert calls == 2
    assert store.claims == []


async def test_a_replay_rebuilds_the_stored_body_through_the_response_model() -> None:
    """Validating the stored JSON back through the model is what stops a
    schema change from replaying a body the current contract no longer
    describes — a silent lie, since the client would see a well-formed 201."""
    store = InMemoryIdempotencyStore()
    calls = 0

    async def run() -> _Out:
        nonlocal calls
        calls += 1
        return _Out(id="file-1", note="minted once")

    first = await idempotent(
        store,
        _ctx(),
        endpoint="POST /files",
        key="k",
        body=_In(name="a"),
        model=_Out,
        run=run,
    )
    second = await idempotent(
        store,
        _ctx(),
        endpoint="POST /files",
        key="k",
        body=_In(name="a"),
        model=_Out,
        run=run,
    )

    assert calls == 1
    assert second == first
    assert isinstance(second, _Out)


async def test_two_workspaces_may_use_the_same_key() -> None:
    """The scope is (workspace, endpoint, key). Two tenants whose client
    libraries both default to a request counter must not collide — and one
    must never read the other's stored response back."""
    store = InMemoryIdempotencyStore()

    async def run_for(tenant: str) -> _Out:
        return _Out(id=f"file-{tenant}")

    one = await idempotent(
        store,
        _ctx(_W1),
        endpoint="POST /files",
        key="1",
        body=_In(name="a"),
        model=_Out,
        run=lambda: run_for("w1"),
    )
    two = await idempotent(
        store,
        _ctx(_W2),
        endpoint="POST /files",
        key="1",
        body=_In(name="a"),
        model=_Out,
        run=lambda: run_for("w2"),
    )

    assert one.id == "file-w1"
    assert two.id == "file-w2"


async def test_an_in_flight_duplicate_conflicts_rather_than_running_twice() -> None:
    """A concurrent duplicate has no first response to replay. Running it
    anyway would defeat the whole point; blocking would pin a connection on a
    sibling request that may take minutes."""
    store = InMemoryIdempotencyStore()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow() -> _Out:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _Out(id="x")

    async def quick() -> _Out:  # pragma: no cover - must never run
        raise AssertionError("the duplicate must not run the operation")

    first = asyncio.create_task(
        idempotent(
            store,
            _ctx(),
            endpoint="POST /files",
            key="k",
            body=_In(name="a"),
            model=_Out,
            run=slow,
        )
    )
    await started.wait()

    with pytest.raises(ConflictError):
        await idempotent(
            store,
            _ctx(),
            endpoint="POST /files",
            key="k",
            body=_In(name="a"),
            model=_Out,
            run=quick,
        )

    release.set()
    await first
    assert calls == 1


async def test_a_cancelled_operation_releases_its_claim() -> None:
    """``except BaseException``, not ``except Exception``: a client that hung
    up leaves the claim exactly as unusable as a raised one, and leaking it
    would mean the client's own retry meets its own abandoned attempt."""
    store = InMemoryIdempotencyStore()

    async def cancelled() -> _Out:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await idempotent(
            store,
            _ctx(),
            endpoint="POST /files",
            key="k",
            body=_In(name="a"),
            model=_Out,
            run=cancelled,
        )

    assert store.rows == {}


async def test_a_completed_key_survives_a_late_release() -> None:
    """``release`` is scoped to UNFINISHED claims. A late or duplicated one
    must not delete a row that already recorded a real response — that would
    silently un-protect a key whose operation actually succeeded."""
    store = InMemoryIdempotencyStore()
    ctx = _ctx()

    await idempotent(
        store,
        ctx,
        endpoint="POST /files",
        key="k",
        body=_In(name="a"),
        model=_Out,
        run=_done,
    )
    await store.release(ctx, endpoint="POST /files", key="k")

    claim = await store.claim(
        ctx, endpoint="POST /files", key="k", request_hash=request_hash(_In(name="a"))
    )
    assert claim.response_body == {"id": "file-1", "note": ""}


async def _done() -> _Out:
    return _Out(id="file-1")


@pytest.mark.parametrize("key", ["", "   ", "x" * 256])
async def test_an_unusable_key_is_a_422_before_any_store_call(key: str) -> None:
    store = InMemoryIdempotencyStore()

    async def run() -> _Out:  # pragma: no cover - must never run
        raise AssertionError("the operation must not run on a malformed key")

    with pytest.raises(ValidationError):
        await idempotent(
            store,
            _ctx(),
            endpoint="POST /files",
            key=key,
            body=_In(name="a"),
            model=_Out,
            run=run,
        )

    assert store.claims == []
