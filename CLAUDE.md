# AIZZAK — instructions for agents

## What this is
- `aizzak-platform`: a multi-tenant (workspace) AI backend. It provides RAG, image, video, data-analysis and file-editing agents over a modular monolith (`pyproject.toml`, `docs/architecture.md`).
- Python 3.12, FastAPI, SQLAlchemy async/asyncpg, Postgres with RLS, Redis streams/cache, Qdrant, MinIO, Vault, Ollama locally plus cloud LLM adapters. It runs as one Docker Compose stack (`docker-compose.yml`).
- It is a backend only: there is no frontend code in this repo.
- The ROADMAP's 12 phases are done (`docs/ROADMAP.md`). Current work follows the capacity plan in waves 0–8 (`docs/capacity-plan.md` is the plan, `docs/capacity-status.md` is the status).

## Repos
- `/home/AIZZAK`: this repo, the backend and the whole stack. Remote: `github.com/anastawahia/aizzak`.
- TODO(human): no related repo (frontend or mobile) was given or found. Add its absolute path here if one exists.

## Branches
- Main branch: `master`. CI runs on push to `master` and on every PR (`.github/workflows/ci.yml`).
- `capacity`, `rag-agent-scenarios`, `retrieval`, `summarization-scenarios-plan`: old branches, fully merged into `master` (`git branch --no-merged master` is empty). Do not base work on them, and do not push to or delete them.
- Feature work goes on a new branch from `master`.

## Quality gates
- Run from `/home/AIZZAK` inside WSL/Linux, using `.venv` (stdlib venv, Python 3.12). Install with `pip install -e ".[dev,parsers]"`.
- Run in this order (same as CI job `quality`; `README.md` "البوّابات الخمس"):
  1. `.venv/bin/ruff format --check .`
  2. `.venv/bin/ruff check .`
  3. `.venv/bin/mypy src` (strict)
  4. `.venv/bin/lint-imports` (8 contracts)
  5. `.venv/bin/pytest -rs`
  6. `deploy/prometheus/test-rules.sh` (needs Docker)
- Baseline (2026-10-05) is green: `docs/delivery/baseline.md`.
- Tests under `tests/integration/` carry `live_*` markers. They need live services (Postgres 15432, Redis 16379/16380, MinIO 19000, Qdrant 16333, Vault 18200, Ollama 11434, embedding 8080), and they skip when a service is down.
  - **The live stack on this host is in use.** Integration tests reach the same Redis, Qdrant and Vault, and some flush. Without human approval, run only `pytest tests/unit tests/architecture tests/eval`.
  - Isolated harness: CI job `integration` (`docker-compose.test.yml`, `REQUIRE_LIVE=1`), and `docs/quickstart.md`.
  - `live_edge` and `live_stack_slo` never run automatically (they need `RUN_EDGE_CAPACITY_TEST=1` / `RUN_P1_6_LOAD_TEST=1`).
- Commit only when all gates are green.

## Architecture rules
- Binding design: `docs/design/00…12-*.md`, `docs/design/openapi.yaml` (API contract, checked by a test), `docs/design/events/schemas/`, `docs/architecture.md` (D-01…D-26) and `docs/Requirements-v1.md` (FR/NFR/SEC/AC IDs).
- Layers are enforced by `.importlinter` and run by `lint-imports`:
  - The order is `app.api` → `app.agents` → `app.modules` → `app.framework`, inward only.
  - `modules.*.domain` imports stdlib only.
  - `modules.*.application` may not import api, agents, infrastructure, fastapi or sqlalchemy.
  - The 11 modules are mutually independent, and so are the 5 agents.
  - Only `framework.di.composition_root` (plus `storage_binding` and `vault_binding`) may import `app.infrastructure`.
  - Never add `ignore_imports` without human approval.
- Tenancy:
  - Postgres RLS uses `SET LOCAL app.workspace_id` per transaction (`src/app/framework/repository.py`, `framework/ports/unit_of_work.py`, DD-04).
  - Each module has its own least-privilege DB roles (`deploy/postgres/initdb/10-roles.sh`).
  - Qdrant has one collection per workspace (ق-3).
  - Every new repository gets an RLS test (`tests/integration/test_*_repository_rls.py`).
- Errors: raise `AppError` subclasses (`src/app/framework/errors.py`). These map to RFC 9457 problem+json (`src/app/api/errors.py`).
- Migrations: Alembic, under `migrations/versions/<module>/`. They are applied only through `python -m app.ops.provision` (the `migrate` service). Running `alembic upgrade head` directly is forbidden (`docs/stack-commands.md`).
- Style: line length 100, double quotes (`docs/design/10-code-standards.md`, `[tool.ruff]`).

## Docs conventions
- Docs are written in Arabic; code, comments and commit messages are in English.
- Top-level docs (README, ROADMAP, architecture, capacity-*, quickstart, stack-commands, runbooks) are wrapped in `<div dir="rtl">` … `</div>`. `docs/design/*` and `Requirements-v1.md` are not wrapped.
- IDs use a non-breaking hyphen (U+2011): `FR‑01`, `D‑12`, `ق‑1` (decision), `ح‑3` (bottleneck), `د‑29` (measurement). Capacity steps look like `5.6`, `7.3`.
- Plan and status are kept apart: `capacity-plan.md` is the plan, `capacity-status.md` is the status. Every capacity status change also updates `docs/capacity-summary.html`.
- Each alert has a section in `docs/runbooks/alerts.md`.
- Delivery docs go in `docs/delivery/<slug>/`.

## Staging & deploy
- TODO(human): **there is no staging target.** Today the only stack is the local Compose stack on this host (`docker compose up -d`, rolling replace via `deploy/rolling-deploy.sh`). It also acts as the live test environment.
- A second target is RunPod (`deploy/runpod/`, an all-in-one image).
- Nothing deploys automatically on push; CI only tests.
- Wave 8, the production gate, is deferred by the owner. Do not expose the stack publicly (`docs/capacity-status.md`).
- **Production deploys (RunPod, or anything exposed publicly) are human-only.**
- The local Compose stack is a development environment (owner decision, 2026-10-05). Agents may start, stop, restart and recreate its containers (`docker compose up -d [--no-deps] [--force-recreate] <service>`, `restart`, `stop`):
  - name the services you touch, and prefer `--no-deps`;
  - check the services' health afterwards;
  - volume deletion stays blocked by the hook, and `.env` stays unreadable.

## Secrets
- Never read, print or copy these:
  - `.env`, `.env.bak.*`, `.env.test`
  - `deploy/load/accounts.json`, `deploy/load/tokens.json`, `deploy/load/include-workspaces.txt`
  - `deploy/nginx/certs/*`
  - the Vault `vault-init` volume (`/vault/init/init.json`)
- Templates you may read and edit: `.env.example` and `.env.test.example`.
- New secrets work like this: add the variable name to `.env.example` (and to `.env.test.example` if tests need it) and store the value in Vault (`deploy/vault/`, the `vault-bootstrap` service). A human sets the real value.

## Do-not-touch (human approval required)
- `deploy/`, `docker-compose*.yml`, `Dockerfile`, `.github/workflows/`, `.claude/settings*.json`, `.importlinter`.
- Auth/RBAC and secrets code: `src/app/framework/auth/`, `src/app/modules/access/`, `src/app/modules/credentials/`, and DB roles/grants.
- Running migrations or destructive SQL against any DB except `aizzak_test`.
- Docker volumes: the `.claude/settings.json` hook blocks `docker volume prune`, `docker system prune` and `docker compose down -v`.
