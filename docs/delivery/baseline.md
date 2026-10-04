# Baseline gate run — 2026-10-05

Branch `master` @ `568da32`, working tree clean. Run from `/home/AIZZAK` with `.venv` activated.
Gates mirror `.github/workflows/ci.yml` (job `lint-type-test`).

| # | Gate | Command | Result | Last line |
|---|------|---------|--------|-----------|
| 1 | Format | `ruff format --check .` | ✅ exit 0 | `795 files already formatted` |
| 2 | Lint | `ruff check .` | ✅ exit 0 | `All checks passed!` |
| 3 | Types | `mypy src` (strict via pyproject) | ✅ exit 0 | `Success: no issues found in 467 source files` |
| 4 | Imports | `lint-imports` | ✅ exit 0 | `Contracts: 8 kept, 0 broken.` |
| 5 | Tests (no services) | `pytest -rs tests/unit tests/architecture tests/eval` | ✅ exit 0 | `5056 passed, 7 warnings in 94.47s` (0 skipped) |
| 6 | Alert rules | `deploy/prometheus/test-rules.sh` | ✅ exit 0 | `test-rules: OK` |

## Not run during onboarding
- **`tests/integration/`** (the `live_*` markers): the full Compose stack is running on this host
  (and holds the live test tenant). These tests connect to the same Redis (16379), Qdrant (16333),
  Vault (18200) and MinIO, and `tests/integration/test_ws_connection_cap_live.py` calls flush.
  Onboarding is read-only, so they were skipped on purpose. They need a run against the
  isolated harness (`docker-compose.test.yml`, as in CI job `integration`) or a human go-ahead.
- CI job `integration` (containerised harness): not run locally.

## Warnings (not failures)
- `StarletteDeprecationWarning`: `httpx` with `starlette.testclient` is deprecated (suggests `httpx2`).
- 6 × camelot `UserWarning` from `tests/unit/test_knowledge_parsers.py` (expected: fixtures without tables or with image-only pages).

## Pre-existing failures
None in the gates that were run.
