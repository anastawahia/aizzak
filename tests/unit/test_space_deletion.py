"""Unit tests for the space cascade (``docs/spaces-backend-plan.md`` step 11,
§3.6) — the coordination service and the three module purges it drives.

Pure: every store is in-memory, so nothing here touches Postgres, Qdrant or
MinIO. What the live file next door proves is that the SQL reaches the right
tables in an order the foreign keys accept; what THIS file proves is that the
cascade asks for all seven steps, in §3.6's order, and that each module empties
what it owns and nothing else.

Ordering carries most of the correctness, so the assertions are traces rather
than end-states: a purge that deleted a file's ROW before its OBJECT leaves
exactly the same empty space behind, and loses the bytes forever.
"""

from __future__ import annotations

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.space_deletion import DeleteSpaceService, SpaceDeletion
from app.framework.errors import NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.application.use_cases import PurgeSpaceConversations
from app.modules.conversations.ports.repository import ConversationRepository
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import PurgeSpaceFiles
from app.modules.files.ports.repository import FileRepository
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application.use_cases import PurgeSpaceKnowledge
from app.modules.knowledge.domain.value_objects import VectorRef
from app.modules.knowledge.ports.repository import DocumentRepository
from app.modules.spaces.application.use_cases import DeleteSpace
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName
from tests.unit.support_conversations import build_conversations
from tests.unit.support_files_media import build_files_media
from tests.unit.support_knowledge import build_knowledge, seed_document


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


class _FakeSpaces:
    """In-memory ``SpaceRepository`` — ``get`` returns soft-deleted rows, which
    is what makes ``DeleteSpace`` idempotent and the cascade re-runnable."""

    def __init__(self) -> None:
        self.rows: dict[str, Space] = {}

    async def get(self, ctx: ExecutionContext, space_id: str) -> Space | None:
        row = self.rows.get(space_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, space: Space) -> None:
        self.rows[space.id] = space

    async def save(self, ctx: ExecutionContext, space: Space) -> None:
        self.rows[space.id] = space

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[Space]:
        rows = [row for row in self.rows.values() if row.deleted_at is None]
        return Page(data=rows[:limit], next_cursor=None, limit=limit)


def _seed_space(spaces: _FakeSpaces, ctx: ExecutionContext, name: str = "Research") -> Space:
    now = utc_now()
    space = Space(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        name=SpaceName(name),
        created_by=ctx.user_id,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        version=1,
    )
    spaces.rows[space.id] = space
    return space


class _CountingPurge:
    """A ``SpaceContentPurge`` that says WHEN it ran and returns a number of
    its own — distinct per instance, so a service that reported the counts
    under the wrong names could not pass."""

    def __init__(self, trace: list[str], name: str, count: int) -> None:
        self._trace = trace
        self._name = name
        self._count = count
        self.calls: list[str] = []

    async def execute(self, ctx: ExecutionContext, space_id: str) -> int:
        self._trace.append(self._name)
        self.calls.append(space_id)
        return self._count


def _build_service() -> tuple[
    DeleteSpaceService, _FakeSpaces, dict[str, _CountingPurge], list[str]
]:
    trace: list[str] = []
    spaces = _FakeSpaces()

    class _TracingMarker:
        def __init__(self, inner: DeleteSpace) -> None:
            self._inner = inner

        async def execute(self, ctx: ExecutionContext, space_id: str) -> object:
            trace.append("mark")
            return await self._inner.execute(ctx, space_id)

    purges = {
        "knowledge": _CountingPurge(trace, "knowledge", 7),
        "files": _CountingPurge(trace, "files", 3),
        "conversations": _CountingPurge(trace, "conversations", 5),
    }
    service = DeleteSpaceService(
        _TracingMarker(DeleteSpace(spaces)),
        knowledge=purges["knowledge"],
        files=purges["files"],
        conversations=purges["conversations"],
    )
    return service, spaces, purges, trace


# --------------------------------------------------------------------------- #
# (1) the seven steps, in §3.6's order                                        #
# --------------------------------------------------------------------------- #
async def test_the_cascade_marks_the_space_first_then_empties_every_module() -> None:
    """The order is the design (§3.6). Marking last would leave a space listed
    as live while its contents were being destroyed under it, and any two
    purges swapped would change which half survives a failure."""
    service, spaces, purges, trace = _build_service()
    ctx = _ctx()
    space = _seed_space(spaces, ctx)

    result = await service.delete(ctx, space.id)

    assert trace == ["mark", "knowledge", "files", "conversations"]
    assert spaces.rows[space.id].deleted_at is not None
    assert all(purge.calls == [space.id] for purge in purges.values())
    # Every count under its OWN name: the three purges share one protocol, so
    # a keyword swapped at the wiring site is invisible to mypy and this is
    # what catches it.
    assert result == SpaceDeletion(space_id=space.id, documents=7, files=3, conversations=5)


async def test_a_space_this_workspace_does_not_own_is_a_404_and_nothing_is_destroyed() -> None:
    """The marking step is the cascade's only existence check: the six that
    follow cannot tell an empty space from an absent one, and would report a
    successful deletion of nothing."""
    service, _spaces, purges, trace = _build_service()

    with pytest.raises(NotFoundError):
        await service.delete(_ctx(), new_uuid7())

    assert trace == ["mark"]
    assert all(purge.calls == [] for purge in purges.values())


async def test_deleting_an_already_deleted_space_runs_the_whole_cascade_again() -> None:
    """The resume path (§3.6): an interrupted cascade leaves a marked space
    with contents still in it, and the only way back is to run it again. A
    service that short-circuited on ``deleted_at`` would strand exactly the
    rows the first run failed to reach."""
    service, spaces, purges, trace = _build_service()
    ctx = _ctx()
    space = _seed_space(spaces, ctx)
    await service.delete(ctx, space.id)
    trace.clear()

    await service.delete(ctx, space.id)

    assert trace == ["mark", "knowledge", "files", "conversations"]
    assert all(purge.calls == [space.id, space.id] for purge in purges.values())


# --------------------------------------------------------------------------- #
# (2) knowledge — steps 2, 3 and 4                                            #
# --------------------------------------------------------------------------- #
async def test_the_corpus_points_are_deleted_before_its_rows() -> None:
    """A point outlives its row invisibly: retrieval filters on the payload
    and never joins Postgres, so a chunk row deleted first leaves content
    answering searches with nothing left to identify it. Deleting the points
    first makes the failure recoverable instead."""
    stack = build_knowledge()
    ctx = _ctx()
    space = new_uuid7()
    document = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, space_id=space)
    stack.repository.rows[document.id] = document
    stack.repository.refs[document.id] = [VectorRef("kn-w1", "point-1"), VectorRef("kn-w1", "p2")]

    purged = await PurgeSpaceKnowledge(stack.repository, stack.vectors).execute(ctx, space)

    assert purged == 1
    assert stack.vectors.deleted == [("kn-w1", ["point-1", "p2"])]
    # The rows went too — and they went AFTER, which is why the refs above
    # were still readable when the vector call was made.
    assert stack.repository.rows == {}


async def test_only_this_spaces_corpus_is_destroyed() -> None:
    stack = build_knowledge()
    ctx = _ctx()
    doomed, kept = new_uuid7(), new_uuid7()
    mine = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, space_id=doomed)
    theirs = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, space_id=kept)
    stack.repository.rows = {mine.id: mine, theirs.id: theirs}
    stack.repository.refs = {
        mine.id: [VectorRef("kn-w1", "p1")],
        theirs.id: [VectorRef("kn", "p2")],
    }

    purged = await PurgeSpaceKnowledge(stack.repository, stack.vectors).execute(ctx, doomed)

    assert purged == 1
    assert stack.vectors.deleted == [("kn-w1", ["p1"])]
    assert list(stack.repository.rows) == [theirs.id]


async def test_points_are_grouped_by_their_own_collection() -> None:
    """Every chunk of a workspace lives in one collection today, but each
    ``VectorRef`` names its own — and a purge that assumed otherwise would
    delete one collection's ids from another's, which Qdrant accepts silently."""
    stack = build_knowledge()
    ctx = _ctx()
    space = new_uuid7()
    first = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, space_id=space)
    second = seed_document(document_id=new_uuid7(), workspace_id=ctx.workspace_id, space_id=space)
    stack.repository.rows = {first.id: first, second.id: second}
    stack.repository.refs = {
        first.id: [VectorRef("kn-old", "p1")],
        second.id: [VectorRef("kn-new", "p2")],
    }

    await PurgeSpaceKnowledge(stack.repository, stack.vectors).execute(ctx, space)

    assert sorted(stack.vectors.deleted) == [("kn-new", ["p2"]), ("kn-old", ["p1"])]


async def test_a_space_with_nothing_indexed_asks_the_vector_store_for_nothing() -> None:
    """A ``delete`` with an empty id list is a round trip that can fail, in a
    cascade whose whole contract is that re-running it is cheap."""
    stack = build_knowledge()

    purged = await PurgeSpaceKnowledge(stack.repository, stack.vectors).execute(_ctx(), new_uuid7())

    assert purged == 0
    assert stack.vectors.deleted == []


# --------------------------------------------------------------------------- #
# (3) files — steps 5 and 6                                                   #
# --------------------------------------------------------------------------- #
async def _register(stack: object, ctx: ExecutionContext, space_id: str, name: str) -> str:
    files = stack.files  # type: ignore[attr-defined]
    registered = await files.transfers.register(
        ctx, space_id=space_id, name=name, content_type="application/pdf", size_bytes=11
    )
    return str(registered.file.id)


async def test_the_objects_are_deleted_before_the_rows_that_name_them() -> None:
    """A row still names its object, so objects-first is recoverable (the next
    run deletes keys that are already gone, a 204) while rows-first loses the
    keys forever: nothing else in the platform knows them."""
    stack = build_files_media()
    ctx = _ctx()
    space = new_uuid7()
    stack.spaces.active.add(space)
    file_id = await _register(stack, ctx, space, "paper.pdf")
    key = stack.file_repository.rows[file_id].storage_key.value

    purged = await PurgeSpaceFiles(stack.file_repository, stack.storage).execute(ctx, space)

    assert purged == 1
    assert stack.storage.deleted == [key]
    assert stack.file_repository.rows == {}


async def test_a_soft_deleted_file_still_has_its_object_destroyed() -> None:
    """``deleted_at`` gave the bytes back to the quota; it did not remove the
    object. Skipping it would leave storage behind with no row left to name
    it — reachable only by the whole-workspace purge, and only when the
    workspace itself dies."""
    stack = build_files_media()
    ctx = _ctx()
    space = new_uuid7()
    stack.spaces.active.add(space)
    file_id = await _register(stack, ctx, space, "paper.pdf")
    key = stack.file_repository.rows[file_id].storage_key.value
    await stack.files.delete.delete(ctx, file_id)

    purged = await PurgeSpaceFiles(stack.file_repository, stack.storage).execute(ctx, space)

    assert purged == 1
    assert stack.storage.deleted == [key]


async def test_only_this_spaces_files_are_destroyed() -> None:
    stack = build_files_media()
    ctx = _ctx()
    doomed, kept = new_uuid7(), new_uuid7()
    stack.spaces.active.update({doomed, kept})
    await _register(stack, ctx, doomed, "doomed.pdf")
    survivor = await _register(stack, ctx, kept, "kept.pdf")
    survivor_key = stack.file_repository.rows[survivor].storage_key.value

    purged = await PurgeSpaceFiles(stack.file_repository, stack.storage).execute(ctx, doomed)

    assert purged == 1
    assert list(stack.file_repository.rows) == [survivor]
    assert survivor_key not in stack.storage.deleted


async def test_an_empty_space_deletes_no_object_and_no_row() -> None:
    stack = build_files_media()

    purged = await PurgeSpaceFiles(stack.file_repository, stack.storage).execute(
        _ctx(), new_uuid7()
    )

    assert purged == 0
    assert stack.storage.deleted == []


# --------------------------------------------------------------------------- #
# (4) conversations — step 7                                                  #
# --------------------------------------------------------------------------- #
async def test_a_spaces_threads_go_with_their_messages_and_their_pins() -> None:
    stack = build_conversations()
    ctx = _ctx()
    doomed, kept = new_uuid7(), new_uuid7()
    stack.spaces.live.update({doomed, kept})
    thread, _ = await stack.use_cases.start.execute(ctx, space_id=doomed, agent_key="rag-agent")
    survivor, _ = await stack.use_cases.start.execute(ctx, space_id=kept, agent_key="rag-agent")
    await stack.service.append(ctx, thread.id, role="user", text="hello")
    pinned = new_uuid7()
    stack.files.ready.add(pinned)
    stack.files.spaces[pinned] = doomed
    await stack.use_cases.pin_file.execute(ctx, thread.id, pinned)
    assert stack.repository.messages[thread.id] and stack.repository.pins[thread.id]

    purged = await PurgeSpaceConversations(stack.repository).execute(ctx, doomed)

    assert purged == 1
    assert list(stack.repository.rows) == [survivor.id]
    # The children go with the parent: a message or a pin left behind names a
    # thread that no longer exists, in a space that no longer exists.
    assert thread.id not in stack.repository.messages
    assert thread.id not in stack.repository.pins


async def test_a_soft_deleted_thread_is_destroyed_too() -> None:
    """A tombstone under a destroyed space is a row nothing can ever reach:
    every listing filters it out, and the space it belonged to is gone."""
    stack = build_conversations()
    ctx = _ctx()
    space = new_uuid7()
    stack.spaces.live.add(space)
    thread, _ = await stack.use_cases.start.execute(ctx, space_id=space, agent_key="rag-agent")
    await stack.use_cases.soft_delete.execute(ctx, thread.id)

    assert await PurgeSpaceConversations(stack.repository).execute(ctx, space) == 1
    assert stack.repository.rows == {}


# --------------------------------------------------------------------------- #
# (5) structural fit — the adapters against the ports they are bound to       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("port", "adapter", "added"),
    [
        (FileRepository, SqlFileRepository, {"storage_keys_in_space", "purge_space"}),
        (DocumentRepository, SqlDocumentRepository, {"vector_refs_in_space", "purge_space"}),
        (ConversationRepository, SqlConversationRepository, {"purge_space"}),
    ],
)
def test_each_adapter_implements_the_cascade_methods_its_port_declares(
    port: type, adapter: type, added: set[str]
) -> None:
    """The ports are structural Protocols matched at the Composition Root, so
    a method declared on one and missing from its adapter is caught at the
    wiring site — but only for the wiring that exists. This pins the step-11
    additions on both sides at once."""
    declared = {
        name for name, value in vars(port).items() if not name.startswith("_") and callable(value)
    }
    assert added <= declared
    assert declared <= {name for name in dir(adapter) if not name.startswith("_")}
