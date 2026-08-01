# AIZZAK single-container image for a RunPod Pod.
#
# ── WHY THIS FILE EXISTS AT ALL ────────────────────────────────────────────
# The supported topology of this platform is docker-compose.yml: twelve
# containers ordered by `depends_on` + healthchecks. A RunPod Pod CANNOT run
# it. A Pod *is* one container, and RunPod states plainly that "Docker
# Compose is not supported: Runpod runs Docker for you, so you cannot spin up
# your own Docker instance"; Docker-in-Docker was withdrawn along with the
# Kata-based pods. So a RunPod deployment has exactly one available shape:
# every process inside ONE image, ordered by supervisord instead of Compose.
#
# This image is a DEPLOYMENT TARGET, not a second source of truth. Every
# component version below is pinned to the same tag docker-compose.yml pins,
# and the bootstrap re-uses the repository's own scripts verbatim
# (deploy/postgres/initdb/10-roles.sh, deploy/vault/bootstrap.sh,
# deploy/minio/bootstrap.sh) rather than restating them.
#
# ── DELIBERATE DEVIATIONS FROM THE COMPOSE TOPOLOGY ────────────────────────
#   * NO PgBouncer. D-21's transaction pooling multiplexes MANY app replicas
#     onto few Postgres connections; one Pod runs one app with one pool, so
#     the pooler adds a failure mode and buys nothing. OPS-02's other half --
#     asyncpg's statement_cache_size=0 -- is enforced in create_engine for
#     every engine regardless, so nothing app-side changes.
#   * NO TLS inside. RunPod's proxy terminates TLS at
#     https://<POD_ID>-80.proxy.runpod.net and forwards plaintext to :80.
#     A second self-signed layer inside the Pod would only be in the way.
#   * Ollama lives INSIDE the image. docker-compose.yml expects it on the
#     host at host.docker.internal:11434; on a GPU Pod the LLM is the whole
#     reason for the GPU, so it belongs in the Pod.
#   * TWO virtualenvs. /opt/venv holds the app; /opt/venv-emb holds torch +
#     sentence-transformers. Keeping them apart preserves the separation
#     services/embedding/requirements.txt exists to enforce -- torch never
#     enters the app's dependency resolution -- and avoids a resolver fight
#     between two pydantic pins.

# ── binary sources: the same pinned images docker-compose.yml uses ─────────
FROM qdrant/qdrant:v1.13.4                      AS qdrant_src
FROM minio/minio:RELEASE.2025-04-22T22-12-26Z   AS minio_src
FROM minio/mc:RELEASE.2025-04-16T18-13-26Z      AS mc_src
FROM hashicorp/vault:1.18                       AS vault_src

# --------------------------------------------------------------------------
# Stage 1 -- build both virtualenvs.
# --------------------------------------------------------------------------
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        python3.12 python3.12-dev python3.12-venv python3-pip \
        build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# ── the application venv ──────────────────────────────────────────────────
# Installed to RESOLVE the dependency tree, then uninstalled again: the
# runtime runs `app` from /app/src on PYTHONPATH, not from site-packages.
# That is not a preference -- app.ops.provision locates alembic.ini relative
# to its own file (parents[3]), which is the repo root under the source
# layout and a stdlib directory under a site-packages install. The main
# ./Dockerfile does exactly this, for exactly this reason.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python3.12 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir '.[parsers]' \
    && /opt/venv/bin/pip uninstall --yes aizzak-platform

# ── the embedding venv, with the model baked in ───────────────────────────
# The weights are downloaded at BUILD time so the runtime never makes an
# outbound call to huggingface.co (HF_HUB_OFFLINE=1 below makes that a
# guarantee rather than an expectation). ~470 MB, once.
COPY services/embedding/requirements.txt /tmp/emb-requirements.txt
RUN python3.12 -m venv /opt/venv-emb \
    && /opt/venv-emb/bin/pip install --no-cache-dir -r /tmp/emb-requirements.txt \
    && /opt/venv-emb/bin/python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# --------------------------------------------------------------------------
# Stage 2 -- runtime.
# --------------------------------------------------------------------------
FROM ubuntu:24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    LANG=C.UTF-8 \
    HF_HOME=/opt/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PATH="/opt/venv/bin:/usr/lib/postgresql/16/bin:/usr/local/bin:${PATH}"

# postgresql-16 is Ubuntu 24.04's own version -- no third-party apt repo is
# needed, which keeps this image's provenance to two sources (Ubuntu and the
# four pinned vendor images above).
#
# tesseract-ocr backs the knowledge module's OCR adapter (pytesseract is a
# binding, not an implementation). gosu drops privilege for the Postgres
# cluster init. netcat/curl are what the bootstrap waits with.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        python3.12 python3.12-venv \
        postgresql-16 postgresql-client-16 \
        redis-server \
        nginx \
        supervisor \
        tesseract-ocr \
        openssh-server \
        curl ca-certificates gosu netcat-openbsd jq procps tzdata \
    && rm -rf /var/lib/apt/lists/*

# Vendor binaries, lifted from the pinned images rather than curl'd off the
# internet at build time. All four are static Go binaries except qdrant,
# which is glibc-linked against Debian 12 and runs unchanged on Ubuntu 24.04.
COPY --from=vault_src  /bin/vault        /usr/local/bin/vault
COPY --from=minio_src  /usr/bin/minio    /usr/local/bin/minio
COPY --from=mc_src     /usr/bin/mc       /usr/local/bin/mc
COPY --from=qdrant_src /qdrant/qdrant    /opt/qdrant/qdrant
COPY --from=qdrant_src /qdrant/config    /opt/qdrant/config

# Ollama: the platform's sole local LLMProvider (DD-13, 2.8-a). The installer
# only drops the binary in when there is no systemd to wire it to, which is
# exactly the case inside an image.
RUN curl -fsSL https://ollama.com/install.sh | sh

COPY --from=builder /opt/venv     /opt/venv
COPY --from=builder /opt/venv-emb /opt/venv-emb
COPY --from=builder /opt/hf-cache /opt/hf-cache

WORKDIR /app

# alembic.ini + migrations/ are needed at RUNTIME: app.ops.provision runs the
# eleven chains out of this image and resolves them relative to the repo
# root, which inside the container is /app.
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
COPY src/ ./src/
COPY services/ ./services/
COPY deploy/ ./deploy/

COPY deploy/runpod/entrypoint.sh    /usr/local/bin/entrypoint.sh
COPY deploy/runpod/bootstrap.sh     /usr/local/bin/aizzak-bootstrap.sh
COPY deploy/runpod/supervisord.conf /etc/supervisor/supervisord.conf
COPY deploy/runpod/nginx.conf       /etc/nginx/nginx.conf

RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/aizzak-bootstrap.sh \
    && useradd --create-home --uid 10001 aizzak \
    && chown -R aizzak:aizzak /app /opt/qdrant /opt/hf-cache

# ── ports ─────────────────────────────────────────────────────────────────
#   80   nginx -> the API. This is the ONE port an end user needs.
#   9000 MinIO API. Published because PRESIGNED URLS ARE SIGNED AGAINST IT:
#        SigV4 covers the host, so a URL the browser must open has to be
#        signed against a host the browser can reach. Not fixable later.
#   9001 MinIO console (optional, operator convenience).
# Everything else -- Postgres 5432, Redis 6379, Qdrant 6333, Vault 8200,
# embedding 8080, Ollama 11434, app 8000 -- binds 127.0.0.1 ONLY and is never
# exposed. Same trust boundary the Compose topology draws.
#   22   sshd, for the operator. Expose it as a TCP port on RunPod, not HTTP.
EXPOSE 22 80 9000 9001

# CMD, not ENTRYPOINT: RunPod's template offers a "Container Start Command"
# field that overrides CMD, and an ENTRYPOINT would silently defeat it.
CMD ["/usr/local/bin/entrypoint.sh"]
