# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

This is an NM AI project built from Zypp's Python project template. It uses `uv` for dependency management and Ruff for linting/formatting.

## Commands

```bash
# Install all dependencies (including dev)
uv sync --dev

# Run tests
pytest

# Lint and format
ruff check --fix --extend-select=I   # lint + sort imports
ruff format                           # format code

# Run a single test
pytest src/tests/test_example.py::test_name

# Run all pre-commit hooks
pre-commit run --all-files

# Install pre-commit hooks (one-time setup)
pre-commit install
pre-commit install --hook-type commit-msg
```

## Architecture

### Entry point & initialization
- [src/__init__.py](src/__init__.py) — runs at import time: configures logging (ISO format + ms), detects environment (darwin/win32 → `development`, linux → `production`), manages a `tmp_dir`, and initialises Sentry. The Sentry DSN is left commented out until configured.
- [src/main.py](src/main.py) — application entry point; currently empty, add business logic here.

### Key conventions
- **Line length:** 120 characters (Ruff).
- **Temp directory:** `tmp_dir` in `src/__init__.py` — on macOS/Windows points to `data/`, on Linux uses a real `TemporaryDirectory`.
- **Sentry environment** is driven by `sys.platform`, not an env var — update the DSN string in `src/__init__.py` before deploying.
- **Tests** live in `src/tests/` and must follow `test_*.py` naming (enforced by pre-commit).

### Infrastructure
- [terraform/main.tf](terraform/main.tf) — Azure provider + remote backend config (fill in storage account details).
- [terraform/variables.tf](terraform/variables.tf) — `RG_NAME` and `LOC` variables; secrets go in `secrets.auto.tfvars` (never committed).

### CI/CD
- [.github/workflows/ci.yaml](.github/workflows/ci.yaml) — triggers on push to feature branches (not `main`/`development`/`staging`). Runs pre-commit and pytest against Python 3.12.
- [.github/workflows/_example_deploy_docker_image.yaml](.github/workflows/_example_deploy_docker_image.yaml) — Docker → ACR deployment template; disabled (`on: never`) until configured with `IMAGE_NAME` and Azure credentials.
