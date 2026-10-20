"""Text checks for the Cloudflare certificate documentation (cloudflare-tls US-6).

Covers AC-2.1, AC-4.4, AC-6.1 to AC-6.5 and AC-7.3.

Each phrase list is also shown failing on an edited copy of the text.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUIDE = _REPO_ROOT / "docs" / "deploy-server.md"
_PLAN = _REPO_ROOT / "docs" / "server-gpu-plan.md"
_SUMMARY = _REPO_ROOT / "docs" / "server-gpu-summary.html"
_RUNBOOK = _REPO_ROOT / "docs" / "design" / "08-local-runbook.md"
_SECRETS = _REPO_ROOT / "docs" / "design" / "05-rbac-config-secrets.md"
_NGINX = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section_four(doc: str) -> str:
    start = doc.index("## 4) ")
    end = doc.index("\n## ", start + 1)
    return doc[start:end]


def in_order(text: str, phrases: list[str]) -> bool:
    pos = -1
    for phrase in phrases:
        pos = text.find(phrase, pos + 1)
        if pos < 0:
            return False
    return True


def has_all(text: str, phrases: list[str]) -> bool:
    return all(p in text for p in phrases)


def plan_row(plan: str, step: str) -> str:
    return next(ln for ln in plan.splitlines() if ln.startswith(f"| **{step}** |"))


def plan_section(plan: str) -> str:
    start = plan.index("#### 6.1 ")
    return plan[start : plan.index("#### 6.2 ")]


_ORDER = [
    "openssl req -new",
    "Use my private key and CSR",
    "server.crt",
    "nginx -s reload",
    "-checkend 31536000",
    "up -d nginx-certs",
]


def test_the_certificate_steps_are_in_order() -> None:
    four = section_four(_read(_GUIDE))
    assert in_order(four, _ORDER)
    assert "أضعف" in four


def test_hsts_timing_and_rollback_are_said_in_the_guide_and_the_plan() -> None:
    four = section_four(_read(_GUIDE))
    assert has_all(four, ["HSTS", "DNS", "7.3", "max-age=0"])
    assert has_all(plan_section(_read(_PLAN)), ["HSTS", "DNS", "7.3"])


def test_the_plan_row_step_and_decision_4_say_cloudflare() -> None:
    plan = _read(_PLAN)
    row = plan_row(plan, "6.1")
    assert "⬜" not in row and "7.3" in row
    decision = plan_row(plan, "قرار 4")
    assert has_all(decision, ["Cloudflare", "2026\u201110\u201120"])
    assert has_all(plan_section(plan), ["Origin CA", "Full (strict)", "-checkend 31536000"])
    html = _read(_SUMMARY)
    before, after = html.split('<div class="step-id">\u0666\u066b\u0661</div>', 1)
    assert before.rsplit("<article", 1)[1].startswith(' class="step partial"')
    assert "Cloudflare" in after.split("</article>", 1)[0]


def test_the_guide_section_has_the_zone_table_limits_and_error_codes() -> None:
    guide = _read(_GUIDE)
    four = section_four(guide)
    codes = ["`521`", "`522`", "`524`", "`525`", "`526`"]
    assert has_all(
        four,
        [*codes, "100", "api.cloudflare.com/client/v4/ips", "ping", "CF-Connecting-IP"],
    )
    assert "| الإعداد | القيمة | النطاق | من ومتى |" in four
    assert "يبقى ما يولّده `nginx-certs` ذاتيّ التوقيع" not in guide


def test_the_three_new_gaps_are_rows_of_the_one_security_table() -> None:
    guide = _read(_GUIDE)
    rows = {
        n: [ln for ln in guide.splitlines() if ln.startswith(f"| ث\u2011{n} |")]
        for n in (13, 14, 15)
    }
    for n, found in rows.items():
        assert len(found) == 1, n
        assert found[0].count("|") == 5, n
    assert "?token=" in rows[13][0]
    assert has_all(rows[14][0], ["6.3", "Authenticated Origin Pulls"])
    assert has_all(rows[15][0], ["Vault", "server.key"])


def test_no_reference_to_the_missing_linux_server_guide_remains() -> None:
    files = [
        *(_REPO_ROOT / "docs" / "design").glob("*.md"),
        *(_REPO_ROOT / "docs").glob("*.md"),
        _NGINX,
        _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf",
        _REPO_ROOT / "deploy" / "nginx" / "gen-cert.sh",
        *(_REPO_ROOT / "deploy" / "release").glob("*"),
    ]
    needle = "deploy-linux-" + "server"
    offenders = [str(f) for f in files if f.is_file() and needle in _read(f)]
    assert offenders == []


def test_the_two_binding_documents_name_the_origin_certificate_and_its_key() -> None:
    runbook = _read(_RUNBOOK)
    step = runbook[runbook.index("### 3.2 شهادة TLS") :].split("### 3.2\u2011ب", 1)[0]
    assert has_all(step, ["Origin CA", "docs/deploy-server.md"])
    secrets = _read(_SECRETS)
    catalog = secrets[secrets.index("## 3) كتالوج الأسرار") :].split("### 3.1", 1)[0]
    assert has_all(catalog, ["server.key", "Vault"])


def test_the_524_limit_and_the_sse_advice_are_written() -> None:
    four = section_four(_read(_GUIDE))
    assert has_all(four, ["524", "runWorkflow", "SSE"])


def test_no_line_pairs_the_installer_with_a_key_mode() -> None:
    # the existing guard for .env permissions reads these documents line by line
    for path in (_GUIDE, _PLAN, _RUNBOOK, _SECRETS):
        for line in _read(path).splitlines():
            assert not (re.search(r"install\.sh", line) and "0600" in line), (path, line[:80])


def test_each_phrase_check_fails_on_an_edited_copy() -> None:
    four = section_four(_read(_GUIDE))
    assert in_order(four, _ORDER)
    swapped = four.replace("-checkend 31536000", "X").replace(
        "nginx -s reload", "-checkend 31536000"
    )
    assert not in_order(swapped, _ORDER)
    assert not has_all(four.replace("`524`", ""), ["`524`"])
    plan = _read(_PLAN)
    assert "⬜" in plan_row(plan, "6.1").replace("🟡", "⬜")
    pipes = next(ln for ln in _read(_GUIDE).splitlines() if ln.startswith("| ث\u201113 |"))
    assert (pipes + " | extra").count("|") != 5
