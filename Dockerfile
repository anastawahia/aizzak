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

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Alembic needs these at runtime: the `migrate` service runs the eleven
# chains out of this image (app.ops.provision resolves them relative to the
# repo root, which inside the container is /app).
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
COPY src/ ./src/

# Non-root. The app writes nothing to the filesystem -- objects go to MinIO,
# state to Postgres/Redis -- so it needs no writable mount at all.
RUN useradd --create-home --uid 10001 aizzak \
    && chown -R aizzak:aizzak /app
USER aizzak

EXPOSE 8000

# Default command = the API. Overridden per service in docker-compose.yml.
# The trailing `()` is how GUNICORN spells "this is a factory, call it" --
# `--factory` is uvicorn's flag and gunicorn rejects it outright.
CMD ["gunicorn", "app.api.main:create_production_app()", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
