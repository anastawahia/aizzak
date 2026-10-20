"""The documents say what install-bundle built (US-8, AC-7.5, AC-8.1 ... AC-8.4).

These are text checks. Each phrase list is shown failing in both directions on an edited copy, so a
guard that stopped looking would be noticed (monitoring-profile/retro.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "deploy" / "release" / "install.sh"
DEPLOY_SERVER = REPO / "docs" / "deploy-server.md"
PLAN = REPO / "docs" / "server-gpu-plan.md"
SUMMARY = REPO / "docs" / "server-gpu-summary.html"

VAULT_DOCS = (
    REPO / "docs" / "design" / "08-local-runbook.md",
    REPO / "docs" / "quickstart.md",
    REPO / "docs" / "stack-commands.md",
)
KEY_DOCS = (
    REPO / "docs" / "design" / "05-rbac-config-secrets.md",
    *VAULT_DOCS,
    REPO / "docker-compose.yml",
    REPO / ".env.example",
)
FORBIDDEN_SENTENCES = (
    "لا يُكتب في `.env` أبداً",
    "لا من `.env`",
    "يُحقن خارج الملفّ",
    "NEVER put this in .env",
    "must NEVER appear here",
)
_SECRET_SHAPES = re.compile(
    r"dckr_pat_|hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|hvs\.[A-Za-z0-9]{10,}"
)


# The two commands that stopped working when Vault left dev mode: secret_id and role_id.
OLD_VAULT_COMMANDS = r"exec\s+vault\s+vault\s+(write\s+-f\s+-field=secret_id|read\s+-field=role_id)"
FIVE, TWO, DOT = "\u0665", "\u0662", "\u066b"
ONE = "\u0661"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def mint_script() -> str:
    found = re.search(r"readonly MINT_SCRIPT='([^\n]*)'\n", text(INSTALL))
    assert found is not None
    return found.group(1)


# ── AC-7.5: the exit codes in the header and in the document ─────────────────


def header_codes(script: str) -> list[int]:
    lines = script.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("# Exit codes"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("set -euo pipefail"))
    codes = []
    for line in lines[start + 1 : end]:
        found = re.match(r"^#\s+(\d{1,2})\s{2}\S", line)
        if found:
            codes.append(int(found.group(1)))
    return codes


def section(doc: str, number: str) -> str:
    """The text of '## <number>) ...' up to the next '## '."""
    head = re.search(rf"^## {re.escape(number)}\) .*$", doc, re.M)
    assert head is not None, number
    rest = doc[head.end() :]
    following = re.search(r"^## ", rest, re.M)
    return rest[: following.start()] if following else rest


def doc_codes(body: str) -> list[int]:
    """Rows of the table under '### 3.5' only."""
    table = body.split("### 3.5", 1)[1].split("\n### ", 1)[0]
    return [int(m.group(1)) for m in re.finditer(r"^\|\s*(\d{1,2})\s*\|", table, re.M)]


def test_the_header_and_the_document_list_the_same_exit_codes() -> None:
    expected = [0, 2, 3, 4, 10, 11, 12, 13, 14, 20, 30, 40, 41, 50, 51, 60, 61, 70, 71]
    assert header_codes(text(INSTALL)) == expected
    assert doc_codes(section(text(DEPLOY_SERVER), "3")) == expected
    assert "Exit 1 is an unexpected error" in text(INSTALL)


def test_the_comparison_fails_when_either_side_loses_or_gains_a_code() -> None:
    script = text(INSTALL)
    body = section(text(DEPLOY_SERVER), "3")
    base = header_codes(script)
    dropped = re.sub(r"(?m)^#\s+61\s{2}.*\n", "", script)
    assert header_codes(dropped) != base
    added = script.replace("#   71  stack_smoke", "#   72  invented\n#   71  stack_smoke", 1)
    assert header_codes(added) != base
    assert doc_codes(re.sub(r"(?m)^\| 61 \|.*\n", "", body)) != doc_codes(body)
    assert doc_codes(body.replace("| 71 |", "| 71 |\n| 72 |", 1)) != doc_codes(body)


def test_every_exit_the_script_takes_is_in_the_table() -> None:
    table = set(header_codes(text(INSTALL))) | {1}
    used = {int(m.group(1)) for m in re.finditer(r"\bdie (\d{1,2})\b", text(INSTALL))}
    used |= {int(m.group(1)) for m in re.finditer(r"return (7[01])\b", text(INSTALL))}
    assert used <= table, sorted(used - table)


# ── AC-8.1: the Vault commands ───────────────────────────────────────────────


@pytest.mark.parametrize("doc", VAULT_DOCS, ids=lambda p: p.name)
def test_the_broken_vault_commands_are_gone_and_the_working_one_is_there(doc: Path) -> None:
    body = text(doc)
    assert not re.search(OLD_VAULT_COMMANDS, body)
    assert mint_script() in body, f"{doc.name} does not carry the exact command install.sh runs"
    role = mint_script().replace(
        "vault write -f -field=secret_id auth/approle/role/app/secret-id",
        "vault read -field=role_id auth/approle/role/app/role-id",
    )
    assert role in body, f"{doc.name} reads role_id with the old command"


def test_the_mint_command_reads_the_token_inside_the_container() -> None:
    command = mint_script()
    assert "VAULT_ADDR=http://127.0.0.1:8200" in command
    assert "/vault/init/init.json" in command
    assert not re.search(r"hvs\.", command)
    broken = (
        "docker compose exec vault vault write -f -field=secret_id auth/approle/role/app/secret-id"
    )
    assert re.search(r"exec\s+vault\s+vault\s+(write|read)\b", broken)


# ── AC-8.2: where the key lives ──────────────────────────────────────────────


def key_policy_problems(body: str) -> list[str]:
    """What is wrong with a document's statement about where VAULT_SECRET_ID lives: a sentence
    that forbids .env, or no mention of install.sh writing it."""
    problems = [
        f"forbidden sentence: {sentence}" for sentence in FORBIDDEN_SENTENCES if sentence in body
    ]
    if not re.search(
        r"VAULT_SECRET_ID[\s\S]{0,400}install\.sh|install\.sh[\s\S]{0,400}VAULT_SECRET_ID", body
    ):
        problems.append("does not say install.sh writes VAULT_SECRET_ID")
    return problems


@pytest.mark.parametrize("doc", KEY_DOCS, ids=lambda p: p.name)
def test_no_document_still_says_the_key_must_not_be_in_env(doc: Path) -> None:
    assert key_policy_problems(text(doc)) == []


@pytest.mark.parametrize("sentence", FORBIDDEN_SENTENCES)
def test_the_key_policy_guard_flags_each_forbidden_sentence(sentence: str) -> None:
    """The real guard, run on a fixture document that is good except for one sentence."""
    good = "VAULT_SECRET_ID is written by install.sh into .env on a server."
    assert key_policy_problems(good) == []
    assert key_policy_problems(f"{good}\n{sentence}") == [f"forbidden sentence: {sentence}"]


def test_the_key_policy_guard_flags_a_document_that_never_mentions_install_sh() -> None:
    assert key_policy_problems("VAULT_SECRET_ID lives in .env.") == [
        "does not say install.sh writes VAULT_SECRET_ID"
    ]


def test_no_document_says_install_sh_sets_env_to_0600() -> None:
    for doc in (*KEY_DOCS, DEPLOY_SERVER, PLAN):
        for line in text(doc).splitlines():
            assert not re.search(r"install\.sh[^\n]*0600", line), (doc.name, line[:120])
    assert re.search(r"install\.sh[^\n]*0600", "install.sh sets 0600")


@pytest.mark.parametrize("doc", VAULT_DOCS, ids=lambda p: p.name)
def test_the_shell_passing_examples_stay(doc: Path) -> None:
    assert "VAULT_SECRET_ID='<" in text(doc)


# ── AC-8.3: the install section ──────────────────────────────────────────────


def test_the_install_section_covers_requirements_options_and_the_env_mode() -> None:
    body = section(text(DEPLOY_SERVER), "3")
    for needle in (
        "sudo",
        "docker login",
        "python3",
        "--firebase-project",
        "aizzak-agent",
        "umask",
        "chmod 600 .env",
        "sha256sum",
        "sudo tar -xzf",
        "docker compose up",
    ):
        assert needle in body, needle
    assert not _SECRET_SHAPES.search(text(DEPLOY_SERVER))


def test_the_two_new_gaps_are_rows_of_the_one_security_table() -> None:
    lines = text(DEPLOY_SERVER).splitlines()
    for number in ("ث\u201110", "ث\u201111", "ث\u201112"):
        rows = [ln for ln in lines if ln.startswith(f"| {number} |")]
        assert len(rows) == 1, number
        assert rows[0].count("|") == 5, number  # four columns, like every other row
    ten = next(ln for ln in lines if ln.startswith("| ث\u201110 |"))
    eleven = next(ln for ln in lines if ln.startswith("| ث\u201111 |"))
    assert "VAULT_SECRET_ID" in ten and ".env" in ten
    assert "0600" in eleven or "chmod 600" in eleven


def test_the_document_still_opens_and_closes_its_rtl_wrapper() -> None:
    body = text(DEPLOY_SERVER)
    assert body.startswith('<div dir="rtl">') and body.rstrip().endswith("</div>")


# ── AC-8.4: the plan and its summary ─────────────────────────────────────────


def plan_row(step: str) -> str:
    found = re.search(rf"^\| \*\*{re.escape(step)}\*\* .*$", text(PLAN), re.M)
    assert found is not None, step
    return found.group(0)


def test_the_plan_counts_27_paths_and_25_in_the_bundle() -> None:
    body = text(PLAN)
    assert "27 مساراً تحت `deploy/` مربوطةٌ في Compose، 25 منها في الحزمة" in body
    assert "24 مساراً" not in body


def test_the_plan_names_what_was_built() -> None:
    body = text(PLAN)
    five_one = body.split("#### 5.1 ", 1)[1].split("#### 5.2 ", 1)[0]
    five_two = body.split("#### 5.2 ", 1)[1].split("#### 5.3 ", 1)[0]
    assert "auto_size.py" in five_one and "gpu-preflight.sh" in five_one
    for needle in ("metrics_exporter", "--firebase-project", "docker login", "sudo ./install.sh"):
        assert needle in five_two, needle
    assert "docker compose pull" not in five_two.replace("لا `docker compose pull`", "")


def test_the_status_rows_of_phase_5_are_not_not_started() -> None:
    for step in ("5.1", "5.2", "5.3"):
        assert "⬜" not in plan_row(step), step
    assert "ميزةٌ تالية" in plan_row("5.3")
    assert "مبنيّة في PR" in plan_row("5.1") and "مبنيّة في PR" in plan_row("5.2")


def _flex(html: str, kind: str) -> int:
    found = re.search(rf'class="s-{kind}" style="flex:(\d+)"', html)
    assert found is not None, kind
    return int(found.group(1))


def _articles(html: str) -> list[str]:
    return re.findall(r'<article class="step (done|partial|pending)">', html)


def test_the_summary_matches_the_plan_and_its_bar_matches_its_articles() -> None:
    html = text(SUMMARY)
    assert "ميزةٌ تالية" in html
    articles = _articles(html)
    bar = {kind: _flex(html, kind) for kind in ("done", "partial", "pending")}
    assert bar == {k: articles.count(k) for k in bar}, bar
    digits = str.maketrans("".join(chr(0x0660 + i) for i in range(10)), "0123456789")
    legend = [
        int(n.translate(digits)) for n in re.findall(r"\u2014 ([\u0660-\u0669]+)</span>", html)
    ]
    assert legend == [bar["done"], bar["partial"], bar["pending"]]
    assert sum(bar.values()) == 29
    for step in (f"{FIVE}{DOT}{ONE}", f"{FIVE}{DOT}{TWO}"):
        before, after = html.split(f'<div class="step-id">{step}</div>', 1)
        assert before.rsplit("<article", 1)[1].startswith(' class="step partial"'), step
        block = after.split("</article>", 1)[0]
        assert "مبنيّة في PR" in block and "لم تبدأ" not in block, step


def test_the_bar_check_fails_when_a_count_drifts() -> None:
    drifted = text(SUMMARY).replace(
        'class="s-partial" style="flex:11"', 'class="s-partial" style="flex:12"'
    )
    assert _flex(drifted, "partial") != _articles(drifted).count("partial")
