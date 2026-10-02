"""The backlog profile's copies of the platform's numbers (capacity 5.5 ·
``deploy/load/backlog.js`` · ``08 §4.21``).

``backlog.js`` refuses a pool too small to carry 100 jobs a minute without
provoking two of the platform's own refusals, and it needs both ceilings to
say so -- written there in JavaScript, because k6 cannot import them. If a
copy drifts from its source the guard checks the wrong number and nothing
fails: the run just offers less than the criterion's load, as 429s and 409s
that never become events, while reporting that it offered all of it.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.framework.settings.settings import Limits
from app.ops.mint_load_tokens import UPLOAD_HEADROOM_FILES

_BACKLOG_JS = Path("deploy/load/backlog.js")
_SCENARIO_JS = Path("deploy/load/scenarios/index_backlog.js")


def _declared(name: str) -> int:
    source = _BACKLOG_JS.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = ([\d_]+);", source)
    assert match, f"{_BACKLOG_JS} no longer declares {name} in the expected shape"
    return int(match.group(1).replace("_", ""))


def test_the_heavy_job_ceiling_is_the_apis_own() -> None:
    assert _declared("HEAVY_JOBS_PER_MIN") == Limits().heavy_jobs_per_min


def test_the_file_room_is_the_one_the_pool_leaves() -> None:
    assert _declared("UPLOAD_HEADROOM_FILES") == UPLOAD_HEADROOM_FILES


def test_the_backlog_does_not_wait_on_its_jobs() -> None:
    """The reason the scenario exists. With the worker stopped no job ever
    finishes, so a scenario that polled would park a VU on every arrival and
    add API load the criterion does not name (``index_file.js`` polls for up
    to 300 s)."""
    source = _SCENARIO_JS.read_text(encoding="utf-8")
    assert "http.get(" not in source
    assert "sleep(" not in source
