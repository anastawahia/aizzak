"""The one static consumer topology table (`docs/stream-topology-plan.md`
§1-ج · §2, step 2).

Before this module the same four `(stream, group)` pairs lived in **three**
places with nothing between them: the four literal `Subscription`s built by
`build_knowledge_worker`/`build_media_worker`/`build_memory_worker`
(`app/workers/bootstrap.py`), the four literal `STREAM` constants in each
module's own `event_mapping.py` (`app.modules.files/knowledge/media/memory.
application.event_mapping`), and the reference table in
`docs/design/04-event-catalog.md` §2. A one-character drift in any of them
was invisible until a stream went silent in production — no test compared
the three. This module is the fourth place, and the only one meant to be
read by code rather than typed by hand a second time: step 3 of the plan
above makes the Outbox relay walk `STATIC_CONSUMER_TOPOLOGY` and
`ensure_group` every pair *before* its first publish, closing the "workers
before the relay" boot-order rule at its root instead of enforcing it with a
manual `docker-compose.yml` ordering note.

**(a) Why the four `stream`/`group` strings below are written out literally,
never derived from the modules that own them.** `.importlinter`'s `layers`
contract (`.importlinter`, contract 1) puts `app.framework` at the BOTTOM of
the five-layer stack — `app.api` -> `app.agents` -> `app.modules` ->
`app.framework`, inward only. Importing `app.modules.files.application.
event_mapping.STREAM` from here to derive the value would be `app.framework`
reaching UP into `app.modules`, a direct contract violation this module must
never commit no matter how tempting the deduplication looks. The substitute
for derivation is **being guarded by a test instead**:
`tests/unit/test_stream_topology.py` imports the four `STREAM` constants
itself (a *test* importing `app.modules.*` breaks no layer — only
production code is bound by `.importlinter`) and asserts this table's
`stream` fields equal them, and separately asserts this table matches
exactly what the three worker composition roots build their `Subscription`s
with. A one-character slip in either this table or a module's own constant
fails that test by name, which is the enforcement a static import could
never be here.

**(b) Why `cg.notify.<host>.<pid>` is absent, and why that is not a gap.**
This table lists the groups that must exist *before anything is ever
published to the stream they read* — global, cross-tenant Streams delivering
domain events between modules and workers. The notification bridge inside
the API process (`framework/di/composition_root.py::build_notification_
consumer`) is a different animal entirely: it is a **per-process** family of
groups, one born every time an API process boots and named after that
process's own `<host>.<pid>` (`docs/design/04-event-catalog.md` §2's
footnote), torn down when that process shuts down cleanly, and swept by
`_sweep_stale_notify_groups` when the process died without a clean
shutdown (`docs/log/3.81.md`). A row pre-created here, once, at relay
start-up, would describe a group that is supposed to be born and die with a
specific process instead — wrong by definition, not merely incomplete.
Nothing this table's consumer (the relay, step 3) does should ever touch
that family; `cg.notify.*` is provisioned and reaped entirely inside the API,
by design, and stays that way.

**(c) Who reads this table.** Nobody yet — this module is data, not wiring
(deliberately zero I/O, so it sits in the framework kernel layer without
tripping contract 6 either). Step 3 of the plan above is what makes
`workers/bootstrap.py::build_relay_from_env` walk it and call
`RedisStreamsConsumer.ensure_group` for each pair before the relay's first
`XADD`, so a worker that has never booted still gets its consumer group
created at the stream's tail in time to see everything published from then
on (`redis_streams.py`'s `xgroup_create(..., id="$", mkstream=True)`, §1-ب
of the plan — unchanged by this step).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConsumerBinding:
    """One `(stream, group)` pair from the static topology below."""

    stream: str
    group: str


# The four pairs §1-ج's table names, written literally for the reason (a)
# above explains -- guarded against drift by
# `tests/unit/test_stream_topology.py`, not by any import from this file.
STATIC_CONSUMER_TOPOLOGY: tuple[ConsumerBinding, ...] = (
    ConsumerBinding(stream="stream.files", group="cg.knowledge"),
    ConsumerBinding(stream="stream.knowledge", group="cg.knowledge"),
    ConsumerBinding(stream="stream.media", group="cg.media"),
    ConsumerBinding(stream="stream.memory", group="cg.memory"),
)
