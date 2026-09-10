"""The embedding fleet, the balancer in front of it, and the two switches
that turn 4.3 off -- capacity steps 4.1, 4.2 and 4.3 (``docs/capacity-plan.md``
§5 wave 4).

⭐ WHY THIS FILE EXISTS AT ALL, and it is the whole of 4.2: the defect these
two steps close **does not raise anything**. Three replicas behind one DNS
name, reached by a client that pools connections, is not a fleet -- it is one
replica and two spares, at full health, on every dashboard, forever. MEASURED
on the live stack, one pooled ``httpx`` connection (the shape
``worker-knowledge`` actually has: ``engine.py:250`` processes one message at
a time) for 45 seconds against ``embedding:8080``:

    without `embedding-lb`   33 / 0 / 0     requests per replica
    with    `embedding-lb`   10 / 15 / 8

⚠️ AND AT EIGHT CONCURRENT CONNECTIONS THE SAME STACK MEASURES 24 / 24 / 22
WITHOUT THE BALANCER -- because eight connections resolve the name eight
times and Docker's DNS rotates its answers. So the defect hides from exactly
the kind of probe anyone would reach for to look for it, and shows up only on
the low-concurrency path that carries the real indexing work. That asymmetry
is why the wiring is asserted here rather than trusted to a load run.

Every assertion is on FILES, not on a running stack: the numbers above are
evidence for the decisions, and `docs/capacity-status.md` holds them. What
this module guards is that the decisions stay wired.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.framework.settings.settings import EmbeddingServiceSettings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_COMPOSE_TEST = _REPO_ROOT / "docker-compose.test.yml"
_LB_CONF = _REPO_ROOT / "deploy" / "nginx" / "embedding-lb.conf"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

_SERVICE = "embedding"
_BALANCER = "embedding-lb"
_WORKER_BOOTSTRAP = _REPO_ROOT / "src" / "app" / "workers" / "bootstrap.py"


def _compose(path: Path = _COMPOSE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _service(name: str, path: Path = _COMPOSE) -> dict:
    return _compose(path)["services"][name]


def _lb_conf() -> str:
    return _LB_CONF.read_text(encoding="utf-8")


# ------------------------------------------------------------------- 4.1 --
def test_the_fleet_is_three_replicas() -> None:
    """4.1's own shape: scaled by REPLICAS, never by `--workers` (one model
    load per process, `services/embedding/Dockerfile`'s `CMD` comment)."""
    assert int(_service(_SERVICE)["deploy"]["replicas"]) == 3


def test_both_thread_knobs_are_pinned_and_agree_with_the_cpu_limit() -> None:
    """⚠️ THE ONE THAT MAKES THREE REPLICAS SLOWER THAN ONE IF IT DRIFTS.

    Unpinned, torch sizes its intra-op pool from the HOST's core count --
    MEASURED inside this container: **5 threads against a 1.0-vCPU quota** --
    so three replicas contend for the same cores. 4.1 states the fix as two
    variables because they are read by two different runtimes at two
    different moments (OpenMP at process start, torch at model load), and a
    pin that only half happened is the case neither of them reports.

    They are asserted against `cpus` rather than against the literal 1: the
    right number is one thread per vCPU the cgroup allows, so a replica given
    a bigger cap must bring both knobs with it.
    """
    service = _service(_SERVICE)
    environment = service["environment"]
    cpus = float(service["deploy"]["resources"]["limits"]["cpus"])

    assert cpus.is_integer(), (
        f"`{_SERVICE}` is capped at {cpus} vCPU, which no whole number of threads matches; "
        "the thread pin below cannot be derived from a fractional cap"
    )
    for key in ("OMP_NUM_THREADS", "EMB_TORCH_THREADS"):
        assert key in environment, (
            f"`{_SERVICE}` no longer pins {key}. Unpinned, torch chose 5 threads against a "
            "1.0-vCPU quota on the machine this was measured on, and three such replicas are "
            "slower than one container was (capacity 4.1)."
        )
        assert int(environment[key]) == int(cpus), (
            f"{key}={environment[key]!r} against a cap of {cpus} vCPU: the two move together"
        )


# ------------------------------------------------------------------- 4.2 --
def test_the_application_is_pointed_at_the_balancer_not_at_the_service() -> None:
    """The one line that decides whether 4.1 bought anything. `embedding` as
    the destination is not an outage -- it is 33/0/0 with every container
    healthy."""
    url = _compose()["x-app-env"]["EMBEDDING_SERVICE_URL"]

    assert f"{_BALANCER}:8080" in url, (
        f"EMBEDDING_SERVICE_URL is {url!r}. A pooled client resolves this name once and stays "
        f"on that address; pointed at `{_SERVICE}` it leaves two of three replicas idle at full "
        "health (capacity 4.2)."
    )
    assert url.startswith("${EMBEDDING_SERVICE_URL:-"), (
        "the variable must stay env-overridable: it is half the kill switch back to the "
        "pre-4.1 stack that `م-8` requires of every step in this plan"
    )


def test_the_shipped_env_example_does_not_carry_the_silent_bypass() -> None:
    """⚠️ `.env` IS INTERPOLATED INTO THAT DEFAULT, so a file still carrying
    `EMBEDDING_SERVICE_URL=http://embedding:8080` reinstates the defect
    WITHOUT anyone choosing it -- and reinstates it silently. `.env` itself is
    gitignored and cannot be guarded; `.env.example` is what every new one is
    copied from, so it is the only place this can be caught."""
    setting = [
        line
        for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.startswith("EMBEDDING_SERVICE_URL=")
    ]

    assert setting == [f"EMBEDDING_SERVICE_URL=http://{_BALANCER}:8080"], setting


def test_the_balancer_resolves_per_request_and_declares_no_upstream_block() -> None:
    """The 7.1 decision that 3.2 re-confirmed live, inherited by the second
    proxy in this repository: a name inside an `upstream` block is resolved
    ONCE at config load, so the first `docker compose up -d embedding` after
    any image change leaves this proxying at addresses nothing is listening
    on -- and only when Docker's IPAM hands those addresses elsewhere, which
    is worse than failing every time.

    ⚠️ AND HERE IT COSTS SOMETHING THE EDGE DOES NOT PAY: `least_conn` is
    legal only inside an `upstream` block, and it is the balancing method
    this upstream actually wants (one request at a time per replica, seconds
    each). That trade is taken deliberately -- see the file's own header --
    and this test is what stops it being reversed by accident.
    """
    conf = _lb_conf()

    assert not re.search(r"^\s*upstream\s+\w+\s*\{", conf, re.M), (
        "an `upstream` block caches its resolution for the process lifetime; use a variable "
        "destination plus `resolver`, as deploy/nginx/app-locations.conf argues in full"
    )
    assert re.search(r"^\s*resolver\s+127\.0\.0\.11\b", conf, re.M), (
        "no Docker embedded DNS resolver: without it a variable `proxy_pass` cannot resolve "
        "anything at all"
    )
    assert re.search(r"proxy_pass\s+http://\$\w+\$request_uri;", conf), (
        "the destination must be a VARIABLE (per-request resolution), and `$request_uri` must "
        "be appended explicitly because a variable stops nginx passing the matched URI"
    )


def test_the_balancer_writes_down_where_each_request_went() -> None:
    """4.2's acceptance criterion -- «سجلّات النسخ الثلاث تُظهر توزّعاً
    متقارباً تحت الحمل» -- is only checkable because this line exists. It is
    also the only place a request RETRIED onto a second replica is visible at
    all (nginx writes both addresses, comma-separated)."""
    assert "$upstream_addr" in _lb_conf()


def test_the_balancer_never_becomes_the_clock_that_fires_first() -> None:
    """The timeout ladder of 08 §2-ج, applied to one more hop: the ADAPTER's
    own budget must expire before this proxy's, or a typed, retried,
    correlation-carrying timeout is replaced by a bare 504 minted somewhere
    that cannot name the request."""
    read_timeout = re.search(r"proxy_read_timeout\s+(\d+)s;", _lb_conf())
    assert read_timeout, "deploy/nginx/embedding-lb.conf declares no `proxy_read_timeout`"

    adapter_budget = EmbeddingServiceSettings().timeout_s
    assert int(read_timeout.group(1)) > adapter_budget, (
        f"proxy_read_timeout {read_timeout.group(1)}s does not outlast the adapter's own "
        f"{adapter_budget}s, so this proxy becomes the clock that decides"
    )


def test_the_balancer_waits_for_a_real_replica_before_it_reports_healthy() -> None:
    """The 7.3 argument -- a healthcheck that crosses the link -- applied to
    the proxy nothing else observes. It probes `/health` through its own
    listener, so a stale resolver cache or a fleet with no healthy member
    surfaces as an unhealthy BALANCER instead of a 502 in the middle of an
    indexing run."""
    balancer = _service(_BALANCER)

    assert balancer["depends_on"][_SERVICE]["condition"] == "service_healthy"
    assert "127.0.0.1:8080/health" in " ".join(balancer["healthcheck"]["test"])
    assert "ports" not in balancer, (
        "the balancer carries the same tenant text as `embedding` and inherits its trust "
        "boundary: `expose`, never `ports`"
    )


def test_everything_that_calls_the_service_waits_on_the_balancer() -> None:
    """`embedding-lb`'s own healthcheck proxies THROUGH to a replica, so
    waiting on it is strictly stronger than waiting on the fleet -- and it is
    the thing these processes actually call."""
    compose = _compose()
    waiters = {
        name
        for name, block in list(compose["services"].items()) + list(compose.items())
        if isinstance(block, dict) and _BALANCER in (block.get("depends_on") or {})
    }

    # `app`, the three workers, and the `x-worker-service` anchor they merge.
    assert len(waiters) >= 5, (
        f"only {sorted(waiters)} wait on the balancer; anything that embeds must, or it reaches "
        "the fleet before there is one"
    )
    for name in waiters:
        block = compose["services"].get(name) or compose[name]
        assert block["depends_on"][_BALANCER]["condition"] == "service_healthy"


# ------------------------------------------------------- the test overlay --
def test_the_overlay_that_publishes_the_port_pins_one_replica() -> None:
    """⚠️ A PUBLISHED PORT AND `deploy.replicas` FAIL HALF-WAY, not cleanly:
    replica 1 binds 8080 and the rest die with "port is already allocated"
    AFTER it started. `test_resource_budget.py` guards the base file and
    cannot see this overlay, and CI runs the two together
    (`COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml`)."""
    overlay = _service(_SERVICE, _COMPOSE_TEST)

    assert overlay.get("ports"), (
        "this overlay exists to publish the embedding port for the live suite; if it no longer "
        "does, the replica pin below is guarding nothing and should go with it"
    )
    assert int(overlay["deploy"]["replicas"]) == 1


# ------------------------------------------------------------------- 4.3 --
def test_the_batching_window_is_declared_and_stays_switchable() -> None:
    """`م-8` requires every optimisation in this plan to ship with a way to
    turn it off IN THE SAME IMAGE, so a baseline measures the deployment
    rather than a different build. For 4.3's service half that is
    `EMB_BATCH_WINDOW_MS=0`, which builds no batcher and restores the pre-4.3
    inline path."""
    environment = _service(_SERVICE)["environment"]

    for key in ("EMB_BATCH_WINDOW_MS", "EMB_MAX_BATCH_TEXTS"):
        assert key in environment, (
            f"`{_SERVICE}` no longer declares {key}; the service defaults would still apply, "
            "but the knob would be invisible to anyone reading the deployment (capacity 4.3)"
        )
        assert str(environment[key]).startswith(f"${{{key}:-"), (
            f"{key}={environment[key]!r} is no longer env-overridable, and it is half the "
            "kill switch `م-8` requires"
        )


def test_the_query_vector_cache_ttl_is_declared_and_stays_switchable() -> None:
    """4.3's adapter half, same rule: `EMBEDDING_CACHE_TTL_S=0` makes the
    Composition Root build no wrapper at all."""
    value = _compose()["x-app-env"]["EMBEDDING_CACHE_TTL_S"]

    assert value.startswith("${EMBEDDING_CACHE_TTL_S:-"), value
    assert int(value.split(":-")[1].rstrip("}")) > 0, (
        "the shipped default must actually build the cache; `0` belongs in a baseline run, "
        "not in the file every deployment starts from"
    )


def test_the_knowledge_worker_builds_no_query_vector_cache() -> None:
    """⚠️ THE ASYMMETRY, and it is exactly the thing a later edit would
    "fix". `EMBEDDING_CACHE_TTL_S` reaches the workers as well -- `x-app-env`
    is a shared block -- and the knowledge worker must go on ignoring it: it
    embeds CHUNKS, a hundred thousand per corpus, each seen exactly once.
    Caching those would fill Redis with entries that can never be hit and
    evict the query vectors that would."""
    assert "CachingEmbeddingProvider" not in _WORKER_BOOTSTRAP.read_text(encoding="utf-8"), (
        "the knowledge worker's composition root now wraps its embedding adapter in the query "
        "cache; that cache is for the API's query path, not for one-shot indexing chunks "
        "(capacity 4.3)"
    )


def test_the_patterns_actually_find_something() -> None:
    """Every parser here can match nothing and leave a green assertion -- the
    `test_deploy_worker_default.py` precedent this repository keeps."""
    assert _LB_CONF.exists() and len(_lb_conf()) > 1000
    assert _service(_SERVICE)["environment"]["EMB_MAX_SEQ_LEN"] == "512"
    assert "x-app-env" in _compose()
    assert EmbeddingServiceSettings().timeout_s > 0
    assert EmbeddingServiceSettings().cache_ttl_s > 0
    assert (
        _WORKER_BOOTSTRAP.exists()
        and "ExternalEmbeddingProvider" in _WORKER_BOOTSTRAP.read_text(encoding="utf-8")
    )
