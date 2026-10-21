"""Text checks for the Cloudflare certificate documentation (cloudflare-tls US-6).

Covers AC-2.1, AC-4.4, AC-6.1 to AC-6.5 and AC-7.3.

Each phrase list is also shown failing on an edited copy of the text.
"""

from __future__ import annotations

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


# `-checkend` belongs to the check BEFORE activation (QA BUG-2), the `mv` to server.crt and the
# reload are the activation, `up -d nginx-certs` is the rollback.
_ORDER = [
    "openssl req -new",
    "Use my private key and CSR",
    "-checkend 31536000",
    "origin.crt deploy/nginx/certs/server.crt",
    "nginx -s reload",
    "up -d nginx-certs",
]
_CODES = ["`521`", "`522`", "`524`", "`525`", "`526`"]
_LIMITS = [*_CODES, "100 م.ب", "api.cloudflare.com/client/v4/ips", "`ping`", "CF-Connecting-IP"]
_HSTS = ["HSTS", "سجلّ DNS", "7.3", "max-age=0"]


def subsection(four: str, number: str) -> str:
    start = four.index(f"### {number} ")
    end = four.find("\n### ", start + 1)
    return four[start:] if end < 0 else four[start:end]


def hsts_said(four: str) -> bool:
    return has_all(subsection(four, "4.6"), _HSTS)


def plan_row_done(row: str) -> bool:
    return "⬜" not in row and "7.3" in row


def gap_row_ok(row: str) -> bool:
    return row.count("|") == 5


def test_the_certificate_steps_are_in_order() -> None:
    four = section_four(_read(_GUIDE))
    assert in_order(four, _ORDER)
    assert "أضعف" in four


def test_hsts_timing_and_rollback_are_said_in_the_guide_and_the_plan() -> None:
    four = section_four(_read(_GUIDE))
    assert hsts_said(four)
    assert has_all(plan_section(_read(_PLAN)), ["HSTS", "DNS", "7.3"])


def test_the_plan_row_step_and_decision_4_say_cloudflare() -> None:
    plan = _read(_PLAN)
    row = plan_row(plan, "6.1")
    assert plan_row_done(row)
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
    assert has_all(four, _LIMITS)
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
        assert gap_row_ok(found[0]), n
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


def test_each_phrase_check_fails_on_an_edited_copy() -> None:
    four = section_four(_read(_GUIDE))
    assert in_order(four, _ORDER)
    # activation before the check, as it used to be
    swapped = four.replace("-checkend 31536000", "X").replace(
        "nginx -s reload", "-checkend 31536000"
    )
    assert not in_order(swapped, _ORDER)
    # every phrase of the order and of the limits list is needed: the real check fails without it
    for phrase in _ORDER:
        assert not in_order(four.replace(phrase, ""), _ORDER), phrase
    for phrase in _LIMITS:
        assert not has_all(four.replace(phrase, ""), _LIMITS), phrase
    # the HSTS facts are read from 4.6 alone: removing one phrase there fails the check
    assert hsts_said(four)
    for phrase in _HSTS:
        cut = four.replace(subsection(four, "4.6"), subsection(four, "4.6").replace(phrase, ""))
        assert not hsts_said(cut), phrase
    # the plan row and the gap rows
    row = plan_row(_read(_PLAN), "6.1")
    assert plan_row_done(row) and not plan_row_done(row + " ⬜")
    gap = next(ln for ln in _read(_GUIDE).splitlines() if ln.startswith("| ث\u201113 |"))
    assert gap_row_ok(gap) and not gap_row_ok(gap + " | extra")


_D = "deploy/nginx/certs/"
_RM_PREV = f"rm -f {_D}server.crt.prev {_D}server.key.prev"


def activation_step(four: str) -> str:
    start = four.index("5. التفعيل")
    return four[start : four.index("\n6. ", start)]


def activation_is_safe(step: str) -> bool:
    """Both working files are copied to .prev before the first mv, and the restore sets the new
    pair aside (origin.*) before putting the .prev files back."""
    first_mv = step.find("sudo mv -f")
    copies = [f"cp -p {_D}server.{k} {_D}server.{k}.prev" for k in ("crt", "key")]
    if first_mv < 0 or not all(0 <= step.find(c) < first_mv for c in copies):
        return False
    restore = step[step.find("nginx -t` فلا") :]
    aside = [f"mv -f {_D}server.{k} {_D}origin.{k}" for k in ("crt", "key")]
    back = [f"mv -f {_D}server.{k}.prev {_D}server.{k}" for k in ("crt", "key")]
    spots = [restore.find(x) for x in (*aside, *back)]
    return all(x >= 0 for x in spots) and max(spots[:2]) < min(spots[2:])


def test_activation_keeps_the_working_pair_and_restores_it_without_losing_the_new_one() -> None:
    four = section_four(_read(_GUIDE))
    step = activation_step(four)
    assert activation_is_safe(step)
    # the two mutations QA reported surviving, and the overwrite the reviewer measured
    no_key_copy = step.replace(f"cp -p {_D}server.key {_D}server.key.prev", "true")
    assert not activation_is_safe(no_key_copy)
    no_key_restore = step.replace(f"mv -f {_D}server.key.prev {_D}server.key", "true")
    assert not activation_is_safe(no_key_restore)
    no_crt_restore = step.replace(f"mv -f {_D}server.crt.prev {_D}server.crt", "true")
    assert not activation_is_safe(no_crt_restore)
    no_aside = step.replace(f"mv -f {_D}server.key {_D}origin.key", "true")
    assert not activation_is_safe(no_aside)


def test_the_previous_pair_is_deleted_after_the_check_and_after_the_revocation() -> None:
    four = section_four(_read(_GUIDE))
    install = four[four.index("### 4.2 ") : four.index("### 4.3 ")]
    rotation = four[four.index("### 4.3 ") : four.index("### 4.4 ")]
    # 4.2: after the external check (step 6); 4.3: after the revocation (step 5) and the probe
    assert install.index("\n6. ") < install.index(_RM_PREV)
    assert rotation.index("\n5. ") < rotation.index("\n6. ") < rotation.index(_RM_PREV)
    assert _RM_PREV not in install[: install.index("\n6. ")]
    assert _RM_PREV not in four.replace(_RM_PREV, "", 2)
