"""Tests for the operator CLI over the orphan-notify-group sweep
(``app.ops.notify_groups``, docs/log/3.135.md).

**Scope narrowed on purpose (ت-2).** The RULE this tool applies -- which
groups count as orphans, the settle window, the three refusal gates -- moved
into ``infrastructure/messaging/consumers/sweeper.py`` when the API process
started running it on a timer, and its tests moved with it
(``tests/unit/test_consumer_sweeper.py``). Duplicating them here would only
create a second place to update. What is left is what this module still owns
and nothing else does: the destructive verb's confirmation gate, and the set
of streams it scans.
"""

from __future__ import annotations

import pytest

from app.framework.events.topology import STATIC_CONSUMER_TOPOLOGY
from app.ops import notify_groups as module


def test_sweep_refuses_to_run_without_an_explicit_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """`XGROUP DESTROY` is irreversible, so the `app.ops.dlq` precedent holds:
    the destructive verb never runs on a bare invocation, and the refusal
    names `list` as the thing to run first."""
    monkeypatch.setattr("sys.argv", ["app.ops.notify_groups", "sweep"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "sweep refused" in str(excinfo.value)


def test_the_scanned_streams_are_the_static_topologys_deduplicated() -> None:
    """Two bindings share `cg.knowledge` across two streams, so a naive list
    comprehension would scan one stream twice."""
    streams = module.topology_streams()

    assert len(streams) == len(set(streams))
    assert set(streams) == {binding.stream for binding in STATIC_CONSUMER_TOPOLOGY}
