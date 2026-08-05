"""CI must actually run, and every marker it selects on must actually exist
(release-blockers-plan.md §3, step 5 · pre-release-review.md P1).

Three defects, one family -- a workflow that looks green while proving
nothing. The third (docs/log/3.96.md) was added after the first two were
fixed, when the first-ever run was finally SIMULATED from a clean
`git archive HEAD` instead of being reasoned about; see
`test_the_workflow_installs_every_extra_the_tests_import_from`.

1. ``on: push: branches: [main]`` in a repository whose branch is ``master``.
   A push trigger naming a branch that does not exist never fires, so every
   push for months ran **no** CI at all. Nothing reports that: the commit list
   simply shows no check, which reads exactly like a repository whose checks
   have not finished yet.

2. A second job running ``pytest -m integration`` while the ``integration``
   marker was declared in ``pyproject.toml`` and applied to **zero** tests.
   It collected ``2272 deselected`` and ran nothing. ``--strict-markers`` did
   not catch it: it rejects an *applied* marker that was never declared, never
   a *declared* marker that nothing applies. That is the asymmetry this module
   closes.

   Correction recorded during the step-5 review: that job did not go
   permanently green, as the original diagnosis said. ``pytest -m <orphan>``
   collects nothing and exits **5**, failing the step -- so it would have been
   red. It never ran at all: defect 1 silenced the whole workflow, and at the
   time of that review ``gh run list`` reported zero runs of it, ever. Defect
   1 masked defect 2, which is why both had to be fixed in the same change.

   ⚠️ Superseded in part, and corrected rather than left standing
   (docs/log/3.96.md §0): "zero runs, ever" was true when written and is not
   true now. Fixing defect 1 worked -- CI has run since 2026-07-27, five
   times, failing every one on the FIRST gate (``ruff format --check``), so
   nothing after it ever executed. Defect 3 below was hiding behind that.

So: the branches CI triggers on must be real branches here; any marker a
``pytest -m ...`` step selects on must be both declared and applied; no
declared marker may be an orphan; and -- because the fix deleted the separate
job and let the one ``pytest`` step collect the whole tree -- every
``tests/integration/`` module must carry a ``live_*`` gate, or a bare runner
with no services would fail the suite instead of skipping it.

Same shape as ``test_deploy_worker_default.py`` (step 3): documents drifted
and nothing compared them. This compares them.

The quality job still collects the whole suite on a bare runner, while the
second job deliberately carries a negative ``-m`` expression and stands up
the local services that remain selected.  The marker-expression check now has
direct teeth: every name excluded by that job must remain declared and used.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from importlib.metadata import packages_distributions
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_TESTS_DIR = _REPO_ROOT / "tests"
_INTEGRATION_DIR = _TESTS_DIR / "integration"

# This module's own prose names `pytest.mark.<something>` while explaining the
# defect. Counting itself as a *user* of those markers would let the guard
# satisfy its own assertion -- a guard that guards itself guards nothing.
_SELF = Path(__file__).resolve()

# `@pytest.mark.live_db` / `pytest.mark.live_db` inside a `pytestmark` list.
_MARKER_APPLICATION = re.compile(r"pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)")
# A bare identifier inside a `-m` expression, e.g. `not integration`.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# `-m <expr>`, quoted or not. Only ever applied to a command that runs pytest,
# so `python -m pip install` is never mistaken for a marker expression.
_DASH_M = re.compile(r"-m\s+(\"[^\"]*\"|'[^']*'|[^\s|&;]+)")
# `-m "not integration"` is a boolean expression, not a bare marker name.
_MARKER_EXPR_KEYWORDS = frozenset({"not", "and", "or"})
# A module-level `pytestmark = ...` assignment (single marker or a list).
_PYTESTMARK_BLOCK = re.compile(r"^pytestmark\s*=\s*(.+?)$", re.MULTILINE)
# `pip install -e ".[dev,parsers]"`, quoted or not, with or without `-e`.
_PIP_INSTALL_EXTRAS = re.compile(r"pip\s+install\s+(?:-e\s+)?[\"']?\.\[([^\]]+)\]")
# Top-level packages of this repository, which resolve by path, not by wheel.
_FIRST_PARTY = frozenset({"app", "tests", "services"})


def _workflow() -> dict[str, Any]:
    """The parsed workflow.

    ``yaml.safe_load`` follows YAML 1.1, where the bare key ``on`` is the
    BOOLEAN ``True``, not the string ``"on"`` -- the single likeliest way for
    a check like this one to look up nothing and pass forever. Both spellings
    are therefore accepted, and ``test_the_extractors_are_not_finding_nothing``
    fails loudly if neither resolves.
    """
    loaded = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{_WORKFLOW} did not parse as a YAML mapping"
    return loaded


def _triggers() -> dict[str, Any]:
    doc = _workflow()
    raw = doc.get("on", doc.get(True))
    assert isinstance(raw, dict), (
        "ci.yml has no `on:` mapping -- note that PyYAML parses the bare key "
        "`on` as the boolean True (YAML 1.1), which is checked for here."
    )
    return raw


def _push_branches() -> list[str]:
    push = _triggers().get("push")
    if not isinstance(push, dict):
        return []
    branches = push.get("branches")
    if branches is None:
        # `on: push:` with no branch filter fires on every branch; nothing to
        # compare against the repository's refs.
        return []
    assert isinstance(branches, list), "`on.push.branches` must be a list"
    return [str(branch) for branch in branches]


def _run_commands() -> list[str]:
    """Every `run:` script in the workflow. YAML-level, so the file's prose
    comments (which quote the deleted `pytest -m integration` command while
    explaining why it is gone) are structurally invisible here."""
    commands: list[str] = []
    jobs = _workflow().get("jobs")
    if not isinstance(jobs, dict):
        return commands
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                commands.append(step["run"])
    return commands


def _markers_selected_in_workflow() -> frozenset[str]:
    selected: set[str] = set()
    for command in _run_commands():
        for line in command.splitlines():
            if "pytest" not in line:
                continue
            for match in _DASH_M.finditer(line):
                expression = match.group(1).strip("\"'")
                selected.update(
                    token
                    for token in _IDENTIFIER.findall(expression)
                    if token not in _MARKER_EXPR_KEYWORDS
                )
    return frozenset(selected)


def _declared_markers() -> frozenset[str]:
    with _PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    entries = data["tool"]["pytest"]["ini_options"]["markers"]
    assert isinstance(entries, list)
    return frozenset(str(entry).split(":", 1)[0].split("(", 1)[0].strip() for entry in entries)


def _test_sources() -> Iterator[tuple[Path, str]]:
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.resolve() == _SELF:
            continue
        yield path, path.read_text(encoding="utf-8")


def _applied_markers() -> frozenset[str]:
    applied: set[str] = set()
    for _, text in _test_sources():
        applied.update(_MARKER_APPLICATION.findall(text))
    return frozenset(applied)


def _normalize(distribution: str) -> str:
    """PEP 503 name normalization -- `PyMuPDF`, `py_mu_pdf` and `py.mu.pdf` are
    one distribution, and `importlib.metadata` and `pyproject.toml` do not
    agree on which spelling to hand back."""
    return re.sub(r"[-_.]+", "-", distribution).lower()


def _extras_the_workflow_installs() -> frozenset[str]:
    """The extras named in ci.yml's own `pip install -e ".[a,b]"`."""
    extras: set[str] = set()
    for command in _run_commands():
        for match in _PIP_INSTALL_EXTRAS.finditer(command):
            extras.update(part.strip() for part in match.group(1).split(","))
    return frozenset(extra for extra in extras if extra)


def _distributions_by_extra() -> dict[str, frozenset[str]]:
    """`{extra name: {normalized distribution names}}` from pyproject.toml."""
    with _PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    groups = data["project"].get("optional-dependencies", {})
    return {
        str(extra): frozenset(_normalize(re.split(r"[<>=!~\[;\s]", str(spec))[0]) for spec in specs)
        for extra, specs in groups.items()
    }


def _modules_imported_by_tests() -> frozenset[str]:
    """Top-level module names imported anywhere under `tests/`, third-party
    only -- the standard library and this repository's own top-level packages
    are dropped. Parsed with `ast`, not a regex: an import named inside a
    docstring or a comment is not an import, and this module's prose names
    several while explaining the defect."""
    modules: set[str] = set()
    for path, text in _test_sources():
        for node in ast.walk(ast.parse(text, filename=str(path))):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".", 1)[0])
    return frozenset(modules - _FIRST_PARTY - sys.stdlib_module_names)


def _git_branch_names() -> frozenset[str]:
    """Short names of this checkout's local heads and `origin/*` branches.

    Returns an empty set when the checkout cannot answer -- notably the
    shallow, detached-HEAD tree `actions/checkout` produces for a
    `pull_request` event, which has neither. Callers skip on empty rather than
    fail: a guard that reports a false failure in CI gets deleted, and then it
    guards nothing.
    """
    try:
        completed = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git binary
        return frozenset()
    if completed.returncode != 0:  # pragma: no cover - not a git work tree
        return frozenset()
    names: set[str] = set()
    for line in completed.stdout.splitlines():
        ref = line.strip()
        for prefix in ("refs/heads/", "refs/remotes/origin/"):
            if ref.startswith(prefix):
                short = ref[len(prefix) :]
                if short != "HEAD":
                    names.add(short)
                break
    return frozenset(names)


def test_the_extractors_are_not_finding_nothing() -> None:
    """A guard whose parser silently matches nothing passes forever while
    guarding nothing (the 3.69 lesson, restated in `test_ops_provision.py`
    and `test_deploy_worker_default.py`). Every extractor below must return
    something before the real assertions are worth believing."""
    assert _push_branches(), (
        "ci.yml declares no `on.push.branches` -- either the trigger was "
        "removed (then push runs no CI at all again) or this extractor broke."
    )
    assert _run_commands(), "ci.yml has no `run:` steps -- the parser is broken."
    assert any("pytest" in command for command in _run_commands()), (
        "no `run:` step in ci.yml invokes pytest -- the test gate is gone."
    )
    declared = _declared_markers()
    assert len(declared) >= 5, f"only {len(declared)} markers parsed from pyproject.toml"
    assert len(_applied_markers() & declared) >= 5, (
        "fewer than 5 declared markers were found applied anywhere in tests/ "
        "-- the `pytest.mark.<name>` scan is broken."
    )
    assert _extras_the_workflow_installs(), (
        "no `pip install -e '.[...]'` extras parsed out of ci.yml -- either the "
        "install step stopped naming any extra (then `parsers` is gone and the "
        "suite aborts at collection) or `_PIP_INSTALL_EXTRAS` broke."
    )
    assert _distributions_by_extra(), (
        "pyproject.toml declares no `[project.optional-dependencies]` groups."
    )
    assert "pytest" in _modules_imported_by_tests(), (
        "the ast import scan did not even find `pytest` imported under tests/ "
        "-- it is finding nothing, and the extras check below proves nothing."
    )


def test_push_branches_are_real_branches_of_this_repository() -> None:
    """The defect verbatim: `branches: [main]` in a `master` repository. The
    push trigger silently never fires, so no push has been tested since the
    workflow was written."""
    known = _git_branch_names()
    if not known:
        pytest.skip("no local/origin branch refs available in this checkout")
    configured = [branch for branch in _push_branches() if "*" not in branch]
    unknown = sorted(branch for branch in configured if branch not in known)
    assert not unknown, (
        f".github/workflows/ci.yml triggers CI on push to {unknown!r}, but this "
        f"repository has no such branch -- its branches are {sorted(known)!r}. "
        "A push trigger naming a branch that does not exist NEVER fires: the "
        "workflow simply does not run, which on the commit list is "
        "indistinguishable from a workflow that always passes."
    )


def test_integration_job_selects_real_markers_and_requires_live_dependencies() -> None:
    """The integration job must fail rather than skip when its local stack is
    unavailable, and every marker in its selector must name real tests."""
    selected = _markers_selected_in_workflow()
    undeclared = sorted(selected - _declared_markers())
    assert not undeclared, (
        f"ci.yml runs pytest with `-m` selecting on {undeclared!r}, which "
        "pyproject.toml's `[tool.pytest.ini_options].markers` does not declare."
    )
    unapplied = sorted(selected - _applied_markers())
    assert not unapplied, (
        f"ci.yml runs pytest with `-m` selecting on {unapplied!r}, but no test "
        "under tests/ ever applies that marker. Such a job reports "
        "'N deselected', runs ZERO tests, and exits 5 -- either apply the "
        "marker to the tests it is meant to select, or delete the step that "
        "selects on it."
    )

    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    integration = jobs.get("integration")
    assert isinstance(integration, dict), (
        "ci.yml has no integration job -- the quality job's live tests skip "
        "on a bare runner, so removing this job removes all live CI coverage."
    )
    assert integration.get("needs") == "quality", (
        "the integration job must run only after all five quality gates pass"
    )
    job_env = integration.get("env")
    assert isinstance(job_env, dict)
    assert job_env.get("COMPOSE_FILE") == "docker-compose.yml:docker-compose.test.yml", (
        "the integration job must name docker-compose.test.yml explicitly; "
        "that opt-in overlay is the only source of the test-database script"
    )

    steps = integration.get("steps")
    assert isinstance(steps, list)
    pytest_steps = [
        step
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str) and "pytest" in step["run"]
    ]
    assert len(pytest_steps) == 1, (
        f"expected exactly one pytest step in the integration job, found {len(pytest_steps)}"
    )
    pytest_env = pytest_steps[0].get("env")
    assert isinstance(pytest_env, dict) and str(pytest_env.get("REQUIRE_LIVE")) == "1", (
        "the integration pytest step must set REQUIRE_LIVE=1; without it an "
        "unavailable Compose dependency becomes a green skip"
    )

    scripts = "\n".join(
        step["run"] for step in steps if isinstance(step, dict) and isinstance(step.get("run"), str)
    )
    for required in (
        "vault-bootstrap",
        "minio-bootstrap",
        "migrate",
        "/opt/aizzak/testdb/20-test-database.sh",
        ". ./.env.test",
    ):
        assert required in scripts, (
            f"the integration job no longer invokes {required!r}; its live "
            "tests would be unprovisioned or would read the wrong environment"
        )

    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("run"), str):
            continue
        syntax = subprocess.run(
            ["bash", "-n"],
            input=step["run"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert syntax.returncode == 0, (
            f"invalid Bash in integration step {step.get('name')!r}: {syntax.stderr}"
        )


def test_no_declared_marker_is_an_orphan() -> None:
    """The exact trap that produced this whole step. `--strict-markers`
    rejects an *applied* marker that was never declared; it is completely
    silent about a *declared* marker that nothing applies. So a plausible
    name sits in pyproject.toml and a CI job selects on it -- a job that
    reads, on the workflow list, like an integration suite and is in fact a
    step that can only ever collect zero tests."""
    orphans = sorted(_declared_markers() - _applied_markers())
    assert not orphans, (
        f"pyproject.toml declares {orphans!r} under "
        "`[tool.pytest.ini_options].markers`, but no test under tests/ applies "
        "any of them. `--strict-markers` will never tell you this. A declared "
        "marker with no user is a `-m` selector waiting to collect nothing at "
        "all -- apply it, or delete the declaration."
    )


def test_the_workflow_installs_every_extra_the_tests_import_from() -> None:
    """The third defect of the same family, found by simulating the first-ever
    CI run from a clean `git archive` (`docs/log/3.96.md`).

    ci.yml installed `.[dev]`. `test_knowledge_parsers.py` imports `fitz`,
    which `pyproject.toml` declares in the SEPARATE `parsers` group. The
    result is not a failing test -- it is `ModuleNotFoundError` at import
    time, "Interrupted: 1 error during collection", exit 2: the whole suite
    never starts. Zero tests run and the job is red for a reason that names a
    parser library rather than the packaging mistake underneath it.

    Only *optional* groups are checked. A module that arrives transitively
    through a base dependency (`starlette` via fastapi, `cryptography` via
    `pyjwt[crypto]`) is installed on every runner no matter which extras are
    named, so requiring it to be declared directly would be a false alarm --
    and a guard that cries wolf gets deleted, taking the real check with it.

    Honest limit, stated rather than papered over: the module -> distribution
    map comes from `importlib.metadata`, so it can only see packages
    installed HERE. In an environment that already lacks `parsers` the lookup
    returns nothing and this test cannot fire -- but that environment fails at
    collection anyway. What this guards is the regression: someone trimming
    the install line back while the local venv stays complete.
    """
    installed_extras = _extras_the_workflow_installs()
    by_extra = _distributions_by_extra()
    provided_by = packages_distributions()

    missing: list[str] = []
    for module in sorted(_modules_imported_by_tests()):
        distributions = {_normalize(name) for name in provided_by.get(module, [])}
        if not distributions:
            continue  # not installed here -- see the docstring's stated limit
        for extra, declared in by_extra.items():
            if distributions & declared and extra not in installed_extras:
                missing.append(f"{module} (provided by {sorted(distributions)!r}, extra {extra!r})")

    assert not missing, (
        "tests/ imports these, but .github/workflows/ci.yml installs only the "
        f"extras {sorted(installed_extras)!r}: {missing!r}. A missing extra is "
        "not a failing test -- it is an ImportError during COLLECTION, which "
        "aborts the entire run before a single test executes."
    )


def test_every_integration_module_is_gated_by_a_live_marker() -> None:
    """The deleted job let the surviving `pytest` step collect the whole
    tree, including `tests/integration/`. That is only safe because every one
    of those modules is gated by a `live_*` marker whose fixture probes its
    service and skips when unreachable. An ungated module would turn a bare
    runner (no Postgres, no Redis, no Vault) from 'skips honestly' into 'the
    suite fails', and the honest reaction to that is to reintroduce a `-m`
    filter -- straight back to where this started."""
    live_markers = {marker for marker in _declared_markers() if marker.startswith("live_")}
    assert live_markers, "no `live_*` markers declared in pyproject.toml"
    modules = sorted(_INTEGRATION_DIR.glob("test_*.py"))
    assert modules, f"no test modules found under {_INTEGRATION_DIR}"
    ungated: list[str] = []
    for module in modules:
        text = module.read_text(encoding="utf-8")
        gates: set[str] = set()
        for assignment in _PYTESTMARK_BLOCK.findall(text):
            gates.update(_MARKER_APPLICATION.findall(assignment))
        if not gates & live_markers:
            ungated.append(module.name)
    assert not ungated, (
        f"these tests/integration/ modules declare no module-level "
        f"`pytestmark` naming a live_* marker: {ungated!r}. Without one they "
        "run unconditionally -- and fail, not skip, on any machine or runner "
        "where their service is absent."
    )
