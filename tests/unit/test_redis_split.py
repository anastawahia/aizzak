"""Two Redis servers, and the rule for which keys may live on which --
capacity step 5.2 (``docs/capacity-plan.md`` §5 wave 5, ``ح-10`` · ``ق-4``).

**What ``ح-10`` actually is, measured rather than quoted.** The bottleneck is
recorded as "Redis واحدٌ بلا `maxmemory`" with the consequence "تضخّمُ الذاكرة
المخبّأة يقتل المجاري". Live, before this step: ``maxmemory:0`` and
``maxmemory_policy:noeviction``. So the *policy* was never the missing half --
``noeviction`` is Redis's default. What was missing is a ceiling for it to
refuse writes at, and ``noeviction`` without one is not a safety property, it
is the absence of a bound: the process grows until the 5 GB cgroup limit kills
it and takes the streams, the WS registry and the rate limiter with it. The
only single-instance alternative, ``allkeys-lru``, is strictly worse -- it
makes the same heap discard stream entries to make room for cached vectors,
silently. There is no configuration of ONE server that is correct, which is
what ``ق-4`` decided by recommending two.

⭐ **AND THE STEP'S OWN WORDING WOULD HAVE PUT A SESSION DENYLIST ON AN LRU
SERVER.** ``5.2`` says: add ``CACHE_REDIS_URL`` and "يوجَّه ``RedisCache`` وحده
إليه" -- point ``RedisCache`` alone at it. But ``RedisCache`` is not a cache;
it is the single adapter behind the ``CacheProvider`` port, and that port has
six callers carrying two incompatible contracts. Three of them are not caches
at all:

* ``auth:revoked:<sub>`` -- the session denylist (``framework/auth/
  revocation.py``). Its own docstring: "A cache MISS means 'not revoked' and
  the request proceeds." An evicted entry therefore re-validates a revoked
  token until its ``exp`` -- which is the exact residual risk that module was
  written to close, and which ``1.1`` names in writing as the thing that
  carries "تعطيل حساب يسري في الطلب التالي". Worse, it is the entry LRU picks
  first: the dangerous case is a subject revoked before their stolen token is
  used, which is by construction a key nobody reads.
* ``integrations:oauth:state:<state>`` -- the single-use CSRF binding.
  Eviction fails closed, so this one is an availability defect rather than a
  hole.
* ``admin:provider-probe:<provider>`` -- the abuse window, silently reset.

So the split is made at the CALLER, not at the adapter, and the direction is
the conservative one: ``cache`` stays on the ``noeviction`` instance and
nothing that used it moved, while a second provider is added and exactly two
callers are pointed at it. A caller added later and thought about less
carefully lands on the safe instance by default.

⚠️ **The adapter-level split would also have broken an ops tool in silence.**
``app.ops.revoke`` builds its own ``RedisCache`` over ``settings.redis`` in a
separate process. Had ``cache`` been the field that moved, ``python -m
app.ops.revoke`` would write the denylist to one server while the request path
read the other: a revocation that reports success and denies nothing. Under
the wiring guarded here that tool needed no change at all.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SRC = _REPO_ROOT / "src"
_ROOT_PY = _SRC / "app" / "framework" / "di" / "composition_root.py"
_MAIN_PY = _SRC / "app" / "api" / "main.py"

_STREAM = "redis-stream"
_CACHE = "redis-cache"


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _command(service: str) -> list[str]:
    command = _compose()["services"][service]["command"]
    assert isinstance(command, list), (
        f"{_COMPOSE.name}: `{service}.command` must be the exec-form list -- a shell "
        "string is re-parsed by /bin/sh and this module could not read the flags out of it"
    )
    return [str(part) for part in command]


def _flag(service: str, name: str) -> str | None:
    """The value following ``name`` in a service's exec-form command."""
    command = _command(service)
    if name not in command:
        return None
    index = command.index(name)
    assert index + 1 < len(command), f"{_COMPOSE.name}: `{service}` passes {name} with no value"
    return command[index + 1]


# ------------------------------------------------------------------ the two servers --


def test_the_stream_instance_refuses_writes_instead_of_dropping_keys() -> None:
    """``noeviction`` AND an explicit ``maxmemory`` -- neither is worth
    anything alone.

    A policy with no ceiling never applies (the pre-5.2 state, measured:
    ``maxmemory:0``); a ceiling with the wrong policy converts a memory
    problem into silent data loss. This asserts both because the defect
    ``ح-10`` describes is reachable by removing either one.
    """
    assert _flag(_STREAM, "--maxmemory-policy") == "noeviction", (
        f"{_COMPOSE.name}: `{_STREAM}` must run `noeviction`. Everything on it is "
        "correctness-bearing: unconsumed stream entries, WebSocket session records, "
        "rate-limit windows, and the `auth:revoked:` denylist whose absence reads as "
        "'not revoked'"
    )
    ceiling = _flag(_STREAM, "--maxmemory")
    assert ceiling and ceiling != "0", (
        f"{_COMPOSE.name}: `{_STREAM}` needs an explicit `--maxmemory`. `noeviction` with "
        "no ceiling is not a bound at all -- it is what the stack shipped before 5.2, and "
        "it grows until the cgroup limit kills the process"
    )


def test_the_cache_instance_evicts_and_keeps_nothing_across_a_restart() -> None:
    """``allkeys-lru``, and no persistence at all.

    The persistence half is a decision, not an omission: everything on this
    instance is reconstructible from a source of truth, so an empty cache
    after a restart is CORRECT, while a restored one is a heap of LRU
    survivors selected by nothing. It also removes the fork/COW spike a
    BGREWRITEAOF causes, on the one instance with no reason to fork.
    """
    assert _flag(_CACHE, "--maxmemory-policy") == "allkeys-lru", (
        f"{_COMPOSE.name}: `{_CACHE}` must run `allkeys-lru` -- the whole point of a second "
        "instance is that this one may discard a cold key rather than refuse a write"
    )
    ceiling = _flag(_CACHE, "--maxmemory")
    assert ceiling and ceiling != "0", (
        f"{_COMPOSE.name}: `{_CACHE}` needs an explicit `--maxmemory`; `allkeys-lru` with no "
        "ceiling never evicts either, and the cgroup limit is the only thing left"
    )
    assert _flag(_CACHE, "--appendonly") == "no" and _flag(_CACHE, "--save") == "", (
        f'{_COMPOSE.name}: `{_CACHE}` must persist nothing (`--save ""` + `--appendonly no`)'
    )
    assert not _compose()["services"][_CACHE].get("volumes"), (
        f"{_COMPOSE.name}: `{_CACHE}` must mount no volume -- a disposable cache that "
        "survives a restart is a stale cache, and it would compete for the disk the AOF "
        f"on `{_STREAM}` needs"
    )


def test_the_stream_instance_still_owns_the_volume_the_old_service_held() -> None:
    """Renaming `redis` -> `redis-stream` must not orphan the AOF.

    A volume rename here would be data loss dressed as a rename: every event
    stream, and the entire pending-entry state of every consumer group, lives
    in that file.
    """
    volumes = _compose()["services"][_STREAM]["volumes"]
    assert any(str(v).startswith("redis-data:") for v in volumes), (
        f"{_COMPOSE.name}: `{_STREAM}` must keep mounting `redis-data` -- the volume the "
        "pre-5.2 `redis` service held. A new name leaves the streams behind a service "
        "that no longer mounts them"
    )


def test_neither_instance_is_reachable_off_the_host() -> None:
    for service in (_STREAM, _CACHE):
        for published in _compose()["services"][service].get("ports", []):
            assert str(published).startswith("127.0.0.1:"), (
                f"{_COMPOSE.name}: `{service}` publishes {published!r} on every interface. "
                "Redis has no authentication in this deployment"
            )


# ------------------------------------------------------- the ledger of cache callers --

# Every place in `src/` that accepts a `CacheProvider`, and WHICH of the two
# servers it must be handed. `evictable` is a claim about the value, not about
# the caller: it may be dropped at any moment and rebuilt from a source of
# truth. Anything that cannot make that claim is `retained`.
#
# This is the whole finding of 5.2 in one table. A new entry on the
# `evictable` side is a decision someone has to defend; a new one appearing at
# all is caught by `test_no_cache_caller_escapes_the_ledger` below.
_EVICTABLE = "evictable"
_RETAINED = "retained"

_CACHE_CALLERS: dict[tuple[str, str], str] = {
    # A miss re-runs `provision_on_login` + `roles_of` -- exactly the pre-1.1
    # request. And note the direction: for a principal, a STALE entry is the
    # risk and a MISSING one is free, so eviction can only move this key the
    # safe way. 1.1's security criterion rests on the denylist below, not on
    # this cache, which is what makes the two separable at all.
    ("framework/auth/principal_cache.py", "PrincipalCache"): _EVICTABLE,
    # Deterministic recompute of a vector from the string that produced it,
    # and by far the largest tenant of the LRU instance.
    (
        "infrastructure/ai_providers/embedding/caching_embedding.py",
        "CachingEmbeddingProvider",
    ): _EVICTABLE,
    # Public web results behind a hash of the query. Not wired today
    # (`web_search` is None in the root), listed so that wiring it is not also
    # a decision about which server it lands on.
    ("infrastructure/web_search/exa_web_search.py", "ExaWebSearchAdapter"): _EVICTABLE,
    # ⚠️ THE ONE THAT MAKES THIS TABLE EXIST. A miss means "not revoked".
    ("framework/auth/revocation.py", "SessionRevocationList"): _RETAINED,
    # Single-use OAuth `state`. Eviction fails closed -- a legitimate connect
    # flow is rejected at the callback -- so this is availability, not a hole.
    ("modules/integrations/application/use_cases.py", "BeginConnection"): _RETAINED,
    ("modules/integrations/application/use_cases.py", "CompleteOAuth"): _RETAINED,
    # An abuse counter. Evicting it silently resets the window it enforces.
    ("modules/admin/application/providers.py", "ProbePlatformProvider"): _RETAINED,
    # The three composition-root helpers that pass one through.
    ("framework/di/composition_root.py", "_build_embedding"): _EVICTABLE,
    ("framework/di/composition_root.py", "_build_integrations"): _RETAINED,
    ("framework/di/composition_root.py", "_build_platform_providers"): _RETAINED,
}

# The identifiers the wiring uses for each server, as they appear at a call
# site in either file.
_NAMES = {
    _EVICTABLE: {"evictable_cache", "root.evictable_cache"},
    _RETAINED: {"cache", "root.cache"},
}


def _cache_parameter_holders() -> dict[tuple[str, str], str]:
    """Every function in ``src/`` with a parameter annotated ``CacheProvider``,
    keyed by (module path relative to ``src/app``, owning class or function).

    Read out of the AST rather than listed by hand for the
    ``test_role_provisioning_wiring.py`` reason: a hand-written list is a
    snapshot, and the failure this module exists to prevent is a caller added
    later by someone who never read it.
    """
    holders: dict[tuple[str, str], str] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = str(path.relative_to(_SRC / "app"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                owner: str | None = node.name
                bodies: list[ast.AST] = list(node.body)
            elif isinstance(node, ast.Module):
                owner, bodies = None, list(node.body)
            else:
                continue
            for child in bodies:
                if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                arguments = child.args
                every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
                for argument in every:
                    annotation = argument.annotation
                    if annotation is None or "CacheProvider" not in ast.unparse(annotation):
                        continue
                    holders[(relative, owner or child.name)] = argument.arg
    return holders


def test_no_cache_caller_escapes_the_ledger() -> None:
    """The scope guard: a NEW ``CacheProvider`` caller fails here until
    someone says which server it belongs on.

    This is the guard that actually prevents ``ح-10``'s defect from coming
    back. Both instances satisfy the same Protocol and behave identically
    under every test that does not fill one of them, so a caller wired to the
    wrong one is invisible in every ordinary way -- it only shows up as a
    revoked token that still works, months later, on a busy production cache.
    """
    found = set(_cache_parameter_holders())
    declared = set(_CACHE_CALLERS)
    assert found == declared, (
        "the set of CacheProvider callers drifted from the 5.2 ledger.\n"
        f"  unledgered: {sorted(found - declared)}\n"
        f"  stale:      {sorted(declared - found)}\n"
        "Every caller must be classified `evictable` (the value is reconstructible from a "
        "source of truth, so losing it costs work and nothing else) or `retained` (losing "
        "it is a correctness or security defect). Add it to _CACHE_CALLERS with the reason."
    )


def _call_arguments(tree: ast.AST, callee: str) -> list[set[str]]:
    """For each call to ``callee``, the set of argument expressions that name
    one of the two cache fields."""
    known = _NAMES[_EVICTABLE] | _NAMES[_RETAINED]
    calls: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name != callee:
            continue
        passed = {ast.unparse(a) for a in node.args}
        passed |= {ast.unparse(k.value) for k in node.keywords}
        calls.append(passed & known)
    return calls


def test_every_wiring_site_hands_over_the_server_the_ledger_names() -> None:
    """The ledger, checked against the two files that actually do the wiring.

    ``CacheProvider`` says nothing about eviction on purpose -- the port is a
    get/set/delete contract and the guarantee lives in the server behind it.
    That is precisely why the guarantee can only be applied here, at the
    wiring, and why nothing at a call site's own type would ever catch a
    mistake in it.
    """
    sources = {path: ast.parse(path.read_text(encoding="utf-8")) for path in (_ROOT_PY, _MAIN_PY)}
    wired: dict[str, set[str]] = {}
    for tree in sources.values():
        for (_, owner), _ in _CACHE_CALLERS.items():
            for passed in _call_arguments(tree, owner):
                if passed:
                    wired.setdefault(owner, set()).update(passed)

    for (module, owner), expected in _CACHE_CALLERS.items():
        if owner not in wired:
            continue  # not constructed in either wiring file (e.g. the unwired Exa adapter)
        allowed = _NAMES[expected]
        assert wired[owner] <= allowed, (
            f"{module}'s `{owner}` is wired to {sorted(wired[owner])}, but the 5.2 ledger "
            f"classifies it `{expected}` -- it must be handed one of {sorted(allowed)}.\n"
            "A `retained` caller on the allkeys-lru server loses entries silently; the "
            "denylist entry it loses re-validates a revoked token until that token expires."
        )


def test_the_two_callers_that_moved_really_did_move() -> None:
    """The positive half: a ledger everything satisfies by leaving the wiring
    alone would pass while the step did nothing.

    Exactly two callers are on the LRU instance, and both are named here so
    that a revert -- or a merge that quietly puts them back -- fails rather
    than passes.
    """
    root_tree = ast.parse(_ROOT_PY.read_text(encoding="utf-8"))
    main_tree = ast.parse(_MAIN_PY.read_text(encoding="utf-8"))

    embedding = _call_arguments(root_tree, "CachingEmbeddingProvider")
    assert embedding and all(call == {"evictable_cache"} for call in embedding), (
        "the query-vector cache (4.3) must be built over `evictable_cache`; it is the "
        "largest tenant of the LRU instance and what its ceiling is sized from"
    )
    principal = _call_arguments(main_tree, "PrincipalCache")
    assert principal and all(call == {"root.evictable_cache"} for call in principal), (
        "the principal cache (1.1) must be built over `root.evictable_cache`"
    )
    denylist = _call_arguments(main_tree, "SessionRevocationList")
    assert denylist and all(call == {"root.cache"} for call in denylist), (
        "the session denylist must stay on `root.cache` (noeviction). This is the line "
        "5.2's own wording -- 'point RedisCache alone at the cache instance' -- would "
        "have moved, and moving it re-opens the residual risk revocation.py exists to close"
    )
