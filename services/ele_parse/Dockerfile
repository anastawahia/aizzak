# AIZZAK ele-parse image (WireTwin INC-1, CMP-07, DEC-04, LLD 3.3.10).
#
# The isolated PDF inspector: PyMuPDF behind a stdlib HTTP server, one limited child process per
# request. It holds no secret, no database client and no Vault client, and Compose puts it on an
# `internal: true` network only. Nothing in `src/` is copied in: the service imports the standard
# library and PyMuPDF and nothing else.
#
# Base tag PINNED (never `latest`) -- the ./Dockerfile precedent. Single stage and no OS
# packages: PyMuPDF's manylinux wheel bundles MuPDF, so there is no compiler, no apt layer and
# no build tool in the final image. (The healthcheck uses python's urllib, not curl, for the
# same reason.)
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/ele_parse/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# The package keeps its `services.ele_parse` path: the child is started as
# `python -m services.ele_parse.child` with PYTHONPATH=/app (sandbox.ROOT).
COPY services/__init__.py ./services/__init__.py
COPY services/ele_parse ./services/ele_parse

# Non-root, the app image's uid, so the 0640 stage files its worker writes are readable here.
# The code is root-owned and read-only to this user; Compose also mounts the root read-only.
# The stage mount point is created owned by the same uid as in the app image, so whichever
# service first mounts the new `ele-stage` volume leaves it writable by the worker.
RUN useradd --no-create-home --uid 10001 --shell /usr/sbin/nologin eleparse \
    && mkdir -p /var/lib/aizzak/ele-stage \
    && chown 10001:10001 /var/lib/aizzak/ele-stage \
    && chmod 0750 /var/lib/aizzak/ele-stage
USER eleparse

EXPOSE 8090

CMD ["python", "-m", "services.ele_parse"]
