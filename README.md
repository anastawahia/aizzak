# AIZZAK — Multi-Agent AI Platform

AIZZAK is a backend platform for building AI products that bring **conversations, specialized agents, organizational knowledge and workflows** together in a single workspace. It is designed to scale and to be extended, with a strict separation between business logic and infrastructure providers.

The platform exposes an HTTP API for managing agents, conversations, files, workspaces and workflows. Long-running work is handled by background workers, and progress is streamed back to clients in real time. Model and service providers are pluggable and selected through deployment configuration.

> This repository contains the backend and its deployment stack only. There is no frontend code here.

## Key capabilities

- **Conversational experiences** — agents are discovered and registered as plugins, with full conversation and message-history management.
- **Knowledge from your files** — upload content, index it, and use it for search and retrieval-augmented generation (RAG), scoped to each workspace.
- **Specialized agents** — RAG, image generation, video generation, data analysis and file editing, coordinated by an orchestrator.
- **Workflows and tools** — multi-step operations, with asynchronous jobs for heavy processing.
- **Workspaces and access control** — authentication, role-based permissions and strict tenant data isolation.
- **Pluggable AI providers** — LLMs, embeddings and reranking. Ollama runs locally; adapters for Claude, Gemini, OpenAI and OpenRouter are enabled when credentials are configured.
- **Extensibility** — new agents and modules can be added without coupling domain logic to any specific technology or vendor.
- **Built-in observability** — logs, metrics and alerts ship with the deployment stack.

> The features available in a given deployment depend on its environment settings and provider credentials. See the [Quickstart guide](docs/quickstart.md) for the local setup and current requirements.

## Architecture

AIZZAK is a **modular monolith** organized around independent business modules, following a **hexagonal (ports and adapters)** architecture that keeps domain logic isolated from databases and external services.

- The **API layer** receives requests and coordinates agents and use cases.
- **Background workers** process heavy tasks and publish their events back to the application through messaging and streaming.
- **Dependencies point inward only:** `api` → `agents` → `modules` → `framework`. Modules are independent of one another, as are agents, and infrastructure adapters are wired in through a single composition root. These rules are enforced automatically with import-linter contracts.
- **Tenant isolation** is enforced at the data layer: PostgreSQL row-level security per workspace, least-privilege database roles per module, and a dedicated vector collection per workspace.

This design makes it straightforward to add a new capability or swap a service provider while keeping module boundaries and dependency rules explicit. See the [architecture document](docs/architecture.md) for the full set of decisions.

## Technology stack

| Area | Technologies |
|---|---|
| Backend & API | Python 3.12 · FastAPI · SQLAlchemy (async) |
| Data & storage | PostgreSQL · Redis (cache and streams) · MinIO · Qdrant |
| Security & secrets | Firebase Authentication · HashiCorp Vault |
| Runtime | Docker Compose · Nginx · Gunicorn/Uvicorn |
| Observability | Prometheus · Grafana · Loki · Alertmanager |
| Models | Ollama (local) · cloud LLM adapters when configured |

## Getting started

The repository targets development and self-hosted operation.

**Prerequisites**

- WSL with Ubuntu 24.04 (recommended) or another Linux host
- Docker and Docker Compose v2
- Python 3.12+ for running tools and tests outside containers

Before starting the services, you need to create an environment file from `.env.example` and set a few passwords. The [Quickstart guide](docs/quickstart.md) walks through configuration, starting the stack, health checks and troubleshooting.

**Local development setup**

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev,parsers]"
```

## Quality gates

Every change must pass the same gates that CI runs (`.github/workflows/ci.yml`), in this order:

| # | Gate | Command |
|---|---|---|
| 1 | Formatting | `.venv/bin/ruff format --check .` |
| 2 | Linting | `.venv/bin/ruff check .` |
| 3 | Type checking (strict) | `.venv/bin/mypy src` |
| 4 | Architecture contracts | `.venv/bin/lint-imports` |
| 5 | Tests | `.venv/bin/pytest -rs` |
| 6 | Alert rules (requires Docker) | `deploy/prometheus/test-rules.sh` |

Integration tests under `tests/integration/` require live services and are skipped automatically when those services are unavailable.

## Documentation

> Most project documentation is written in Arabic.

| Topic | Guide |
|---|---|
| Running the services and Compose commands | [Quickstart](docs/quickstart.md) · [Stack commands](docs/stack-commands.md) |
| Modules, request flow and architecture decisions | [Architecture](docs/architecture.md) |
| Detailed design, API, events and permissions | [Design documents](docs/design/README.md) · [OpenAPI contract](docs/design/openapi.yaml) |
| Deployment | [Server deployment](docs/deploy-server.md) |
| Adding an agent or a business module | [Agent authoring guide](docs/design/11-agent-authoring-guide.md) · [Module authoring guide](docs/design/12-module-authoring-guide.md) |
| Operations and alerts | [Alert runbooks](docs/runbooks/alerts.md) |
| Capacity work | [Capacity plan](docs/capacity-plan.md) · [Capacity status](docs/capacity-status.md) |

## Repository layout

```text
src/app/
├── api/              # HTTP endpoints, streaming, authentication and authorization
├── agents/           # Pluggable agents and the orchestrator
├── modules/          # Business modules with independent boundaries
├── framework/        # Ports, registries, events and runtime
├── infrastructure/   # Adapters for databases, AI providers and external services
├── workers/          # Background job processing
└── ops/              # Operations and maintenance tools

services/embedding/   # Standalone embedding service
deploy/               # Container, monitoring and deployment configuration
migrations/           # Database migrations (Alembic)
tests/                # Unit, integration and architecture-contract tests
docs/                 # Operations, design and architecture documentation
```

For the layer dependency rules and how to add an agent or a module, see the [architecture document](docs/architecture.md) and the authoring guides listed above.

## Project status

The repository includes the application, the local runtime stack and the deployment guides. Some external features require provider-specific keys and credentials, and large-scale capacity validation follows its own plan and status tracking. Readiness for any deployment therefore depends on its environment configuration and operational requirements. Current details are in the [Quickstart guide](docs/quickstart.md) and the [capacity status](docs/capacity-status.md).

## License

Proprietary. All rights reserved.
