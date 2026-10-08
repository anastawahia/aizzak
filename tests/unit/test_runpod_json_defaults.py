"""The RunPod entrypoint's JSON defaults must survive bash's `${VAR:-...}`.

Inside `${VAR:-word}` the first literal `}` closes the expansion and the rest
of `word` is pasted on as plain text. Both JSON defaults in
`deploy/runpod/entrypoint.sh` were once written inline and came out as VALID
JSON with the wrong nesting -- `cost_micros` inside `tokens`, `embedding`
inside `llm` -- and the app refused the routing table at startup. Nothing but
a real boot showed it, so these tests run the lines through bash.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_MODEL = "gemma3:1b"

# A `${VAR:-` whose default holds a literal `{` before any nested `${`.
_BRACE_IN_DEFAULT = re.compile(r"\$\{[A-Z_]+:-[^$}\n]*\{")


def _evaluate(*names: str) -> dict[str, str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    wanted = "|".join(names)
    lines = re.findall(rf"^(?:default_\w+=.*|export (?:{wanted})=.*)$", text, flags=re.MULTILINE)
    script = "\n".join([*lines, *(f'printf "%s\\0" "${name}"' for name in names)])
    env = {"PATH": os.environ.get("PATH", ""), "OLLAMA_MODEL": _MODEL}
    out = subprocess.run(
        [bash, "-euc", script], env=env, capture_output=True, text=True, check=True
    ).stdout
    return dict(zip(names, out.split("\0")[:-1], strict=True))


def test_no_default_holds_a_literal_brace() -> None:
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    hits = [line for line in text.splitlines() if _BRACE_IN_DEFAULT.search(line)]
    assert not hits, f"a literal `{{` inside ${{VAR:-...}} is cut at its first `}}`: {hits}"


def test_usage_default_limits_match_the_env_template() -> None:
    value = _evaluate("USAGE_DEFAULT_LIMITS")["USAGE_DEFAULT_LIMITS"]
    match = re.search(
        r"^USAGE_DEFAULT_LIMITS=(.+)$", _ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match is not None
    assert json.loads(value) == json.loads(match.group(1))


def test_provider_routing_routes_llm_and_embedding_separately() -> None:
    routing = json.loads(_evaluate("PROVIDER_ROUTING")["PROVIDER_ROUTING"])
    assert routing == {
        "llm": {"default": {"provider": "ollama", "model": _MODEL}},
        "embedding": {
            "default": {
                "provider": "embedding-local",
                "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            }
        },
    }
