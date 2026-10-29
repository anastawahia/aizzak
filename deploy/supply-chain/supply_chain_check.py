#!/usr/bin/env python3
"""Apply the licence and vulnerability policy to an SBOM and a pip-audit result.

PLT-19 (NFR-LIC-01, NFR-LIC-02, NFR-LIC-07, NFR-SEC-04). Called by
``deploy/supply-chain/scan.sh``; standard library only (``tomllib``, ``json``),
so it runs on the bare interpreter of a CI runner, before anything of the
project is installed.

It fails CLOSED. A component whose licence cannot be read, a text with no alias,
a vulnerability no valid exception covers, an audit that skipped a package: each
is a violation or a missing input, never a pass.

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


@dataclass(frozen=True)
class PackageDecision:
    expression: str
    reason: str
    decision: str


@dataclass(frozen=True)
class VulnException:
    id: str
    package: str
    reason: str
    decision: str
    added: date | None
    expires: date | None


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


def _date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def load_policy(path: Path) -> Policy:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InputError(f"policy {path}: {exc}") from exc
    if data.get("version") != 1:
        raise InputError("policy: unsupported or missing `version` (expected 1)")

    allow = _section(data, "allow")
    approved: set[str] = set()
    for entry in allow.get("approved", []):
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("decision"), str)
            and entry["decision"].strip()
        ):
            raise InputError(
                f"policy: every [allow].approved entry needs `id` and `decision`: {entry!r}"
            )
        approved.add(entry["id"])

    fonts = _section(data, "fonts")
    aliases = _section(data, "aliases")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()):
        raise InputError("policy: [aliases] must map text to text")

    packages: dict[str, PackageDecision] = {}
    for entry in data.get("package", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise InputError(f"policy: a [[package]] entry needs a `name`: {entry!r}")
        packages[normalise_name(entry["name"])] = PackageDecision(
            expression=str(entry.get("expression", "")),
            reason=str(entry.get("reason", "")),
            decision=str(entry.get("decision", "")),
        )

    vulnerabilities: list[VulnException] = []
    for entry in data.get("vulnerability", []):
        if not isinstance(entry, dict):
            raise InputError(f"policy: a [[vulnerability]] entry must be a table: {entry!r}")
        vulnerabilities.append(
            VulnException(
                id=str(entry.get("id", "")),
                package=normalise_name(str(entry.get("package", ""))),
                reason=str(entry.get("reason", "")),
                decision=str(entry.get("decision", "")),
                added=_date(entry.get("added")),
                expires=_date(entry.get("expires")),
            )
        )

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
    basis: str  # "allowed" | "approved" | "package decision" | "font" | "violation"
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


def _check_component(component: Mapping[str, Any], policy: Policy) -> tuple[LicenceRow, str | None]:
    name = str(component.get("name", ""))
    version = str(component.get("version", ""))
    decision = policy.packages.get(normalise_name(name))

    if decision is not None and decision.decision.strip():
        # The owner's decision names the expression; it is not re-checked against
        # [allow] (SRS D-32: pyphen is LGPL-2.1-or-later, which [allow] lacks).
        try:
            expr = parse_expression(decision.expression)
        except ValueError:
            row = LicenceRow(name, version, decision.expression, "violation", False)
            return row, f"{name}: the package decision has an unreadable expression"
        agpl = bool(expression_ids(expr) & policy.agpl)
        return LicenceRow(name, version, expression_text(expr), "package decision", agpl), None

    expr_or_none = component_expression(component, policy)
    raw = "; ".join(_licence_texts(component)) or "(none declared)"
    note = ""
    if decision is not None:
        note = " (the [[package]] decision has no `decision` id, so it is not valid)"
    if expr_or_none is None:
        row = LicenceRow(name, version, raw, "violation", False)
        return row, f"{name} {version}: licence cannot be read: {raw}{note}"

    font = _is_font_file(component, policy)
    ok_ids = policy.allowed | policy.approved | (policy.font_licences if font else frozenset())
    unmet = _unmet(expr_or_none, ok_ids, policy.exceptions)
    text = expression_text(expr_or_none)
    ids = expression_ids(expr_or_none)
    agpl = bool(ids & policy.agpl)
    if unmet:
        row = LicenceRow(name, version, text, "violation", agpl)
        return row, f"{name} {version}: {text} -- not allowed: {', '.join(unmet)}{note}"
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
class VulnReport:
    audited: int
    rows: tuple[VulnRow, ...]
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
    """Why an exception is not valid, or None when it is."""
    if not exc.decision.strip():
        return "no `decision`"
    if exc.expires is None or exc.added is None:
        return "`added` and `expires` are required"
    if exc.expires < today:
        return f"expired on {exc.expires}"
    if (exc.expires - exc.added).days > MAX_EXCEPTION_DAYS:
        return f"lives longer than {MAX_EXCEPTION_DAYS} days"
    if (today - exc.added).days > MAX_EXCEPTION_DAYS:
        return f"older than {MAX_EXCEPTION_DAYS} days"
    return None


def check_vulnerabilities(
    audit: Mapping[str, Any], frozen: Sequence[str], policy: Policy, today: date
) -> VulnReport:
    deps = audit.get("dependencies")
    if not isinstance(deps, list):
        raise InputError("audit: no `dependencies` list -- the vulnerability scan did not run")

    audited: set[str] = set()
    for dep in deps:
        if isinstance(dep, dict) and "skip_reason" not in dep and isinstance(dep.get("name"), str):
            audited.add(normalise_name(dep["name"]))
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
    for (package, version, vid), (aliases, fixes) in sorted(advisories.items()):
        status = _vulnerability_status(package, {vid, *aliases}, policy, today)
        rows.append(
            VulnRow(package, version, vid, tuple(sorted(aliases)), tuple(sorted(fixes)), status)
        )
        if status != "excepted":
            fixed = ", ".join(sorted(fixes)) if fixes else "NO FIXED VERSION"
            violations.append(f"{package} {version}: {vid} (fix: {fixed})")
    return VulnReport(len(audited), tuple(rows), tuple(violations))


def _vulnerability_status(package: str, ids: set[str], policy: Policy, today: date) -> str:
    problems: list[str] = []
    for exc in policy.vulnerabilities:
        if exc.package != package or exc.id not in ids:
            continue
        problem = _exception_problem(exc, today)
        if problem is None:
            return "excepted"
        problems.append(f"{exc.id}: {problem}")
    if problems:
        return f"VIOLATION (exception invalid: {'; '.join(problems)})"
    return "VIOLATION"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render(licences: LicenceReport, vulns: VulnReport) -> str:
    out: list[str] = ["# Supply chain report", ""]
    verdict = "FAIL" if licences.violations or vulns.violations else "PASS"
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
    except (InputError, AttributeError, TypeError, KeyError) as exc:  # malformed shape: fail closed
        message = f"supply-chain: the scan did not run: {exc}"
        print(message, file=sys.stderr)
        with contextlib.suppress(OSError):
            args.report.write_text(
                f"# Supply chain report\n\n**Result: NOT RUN**\n\n{exc}\n", encoding="utf-8"
            )
        return 2

    report = render(licences, vulns)
    args.report.write_text(report, encoding="utf-8")
    failed = bool(licences.violations or vulns.violations)
    print(
        f"supply-chain: {len(licences.rows)} components, {len(licences.violations)} licence "
        f"violation(s); {vulns.audited} packages audited, {len(vulns.violations)} "
        f"vulnerability finding(s) -> {'FAIL' if failed else 'PASS'}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
