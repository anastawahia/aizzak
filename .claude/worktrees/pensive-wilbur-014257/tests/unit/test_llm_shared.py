"""Unit tests for the LLM-adapter shared primitives
(``infrastructure/ai_providers/llm/shared.py``, Phase 2.8-ب-1): the eight
primitives extracted from the Ollama (2.8-a) and OpenAI (2.8-ب-1) adapters
once two shipped adapters demonstrably duplicated them
(09-testing-strategy §5's Liskov mandate becomes satisfiable only at N=2).
See ``test_llm_provider_contract.py`` for the parametrized port<->adapter
suite this extraction also unlocked, and ``test_ollama_mapping.py``/
``test_openai_mapping.py`` for the adapters that consume these.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.framework.errors import AppError
from app.framework.types import Json
from app.infrastructure.ai_providers.llm.shared import (
    ROLES,
    create_llm_http_client,
    estimate_tokens,
    neutral_tool_call,
    off_contract,
    parse_json_object,
    reported_token_count,
    token_count,
    translate_http_error,
)


# --------------------------------------------------------------------------- #
# create_llm_http_client -- the V7 trust_env coverage regression, closed     #
# --------------------------------------------------------------------------- #
def test_create_llm_http_client_disables_trust_env() -> None:
    """Closes a verified test-coverage regression: 2.7 (``test_firebase_
    auth.py``'s T41) asserted ``trust_env is False``; 2.8-a's suite had ZERO
    such assertion. Structural, per httpx's own
    ``allow_env_proxies = trust_env and transport is None``
    (``httpx/_client.py``) -- the proxy EFFECT cannot be proven through a
    ``MockTransport`` seam, exactly the T41 idiom."""
    client = create_llm_http_client(base_url="http://x.test", timeout_s=60.0)
    assert client.trust_env is False


def test_create_llm_http_client_sets_the_timeout_pair() -> None:
    client = create_llm_http_client(base_url="http://x.test", timeout_s=60.0)
    assert client.timeout == httpx.Timeout(60.0, connect=5.0)


def test_create_llm_http_client_sets_the_base_url() -> None:
    client = create_llm_http_client(base_url="http://x.test", timeout_s=60.0)
    assert str(client.base_url) == "http://x.test"


def test_create_llm_http_client_accepts_a_transport() -> None:
    """The unit-test seam every adapter's OWN factory is built through (the
    ``create_firebase_http_client`` precedent) -- proves the parameter is
    threaded all the way down, not just accepted and ignored."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = create_llm_http_client(
        base_url="http://x.test", timeout_s=60.0, transport=httpx.MockTransport(handler)
    )
    assert client.trust_env is False


# --------------------------------------------------------------------------- #
# off_contract -- the ONE agent.failed/502 construction                      #
# --------------------------------------------------------------------------- #
def test_off_contract_builds_the_one_agent_failed_error() -> None:
    error = off_contract("openai", "x")

    assert isinstance(error, AppError)
    assert error.code == "agent.failed"
    assert error.status == 502
    assert error.detail == "openai x"


def test_off_contract_prefixes_with_the_calling_providers_own_name() -> None:
    assert off_contract("ollama", "call failed").detail == "ollama call failed"
    assert off_contract("openai", "call failed").detail == "openai call failed"


# --------------------------------------------------------------------------- #
# translate_http_error -- timeout vs generic, distinguishable                #
# --------------------------------------------------------------------------- #
def test_translate_http_error_maps_timeout_and_other_http_errors_distinctly() -> None:
    timeout_error = translate_http_error("openai", httpx.ReadTimeout("timed out"))
    other_error = translate_http_error("openai", httpx.ConnectError("refused"))

    assert timeout_error.code == other_error.code == "agent.failed"
    assert timeout_error.status == other_error.status == 502
    assert timeout_error.detail == "openai call timed out"
    assert other_error.detail == "openai call failed"


# --------------------------------------------------------------------------- #
# parse_json_object -- R6                                                    #
# --------------------------------------------------------------------------- #
def test_parse_json_object_rejects_non_json() -> None:
    with pytest.raises(AppError) as excinfo:
        parse_json_object("openai", "not json at all")
    assert excinfo.value.code == "agent.failed"
    assert excinfo.value.status == 502


def test_parse_json_object_rejects_a_non_object_body() -> None:
    with pytest.raises(AppError) as excinfo:
        parse_json_object("openai", "[1, 2, 3]")
    assert excinfo.value.code == "agent.failed"


def test_parse_json_object_rejects_an_error_key() -> None:
    with pytest.raises(AppError) as excinfo:
        parse_json_object("openai", json.dumps({"error": "bad request"}))
    assert excinfo.value.code == "agent.failed"


def test_parse_json_object_returns_a_well_formed_body_unchanged() -> None:
    payload = {"choices": [{"message": {"content": "hi"}}]}
    assert parse_json_object("openai", json.dumps(payload)) == payload


# --------------------------------------------------------------------------- #
# estimate_tokens -- never reports zero                                      #
# --------------------------------------------------------------------------- #
def test_estimate_tokens_is_always_at_least_one() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("a") == 1
    assert estimate_tokens("12345678") == 2


# --------------------------------------------------------------------------- #
# token_count -- a reported 0 must estimate (silent under-billing guard)     #
# --------------------------------------------------------------------------- #
def test_token_count_uses_the_reported_counter_when_positive() -> None:
    assert token_count({"n": 42}, "n", "irrelevant") == 42


@pytest.mark.parametrize("payload", [{}, {"n": 0}, {"n": -1}, {"n": "not-an-int"}])
def test_token_count_estimates_when_counter_absent_zero_negative_or_wrong_type(
    payload: Json,
) -> None:
    assert token_count(payload, "n", "12345678") == 2  # 8 chars // 4


# --------------------------------------------------------------------------- #
# reported_token_count -- NEVER estimates (4.7-a, port limit (a))            #
# --------------------------------------------------------------------------- #
def test_reported_token_count_returns_the_counter_when_positive() -> None:
    assert reported_token_count({"n": 42}, "n") == 42


@pytest.mark.parametrize("payload", [{}, {"n": 0}, {"n": -1}, {"n": "not-an-int"}, {"n": None}])
def test_reported_token_count_is_none_when_absent_zero_negative_or_wrong_type(
    payload: Json,
) -> None:
    """``None`` means "the provider reported nothing" -- the distinction
    ``UsageCapture`` bills on. Contrast ``token_count``, which estimates."""
    assert reported_token_count(payload, "n") is None


def test_reported_token_count_never_estimates() -> None:
    """The whole point of this function against ``token_count``: an absent
    counter must NOT become a plausible-looking invented number."""
    assert reported_token_count({}, "n") is None
    assert token_count({}, "n", "12345678") == 2


def test_reported_token_count_rejects_bool_which_is_an_int_subclass() -> None:
    """``True`` is an ``int`` in Python and would otherwise bill as 1 token."""
    assert reported_token_count({"n": True}, "n") is None


# --------------------------------------------------------------------------- #
# neutral_tool_call -- the shape has ONE home                                #
# --------------------------------------------------------------------------- #
def test_neutral_tool_call_builds_the_neutral_shape() -> None:
    assert neutral_tool_call("call_1", "search", {"q": "x"}) == {
        "id": "call_1",
        "name": "search",
        "arguments": {"q": "x"},
    }


@pytest.mark.parametrize("name", [123, None, [], {}, 1.5])
def test_neutral_tool_call_rejects_a_non_string_name(name: object) -> None:
    assert neutral_tool_call("call_1", name, {}) is None


def test_neutral_tool_call_rejects_an_empty_name() -> None:
    assert neutral_tool_call("call_1", "", {}) is None


# 4.7-a (port limit (d)): `id` carries the correlation a `role="tool"` reply
# needs, so it is guarded exactly as strictly as `name` -- an unusable id
# would silently produce a call no result could ever be matched back to.
@pytest.mark.parametrize("call_id", [123, None, [], {}, 1.5])
def test_neutral_tool_call_rejects_a_non_string_id(call_id: object) -> None:
    assert neutral_tool_call(call_id, "search", {}) is None


def test_neutral_tool_call_rejects_an_empty_id() -> None:
    assert neutral_tool_call("", "search", {}) is None


# --------------------------------------------------------------------------- #
# ROLES -- the port's own role set, not any one vendor's                     #
# --------------------------------------------------------------------------- #
def test_roles_matches_the_ports_own_four_roles() -> None:
    assert frozenset({"system", "user", "assistant", "tool"}) == ROLES
