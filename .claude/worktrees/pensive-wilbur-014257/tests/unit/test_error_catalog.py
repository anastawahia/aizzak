"""The error catalog is a MIRROR of behaviour — enforced, not asserted in prose
(Phase 6.2 · 03-api-spec §4 · DD-05).

A catalog is only worth having if it is *complete* and *true*, and neither
property survives a year of edits on trust alone. So this module walks the
whole of ``src/app`` and checks both directions:

* **forward** — every error code the source can put on the wire is defined in
  ``ERROR_CATALOG``. A raise site that invents a code (or fat-fingers one)
  fails here, not in a client's logs;
* **backward** — every catalog entry is actually reachable from the source. A
  code no server emits is a promise no client can use, and it is precisely how
  a published catalog rots: entries accumulate, nothing removes them, and the
  document stops describing the system. This is the direction that made 6.2
  delete 03 §4's ``files.not_ready`` and ``knowledge.not_indexed`` (v1 has no
  site for either — see ``framework/errors.py``) and give
  ``agent.unknown``/``credentials.none_available`` the sites they lacked.

A code reaches the wire three ways, and the scan knows all three: an
``AppError``-family constructor's ``code=``; a class default on the hierarchy
itself; and decision B1's in-band ``{"code", "status", …}`` payload, which is
a plain dict the exception types never touch.

The one loophole a static scan cannot close is a ``code=`` that is not a
literal. Those are ENUMERATED below rather than skipped: three sites, each
bounded by construction, and a fourth would fail this test — which is the
point, because a dynamic ``code=`` is exactly how a code escapes the catalog.
"""

from __future__ import annotations

import ast
import pathlib

import app
from app.framework.errors import (
    ERROR_CATALOG,
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    TooLargeError,
    UnauthorizedError,
    UnsupportedTypeError,
    ValidationError,
)

_SRC = pathlib.Path(app.__file__).parent

# The shared hierarchy (10 §5). Matched by NAME, since every site imports the
# classes directly — an aliased import would simply not be scanned, and the
# backward direction would catch the orphaned catalog entry anyway.
_ERROR_CLASSES = frozenset(
    {
        "AppError",
        "NotFoundError",
        "ConflictError",
        "ValidationError",
        "ForbiddenError",
        "UnauthorizedError",
        "TooLargeError",
        "UnsupportedTypeError",
        "RateLimitedError",
    }
)

_HIERARCHY = (
    AppError,
    NotFoundError,
    ConflictError,
    ValidationError,
    ForbiddenError,
    UnauthorizedError,
    TooLargeError,
    UnsupportedTypeError,
    RateLimitedError,
)

# Every ``code=`` in the source that is not a literal, as (module, expression).
# Each one is bounded: the first two echo or map, the third picks between two
# literals that both live in the same file (so the backward scan still sees
# them). Adding a fourth is a decision, and this test makes you take it.
_DYNAMIC_CODE_SITES = {
    # Echoes the code out of a B1 event this process produced, so the
    # non-streaming path answers the problem the stream would have shown.
    ("agents/orchestrator.py", "code if isinstance(code, str) else 'agent.failed'"),
    # A CLOSED map from `LimitDecision.reason`; an unknown reason falls back
    # to the catalogued generic rather than minting `usage.<anything>`.
    ("agents/orchestrator.py", "_DENIAL_CODES.get(reason, 'common.rate_limited')"),
    # One of two literals, chosen by which domain rule the URL broke.
    ("modules/integrations/application/use_cases.py", "code"),
}


def _sources() -> list[tuple[str, ast.Module]]:
    out = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return out


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``_NAME = "literal"`` bindings — the shape routers use to
    name a code once at the top of the file (``_TOOLS_UNAVAILABLE``)."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = node.value.value
    return out


def _raised_code(node: ast.Call, constants: dict[str, str]) -> str | ast.expr | None:
    """A hierarchy raise's ``code=`` — the literal it resolves to, the raw
    expression when it does not, or ``None`` for a call that is not one."""
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name not in _ERROR_CLASSES:
        return None
    for keyword in node.keywords:
        if keyword.arg != "code":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Name) and value.id in constants:
            return constants[value.id]
        return value
    return None


def _b1_code(node: ast.Dict) -> str | None:
    """Decision B1's in-band ``{"code", "status", …}`` payload — a bare dict
    the exception types never touch, so nothing else would see it."""
    keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
    if not {"code", "status"} <= keys:
        return None
    for key, value in zip(node.keys, node.values, strict=True):
        is_code_key = isinstance(key, ast.Constant) and key.value == "code"
        if is_code_key and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _scan() -> tuple[set[str], set[tuple[str, str]], set[str]]:
    """(emitted codes, dynamic sites, every string literal in the source)."""
    emitted: set[str] = set()
    dynamic: set[tuple[str, str]] = set()
    literals: set[str] = set()

    for rel, tree in _sources():
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
            elif isinstance(node, ast.Call):
                code = _raised_code(node, constants)
                if isinstance(code, str):
                    emitted.add(code)
                elif code is not None:
                    dynamic.add((rel, ast.unparse(code)))
            elif isinstance(node, ast.Dict):
                code = _b1_code(node)
                if code is not None:
                    emitted.add(code)

    return emitted, dynamic, literals


def test_every_emitted_code_is_in_the_catalog() -> None:
    """Forward: nothing reaches the wire that 03 §4 does not define."""
    emitted, _dynamic, _literals = _scan()

    assert emitted, "the scan found no codes at all — it stopped working"
    assert emitted <= set(ERROR_CATALOG), sorted(emitted - set(ERROR_CATALOG))


def test_every_class_default_is_in_the_catalog() -> None:
    """The hierarchy's own defaults are codes too — most raise sites pass no
    ``code=`` at all, so these are the most-emitted entries in the system."""
    defaults = {cls.code for cls in _HIERARCHY}

    assert defaults <= set(ERROR_CATALOG), sorted(defaults - set(ERROR_CATALOG))


def test_every_class_default_status_matches_its_catalog_row() -> None:
    """A class whose status disagreed with its own code would make the
    catalog's status column a lie for every site that raises it bare."""
    for cls in _HIERARCHY:
        assert cls.status == ERROR_CATALOG[cls.code].status, cls.__name__


def test_every_catalog_entry_is_reachable_from_the_source() -> None:
    """Backward: no wish-list entries. Checked against every string literal in
    ``src/app`` rather than the emitted set, so a code that reaches the wire
    through a closed map (``_DENIAL_CODES``) still counts as reachable."""
    _emitted, _dynamic, literals = _scan()

    assert set(ERROR_CATALOG) <= literals, sorted(set(ERROR_CATALOG) - literals)


def test_the_deleted_entries_stay_deleted() -> None:
    """03 §4 listed these two; v1 has no site for either (``FilesQuery``
    collapses missing/not-ready into one ``None``, and no knowledge route is
    per-document retrieval). If a site ever appears, add the entry back with
    it — but re-adding it alone would fail the backward test above, which is
    the guard that matters."""
    assert "files.not_ready" not in ERROR_CATALOG
    assert "knowledge.not_indexed" not in ERROR_CATALOG


def test_only_the_known_sites_choose_a_code_dynamically() -> None:
    """The loophole, held open exactly three fingers wide."""
    _emitted, dynamic, _literals = _scan()

    assert dynamic == _DYNAMIC_CODE_SITES


def test_no_code_is_defined_twice_under_two_names() -> None:
    """Two entries with the same title and status are the ``common.validation``
    /``common.validation_error`` split all over again — one problem answering
    under two names, split by nothing a client can see."""
    seen: dict[tuple[int, str], str] = {}
    for code, spec in ERROR_CATALOG.items():
        key = (spec.status, spec.title)
        assert key not in seen, f"{code} duplicates {seen.get(key)}"
        seen[key] = code


def test_every_entry_has_a_module_prefix_and_a_real_status() -> None:
    """Shape of the contract itself: ``<prefix>.<name>`` (the prefix is the
    first thing a client switches on) and a 4xx/5xx status."""
    for code, spec in ERROR_CATALOG.items():
        prefix, _, name = code.partition(".")
        assert prefix and name and "." not in name, code
        assert 400 <= spec.status <= 599, code
        assert spec.title and spec.title[0].isupper(), code
