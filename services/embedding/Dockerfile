# AIZZAK central embedding service image (Phase 2.10).
#
# A SEPARATE deployable from the main `aizzak/app` image (`./Dockerfile`):
# this container carries torch + sentence-transformers, which must never
# enter the main app's dependency graph (`requirements.txt` in this
# directory is the ONLY place they are pinned -- see that file).
#
# Base tag PINNED (never `latest`) -- the ./Dockerfile precedent.

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache

WORKDIR /app

# `curl` backs the healthcheck's own probe (the ./Dockerfile precedent).
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/embedding/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Bakes the model weights INTO the image at build time -- the runtime env
# below (HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE) then makes this baked cache
# the ONLY source the library is allowed to read from, so a cold-started
# container never makes an outbound call to huggingface.co.
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# Offline from here on.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY services/embedding/app.py ./services/embedding/app.py

# Non-root (the ./Dockerfile precedent). This process writes nothing to the
# filesystem beyond the cache baked above, which it never modifies at
# runtime.
RUN useradd --create-home --uid 10001 embedding \
    && chown -R embedding:embedding /app /opt/hf-cache
USER embedding

EXPOSE 8080

# Single worker -- ONE model load per process (module docstring); scale by
# replicas, never by raising --workers, which would load the model N times
# in one container for no benefit.
CMD ["uvicorn", "services.embedding.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
