# AIZZAK ele-parse image (WireTwin INC-1, CMP-07, DEC-04, LLD 3.3.10).
#
# The isolated PDF inspector: PyMuPDF behind a stdlib HTTP server, one limited child process per
# request. It holds no secret, no database client and no Vault client, and Compose puts it on an
# `internal: true` network only. Nothing in `src/` is copied in: the service imports the standard
# library and PyMuPDF and nothing else.
#
# Base image PINNED by tag AND by the digest of the multi-arch index (security review C-12): the
# tag documents the intent, the digest is what is pulled, so a rebuilt tag cannot change this
# image silently. Refresh both together on purpose (docker buildx imagetools inspect <tag>).
# Dependencies install with `--require-hashes` from requirements.txt (PyMuPDF and pip, hashes
# of every wheel pip may accept). Single stage and no OS packages: PyMuPDF's manylinux wheel
# bundles MuPDF, so there is no compiler, no apt layer and no build tool in the final image. (The
# healthcheck uses python's urllib, not curl, for the same reason.)
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/ele_parse/requirements.txt ./requirements.txt

# The pip of the base image carries six known advisories (INC0-Q-10); requirements.txt upgrades it
# in the same hash-checked install. PIP_VERSION is the same pin as
# deploy/supply-chain/pip-bootstrap.txt (the app image and scan.sh) and requirements.txt
# (tests/unit/test_supply_chain_check.py fails when they differ or fall below 26.2.2); the check
# after the install fails the build when requirements.txt disagrees with it. 26.3: the pip 26.2
# line vendors a certifi that still trusts the compromised emSign root (GHSA-6q48-6c7w-9gw2).
ARG PIP_VERSION=26.3
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && test "$(pip --version | cut -d' ' -f2)" = "${PIP_VERSION}"

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
