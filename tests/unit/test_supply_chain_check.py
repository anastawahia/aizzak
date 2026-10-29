"""The supply-chain checker and its policy (PLT-19, O3/O4/O6).

``deploy/supply-chain/supply_chain_check.py`` applies ``license-policy.toml`` to a
CycloneDX SBOM and a pip-audit result. It is the one place NFR-LIC-07 (licences)
and NFR-SEC-04 (known vulnerabilities) are enforced, so what it must REFUSE is
tested as hard as what it lets through: an unreadable licence, an unknown text, a
library under OFL, an advisory nothing valid covers, an audit that skipped a
package. Each of those is a violation or exit 2, never a pass.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_DIR = Path(__file__).resolve().parents[2] / "deploy" / "supply-chain"
_TODAY = date(2026, 10, 30)

# NFR-LIC-01 as SRS D-01 / D-26 state it. A second copy on purpose: widening
# [allow].spdx must fail here until a human edits the test too.
_NFR_LIC_01 = [
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "LicenseRef-BSD",
    "Apache-2.0",
    "MPL-2.0",
    "PSF-2.0",
    "Python-2.0",
    "ISC",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
]
# SRS D-32 (INC0-Q-4).
_D32_APPROVED = {"MIT-0", "MIT-CMU", "0BSD", "Zlib", "CC0-1.0", "LicenseRef-Public-Domain"}


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "supply_chain_check", _DIR / "supply_chain_check.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their annotations through it
    spec.loader.exec_module(module)
    return module


sc = _load_checker()

_POLICY = """
version = 1

[allow]
spdx = ["MIT", "BSD-3-Clause", "LicenseRef-BSD", "Apache-2.0", "AGPL-3.0-only"]
approved = [{ id = "Zlib", decision = "SRS D-32" }]
exceptions = ["Classpath-exception-2.0"]

[flag]
agpl = ["AGPL-3.0-only"]

[fonts]
spdx = ["OFL-1.1"]
extensions = [".ttf", ".woff2"]

[aliases]
"MIT License" = "MIT"
"BSD License" = "LicenseRef-BSD"
"Dual" = "AGPL-3.0-only OR LicenseRef-Commercial"

[[package]]
name = "Odd_Package"
expression = "LGPL-2.1-or-later"
reason = "chosen branch"
decision = "SRS D-32"

[[package]]
name = "undecided"
expression = "LGPL-2.1-or-later"
reason = "no decision id"
"""


def _policy(tmp_path: Path, extra: str = "") -> Any:
    path = tmp_path / "policy.toml"
    path.write_text(_POLICY + extra, encoding="utf-8")
    return sc.load_policy(path)


def _component(
    name: str, licenses: list[dict[str, Any]] | None, kind: str = "library"
) -> dict[str, Any]:
    component: dict[str, Any] = {"type": kind, "name": name, "version": "1.0"}
    if licenses is not None:
        component["licenses"] = licenses
    return component


def _sbom(*components: dict[str, Any], root_licence: str = "Proprietary") -> dict[str, Any]:
    root = {"name": "aizzak-platform", "type": "application", "version": "0.1.0"}
    if root_licence:
        root["licenses"] = [{"license": {"name": root_licence}}]
    return {"metadata": {"component": root}, "components": list(components)}


def _id(spdx: str) -> dict[str, Any]:
    return {"license": {"id": spdx}}


def _name(text: str) -> dict[str, Any]:
    return {"license": {"name": text}}


def _expr(text: str) -> dict[str, Any]:
    return {"expression": text}


def _violations(tmp_path: Path, *components: dict[str, Any]) -> tuple[str, ...]:
    return sc.check_licences(_sbom(*components), _policy(tmp_path)).violations


# ── the shipped policy ──────────────────────────────────────────────────────


def test_allowed_list_is_nfr_lic_01_and_the_approved_list_is_srs_d_32() -> None:
    policy = sc.load_policy(_DIR / "license-policy.toml")
    assert sorted(policy.allowed) == sorted(_NFR_LIC_01)
    assert policy.approved == _D32_APPROVED
    assert policy.exceptions == frozenset()
    assert policy.font_licences == {"OFL-1.1"}
    assert policy.agpl == {"AGPL-3.0-only", "AGPL-3.0-or-later"}


def test_every_widening_entry_of_the_shipped_policy_names_its_decision() -> None:
    raw = tomllib.loads((_DIR / "license-policy.toml").read_text(encoding="utf-8"))
    for entry in raw["allow"]["approved"]:
        assert entry["decision"] == "SRS D-32", entry
    for entry in raw.get("package", []):
        assert entry["decision"] == "SRS D-32", entry
        assert entry["reason"], entry


def test_an_approved_entry_without_a_decision_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(
        'version = 1\n[allow]\nspdx = ["MIT"]\napproved = [{ id = "Zlib" }]\n', encoding="utf-8"
    )
    with pytest.raises(sc.InputError):
        sc.load_policy(path)


def test_a_policy_with_the_wrong_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text('version = 2\n[allow]\nspdx = ["MIT"]\n', encoding="utf-8")
    with pytest.raises(sc.InputError):
        sc.load_policy(path)


# ── expressions ─────────────────────────────────────────────────────────────


def test_and_binds_tighter_than_or_and_with_tighter_than_and() -> None:
    assert sc.parse_expression("A OR B AND C") == sc.Or(
        (sc.Id("A"), sc.And((sc.Id("B"), sc.Id("C"))))
    )
    assert sc.parse_expression("(A OR B) AND C") == sc.And(
        (sc.Or((sc.Id("A"), sc.Id("B"))), sc.Id("C"))
    )
    assert sc.parse_expression("A WITH X AND B") == sc.And((sc.With("A", "X"), sc.Id("B")))


@pytest.mark.parametrize(
    "text", ["", "(", "A AND", "A B", "A OR OR B", "(A", "A)", "WITH X", "(A OR B) WITH X"]
)
def test_a_malformed_expression_is_an_error(text: str) -> None:
    with pytest.raises(ValueError):
        sc.parse_expression(text)


def test_expression_text_round_trips() -> None:
    text = "(A OR B) AND C WITH X"
    assert sc.expression_text(sc.parse_expression(text)) == "(A OR B) AND C WITH X"


# ── licences ────────────────────────────────────────────────────────────────


def test_allowed_and_approved_licences_pass(tmp_path: Path) -> None:
    assert not _violations(
        tmp_path,
        _component("a", [_id("MIT")]),
        _component("b", [_expr("BSD-3-Clause AND Zlib")]),
    )


def test_or_needs_one_allowed_branch_and_and_needs_all(tmp_path: Path) -> None:
    assert not _violations(tmp_path, _component("a", [_expr("GPL-3.0-only OR MIT")]))
    assert _violations(tmp_path, _component("b", [_expr("GPL-3.0-only AND MIT")]))
    assert _violations(tmp_path, _component("c", [_expr("GPL-3.0-only OR SSPL-1.0")]))


def test_with_needs_an_allowed_base_and_a_listed_exception(tmp_path: Path) -> None:
    assert not _violations(tmp_path, _component("a", [_expr("MIT WITH Classpath-exception-2.0")]))
    assert _violations(tmp_path, _component("b", [_expr("MIT WITH LLVM-exception")]))
    assert _violations(
        tmp_path, _component("c", [_expr("GPL-2.0-only WITH Classpath-exception-2.0")])
    )


def test_an_unknown_licence_text_is_a_violation(tmp_path: Path) -> None:
    violations = _violations(tmp_path, _component("a", [_name("Some Bespoke Terms")]))
    assert len(violations) == 1
    assert "a 1.0" in violations[0]


def test_a_component_with_no_licence_is_a_violation(tmp_path: Path) -> None:
    assert _violations(tmp_path, _component("a", None))
    assert _violations(tmp_path, _component("b", []))


def test_an_alias_maps_text_to_an_allowed_expression(tmp_path: Path) -> None:
    assert not _violations(tmp_path, _component("a", [_name("MIT License")]))
    assert not _violations(
        tmp_path, _component("b", [_name("License :: OSI Approved :: BSD License")])
    )


def test_an_alias_never_widens_the_allowed_list(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text(
        _POLICY.replace('"Dual" =', '"Odd" = "GPL-3.0-only"\n"Dual" ='), encoding="utf-8"
    )
    policy = sc.load_policy(path)
    report = sc.check_licences(_sbom(_component("a", [_name("Odd")])), policy)
    assert report.violations


def test_several_entries_without_an_expression_are_an_and(tmp_path: Path) -> None:
    assert not _violations(tmp_path, _component("a", [_id("MIT"), _id("Apache-2.0")]))
    assert _violations(tmp_path, _component("b", [_id("MIT"), _id("GPL-2.0-only")]))


def test_an_expression_wins_over_the_other_entries(tmp_path: Path) -> None:
    assert not _violations(tmp_path, _component("a", [_name("junk text"), _expr("MIT")]))


def test_spdx_ids_win_over_classifier_names(tmp_path: Path) -> None:
    component = _component("a", [_id("MIT"), _name("License :: OSI Approved :: Weird License")])
    assert not _violations(tmp_path, component)


def test_classifiers_win_over_free_text(tmp_path: Path) -> None:
    free_text = {
        "license": {"name": "declared license of 'a'", "text": {"content": "Dual License"}}
    }
    component = _component("a", [_name("License :: OSI Approved :: MIT License"), free_text])
    assert not _violations(tmp_path, component)


def test_free_text_is_read_when_there_is_no_classifier(tmp_path: Path) -> None:
    free_text = {"license": {"name": "declared license of 'a'", "text": {"content": "MIT License"}}}
    assert not _violations(tmp_path, _component("a", [free_text]))
    unknown = {"license": {"name": "declared license of 'b'", "text": {"content": "Dual License"}}}
    assert _violations(tmp_path, _component("b", [unknown]))


def test_an_attached_licence_file_is_evidence_not_a_declaration(tmp_path: Path) -> None:
    attached = {
        "license": {
            "name": "declared license file: LICENSE.txt",
            "text": {"content": "TUlU", "encoding": "base64"},
        }
    }
    assert not _violations(tmp_path, _component("a", [_id("MIT"), attached]))
    assert _violations(tmp_path, _component("b", [attached]))


def test_ofl_is_refused_for_a_library_and_accepted_for_a_font_file(tmp_path: Path) -> None:
    assert _violations(tmp_path, _component("lib", [_id("OFL-1.1")]))
    assert _violations(tmp_path, _component("data.bin", [_id("OFL-1.1")], kind="file"))
    assert not _violations(tmp_path, _component("Amiri-Regular.ttf", [_id("OFL-1.1")], kind="file"))
    assert not _violations(tmp_path, _component("x.WOFF2", [_id("OFL-1.1")], kind="file"))


def test_ofl_on_a_font_file_does_not_allow_other_unknown_ids(tmp_path: Path) -> None:
    assert _violations(
        tmp_path, _component("f.ttf", [_expr("OFL-1.1 AND GPL-3.0-only")], kind="file")
    )


def test_a_package_decision_overrides_the_metadata(tmp_path: Path) -> None:
    component = _component("odd-package", [_id("GPL-3.0-only")])
    report = sc.check_licences(_sbom(component), _policy(tmp_path))
    assert not report.violations
    assert report.rows[0].basis == "package decision"
    assert report.rows[0].licence == "LGPL-2.1-or-later"


def test_a_package_decision_without_a_decision_id_is_invalid(tmp_path: Path) -> None:
    ok = _component("undecided", [_id("MIT")])
    bad = _component("undecided", [_id("GPL-3.0-only")])
    assert not _violations(tmp_path, ok)
    violations = _violations(tmp_path, bad)
    assert violations
    assert "decision" in violations[0]


def test_agpl_components_are_flagged_not_refused(tmp_path: Path) -> None:
    component = _component("pymupdf", [_name("Dual")])
    report = sc.check_licences(_sbom(component), _policy(tmp_path))
    assert not report.violations
    assert [r.name for r in report.rows if r.agpl] == ["pymupdf"]


def test_the_report_names_agpl_components_and_the_root_licence(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    licences = sc.check_licences(_sbom(_component("pymupdf", [_name("Dual")])), policy)
    vulns = sc.check_vulnerabilities(_audit(("a", "1", [])), ["a==1"], policy, _TODAY)
    text = sc.render(licences, vulns)
    assert "AGPL-flagged components" in text
    assert "pymupdf" in text
    assert "`aizzak-platform` declares `Proprietary`" in text
    assert "DEC-10" in text
    assert "SRS D-24" in text
    assert "Result: PASS" in text


def test_the_root_component_is_reported_and_not_evaluated(tmp_path: Path) -> None:
    report = sc.check_licences(_sbom(_component("a", [_id("MIT")])), _policy(tmp_path))
    assert not report.violations
    assert report.root_licence == "Proprietary"


@pytest.mark.parametrize(
    "sbom",
    [{}, {"metadata": {}, "components": []}, _sbom(), {"metadata": {"component": {}}}],
)
def test_an_empty_or_rootless_sbom_is_not_a_pass(tmp_path: Path, sbom: dict[str, Any]) -> None:
    with pytest.raises(sc.InputError):
        sc.check_licences(sbom, _policy(tmp_path))


# ── vulnerabilities ─────────────────────────────────────────────────────────


def _audit(*deps: tuple[str, str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {"dependencies": [{"name": n, "version": v, "vulns": vs} for n, v, vs in deps]}


def _vuln(
    vid: str, fixes: list[str] | None = None, aliases: list[str] | None = None
) -> dict[str, Any]:
    return {"id": vid, "fix_versions": fixes or [], "aliases": aliases or []}


def _exception(**fields: str) -> str:
    base = {
        "id": "PYSEC-1",
        "package": "lib",
        "reason": "waiting for the fix",
        "decision": "owner 2026-10-30",
        "added": "2026-10-20",
        "expires": "2026-11-20",
    }
    base.update(fields)
    body = "\n".join(
        f"{k} = {v}" if k in {"added", "expires"} else f'{k} = "{v}"'
        for k, v in base.items()
        if v != ""
    )
    return f"\n[[vulnerability]]\n{body}\n"


def test_a_known_vulnerability_is_a_violation(tmp_path: Path) -> None:
    report = sc.check_vulnerabilities(
        _audit(("lib", "1.0", [_vuln("PYSEC-1", ["1.1"])])), ["lib==1.0"], _policy(tmp_path), _TODAY
    )
    assert len(report.violations) == 1
    assert "PYSEC-1" in report.violations[0]
    assert "1.1" in report.violations[0]


def test_a_vulnerability_without_a_fix_says_so(tmp_path: Path) -> None:
    report = sc.check_vulnerabilities(
        _audit(("lib", "1.0", [_vuln("PYSEC-1")])), ["lib==1.0"], _policy(tmp_path), _TODAY
    )
    assert "NO FIXED VERSION" in report.violations[0]


def test_a_clean_audit_passes(tmp_path: Path) -> None:
    report = sc.check_vulnerabilities(
        _audit(("lib", "1.0", [])), ["lib==1.0"], _policy(tmp_path), _TODAY
    )
    assert not report.violations
    assert report.audited == 1


def test_the_same_advisory_listed_twice_counts_once(tmp_path: Path) -> None:
    report = sc.check_vulnerabilities(
        _audit(("lib", "1.0", [_vuln("PYSEC-1", ["1.1"]), _vuln("PYSEC-1", ["1.1.0"])])),
        ["lib==1.0"],
        _policy(tmp_path),
        _TODAY,
    )
    assert len(report.rows) == 1
    assert report.rows[0].fix_versions == ("1.1", "1.1.0")


def _status(tmp_path: Path, extra: str, vuln: dict[str, Any] | None = None) -> tuple[str, int]:
    report = sc.check_vulnerabilities(
        _audit(("Lib", "1.0", [vuln or _vuln("PYSEC-1")])),
        ["Lib==1.0"],
        _policy(tmp_path, extra),
        _TODAY,
    )
    return report.rows[0].status, len(report.violations)


def test_a_dated_exception_with_a_decision_covers_the_advisory(tmp_path: Path) -> None:
    assert _status(tmp_path, _exception()) == ("excepted", 0)


def test_an_exception_matches_an_alias_too(tmp_path: Path) -> None:
    vuln = _vuln("PYSEC-9", aliases=["CVE-2026-1"])
    assert _status(tmp_path, _exception(id="CVE-2026-1"), vuln) == ("excepted", 0)


def test_an_exception_for_another_package_covers_nothing(tmp_path: Path) -> None:
    assert _status(tmp_path, _exception(package="other"))[1] == 1


def test_an_expired_exception_does_not_cover(tmp_path: Path) -> None:
    status, violations = _status(tmp_path, _exception(expires="2026-10-29"))
    assert violations == 1
    assert "expired" in status


def test_an_exception_without_a_decision_does_not_cover(tmp_path: Path) -> None:
    status, violations = _status(tmp_path, _exception(decision=""))
    assert violations == 1
    assert "decision" in status


def test_an_exception_that_lives_longer_than_90_days_does_not_cover(tmp_path: Path) -> None:
    expires = (_TODAY + timedelta(days=91)).isoformat()
    status, violations = _status(tmp_path, _exception(added=_TODAY.isoformat(), expires=expires))
    assert violations == 1
    assert "90" in status


def test_an_exception_older_than_90_days_does_not_cover(tmp_path: Path) -> None:
    added = (_TODAY - timedelta(days=91)).isoformat()
    expires = (_TODAY + timedelta(days=1)).isoformat()
    assert _status(tmp_path, _exception(added=added, expires=expires))[1] == 1


def test_an_exception_without_dates_does_not_cover(tmp_path: Path) -> None:
    assert _status(tmp_path, _exception(added="", expires=""))[1] == 1


def test_a_missing_dependencies_list_means_the_scan_did_not_run(tmp_path: Path) -> None:
    with pytest.raises(sc.InputError):
        sc.check_vulnerabilities({}, ["lib==1.0"], _policy(tmp_path), _TODAY)


def test_an_audit_with_fewer_packages_than_the_freeze_is_input_error(tmp_path: Path) -> None:
    with pytest.raises(sc.InputError, match="other"):
        sc.check_vulnerabilities(
            _audit(("lib", "1.0", [])), ["lib==1.0", "other==2"], _policy(tmp_path), _TODAY
        )


def test_a_skipped_package_is_not_an_audited_package(tmp_path: Path) -> None:
    audit = {"dependencies": [{"name": "lib", "skip_reason": "not on PyPI"}]}
    with pytest.raises(sc.InputError):
        sc.check_vulnerabilities(audit, ["lib==1.0"], _policy(tmp_path), _TODAY)


def test_an_empty_freeze_is_input_error(tmp_path: Path) -> None:
    with pytest.raises(sc.InputError):
        sc.check_vulnerabilities(
            _audit(("lib", "1.0", [])), ["", "# nothing"], _policy(tmp_path), _TODAY
        )


def test_package_names_are_compared_after_pep_503_normalisation(tmp_path: Path) -> None:
    report = sc.check_vulnerabilities(
        _audit(("Typing_Extensions", "4", [])), ["typing-extensions==4"], _policy(tmp_path), _TODAY
    )
    assert not report.violations


# ── the command line: 0 pass · 1 violation · 2 bad input ────────────────────


def _run(tmp_path: Path, sbom: Any, audit: Any, freeze: str, policy: str = _POLICY) -> int:
    files = {"sbom.json": sbom, "audit.json": audit}
    for name, content in files.items():
        if content is not None:
            (tmp_path / name).write_text(json.dumps(content), encoding="utf-8")
    (tmp_path / "freeze.txt").write_text(freeze, encoding="utf-8")
    (tmp_path / "policy.toml").write_text(policy, encoding="utf-8")
    return int(
        sc.main(
            [
                "--sbom", str(tmp_path / "sbom.json"),
                "--audit", str(tmp_path / "audit.json"),
                "--freeze", str(tmp_path / "freeze.txt"),
                "--policy", str(tmp_path / "policy.toml"),
                "--report", str(tmp_path / "report.md"),
                "--today", _TODAY.isoformat(),
            ]
        )
    )  # fmt: skip


def test_exit_0_on_a_clean_scan_and_the_report_is_written(tmp_path: Path) -> None:
    code = _run(tmp_path, _sbom(_component("a", [_id("MIT")])), _audit(("a", "1", [])), "a==1\n")
    assert code == 0
    assert "Result: PASS" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_exit_1_on_a_licence_violation(tmp_path: Path) -> None:
    code = _run(
        tmp_path, _sbom(_component("a", [_id("GPL-3.0-only")])), _audit(("a", "1", [])), "a==1\n"
    )
    assert code == 1
    assert "Result: FAIL" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_exit_1_on_a_vulnerability(tmp_path: Path) -> None:
    audit = _audit(("a", "1", [_vuln("PYSEC-1", ["2"])]))
    assert _run(tmp_path, _sbom(_component("a", [_id("MIT")])), audit, "a==1\n") == 1


def test_exit_2_when_the_audit_file_is_absent(tmp_path: Path) -> None:
    assert _run(tmp_path, _sbom(_component("a", [_id("MIT")])), None, "a==1\n") == 2
    assert "NOT RUN" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_exit_2_when_the_audit_has_fewer_packages_than_the_freeze(tmp_path: Path) -> None:
    sbom = _sbom(_component("a", [_id("MIT")]))
    assert _run(tmp_path, sbom, _audit(("a", "1", [])), "a==1\nb==2\n") == 2


def test_exit_2_on_a_malformed_audit(tmp_path: Path) -> None:
    sbom = _sbom(_component("a", [_id("MIT")]))
    assert _run(tmp_path, sbom, {"dependencies": [{"name": "a", "vulns": [None]}]}, "a==1\n") == 2


def test_exit_2_on_a_broken_policy(tmp_path: Path) -> None:
    sbom = _sbom(_component("a", [_id("MIT")]))
    assert _run(tmp_path, sbom, _audit(("a", "1", [])), "a==1\n", policy="version = [") == 2


def test_exit_2_when_the_sbom_has_no_components(tmp_path: Path) -> None:
    assert _run(tmp_path, _sbom(), _audit(("a", "1", [])), "a==1\n") == 2
