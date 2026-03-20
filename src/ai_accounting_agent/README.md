# `ai_accounting_agent`

This package contains a Cloud Run-ready Tripletex competition agent.

## What It Exposes

- `POST /solve`
- JSON request body matching the challenge contract
- Optional `x-api-key: <token>` endpoint protection via `AI_ACCOUNTING_AGENT_API_KEY`
- Exact success response:

```json
{
  "status": "completed"
}
```

## Request Shape

```json
{
  "prompt": "Opprett en ansatt med navn Ola Nordmann.",
  "files": [
    {
      "filename": "invoice.pdf",
      "content_base64": "JVBERi0xLjQK...",
      "mime_type": "application/pdf"
    }
  ],
  "tripletex_credentials": {
    "base_url": "https://tx-proxy.example/v2",
    "session_token": "abc123"
  }
}
```

## Package Layout

- `main.py`: FastAPI entrypoint and `/solve` handler
- `schemas.py`: public request/response models and typed tool payloads
- `agent.py`: PydanticAI runtime, prompt assembly, and execution entrypoint
- `gemini.py`: Gemini model selection and thinking configuration
- `tripletex_client.py`: authenticated Tripletex HTTP client with structured logging
- `tripletex_tools.py`: curated Tripletex tools plus generic REST escape hatches
- `telemetry.py`: structured logging, redaction, attachment metadata, and tool-call logs
- `task.md`: challenge contract and scoring notes
- `tripletex_api.md`: verified Tripletex endpoint guidance

## Model Behavior

- Default model: `gemini-3.1-pro-preview`
- Thinking is enabled with `include_thoughts=true` and `thinking_level=HIGH`
- Parallel tool calls are disabled to keep the tool trace easier to follow and reduce risky concurrency
- The agent is instructed to optimize for score:
  - correctness first
  - then fewer calls
  - then fewer 4xx errors

Before Tripletex actions, the agent must call `announce_step` to log:

- task understanding
- planned tools
- success criteria

## Logging

The runtime emits structured logs suitable for stdout and Google Cloud Logging.

Logged data includes:

- full prompt text
- attachment metadata and SHA-256 hashes
- agent transcript and provider-exposed thought parts
- agent step announcements
- tool calls, arguments, results, and errors
- Tripletex request and response metadata
- run-level completion or failure

Always redacted:

- session tokens
- bearer or API keys
- auth headers
- raw attachment bytes
- raw base64 file contents

## Local Development

Install dependencies:

```bash
uv sync
```

Run locally:

```bash
uv run fastapi dev
```

Alternative:

```bash
uv run python src/main.py
```

Required environment for local model execution:

- `GEMINI_API_KEY`

Optional:

- `AI_ACCOUNTING_AGENT_API_KEY`
- `TRIPLETEX_API_URL`
- `TRIPLETEX_SESSION_TOKEN`

If `src/ai_accounting_agent/.env` exists, it is loaded automatically for local development.

## Tests

Run unit tests:

```bash
uv run pytest src/ai_accounting_agent/tests
```

Run evaluator-like live capability tests:

```bash
RUN_LIVE_API_TESTS=1 uv run pytest -m live_api src/ai_accounting_agent/tests/test_live_api.py
```

The realistic end-to-end API test artifacts are stored under:

```text
src/ai_accounting_agent/test_runs/realistic_scenarios/
```

Each live run starts by deleting the previous local realistic-scenario artifacts and then writes fresh timestamped folders containing:

- `scenario_spec.json`
- a redacted `request_response.json`
- a matching `cloud_logging.json` fetched from Google Cloud Logging using the same trace token
- `observed_summary.json` with extracted tool sequence, HTTP call counts, and pass/fail checks

Run live Tripletex sandbox tool tests:

```bash
RUN_LIVE_TRIPLETEX_TESTS=1 uv run pytest -m live_tripletex src/ai_accounting_agent/tests/test_tripletex_live_tools.py
```

## Cloud Run Deployment

Terraform provisions:

- Artifact Registry
- Secret Manager secret containers for `GEMINI_API_KEY` and optional endpoint bearer auth
- a public Cloud Run service with `300s` timeout

High-level flow:

1. Build and push the container image
2. Apply Terraform
3. Add a Secret Manager version for `GEMINI_API_KEY`
4. Optionally add a Secret Manager version for `AI_ACCOUNTING_AGENT_API_KEY`
5. Call the public Cloud Run URL directly

Example request:

```bash
curl \
  -X POST \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_ENDPOINT_TOKEN" \
  https://YOUR_CLOUD_RUN_URL/solve \
  -d @request.json
```

## Notes

- Browser CORS support is intentionally not enabled; this service is designed for server-to-server calls.
- The public Cloud Run URL is intended to be invoked directly. When `AI_ACCOUNTING_AGENT_API_KEY` is set, callers and helper scripts send it as `x-api-key` so Google Frontend does not intercept the request before it reaches the app.
- The curated tool layer covers the verified Tripletex workflows. Unsupported or unverified domains still fall back to generic REST tools.
