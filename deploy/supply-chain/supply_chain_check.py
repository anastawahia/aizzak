#!/usr/bin/env python3
"""Apply the licence and vulnerability policy to an SBOM and a pip-audit result.

PLT-19 (NFR-LIC-01, NFR-LIC-02, NFR-LIC-07, NFR-SEC-04). Called by
``deploy/supply-chain/scan.sh``; standard library only (``tomllib``, ``json``),
so it runs on the bare interpreter of a CI runner, before anything of the
project is installed.

It fails CLOSED. A component whose licence cannot be read, a text with no alias,
a package decision the release no longer matches, a vulnerability no valid
exception covers, an SBOM component the audit never saw, an audit that skipped a
package, a policy entry without an owner decision id: each is a violation or a
bad input, never a pass.

Exit code: 0 pass, 1 violation, 2 bad or missing input (the scan did not run).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# An exception to the vulnerability gate may live at most this long (INC0-Q-5).
MAX_EXCEPTION_DAYS = 90


class InputError(Exception):
    """An input is missing, unreadable or incomplete: the scan did not run (exit 2)."""


# ---------------------------------------------------------------------------
# Licence expressions (SPDX subset: AND, OR, WITH, parentheses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Id:
    name: str


@dataclass(frozen=True)
class With:
    base: str
    exception: str


@dataclass(frozen=True)
class And:
    items: tuple[Expr, ...]


@dataclass(frozen=True)
class Or:
    items: tuple[Expr, ...]


Expr = Id | With | And | Or

_TOKEN = re.compile(r"\(|\)|[^\s()]+")
_KEYWORDS = frozenset({"AND", "OR", "WITH"})


def parse_expression(text: str) -> Expr:
    """Parse an SPDX expression. WITH binds tighter than AND, AND tighter than OR."""
    tokens = _TOKEN.findall(text)
    if not tokens:
        raise ValueError("empty licence expression")
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        token = tokens[pos]
        pos += 1
        return token

    def ident() -> str:
        token = peek()
        if token is None or token in _KEYWORDS or token in "()":
            raise ValueError(f"expected a licence id in {text!r}")
        return take()

    def primary() -> Expr:
        if peek() == "(":
            take()
            inner = disjunction()
            if peek() != ")":
                raise ValueError(f"unbalanced parentheses in {text!r}")
            take()
            return inner
        return Id(ident())

    def with_clause() -> Expr:
        left = primary()
        if peek() == "WITH":
            take()
            if not isinstance(left, Id):
                raise ValueError(f"WITH needs a plain licence id on its left in {text!r}")
            return With(left.name, ident())
        return left

    def conjunction() -> Expr:
        items = [with_clause()]
        while peek() == "AND":
            take()
            items.append(with_clause())
        return items[0] if len(items) == 1 else And(tuple(items))

    def disjunction() -> Expr:
        items = [conjunction()]
        while peek() == "OR":
            take()
            items.append(conjunction())
        return items[0] if len(items) == 1 else Or(tuple(items))

    result = disjunction()
    if pos != len(tokens):
        raise ValueError(f"unexpected {tokens[pos]!r} in {text!r}")
    return result


def expression_text(expr: Expr) -> str:
    if isinstance(expr, Id):
        return expr.name
    if isinstance(expr, With):
        return f"{expr.base} WITH {expr.exception}"
    joiner = " AND " if isinstance(expr, And) else " OR "
    parts = [
        f"({expression_text(i)})" if isinstance(i, And | Or) else expression_text(i)
        for i in expr.items
    ]
    return joiner.join(parts)


def expression_ids(expr: Expr) -> frozenset[str]:
    if isinstance(expr, Id):
        return frozenset({expr.name})
    if isinstance(expr, With):
        return frozenset({expr.base})
    return frozenset().union(*(expression_ids(i) for i in expr.items))


def _unmet(expr: Expr, ok_ids: frozenset[str], exceptions: frozenset[str]) -> list[str]:
    """The identifiers that stop ``expr`` from being satisfied; empty means satisfied."""
    if isinstance(expr, Id):
        return [] if expr.name in ok_ids else [expr.name]
    if isinstance(expr, With):
        if expr.base in ok_ids and expr.exception in exceptions:
            return []
        return [f"{expr.base} WITH {expr.exception}"]
    if isinstance(expr, And):
        return [bad for item in expr.items for bad in _unmet(item, ok_ids, exceptions)]
    branches = [_unmet(item, ok_ids, exceptions) for item in expr.items]
    if any(not branch for branch in branches):
        return []
    return [bad for branch in branches for bad in branch]


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


# An owner decision: an SRS decision (`SRS D-32`) or an increment question of an LLD
# (`INC0-Q-10`). ASCII only: the policy is matched as text, so the U+2011 hyphen of
# the Arabic docs, or Arabic-Indic digits, would be another string.
_DECISION_ID = re.compile(r"SRS D-[0-9]+|INC[0-9]+-Q-[0-9]+")

# A version range of a [[package]] decision: comparisons on plain release numbers.
_VERSION_CLAUSE = re.compile(r"(==|!=|>=|<=|>|<)\s*([0-9]+(?:\.[0-9]+)*)")
_RELEASE = re.compile(r"[0-9]+(?:\.[0-9]+)*")

VersionRange = tuple[tuple[str, tuple[int, ...]], ...]


def _release(text: str) -> tuple[int, ...] | None:
    """``5.14.0`` -> ``(5, 14)``; None for anything but a plain release number."""
    if not _RELEASE.fullmatch(text):
        return None
    parts = [int(p) for p in text.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def parse_versions(text: str) -> VersionRange:
    """Parse ``>=4.2.0,<4.3`` or ``==5.14.0``. The range must be closed above.

    An open range (``>=0.18``) would pass every future release unreviewed, which is
    exactly what binding a decision to versions exists to stop.
    """
    clauses: list[tuple[str, tuple[int, ...]]] = []
    for raw in text.split(","):
        found = _VERSION_CLAUSE.fullmatch(raw.strip())
        release = _release(found.group(2)) if found else None
        if found is None or release is None:
            raise ValueError(f"not a version range: {text!r}")
        clauses.append((found.group(1), release))
    ops = {op for op, _ in clauses}
    if "==" not in ops and not ({">=", ">"} & ops and {"<", "<="} & ops):
        raise ValueError(f"version range {text!r} needs `==`, or a lower and an upper bound")
    return tuple(clauses)


def version_in(version: str, versions: VersionRange) -> bool:
    """False for a pre-, post-, dev or local release: it is not what was reviewed."""
    release = _release(version)
    if release is None:
        return False
    checks = {
        "==": release.__eq__,
        "!=": release.__ne__,
        ">=": release.__ge__,
        "<=": release.__le__,
        ">": release.__gt__,
        "<": release.__lt__,
    }
    return all(checks[op](bound) for op, bound in versions)


@dataclass(frozen=True)
class PackageDecision:
    expression: Expr  # the licence the owner chose for the package
    # What the owner decided ON. The decision holds only while the SBOM still says
    # this, for a version in this range; anything else is a stale decision.
    declared: tuple[str, ...]  # the declared licence entries, as the report shows them
    versions: VersionRange
    versions_text: str
    reason: str
    decision: str


@dataclass(frozen=True)
class VulnException:
    id: str
    package: str
    reason: str
    decision: str
    added: date
    expires: date


@dataclass(frozen=True)
class Policy:
    allowed: frozenset[str]  # NFR-LIC-01 SPDX ids (SRS D-01, D-26)
    approved: frozenset[str]  # extra ids the owner approved (SRS D-32)
    exceptions: frozenset[str]  # SPDX `WITH` exceptions; empty
    agpl: frozenset[str]  # flagged, not refused (NFR-LIC-06 item 4)
    font_licences: frozenset[str]  # {"OFL-1.1"} -- font FILES only (SRS D-26)
    font_extensions: tuple[str, ...]  # .ttf .otf .ttc .woff .woff2
    aliases: Mapping[str, str]  # metadata text -> SPDX expression
    packages: Mapping[str, PackageDecision]  # normalised name -> decision
    vulnerabilities: tuple[VulnException, ...]


def normalise_name(name: str) -> str:
    """PEP 503 normalisation, so ``PyYAML`` and ``pyyaml`` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _strings(table: Mapping[str, Any], key: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InputError(f"policy: {key!r} must be a list of strings")
    return value


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise InputError(f"policy: [{key}] must be a table")
    return value


def _entries(data: Mapping[str, Any], key: str, where: str) -> list[Mapping[str, Any]]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(e, dict) for e in value):
        raise InputError(f"policy: {where} must be a list of tables")
    return value


def _text(entry: Mapping[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"policy: {where} needs a non-empty `{key}`: {entry!r}")
    return value.strip()


def _decision(entry: Mapping[str, Any], where: str) -> str:
    """The owner decision an entry rests on. Free text is refused: it names no one."""
    value = entry.get("decision")
    if not isinstance(value, str) or not _DECISION_ID.fullmatch(value):
        raise InputError(
            f"policy: {where} needs `decision` naming an owner decision "
            f"(`SRS D-<n>` or `INC<n>-Q-<n>`, ASCII hyphens), got {value!r}"
        )
    return value


def _calendar_date(entry: Mapping[str, Any], key: str, where: str) -> date:
    value = entry.get(key)
    # tomllib gives `datetime` (a `date` subclass) for a time-of-day value: refused.
    if type(value) is not date:
        raise InputError(f"policy: {where} needs `{key}` as a TOML date (YYYY-MM-DD): {entry!r}")
    return value


def _package_decision(entry: Mapping[str, Any]) -> tuple[str, PackageDecision]:
    name = _text(entry, "name", "a [[package]] entry")
    where = f"[[package]] {name!r}"
    try:
        expression = parse_expression(_text(entry, "expression", where))
    except ValueError as exc:
        raise InputError(f"policy: {where}: {exc}") from exc
    declared = entry.get("declared")
    if not isinstance(declared, list) or not all(isinstance(d, str) for d in declared):
        raise InputError(
            f"policy: {where} needs `declared`, the licence entries the decision was made on"
        )
    versions_text = _text(entry, "versions", where)
    try:
        versions = parse_versions(versions_text)
    except ValueError as exc:
        raise InputError(f"policy: {where}: {exc}") from exc
    return normalise_name(name), PackageDecision(
        expression=expression,
        declared=tuple(declared),
        versions=versions,
        versions_text=versions_text,
        reason=_text(entry, "reason", where),
        decision=_decision(entry, where),
    )


def _vulnerability_exception(entry: Mapping[str, Any]) -> VulnException:
    """One dated exception. What does not depend on the day is checked here."""
    vid = _text(entry, "id", "a [[vulnerability]] entry")
    where = f"[[vulnerability]] {vid!r}"
    added = _calendar_date(entry, "added", where)
    expires = _calendar_date(entry, "expires", where)
    if expires < added:
        raise InputError(f"policy: {where} expires ({expires}) before it was added ({added})")
    if (expires - added).days > MAX_EXCEPTION_DAYS:
        raise InputError(f"policy: {where} lives longer than {MAX_EXCEPTION_DAYS} days")
    return VulnException(
        id=vid,
        package=normalise_name(_text(entry, "package", where)),
        reason=_text(entry, "reason", where),
        decision=_decision(entry, where),
        added=added,
        expires=expires,
    )


def load_policy(path: Path) -> Policy:
    """Read and validate the policy. An entry that widens it without a valid owner
    decision makes the whole policy invalid (exit 2), rather than being skipped."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InputError(f"policy {path}: {exc}") from exc
    if data.get("version") != 1:
        raise InputError("policy: unsupported or missing `version` (expected 1)")

    allow = _section(data, "allow")
    approved: set[str] = set()
    for entry in _entries(allow, "approved", "[allow].approved"):
        spdx = _text(entry, "id", "an [allow].approved entry")
        _decision(entry, f"[allow].approved {spdx!r}")
        approved.add(spdx)

    fonts = _section(data, "fonts")
    aliases = _section(data, "aliases")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()):
        raise InputError("policy: [aliases] must map text to text")

    packages: dict[str, PackageDecision] = {}
    for entry in _entries(data, "package", "[[package]]"):
        name, decision = _package_decision(entry)
        if name in packages:
            raise InputError(f"policy: two [[package]] decisions for {name!r}")
        packages[name] = decision

    vulnerabilities: list[VulnException] = []
    for entry in _entries(data, "vulnerability", "[[vulnerability]]"):
        exception = _vulnerability_exception(entry)
        if any((e.package, e.id) == (exception.package, exception.id) for e in vulnerabilities):
            raise InputError(f"policy: two exceptions for {exception.package} {exception.id}")
        vulnerabilities.append(exception)

    return Policy(
        allowed=frozenset(_strings(allow, "spdx")),
        approved=frozenset(approved),
        exceptions=frozenset(_strings(allow, "exceptions")),
        agpl=frozenset(_strings(_section(data, "flag"), "agpl")),
        font_licences=frozenset(_strings(fonts, "spdx")),
        font_extensions=tuple(_strings(fonts, "extensions")),
        aliases=dict(aliases),
        packages=packages,
        vulnerabilities=tuple(vulnerabilities),
    )


# ---------------------------------------------------------------------------
# Licences
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenceRow:
    name: str
    version: str
    licence: str  # the expression as evaluated, or the raw text that could not be read
    basis: str  # "allowed" | "approved" | "package decision (<id>)" | "font" | "violation"
    agpl: bool


@dataclass(frozen=True)
class LicenceReport:
    root_name: str
    root_licence: str
    rows: tuple[LicenceRow, ...]
    violations: tuple[str, ...]


_CLASSIFIER_PREFIX = "License :: OSI Approved :: "


def _licence_name(lic: Mapping[str, Any]) -> str | None:
    """The text of a named (non-SPDX) licence entry.

    With ``--gather-license-texts`` cyclonedx-py keeps the free-text `License:`
    field as the attached text of a named licence; the name is then only a label.
    It also attaches whole LICENSE files, base64-encoded: those are evidence, not
    a declaration, and are skipped.
    """
    text = lic.get("text")
    if isinstance(text, dict):
        if "encoding" in text:
            return None
        if isinstance(text.get("content"), str):
            return text["content"].strip()
    if isinstance(lic.get("name"), str):
        return lic["name"].strip().removeprefix(_CLASSIFIER_PREFIX)
    return None


def _licence_texts(component: Mapping[str, Any]) -> list[str]:
    """Licence entries of one component, as expression texts or raw metadata names."""
    texts: list[str] = []
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("expression"), str):
            texts.append(entry["expression"])
            continue
        lic = entry.get("license")
        if isinstance(lic, dict):
            if isinstance(lic.get("id"), str):
                texts.append(lic["id"])
            elif (name := _licence_name(lic)) is not None:
                texts.append(name)
    return texts


def component_expression(component: Mapping[str, Any], policy: Policy) -> Expr | None:
    """The licence of one component, or None when it cannot be read.

    An ``expression`` wins. Otherwise the SPDX ids, and only if there are none the
    names (a classifier repeats the id the SPDX entry already gives). Several
    entries are an AND, never an OR: an OR needs an owner decision in a
    ``[[package]]`` entry. A name with no alias is unreadable.
    """
    entries = component.get("licenses") or []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("expression"), str):
            return _parse_or_none(entry["expression"].strip())
    licences = [
        e["license"] for e in entries if isinstance(e, dict) and isinstance(e.get("license"), dict)
    ]
    ids = [Id(lic["id"]) for lic in licences if isinstance(lic.get("id"), str)]
    if ids:
        return ids[0] if len(ids) == 1 else And(tuple(ids))
    classifier_names = {
        str(lic["name"]).removeprefix(_CLASSIFIER_PREFIX).strip()
        for lic in licences
        if isinstance(lic.get("name"), str)
        and lic["name"].startswith(_CLASSIFIER_PREFIX)
        and "text" not in lic
    }
    # A classifier is structured evidence; free text is a prose label that often
    # restates it ("Dual License"). The text is read only when no classifier is.
    names = [n for lic in licences if "id" not in lic and (n := _licence_name(lic)) is not None]
    classifiers = [n for n in names if n in classifier_names]
    parts: list[Expr] = []
    for name in classifiers or names:
        parsed = _parse_or_none(policy.aliases.get(name, name))
        if parsed is None:
            return None
        parts.append(parsed)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else And(tuple(parts))


def _parse_or_none(text: str) -> Expr | None:
    try:
        return parse_expression(text)
    except ValueError:
        return None


def _is_font_file(component: Mapping[str, Any], policy: Policy) -> bool:
    name = str(component.get("name", "")).lower()
    return component.get("type") == "file" and name.endswith(policy.font_extensions)


def _decided(
    component: Mapping[str, Any], decision: PackageDecision, policy: Policy
) -> tuple[LicenceRow, str | None]:
    """A component the owner decided on, provided it is still what was decided on.

    The decision names the expression and is not re-checked against [allow] (SRS
    D-32: pyphen is LGPL-2.1-or-later, which [allow] lacks). But it holds only for
    the declared licence data and the versions it was made on: a release that
    declares something else, or lies outside the range, goes back to the owner.
    """
    name = str(component.get("name", ""))
    version = str(component.get("version", ""))
    declared = sorted(_licence_texts(component))
    stale: list[str] = []
    if not version_in(version, decision.versions):
        stale.append(f"version {version} is outside {decision.versions_text}")
    if declared != sorted(decision.declared):
        stale.append(
            f"it now declares {declared}, the decision was made on {sorted(decision.declared)}"
        )
    if stale:
        row = LicenceRow(
            name, version, "; ".join(declared) or "(none declared)", "violation", False
        )
        return row, (
            f"{name} {version}: the {decision.decision} decision is stale "
            f"({'; '.join(stale)}) -- it needs the owner"
        )
    agpl = bool(expression_ids(decision.expression) & policy.agpl)
    text = expression_text(decision.expression)
    return LicenceRow(name, version, text, f"package decision ({decision.decision})", agpl), None


def _check_component(component: Mapping[str, Any], policy: Policy) -> tuple[LicenceRow, str | None]:
    name = str(component.get("name", ""))
    version = str(component.get("version", ""))
    decision = policy.packages.get(normalise_name(name))
    if decision is not None:
        return _decided(component, decision, policy)

    expr_or_none = component_expression(component, policy)
    raw = "; ".join(_licence_texts(component)) or "(none declared)"
    if expr_or_none is None:
        row = LicenceRow(name, version, raw, "violation", False)
        return row, f"{name} {version}: licence cannot be read: {raw}"

    font = _is_font_file(component, policy)
    ok_ids = policy.allowed | policy.approved | (policy.font_licences if font else frozenset())
    unmet = _unmet(expr_or_none, ok_ids, policy.exceptions)
    text = expression_text(expr_or_none)
    ids = expression_ids(expr_or_none)
    agpl = bool(ids & policy.agpl)
    if unmet:
        row = LicenceRow(name, version, text, "violation", agpl)
        return row, f"{name} {version}: {text} -- not allowed: {', '.join(unmet)}"
    if font and ids & policy.font_licences:
        return LicenceRow(name, version, text, "font", agpl), None
    basis = "approved" if ids & (policy.approved - policy.allowed) else "allowed"
    return LicenceRow(name, version, text, basis, agpl), None


def check_licences(sbom: Mapping[str, Any], policy: Policy) -> LicenceReport:
    metadata = sbom.get("metadata")
    root = metadata.get("component") if isinstance(metadata, dict) else None
    components = sbom.get("components")
    if not isinstance(root, dict) or not isinstance(components, list) or not components:
        raise InputError("sbom: no root component or no components -- an empty SBOM proves nothing")

    root_name = str(root.get("name", ""))
    root_licence = "; ".join(_licence_texts(root)) or "(none declared)"

    rows: list[LicenceRow] = []
    violations: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            raise InputError("sbom: a component is not an object")
        row, violation = _check_component(component, policy)
        rows.append(row)
        if violation is not None:
            violations.append(violation)
    rows.sort(key=lambda r: (r.name.lower(), r.version))
    return LicenceReport(root_name, root_licence, tuple(rows), tuple(violations))


# ---------------------------------------------------------------------------
# Vulnerabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VulnRow:
    package: str
    version: str
    id: str
    aliases: tuple[str, ...]
    fix_versions: tuple[str, ...]
    status: str  # "excepted" | "VIOLATION" | "VIOLATION (exception invalid: <why>)"


@dataclass(frozen=True)
class ExceptionRow:
    id: str
    package: str
    decision: str
    added: date
    expires: date
    state: str  # "applied" | "not reported by this audit" | "expired on <date>"


@dataclass(frozen=True)
class VulnReport:
    audited: int
    rows: tuple[VulnRow, ...]
    violations: tuple[str, ...]
    exceptions: tuple[ExceptionRow, ...] = ()

    @property
    def excepted(self) -> int:
        return sum(1 for r in self.rows if r.status == "excepted")


@dataclass(frozen=True)
class CoverageReport:
    components: int
    missing: tuple[tuple[str, str, str], ...]  # (name, version, why) of each unaudited one
    violations: tuple[str, ...]


def _frozen_names(frozen: Sequence[str]) -> set[str]:
    names: set[str] = set()
    for raw in frozen:
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        names.add(normalise_name(re.split(r"==|\s@\s|\s", line, maxsplit=1)[0]))
    return names


def _exception_problem(exc: VulnException, today: date) -> str | None:
    """Why a (well-formed) exception does not cover today, or None when it does.

    The decision id, the dates and the 90-day span are checked by load_policy.
    """
    if exc.expires < today:
        return f"expired on {exc.expires}"
    return None


def check_vulnerabilities(
    audit: Mapping[str, Any], frozen: Sequence[str], policy: Policy, today: date
) -> VulnReport:
    for exc in policy.vulnerabilities:
        # load_policy caps `expires - added` at 90 days; this caps what is LEFT of
        # it, which a future `added` would otherwise stretch past 90 from today.
        if exc.added > today:
            raise InputError(
                f"policy: [[vulnerability]] {exc.id!r} is dated {exc.added}, after today "
                f"({today}); an exception cannot start in the future"
            )

    deps = _dependencies(audit)
    audited = _audited(deps)
    wanted = _frozen_names(frozen)
    if not wanted:
        raise InputError("freeze: no packages -- the runtime environment is empty")
    missing = sorted(wanted - audited)
    if missing:
        raise InputError(
            f"audit: {len(missing)} frozen package(s) were not audited: {', '.join(missing)}"
        )

    advisories: dict[tuple[str, str, str], tuple[set[str], set[str]]] = {}
    for dep in deps:
        if not isinstance(dep, dict) or "skip_reason" in dep:
            continue
        package = normalise_name(str(dep.get("name", "")))
        for vuln in dep.get("vulns") or []:
            # pip-audit may list one advisory once per source: merge them.
            key = (package, str(dep.get("version", "")), str(vuln.get("id", "")))
            aliases, fixes = advisories.setdefault(key, (set(), set()))
            aliases.update(str(a) for a in vuln.get("aliases") or [])
            fixes.update(str(f) for f in vuln.get("fix_versions") or [])

    rows: list[VulnRow] = []
    violations: list[str] = []
    applied: set[tuple[str, str]] = set()
    for (package, version, vid), (aliases, fixes) in sorted(advisories.items()):
        status, used = _vulnerability_status(package, {vid, *aliases}, policy, today)
        if used is not None:
            applied.add((used.package, used.id))
        rows.append(
            VulnRow(package, version, vid, tuple(sorted(aliases)), tuple(sorted(fixes)), status)
        )
        if status != "excepted":
            fixed = ", ".join(sorted(fixes)) if fixes else "NO FIXED VERSION"
            violations.append(f"{package} {version}: {vid} (fix: {fixed})")

    exceptions = tuple(
        ExceptionRow(
            e.id,
            e.package,
            e.decision,
            e.added,
            e.expires,
            "applied"
            if (e.package, e.id) in applied
            else _exception_problem(e, today) or "not reported by this audit",
        )
        for e in policy.vulnerabilities
    )
    return VulnReport(len(audited), tuple(rows), tuple(violations), exceptions)


def _vulnerability_status(
    package: str, ids: set[str], policy: Policy, today: date
) -> tuple[str, VulnException | None]:
    problems: list[str] = []
    for exc in policy.vulnerabilities:
        if exc.package != package or exc.id not in ids:
            continue
        problem = _exception_problem(exc, today)
        if problem is None:
            return "excepted", exc
        problems.append(f"{exc.id}: {problem}")
    if problems:
        return f"VIOLATION (exception invalid: {'; '.join(problems)})", None
    return "VIOLATION", None


def _dependencies(audit: Mapping[str, Any]) -> list[Any]:
    deps = audit.get("dependencies")
    if not isinstance(deps, list):
        raise InputError("audit: no `dependencies` list -- the vulnerability scan did not run")
    return deps


def _audited(deps: Sequence[Any]) -> set[str]:
    return {
        normalise_name(d["name"])
        for d in deps
        if isinstance(d, dict) and "skip_reason" not in d and isinstance(d.get("name"), str)
    }


def check_coverage(
    sbom: Mapping[str, Any], audit: Mapping[str, Any], frozen: Sequence[str]
) -> CoverageReport:
    """Every component of the SBOM must have been audited; each one that was not is
    named, with why, and is a violation.

    The SBOM describes the environment, the audit covers the freeze, and the two can
    drift apart: `pip` did (CR #2) -- in the SBOM, out of a `pip freeze` without
    `--all`, and so out of the audit, while the report said "No known vulnerability".
    """
    deps = _dependencies(audit)
    audited = _audited(deps)
    skipped = {
        normalise_name(str(d.get("name", ""))): str(d["skip_reason"])
        for d in deps
        if isinstance(d, dict) and "skip_reason" in d
    }
    frozen_names = _frozen_names(frozen)
    components = [c for c in sbom.get("components") or [] if isinstance(c, dict)]
    missing: list[tuple[str, str, str]] = []
    for component in components:
        name = str(component.get("name", ""))
        key = normalise_name(name)
        if key in audited:
            continue
        if key in skipped:
            why = f"pip-audit skipped it: {skipped[key]}"
        elif key not in frozen_names:
            why = "not in runtime-freeze.txt, so pip-audit never saw it"
        else:
            why = "frozen, but absent from audit.json"
        missing.append((name, str(component.get("version", "")), why))
    missing.sort()
    violations = tuple(f"{n} {v}: not audited -- {why}" for n, v, why in missing)
    return CoverageReport(len(components), tuple(missing), violations)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _failed(licences: LicenceReport, vulns: VulnReport, coverage: CoverageReport) -> bool:
    return bool(licences.violations or vulns.violations or coverage.violations)


def render(licences: LicenceReport, vulns: VulnReport, coverage: CoverageReport) -> str:
    out: list[str] = ["# Supply chain report", ""]
    verdict = "FAIL" if _failed(licences, vulns, coverage) else "PASS"
    out += [f"**Result: {verdict}**", ""]

    out += ["## Root component", ""]
    out.append(
        f"`{licences.root_name}` declares `{licences.root_licence}`; it is reported, not evaluated."
    )
    if licences.root_licence.strip().lower() == "proprietary":
        out.append(
            "Reminder (DEC-10, SRS D-24): the licence of the platform itself is still the "
            "owner's open decision."
        )
    out.append("")

    out += [f"## Licences ({len(licences.rows)} components)", ""]
    if licences.violations:
        out += [f"**{len(licences.violations)} violation(s):**", ""]
        out += [f"- {v}" for v in licences.violations]
        out.append("")
    agpl = [r for r in licences.rows if r.agpl]
    out += ["AGPL-flagged components (NFR-LIC-06 item 4, flagged and not refused):", ""]
    out += [f"- {r.name} {r.version}: {r.licence}" for r in agpl] or ["- none"]
    out.append("")
    out += ["| Component | Version | Licence | Basis |", "|---|---|---|---|"]
    out += [f"| {r.name} | {r.version} | {r.licence} | {r.basis} |" for r in licences.rows]
    out.append("")

    out += [f"## Known vulnerabilities ({vulns.audited} packages audited)", ""]
    if coverage.missing:
        out += [f"**{len(coverage.missing)} SBOM component(s) not audited:**", ""]
        out += [f"- {n} {v}: {why}" for n, v, why in coverage.missing]
    else:
        out.append(
            f"Not audited: none. Every SBOM component was audited "
            f"({coverage.components} of {coverage.components})."
        )
    out.append("")
    if vulns.violations:
        out += [f"**{len(vulns.violations)} unexcepted finding(s):**", ""]
        out += [f"- {v}" for v in vulns.violations]
        out.append("")
    if vulns.rows:
        out += [
            "| Package | Version | Advisory | Aliases | Fixed in | Status |",
            "|---|---|---|---|---|---|",
        ]
        out += [
            f"| {r.package} | {r.version} | {r.id} | {', '.join(r.aliases)} | "
            f"{', '.join(r.fix_versions) or '-'} | {r.status} |"
            for r in vulns.rows
        ]
    else:
        out.append("No known vulnerability.")
    out.append("")

    out += ["## Vulnerability exceptions (dated, owner-approved, at most 90 days)", ""]
    if vulns.exceptions:
        out += [
            "| Advisory | Package | Decision | Added | Expires | State |",
            "|---|---|---|---|---|---|",
        ]
        out += [
            f"| {e.id} | {e.package} | {e.decision} | {e.added} | {e.expires} | {e.state} |"
            for e in vulns.exceptions
        ]
    else:
        out.append("None in the policy.")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _read_json(path: Path, what: str) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{what} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"{what} {path}: not a JSON object")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--today", type=date.fromisoformat, default=None, help=argparse.SUPPRESS)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    try:
        policy = load_policy(args.policy)
        sbom = _read_json(args.sbom, "sbom")
        audit = _read_json(args.audit, "audit")
        try:
            frozen = args.freeze.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise InputError(f"freeze {args.freeze}: {exc}") from exc
        licences = check_licences(sbom, policy)
        vulns = check_vulnerabilities(audit, frozen, policy, args.today or date.today())
        coverage = check_coverage(sbom, audit, frozen)
    except (InputError, AttributeError, TypeError, KeyError) as exc:  # malformed shape: fail closed
        message = f"supply-chain: the scan did not run: {exc}"
        print(message, file=sys.stderr)
        with contextlib.suppress(OSError):
            args.report.write_text(
                f"# Supply chain report\n\n**Result: NOT RUN**\n\n{exc}\n", encoding="utf-8"
            )
        return 2

    args.report.write_text(render(licences, vulns, coverage), encoding="utf-8")
    failed = _failed(licences, vulns, coverage)
    print(
        f"supply-chain: {len(_frozen_names(frozen))} frozen; {len(licences.rows)} components, "
        f"{len(licences.violations)} licence violation(s), {len(coverage.missing)} not audited; "
        f"{vulns.audited} packages audited, {len(vulns.violations)} vulnerability finding(s), "
        f"{vulns.excepted} excepted -> {'FAIL' if failed else 'PASS'}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
