# Tripletex Cloud-Ready Competition Agent

This repository contains a FastAPI service packaged for direct Google Cloud Run deployment. The public API is a single JSON `POST /solve` endpoint that executes Tripletex tasks through the submission-specific proxy and returns:

```json
{
  "status": "completed"
}
```

The implementation lives in [`src/ai_accounting_agent`](src/ai_accounting_agent). The package-local guide at [`src/ai_accounting_agent/README.md`](src/ai_accounting_agent/README.md) documents:

- request contract
- package structure
- Gemini and tool behavior
- logging and redaction
- local development
- tests
- Cloud Run deployment

## Quick Start

Install dependencies:

```bash
uv sync
```

Run locally:

```bash
uv run fastapi dev
```

Run unit tests:

```bash
uv run pytest src/ai_accounting_agent/tests
```

Deploy with Terraform from [`terraform/`](terraform) after building and pushing the container image.
