"""Live proof of capacity 5.6's acceptance (``app.ops.replay``): a batch is
published, Redis loses it, and the replay restores every effect -- exactly
once.

Real Postgres (``aizzak_test``) and real Redis, the real relay under the real
``outbox_relay`` role, and the tool itself running under that SAME role -- so
the one read 5.6 added to it (``SELECT`` on ``platform.processed_events``) is
exercised, not assumed. Every stream and group is unique per test
(``stream.test.<uuid>``/``cg.test.<uuid>``, R6), and the tool is handed a
topology naming them instead of the production one.

**"Redis emptied" is ``DEL <stream>``.** The entries and the consumer group go
with the key, which is exactly what a flush does to each stream; a flush of
the whole instance is not something a test on a shared Redis may do, and it
would add nothing a single stream does not already show.

**The handler is the real ``build_knowledge_summary_failure_handler``**, and
it is chosen because of the plan's own warning: its effect is one message in
a thread, and the ledger is its ONLY guard against appending it twice. No
aggregate state stops a second delivery -- so if replay were unsafe, this is
where it would show.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from datetime import timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.events.topology import ConsumerBinding
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.messaging.consumers.engine import StreamConsumer, Subscription
from app.infrastructure.messaging.outbox import OutboxRelay
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox, SqlOutboxRelayStore
from app.infrastructure.persistence.processed_events import SqlProcessedEventLedger
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.application.use_cases import AppendMessage, StartConversation
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import FilesQueryService
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application.event_mapping import to_outbox_record
from app.modules.knowledge.application.use_cases import GetDocumentFileName
from app.modules.knowledge.domain.events import DocumentIndexed, SummaryBuildFailed
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.ops.replay import Selection, Verdict, build_plan, publish_plan, run_replay
from app.workers.bootstrap import build_knowledge_summary_failure_handler
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_redis]

_FAILED = "knowledge.summary.build_failed.v1"
_BATCH = 6
_BEFORE_THE_LOSS = 3


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


def _failed_record(ctx: ExecutionContext, *, stream: str, conversation_id: str) -> OutboxRecord:
    """A ``build_failed`` event for the thread, through the module's REAL
    mapping -- only ``stream`` is retargeted (R6)."""
    event = SummaryBuildFailed(
        job_id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        document_id=new_uuid7(),
        reason="the build could not finish",
        conversation_id=conversation_id,
        occurred_at=utc_now(),
    )
    return replace(to_outbox_record(ctx, event), stream=stream)


def _indexed_record(ctx: ExecutionContext, *, stream: str) -> OutboxRecord:
    """A notification-only type on the same stream: no durable group claims
    it, so the tool must call it ``no_reader`` and leave it alone."""
    event = DocumentIndexed(
        document_id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        file_id=new_uuid7(),
        chunk_count=3,
        collection="kb",
        occurred_at=utc_now(),
    )
    return replace(to_outbox_record(ctx, event), stream=stream)


async def _message_count(
    conversations: SqlConversationRepository, ctx: ExecutionContext, conversation_id: str
) -> int:
    page = await conversations.list_messages(ctx, conversation_id, limit=100, cursor=None)
    return len(page.data)


async def _claims(owner_dsn: str, *, group: str) -> int:
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM platform.processed_events WHERE consumer_group = :g"),
                {"g": group},
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


class _Harness:
    """One thread, one unique stream/group, the real handler behind a real
    engine, and the relay -- the pieces both tests below drive."""

    def __init__(
        self,
        tenant_session: TenantSessionFactory,
        relay_sessionmaker: async_sessionmaker[AsyncSession],
        redis_client: Redis,
    ) -> None:
        self.stream = f"stream.test.{new_uuid7()}"
        self.group = f"cg.test.{new_uuid7()}"
        self.ctx = _ctx(new_uuid7())
        self.redis = redis_client
        self.conversations = SqlConversationRepository(tenant_session)
        self.outbox = SqlEventOutbox(tenant_session)
        self.binding = ConsumerBinding(
            stream=self.stream, group=self.group, event_types=frozenset({_FAILED})
        )
        self.selection = Selection(
            since=timedelta(minutes=15),
            workspace_id=self.ctx.workspace_id,
            streams=(self.stream,),
        )
        handler = build_knowledge_summary_failure_handler(
            AppendMessage(self.conversations),
            tenant_session,
            SqlProcessedEventLedger(tenant_session),
            GetDocumentFileName(
                SqlDocumentRepository(tenant_session),
                FilesQueryService(SqlFileRepository(tenant_session)),
            ),
            consumer_group=self.group,
        )
        self.subscription = Subscription(
            stream=self.stream, group=self.group, handlers={_FAILED: handler}
        )
        self.relay = OutboxRelay(
            SqlOutboxRelayStore(relay_sessionmaker),
            RedisStreamsPublisher(redis_client),
            batch_size=50,
            poll_interval_ms=100,
            max_backoff_ms=1000,
        )
        self._spaces = SpacesQueryService(SqlSpaceRepository(tenant_session))

    def consumer(self, *, batch: int) -> StreamConsumer:
        return StreamConsumer(
            RedisStreamsConsumer(self.redis),
            consumer_name="replay-live-test",
            block_ms=200,
            batch_count=batch,
            max_deliveries=5,
        )

    async def publish_all(self) -> None:
        """Drain the relay's queue. Not an exact count: the relay publishes
        every unpublished row in the test database, not only this test's --
        the plan's totals below are what prove this test's rows went out."""
        while await self.relay.run_once():
            pass

    async def open_thread(self) -> str:
        conversation, _ = await StartConversation(self.conversations, self._spaces).execute(
            self.ctx, space_id=None, agent_key="rag-agent"
        )
        return conversation.id

    async def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            await self.redis.xgroup_destroy(self.stream, self.group)
        await self.redis.delete(self.stream)
        await self.redis.delete(f"{self.stream}.dlq")


@pytest.mark.anyio
async def test_a_batch_lost_with_its_stream_is_restored_exactly_once(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_engine: AsyncEngine,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    h = _Harness(tenant_session, relay_sessionmaker, redis_client)
    try:
        thread = await h.open_thread()
        await h.consumer(batch=_BEFORE_THE_LOSS).setup([h.subscription])

        # 1) A batch, published by the real relay: six deliveries owed to the
        #    thread, plus one notification-only event on the same stream.
        records = [
            _failed_record(h.ctx, stream=h.stream, conversation_id=thread) for _ in range(_BATCH)
        ]
        await h.outbox.append(h.ctx, [*records, _indexed_record(h.ctx, stream=h.stream)])
        await h.publish_all()
        assert await redis_client.xlen(h.stream) == _BATCH + 1

        # 2) The worker gets through half of it...
        assert await h.consumer(batch=_BEFORE_THE_LOSS).run_once([h.subscription]) == 3
        assert await _message_count(h.conversations, h.ctx, thread) == 3

        # 3) ...and Redis loses the stream, entries and group together. The
        #    relay has nothing left to send: every row says published.
        await redis_client.delete(h.stream)
        assert not await redis_client.exists(h.stream)
        assert await h.relay.run_once() == 0

        # 4) The plan sees what happened, without touching anything. Kept:
        #    step 7 publishes it again, stale, to play the race a live system
        #    always has.
        stale = await build_plan(relay_engine, redis_client, h.selection, bindings=(h.binding,))
        totals = stale.totals()
        assert totals[Verdict.PROCESSED] == 3
        assert totals[Verdict.REPLAY] == 3
        assert totals[Verdict.NO_READER] == 1
        assert totals[Verdict.IN_STREAM] == totals[Verdict.BEYOND_LEDGER] == 0
        assert stale.missing_groups == ((h.stream, h.group),)
        assert not await redis_client.exists(h.stream), "plan must not create anything"

        # 5) Run it -- and run it again before any worker reads, as an
        #    operator repeating the command would. The group comes back
        #    BEFORE the entries ("groups first"); the second run finds them
        #    on the stream, owed, and sends nothing.
        _, outcome = await run_replay(
            relay_engine, redis_client, h.selection, bindings=(h.binding,), backstop=100_000
        )
        assert outcome.published == {h.stream: 3}
        assert outcome.failure is None
        again, outcome = await run_replay(
            relay_engine, redis_client, h.selection, bindings=(h.binding,), backstop=100_000
        )
        assert again.totals()[Verdict.IN_STREAM] == 3
        assert outcome.total == 0
        assert await redis_client.xlen(h.stream) == 3

        # 6) The worker drains them: the thread now holds all six messages.
        consumer = h.consumer(batch=10)
        assert await consumer.run_once([h.subscription]) == 3
        assert await _message_count(h.conversations, h.ctx, thread) == _BATCH
        assert await _claims(live_db.owner, group=h.group) == _BATCH

        # 7) The race: a snapshot taken before the worker ran, published after
        #    it. Three duplicates reach the real handler, and its ledger is the
        #    only thing between them and three more messages in the thread.
        raced = await publish_plan(relay_engine, RedisStreamsPublisher(redis_client), stale)
        assert raced.published == {h.stream: 3}
        assert await consumer.run_once([h.subscription]) == 3
        assert await _message_count(h.conversations, h.ctx, thread) == _BATCH
        assert await _claims(live_db.owner, group=h.group) == _BATCH
        pending = await redis_client.xpending(h.stream, h.group)
        assert pending["pending"] == 0

        # 8) Nothing is owed any more, so a last run publishes nothing.
        plan, outcome = await run_replay(
            relay_engine, redis_client, h.selection, bindings=(h.binding,), backstop=100_000
        )
        assert plan.totals()[Verdict.PROCESSED] == _BATCH
        assert outcome.total == 0
        assert await _message_count(h.conversations, h.ctx, thread) == _BATCH
    finally:
        await h.cleanup()


@pytest.mark.anyio
async def test_a_partial_loss_replays_only_what_the_stream_no_longer_holds(
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
    relay_engine: AsyncEngine,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    """The shape an AOF restart leaves: the stream and its group survive, a
    few entries do not. Five deliveries queued behind an idle worker; one is
    dead-lettered, two vanish (``XDEL``), two are simply waiting. Only the
    two that vanished are sent -- the waiting pair would otherwise arrive
    twice, and the quarantined one belongs to ``app.ops.dlq``."""
    h = _Harness(tenant_session, relay_sessionmaker, redis_client)
    streams = RedisStreamsConsumer(redis_client)
    try:
        thread = await h.open_thread()
        await h.consumer(batch=10).setup([h.subscription])
        await h.outbox.append(
            h.ctx,
            [_failed_record(h.ctx, stream=h.stream, conversation_id=thread) for _ in range(5)],
        )
        await h.publish_all()
        entries = await redis_client.xrange(h.stream)
        assert len(entries) == 5

        # The first is handed out and given up on: the engine's own transfer.
        [poisoned] = await streams.read(
            streams=[h.stream], group=h.group, consumer="gave-up", count=1, block_ms=100
        )
        await streams.dead_letter(
            stream=h.stream,
            group=h.group,
            entry_id=poisoned.entry_id,
            raw=poisoned.raw,
            reason="handler_failed: RuntimeError: boom",
            delivery_count=5,
        )
        # Two of the four still waiting are lost.
        await redis_client.xdel(h.stream, entries[1][0], entries[3][0])

        plan = await build_plan(relay_engine, redis_client, h.selection, bindings=(h.binding,))
        totals = plan.totals()
        assert totals[Verdict.DEAD_LETTERED] == 1
        assert totals[Verdict.IN_STREAM] == 2
        assert totals[Verdict.REPLAY] == 2
        assert plan.missing_groups == ()

        _, outcome = await run_replay(
            relay_engine, redis_client, h.selection, bindings=(h.binding,), backstop=100_000
        )
        assert outcome.published == {h.stream: 2}

        # The two that waited and the two replayed: four messages, four claims.
        assert await h.consumer(batch=10).run_once([h.subscription]) == 4
        assert await _message_count(h.conversations, h.ctx, thread) == 4
        assert await _claims(live_db.owner, group=h.group) == 4
    finally:
        await h.cleanup()


@pytest.mark.anyio
async def test_what_the_relay_published_before_the_group_came_back_is_replayed(
    tenant_session: TenantSessionFactory,
    relay_engine: AsyncEngine,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    """After a flush the relay keeps publishing -- ``XADD`` recreates the
    stream, without its group -- and the worker that restarts on ``NOGROUP``
    recreates its group at ``$``, past all of it. Those entries ARE on the
    stream and no one will ever be handed them: present is not owed."""
    h = _Harness(tenant_session, relay_sessionmaker, redis_client)
    try:
        thread = await h.open_thread()
        await h.consumer(batch=10).setup([h.subscription])
        await redis_client.delete(h.stream)  # the flush

        await h.outbox.append(
            h.ctx,
            [_failed_record(h.ctx, stream=h.stream, conversation_id=thread) for _ in range(2)],
        )
        await h.publish_all()
        assert await redis_client.xlen(h.stream) == 2

        # The worker comes back and recreates its group -- at the tail.
        await h.consumer(batch=10).setup([h.subscription])
        assert await h.consumer(batch=10).run_once([h.subscription]) == 0

        plan = await build_plan(relay_engine, redis_client, h.selection, bindings=(h.binding,))
        assert plan.totals()[Verdict.IN_STREAM] == 0
        assert plan.totals()[Verdict.REPLAY] == 2

        _, outcome = await run_replay(
            relay_engine, redis_client, h.selection, bindings=(h.binding,), backstop=100_000
        )
        assert outcome.published == {h.stream: 2}
        assert await h.consumer(batch=10).run_once([h.subscription]) == 2
        assert await _message_count(h.conversations, h.ctx, thread) == 2
    finally:
        await h.cleanup()


@pytest.mark.anyio
async def test_rows_older_than_the_ledger_window_are_never_replayed(
    tenant_session: TenantSessionFactory,
    relay_engine: AsyncEngine,
    relay_sessionmaker: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    """The horizon on the database's own clock: with a window shorter than
    the rows' age, an unclaimed row may have lost its claim to a sweep, so
    the tool counts it and publishes nothing -- and the backstop check, run
    against a real ``XLEN``, refuses a replay that would trim itself."""
    h = _Harness(tenant_session, relay_sessionmaker, redis_client)
    try:
        thread = await h.open_thread()
        await h.consumer(batch=10).setup([h.subscription])
        await h.outbox.append(
            h.ctx,
            [_failed_record(h.ctx, stream=h.stream, conversation_id=thread) for _ in range(2)],
        )
        await h.publish_all()
        assert await redis_client.xlen(h.stream) == 2
        await redis_client.delete(h.stream)

        plan, outcome = await run_replay(
            relay_engine,
            redis_client,
            h.selection,
            bindings=(h.binding,),
            retention=timedelta(0),
            backstop=100_000,
        )
        assert plan.totals()[Verdict.BEYOND_LEDGER] == 2
        assert outcome.total == 0
        assert await redis_client.xlen(h.stream) == 0

        # The same two rows inside the window, but a backstop with no room.
        plan = await build_plan(
            relay_engine, redis_client, h.selection, bindings=(h.binding,), backstop=1
        )
        assert plan.totals()[Verdict.REPLAY] == 2
        assert plan.over_backstop() == [h.stream]
    finally:
        await h.cleanup()
