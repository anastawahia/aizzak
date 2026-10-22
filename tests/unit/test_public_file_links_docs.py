"""Text checks for the public file links documentation (public-file-links US-5 and US-6).

Covers AC-5.6, AC-6.1, AC-6.2, AC-6.3 and the documentation half of AC-6.4.
Each phrase list is also shown failing on an edited copy of the text, so none
of the checks can pass vacuously (the style of ``test_cloudflare_tls_docs.py``).
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUIDE = _REPO_ROOT / "docs" / "deploy-server.md"
_PLAN = _REPO_ROOT / "docs" / "server-gpu-plan.md"
_NGINX = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_SMOKE = _REPO_ROOT / "deploy" / "smoke" / "stack_smoke.py"

_FIVE = [
    "/workspace-files/",
    "MINIO_PUBLIC_ENDPOINT",
    "SignatureDoesNotMatch",
    "`413`",
    "`502`",
    "`526`",
    "50 م.ب",
    "100 م.ب",
    "MINIO_BUCKET",
    "upload_url",
    "download_url",
    "SMOKE_PUBLIC_ROUNDTRIP=1",
]
_STALE_NGINX = (
    "Uploads go DIRECTLY to MinIO",
    "no request body of consequence passes through here",
)
_STALE_SMOKE = "On Compose the public address is the operator's"
_PLAN_62 = ["app-locations.conf", "$http_host", "بلا خريطة", "install.sh", "5.2"]
_RULE_1 = ["استثناءٌ ضيّق رابع", "/workspace-files/", "Q\u20117"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def has_all(text: str, phrases: list[str]) -> bool:
    return all(p in text for p in phrases)


def section_five(doc: str) -> str:
    start = doc.index("## 5) ")
    end = doc.find("\n## ", start + 1)
    return doc[start:] if end < 0 else doc[start:end]


def section_four(doc: str) -> str:
    start = doc.index("## 4) ")
    return doc[start : doc.index("\n## ", start + 1)]


def section_three(doc: str) -> str:
    start = doc.index("## 3) ")
    return doc[start : doc.index("\n## ", start + 1)]


def subsection(text: str, number: str) -> str:
    start = text.index(f"### {number} ")
    end = text.find("\n### ", start + 1)
    return text[start:] if end < 0 else text[start:end]


def security_rows(doc: str, label: str) -> list[str]:
    return [ln for ln in doc.splitlines() if ln.startswith(f"| {label} |")]


def plan_62(plan: str) -> str:
    start = plan.index("#### 6.2 ")
    return plan[start : plan.index("#### 6.3 ")]


def plan_rule_one(plan: str) -> str:
    start = plan.index("1. **المكدّس المحلّيّ لا يتغيّر")
    return plan[start : plan.index("2. **الموافقة قبل التنفيذ")]


def timeouts_said(doc: str) -> bool:
    text = subsection(section_four(doc), "4.7") + subsection(section_four(doc), "4.8")
    return "125" in text and "30 ثانية" in text and "100 ثانية" not in text


def stale_comments_gone(nginx: str, smoke: str) -> bool:
    return not any(p in nginx for p in _STALE_NGINX) and _STALE_SMOKE not in smoke


# --------------------------------------------------------------------------- #
# AC-5.6                                                                       #
# --------------------------------------------------------------------------- #
_GAPS = (
    ("ث\u201116", ["MinIO", "GET", "HEAD", "PUT", "الموجة 8"]),
    ("ث\u201117", ["Content-Type", "response-content-disposition", "staging"]),
)


def gap_problems(doc: str) -> list[str]:
    problems = []
    for label, needs in _GAPS:
        rows = security_rows(doc, label)
        if len(rows) != 1:
            problems.append(f"{label} must be one row, found {len(rows)}")
            continue
        if rows[0].count("|") != 5:
            problems.append(f"{label}: four columns exactly")
        problems += [f"{label}: missing {n}" for n in needs if n not in rows[0]]
    return problems


def test_the_two_new_gaps_are_rows_of_the_one_security_table() -> None:
    assert gap_problems(_read(_GUIDE)) == []


# --------------------------------------------------------------------------- #
# AC-6.1, AC-6.4                                                               #
# --------------------------------------------------------------------------- #
def test_the_guide_has_a_6_2_section_with_its_checks_and_faults() -> None:
    guide = _read(_GUIDE)
    five = section_five(guide)
    assert has_all(five, _FIVE), f"missing {[p for p in _FIVE if p not in five]}"
    bucket = five[five.index("MINIO_BUCKET") - 200 : five.index("MINIO_BUCKET") + 400]
    assert "يكسر" in subsection(five, "5.2") and "يكسر" in bucket
    assert guide.startswith('<div dir="rtl">') and guide.rstrip().endswith("</div>")
    assert "</div>" not in guide[: guide.rindex("</div>")]


def test_section_3_8_no_longer_lists_6_2_as_not_handled() -> None:
    three_eight = subsection(section_three(_read(_GUIDE)), "3.8")
    assert "روابط الملفّات العامّة (6.2)، والمنافذ" not in three_eight
    assert "(6.2) فيكتب لها `install.sh`" in three_eight
    assert "§5.3" in three_eight


def test_the_cloudflare_timeouts_are_125_and_30_seconds() -> None:
    assert timeouts_said(_read(_GUIDE))


# --------------------------------------------------------------------------- #
# AC-6.2                                                                       #
# --------------------------------------------------------------------------- #
def test_the_two_stale_comments_are_gone() -> None:
    assert stale_comments_gone(_read(_NGINX), _read(_SMOKE))


# --------------------------------------------------------------------------- #
# AC-6.3                                                                       #
# --------------------------------------------------------------------------- #
def test_the_plan_s_6_2_text_and_rule_1_say_what_was_built() -> None:
    plan = _read(_PLAN)
    assert has_all(plan_62(plan), _PLAN_62)
    assert has_all(plan_rule_one(plan), _RULE_1)


# --------------------------------------------------------------------------- #
# Each phrase check fails on an edited copy                                    #
# --------------------------------------------------------------------------- #
def test_each_phrase_check_fails_on_an_edited_copy() -> None:
    guide, plan = _read(_GUIDE), _read(_PLAN)
    nginx, smoke = _read(_NGINX), _read(_SMOKE)

    for phrase in _FIVE:
        assert not has_all(section_five(guide).replace(phrase, "x"), _FIVE), phrase
    assert not timeouts_said(guide.replace("125", "100", 1).replace("30 ثانية", "60 ثانية"))
    assert not timeouts_said(
        guide.replace("**125 ثانية**", "100 ثانية", 1).replace("125 ثانية", "100 ثانية")
    )

    for phrase in _PLAN_62:
        assert not has_all(plan_62(plan).replace(phrase, "x"), _PLAN_62), phrase
    for phrase in _RULE_1:
        assert not has_all(plan_rule_one(plan).replace(phrase, "x"), _RULE_1), phrase

    assert not stale_comments_gone(nginx + "\n# Uploads go DIRECTLY to MinIO\n", smoke)
    assert not stale_comments_gone(nginx, smoke + f"\n# {_STALE_SMOKE}\n")

    row = security_rows(guide, "ث\u201116")[0]
    assert gap_problems(guide.replace(row, row.replace("الموجة 8", "x")))
    assert gap_problems(guide + "\n" + row)
    assert gap_problems(guide.replace(row, row.replace("من يملك", "من | يملك", 1)))
    assert gap_problems(guide.replace(row, row.replace("MinIO", "x")))
