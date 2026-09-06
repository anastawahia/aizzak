# AIZZAK application image (7.1 · 08-local-runbook §2).
#
# ONE image, several commands (08 §2/§4): `app` runs Gunicorn+UvicornWorker,
# `worker` runs a Streams consumer, `outbox-relay` runs the relay, and the
# one-shot `migrate` service runs `app.ops.provision`. Nothing about the
# process identity lives here -- it is the `command:` in docker-compose.yml,
# so every process is provably running the same code.
#
# Base tags are PINNED (never `latest`): a deploy artifact that silently
# changes its own base between two builds is not a deploy artifact.

# --------------------------------------------------------------------------
# Stage 1 -- build the virtualenv.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build-only toolchain: some wheels fall back to a source build. Kept out of
# the runtime stage entirely.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

# `parsers` is included: the knowledge worker's document adapters need it and
# it runs from THIS image (08 §2: "نفس الصورة، أمر مختلف"). Dev tooling is
# not -- ruff/mypy/pytest have no business in a runtime image.
#
# The project is installed to RESOLVE its dependency tree, then uninstalled
# again: the runtime stage runs `app` from /app/src on PYTHONPATH, not from
# site-packages. That is not a preference -- `app.ops.provision` locates
# alembic.ini relative to its own file (parents[3]), which is the repo root
# under the source layout and a Python stdlib directory under a site-packages
# install. Keeping ONE layout in dev and in the image keeps that resolution
# true in both, instead of making the container a special case.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir '.[parsers]' \
    && /opt/venv/bin/pip uninstall --yes aizzak-platform

# --------------------------------------------------------------------------
# Stage 2 -- runtime.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH="/opt/venv/bin:${PATH}"

# Runtime-only OS packages. `tesseract-ocr` backs the knowledge module's OCR
# adapter (pytesseract is a binding, not an implementation); `curl` is the
# healthcheck's own probe.
#
# BE-RAG-012 adds the Pango/Cairo stack, which WeasyPrint loads through
# ctypes at import time -- so a missing library is an ImportError on boot,
# not a failed export at 3am. They are here rather than in the builder stage
# because they are needed to RUN, not to compile: WeasyPrint ships pure
# Python and finds these by name at run time.
#
# `fonts-dejavu-core` is NOT enough on its own for this platform's primary
# language -- DejaVu has no Arabic coverage, and a PDF rendered without an
# Arabic face is a page of empty boxes. `fonts-noto-core` carries Noto Naskh
# Arabic, and Pango/HarfBuzz do the shaping and bidi ordering from there.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        tesseract-ocr \
        curl \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        fonts-dejavu-core \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# The PostgreSQL 16 client binaries -- capacity step 2.5's `python -m
# app.ops.backup` shells out to pg_basebackup, pg_dump and pg_restore.
#
# WHY THE PGDG REPOSITORY AND NOT DEBIAN'S OWN. Bookworm ships
# postgresql-client-15. `pg_dump` refuses to dump a server NEWER than itself
# ("server version 16.x; pg_dump version 15.x"), and a base backup taken by a
# mismatched pg_basebackup is a data directory the server may not accept --
# so the client major MUST track the server major, which docker-compose.yml
# pins at `postgres:16`. This is the same version-pinning discipline the base
# images get, applied to a package.
#
# WHY IN THE APPLICATION IMAGE AND NOT A SECOND ONE. `postgres:16` carries
# the binaries but is Debian 13 (trixie) against this image's Debian 12
# (bookworm), so copying them across is a glibc mismatch, and building a
# SECOND image with this repository's source in it would break the one
# property the header of this file states: every process is provably running
# the same code. ~4 MB of binaries is the cheaper half of that trade.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client-16 \
    && apt-get purge --yes gnupg \
    && apt-get autoremove --yes \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Alembic needs these at runtime: the `migrate` service runs the eleven
# chains out of this image (app.ops.provision resolves them relative to the
# repo root, which inside the container is /app).
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
COPY src/ ./src/
# Wave 0 step 0.2 (docs/capacity-plan.md). Named paths out of `deploy/`,
# never `COPY deploy/`: that directory also holds the Vault policy and the
# development TLS material, and an image is the last place either belongs.
# `gunicorn.conf.py` owns the lifecycle of the PROMETHEUS_MULTIPROC_DIR the
# /metrics endpoint aggregates from.
COPY deploy/gunicorn.conf.py ./deploy/gunicorn.conf.py
# The live proofs, and they were NOT here until capacity step 2.5 -- which
# means `docker compose exec app python /app/deploy/smoke/stack_smoke.py`,
# documented in 08-local-runbook §5.1 and in stack_smoke.py's own header as
# the way to run it, has never once worked from a built image:
#
#     python: can't open file '/app/deploy/smoke/stack_smoke.py':
#     [Errno 2] No such file or directory
#
# Found because 2.5's acceptance criterion is "a RESTORED database passes the
# live proofs in deploy/smoke/", so something finally had to run them the
# documented way. These three files are proofs meant to execute INSIDE the
# container, against the real wiring -- the one part of `deploy/` that
# belongs in the image by nature.
COPY deploy/smoke/ ./deploy/smoke/

# Non-root. The app writes no PLATFORM state to the filesystem -- objects go
# to MinIO, state to Postgres/Redis -- so it needs no writable mount.
#
# It does write one thing, and only since Wave 0 step 0.2: the
# `prometheus_client` mmap files under PROMETHEUS_MULTIPROC_DIR (a /tmp path,
# see docker-compose.yml). That is scratch belonging to this container's own
# processes, wiped at arbiter boot and gone with the container -- deliberately
# NOT a volume, and it does not make the statement above any less true of the
# platform's data.
RUN useradd --create-home --uid 10001 aizzak \
    && chown -R aizzak:aizzak /app
USER aizzak

EXPOSE 8000

# Default command = the API. Overridden per service in docker-compose.yml.
# The trailing `()` is how GUNICORN spells "this is a factory, call it" --
# `--factory` is uvicorn's flag and gunicorn rejects it outright.
#
# ⚠️ `--access-logfile` is GONE since Wave 0 step 0.6 because it never did
# anything here -- measured, not assumed. Under `UvicornWorker` the access line
# is emitted by uvicorn's OWN `uvicorn.access` logger, not by gunicorn's; the
# flag set `cfg.accesslog` and nothing read it. Removing it changed the output
# by exactly zero lines, which is the proof. The access line itself is still
# there, and since 0.6 it is JSON carrying `correlation_id` and `request_id`
# like every other line -- see `observability/logging.py`'s `_ADOPTED_LOGGERS`,
# which is the change that actually mattered.
#
# `--error-logfile` stays and is NOT inert: gunicorn's arbiter writes worker
# lifecycle through it, and nothing else reports that.
#
# ── The three explicit flags (capacity 3.3) ───────────────────────────────────
# Until this step this line ended at `--bind` while `deploy/runpod/supervisord.
# conf` passed `--timeout 120 --graceful-timeout 30`: one repository, one
# process, two different servers. Both publishers now carry the same three, and
# each number is written because it was measured, not because it is a default.
#
# `--timeout 30` IS NOT A REQUEST TIMEOUT, and this is the flag the step's own
# acceptance criterion is easiest to misread. Under `UvicornWorker` the worker
# heartbeat is a TIMER -- uvicorn's `on_tick` calls `callback_notify` every
# `timeout/2` seconds regardless of what any request is doing. MEASURED on this
# exact command line before the flag existed (default 30): a 300-second SSE
# response delivered 300 of 300 chunks over 299.8s, same worker pid, zero
# `WORKER TIMEOUT` lines. The criterion "an SSE stream survives 300s" already
# passed, and `--timeout` was never what made it pass.
#
# ⚠️ SO THE PLAN'S 120 GOES THE WRONG WAY, and 30 is deliberate. What the flag
# ACTUALLY catches is a blocked event loop. MEASURED, a 90s blocking call in a
# handler: at `--timeout 30` the arbiter logged `[CRITICAL] WORKER TIMEOUT` and
# replaced the worker (pid 2449 -> 2471); at `--timeout 120` the same block
# survived to a 200 after 90.1s on the same pid. Raising 30 to 120 buys nothing
# measured and quadruples how long a wedged worker keeps its share of §0's 1,500
# WebSockets -- and `ح-5` in docs/capacity-plan.md is that exact failure mode,
# already written down for the embedding service ("even its health check
# freezes"). §1 of that document is what makes 30 safe here: every blocking I/O
# on this path is already off the loop via `asyncio.to_thread`.
#
# ⚠️ AND IT DOES NOT BOUND WORKER BOOT, which is the one argument that could
# have justified a large number -- this app's workers take 12s to reach
# "Application startup complete" (measured from this stack's own log), 40% of
# the budget. MEASURED: a worker needing 40s to load under `--timeout 30` was
# never killed. The reason is in gunicorn's own source: `WorkerTmp` is created
# by `mkstemp` with a WALL-CLOCK mtime while `murder_workers` compares it
# against `time.monotonic()`, so the difference stays hugely negative until the
# worker's first `notify()`. Boot is simply not charged against this number.
#
# `--graceful-timeout 30` is gunicorn's own default made explicit, and it is the
# sum of the budgets step 2.6 set for ONE request still in flight when the
# SIGTERM lands: `DB_POOL_TIMEOUT_S` 5 + pgbouncer's `QUERY_WAIT_TIMEOUT` 20 +
# `DB_STATEMENT_TIMEOUT_MS` 5. ⚠️ It is only reachable because the same step
# raised `stop_grace_period` on the `app` service ABOVE it -- see the comment
# there. The WebSocket half of the criterion is NOT this flag's doing and is
# already true: measured, SIGTERM closed a held socket with code 1012 after
# 0.10s and the arbiter exited at 0.45s, because `api/v1/websocket/streaming.py`
# returns on `WebSocketDisconnect`.
#
# `--keep-alive 65` is the flag `deploy/runpod/nginx.conf` has been waiting for
# by name since 3.2. MEASURED app-side: an idle upstream connection is dropped
# after 2.0s at gunicorn's default and 65.0s with this flag. ⚠️ It is INERT on
# this publisher and shipped anyway: the Compose edge sends `Connection: close`
# (it refuses an `upstream` block, 3.2's resolver decision) and holds no
# persistent upstream socket at all -- measured live, 60 requests through the
# real edge left 124 sockets in TIME_WAIT and 0 ESTABLISHED. It governs RunPod's
# pool, and the two command lines must not drift again to say so.
#
# ⚠️ `--max-requests` IS DELIBERATELY ABSENT. The plan asks for `--max-requests
# 2000 --max-requests-jitter 200` against an UNMEASURED memory leak, and its
# cost is measured: a recycle closes every WebSocket the worker is holding with
# code 1012 (measured directly, `Maximum request limit of 20 exceeded`). Only
# HTTP responses count toward it (`total_requests` increments in uvicorn's
# `on_response_complete`; the WebSocket implementation never touches it), so at
# §0's 300 rps peak 2,000 requests is 13.3s per worker today and 80s after step
# 3.4's twelve processes -- 125 sockets dropped per worker per recycle, roughly
# 94 forced reconnects a second platform-wide, each re-running the auth path. A
# REQUEST COUNTER CANNOT EXPRESS "recycle rarely" on a platform whose rate
# varies 6x by design (§0's peak factor) and whose process count is about to
# change 6x. Recorded as an open debt with the measurement that would settle it
# (a soak plus a per-worker RSS metric), not shipped as a guess with a known
# user-visible price. `tests/unit/test_gunicorn_flags.py` holds the arithmetic.
CMD ["gunicorn", "app.api.main:create_production_app()", \
     "--config", "/app/deploy/gunicorn.conf.py", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--keep-alive", "65", \
     "--error-logfile", "-"]
