"""Regression guard for `app/framework/events/topology.py`
(`docs/stream-topology-plan.md` §1-ج · §2, step 2).

`STATIC_CONSUMER_TOPOLOGY` writes its four `(stream, group)` pairs out
literally rather than importing them from `app.modules.*` -- `.importlinter`'s
`layers` contract forbids `app.framework` from reaching up into
`app.modules` (`topology.py`'s own docstring, part (a)). A *test* importing
`app.modules.*` breaks no layer -- only production code answers to
`.importlinter` -- so this module is the substitute for the derivation the
kernel is not allowed to perform: it cross-checks the table against its two
independent sources of truth instead.

1. `test_every_table_stream_matches_its_owning_modules_stream_constant` --
   every `stream` field equals the literal `STREAM` constant of the module
   that owns it (imported straight from each module's own
   `application/event_mapping.py`).
2. `test_table_matches_exactly_what_the_three_workers_build_no_more_no_less`
   -- the table matches, pair for pair, what `build_knowledge_worker`/
   `build_media_worker`/`build_memory_worker` (`app/workers/bootstrap.py`)
   actually build their `Subscription`s with -- the same `(stream, group)`
   shape `test_workers_bootstrap.py` already asserts against directly (e.g.
   its `test_build_knowledge_worker_from_env_builds_a_real_consumer_without_
   raising`), reused here rather than inventing a second construction
   pattern. Every dependency the three builders take is a plain parameter, so
   a single inert placeholder stands in for all of them -- none of the three
   ever calls a method on what it is handed; they only close over it inside
   handler closures built for later, never-invoked-here delivery.
3. `test_no_binding_group_is_a_per_process_notify_group` -- no group starts
   with `cg.notify`, the family `topology.py`'s docstring (part (b)) explains
   is deliberately absent.

Guard-the-guard (3.69's lesson, restated in `test_deploy_worker_default.py`):
a set-equality check silently passes if BOTH sides are empty, and an empty
`STATIC_CONSUMER_TOPOLOGY` would sail through every comparison above without
guarding anything. `test_table_has_exactly_the_four_pairs_named_by_the_plan`
anchors the count and the non-blankness of every field so that failure mode
has nowhere to hide.
"""

from __future__ import annotations

from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY, ConsumerBinding
from app.modules.files.application.event_mapping import STREAM as _FILES_STREAM
from app.modules.knowledge.application.event_mapping import STREAM as _KNOWLEDGE_STREAM
from app.modules.knowledge.application.indexing import IndexDocument
from app.modules.media.application.event_mapping import STREAM as _MEDIA_STREAM
from app.modules.memory.application.event_mapping import STREAM as _MEMORY_STREAM
from app.workers.bootstrap import build_knowledge_worker, build_media_worker, build_memory_worker


class _Unused:
    """A placeholder for every dependency the three `build_*_worker`
    functions merely store inside a handler closure without ever calling.
    Extracting the topology only needs the `(stream, group)` pairs those
    functions return alongside a `StreamConsumer` this suite never touches --
    not a working worker -- so one inert object stands in for every
    repository/outbox/unit-of-work/ledger/client parameter below."""


def _built_pairs() -> frozenset[tuple[str, str]]:
    """The `(stream, group)` pairs the three worker composition roots in
    `workers/bootstrap.py` actually build their `Subscription`s with."""
    placeholder = _Unused()

    _, knowledge_subscriptions = build_knowledge_worker(
        redis_client=placeholder,  # type: ignore[arg-type]
        documents=placeholder,  # type: ignore[arg-type]
        pipeline=IndexDocument(placeholder, placeholder),  # type: ignore[arg-type]
        content_resolver=placeholder,  # type: ignore[arg-type]
        outbox=placeholder,  # type: ignore[arg-type]
        uow=placeholder,  # type: ignore[arg-type]
        ledger=placeholder,  # type: ignore[arg-type]
        consumer_name="topology-guard-knowledge",
        block_ms=5000,
        batch_count=10,
        max_deliveries=5,
    )
    _, media_subscriptions = build_media_worker(
        redis_client=placeholder,  # type: ignore[arg-type]
        jobs=placeholder,  # type: ignore[arg-type]
        generator=placeholder,  # type: ignore[arg-type]
        outbox=placeholder,  # type: ignore[arg-type]
        uow=placeholder,  # type: ignore[arg-type]
        ledger=placeholder,  # type: ignore[arg-type]
        consumer_name="topology-guard-media",
        block_ms=5000,
        batch_count=10,
        max_deliveries=5,
    )
    _, memory_subscriptions = build_memory_worker(
        redis_client=placeholder,  # type: ignore[arg-type]
        memory=placeholder,  # type: ignore[arg-type]
        embeddings=placeholder,  # type: ignore[arg-type]
        vectors=placeholder,  # type: ignore[arg-type]
        uow=placeholder,  # type: ignore[arg-type]
        ledger=placeholder,  # type: ignore[arg-type]
        model="m",
        api_key="k",
        consumer_name="topology-guard-memory",
        block_ms=5000,
        batch_count=10,
        max_deliveries=5,
    )
    all_subscriptions = (*knowledge_subscriptions, *media_subscriptions, *memory_subscriptions)
    return frozenset((s.stream, s.group) for s in all_subscriptions)


def test_table_has_exactly_the_four_pairs_named_by_the_plan() -> None:
    assert len(STATIC_CONSUMER_TOPOLOGY) == 4
    assert all(isinstance(binding, ConsumerBinding) for binding in STATIC_CONSUMER_TOPOLOGY)
    for binding in STATIC_CONSUMER_TOPOLOGY:
        assert binding.stream, "a blank stream would still pass every set-equality check below"
        assert binding.group, "a blank group would still pass every set-equality check below"


def test_every_table_stream_matches_its_owning_modules_stream_constant() -> None:
    """`stream.files` (owned by `modules.files`) and `stream.knowledge`
    (owned by `modules.knowledge`) both feed `cg.knowledge` -- one group, two
    streams, 04 §4's binding table -- so each `stream` field is checked
    against the UNION of the four modules' own constants rather than a single
    module, which would wrongly demand knowledge's stream come from files."""
    table_streams = {binding.stream for binding in STATIC_CONSUMER_TOPOLOGY}
    assert table_streams == {
        _FILES_STREAM,
        _KNOWLEDGE_STREAM,
        _MEDIA_STREAM,
        _MEMORY_STREAM,
    }


def test_table_matches_exactly_what_the_three_workers_build_no_more_no_less() -> None:
    built = _built_pairs()
    assert len(built) == 4, f"expected exactly 4 built (stream, group) pairs, got {sorted(built)}"
    table = frozenset((binding.stream, binding.group) for binding in STATIC_CONSUMER_TOPOLOGY)
    assert table == built, (
        f"STATIC_CONSUMER_TOPOLOGY {sorted(table)} does not match what "
        "build_knowledge_worker/build_media_worker/build_memory_worker actually build "
        f"{sorted(built)} -- missing from the table: {sorted(built - table)}, "
        f"extra in the table: {sorted(table - built)}"
    )


def test_no_binding_group_is_a_per_process_notify_group() -> None:
    """`cg.notify.<host>.<pid>` is deliberately absent -- `topology.py`'s own
    docstring (part (b)) explains why: it is a per-PROCESS family the API
    mints and tears down itself, never a static row provisioned once here."""
    for binding in STATIC_CONSUMER_TOPOLOGY:
        assert not binding.group.startswith("cg.notify"), (
            f"{binding.group!r} looks like a per-process notify group -- it must never be "
            "pre-created statically (see topology.py's docstring, part (b))"
        )
