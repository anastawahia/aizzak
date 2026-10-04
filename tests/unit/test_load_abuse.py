"""The abuse profile's copies of the platform's numbers and words (capacity 1.2 ·
``deploy/load/abuse.js`` · ``deploy/load/lib/metrics.js``).

``abuse.js`` gates on the abuser being held at the per-user ceiling, and
``refusalScope`` tells that ceiling apart from the tenant one, the in-flight
guard and the edge by reading the 429's ``detail``. Both are written in
JavaScript, because k6 cannot import Python. If the ceiling drifts, the
admitted-requests bound checks the wrong number; if a detail drifts, every
refusal of that kind lands under ``other`` and the run cannot say which layer
held the abuser -- in both cases nothing fails, the verdict is just wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.api.middleware.rate_limit import (
    _DETAILS,
    HEAVY_SCOPE,
    USER_SCOPE,
    WORKSPACE_SCOPE,
)
from app.framework.settings.settings import Limits

_ABUSE_JS = Path("deploy/load/abuse.js")
_METRICS_JS = Path("deploy/load/lib/metrics.js")
_INFLIGHT_PY = Path("src/app/api/middleware/inflight.py")


def _declared(name: str) -> int:
    source = _ABUSE_JS.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = ([\d_]+);", source)
    assert match, f"{_ABUSE_JS} no longer declares {name} in the expected shape"
    return int(match.group(1).replace("_", ""))


def _scope_test(scope: str) -> tuple[str, str]:
    """The (method, literal) ``refusalScope`` uses to recognise ``scope``."""
    source = _METRICS_JS.read_text(encoding="utf-8")
    match = re.search(
        rf"if \(detail\.(startsWith|endsWith|includes)\('([^']+)'\)\) return '{scope}';",
        source,
    )
    assert match, f"{_METRICS_JS} no longer recognises the {scope!r} scope in the expected shape"
    return match.group(1), match.group(2)


def _matches(detail: str, method: str, literal: str) -> bool:
    if method == "startsWith":
        return detail.startswith(literal)
    if method == "endsWith":
        return detail.endswith(literal)
    return literal in detail


def test_the_user_ceiling_is_the_apis_own() -> None:
    assert _declared("USER_RATE_PER_MIN") == Limits().api_rate_per_min


def test_each_bucket_refusal_is_recognised_as_its_own_scope() -> None:
    for scope in (USER_SCOPE, WORKSPACE_SCOPE, HEAVY_SCOPE):
        method, literal = _scope_test(scope)
        assert _matches(_DETAILS[scope], method, literal), (scope, _DETAILS[scope])


def test_a_heavy_job_refusal_is_not_read_as_the_user_ceiling() -> None:
    """Both details end in "for this user"; the heavy test must run first."""
    source = _METRICS_JS.read_text(encoding="utf-8")
    assert source.index("return 'heavy';") < source.index("return 'user';")
    user_method, user_literal = _scope_test(USER_SCOPE)
    assert _matches(_DETAILS[HEAVY_SCOPE], user_method, user_literal)


def test_the_in_flight_refusal_is_recognised() -> None:
    method, literal = _scope_test("in_flight")
    match = re.search(r'detail="([^"]+)"', _INFLIGHT_PY.read_text(encoding="utf-8"))
    assert match, f"{_INFLIGHT_PY} no longer renders its detail in the expected shape"
    assert _matches(match.group(1), method, literal)
