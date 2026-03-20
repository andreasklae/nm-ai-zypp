# Operations Guide: AI Accounting Agent

## Quick Reference

| What | Value |
|---|---|
| GCP Project | `ai-nm26osl-1850` |
| Cloud Run Service | `ai-accounting-agent` |
| Region | `europe-west4` |
| Service URL | `https://ai-accounting-agent-jfzhrxdx4a-ez.a.run.app` |
| Artifact Registry | `europe-west4-docker.pkg.dev/ai-nm26osl-1850/ai-accounting-agent/ai-accounting-agent` |
| gcloud config dir | `.gcloud/` (repo root — use `CLOUDSDK_CONFIG=.gcloud` prefix) |
| Tripletex sandbox | `https://kkpqfuj-amager.tripletex.dev` |
| Sandbox token expiry | 2026-03-31 |

## Deploy to Google Cloud

### Prerequisites

- `gcloud` CLI installed
- Authenticated via the repo-local config: `CLOUDSDK_CONFIG=.gcloud gcloud auth list`
- No Docker daemon needed — we use Cloud Build

### Build and deploy (one command each)

```bash
# 1. Build the image in the cloud and push to Artifact Registry
CLOUDSDK_CONFIG=.gcloud gcloud builds submit . \
  --project=ai-nm26osl-1850 \
  --region=europe-west4 \
  --tag=europe-west4-docker.pkg.dev/ai-nm26osl-1850/ai-accounting-agent/ai-accounting-agent:latest \
  --timeout=600

# 2. Deploy the new image to Cloud Run
CLOUDSDK_CONFIG=.gcloud gcloud run deploy ai-accounting-agent \
  --project=ai-nm26osl-1850 \
  --region=europe-west4 \
  --image=europe-west4-docker.pkg.dev/ai-nm26osl-1850/ai-accounting-agent/ai-accounting-agent:latest \
  --quiet
```

### Verify deployment

```bash
# Check which revision is serving
CLOUDSDK_CONFIG=.gcloud gcloud run services describe ai-accounting-agent \
  --project=ai-nm26osl-1850 \
  --region=europe-west4 \
  --format="table(status.traffic[].revisionName,status.traffic[].percent)"
```

## Query the Agent

### Basic request

```bash
curl -s -X POST "https://ai-accounting-agent-jfzhrxdx4a-ez.a.run.app/solve" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <API_KEY from .env>" \
  -d '{
    "prompt": "Opprett en kunde med navn Test AS.",
    "files": [],
    "tripletex_credentials": {
      "base_url": "https://kkpqfuj-amager.tripletex.dev/v2",
      "session_token": "<TRIPLETEX_SESSION_TOKEN from .env>"
    }
  }'
```

Expected response: `{"status":"completed"}`

### With a trace token (for log correlation)

Add `trace_token=<unique_id>` at the end of the prompt to find this specific run in the logs:

```bash
TOKEN=$(python3 -c "import uuid; print(uuid.uuid4().hex[:10])")
echo "trace_token=$TOKEN"

curl -s -w "\nHTTP:%{http_code} TIME:%{time_total}s\n" \
  -X POST "https://ai-accounting-agent-jfzhrxdx4a-ez.a.run.app/solve" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <API_KEY>" \
  -d "{
    \"prompt\": \"Opprett en kunde med navn Test AS. trace_token=$TOKEN\",
    \"files\": [],
    \"tripletex_credentials\": {
      \"base_url\": \"https://kkpqfuj-amager.tripletex.dev/v2\",
      \"session_token\": \"<SESSION_TOKEN>\"
    }
  }"
```

### With file attachments

Base64-encode the file and include it in the `files` array:

```bash
FILE_B64=$(base64 < invoice.pdf)

curl -s -X POST "https://ai-accounting-agent-jfzhrxdx4a-ez.a.run.app/solve" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <API_KEY>" \
  -d "{
    \"prompt\": \"Bokfør leverandørfakturaen i vedlegget.\",
    \"files\": [{
      \"filename\": \"invoice.pdf\",
      \"content_base64\": \"$FILE_B64\",
      \"mime_type\": \"application/pdf\"
    }],
    \"tripletex_credentials\": {
      \"base_url\": \"https://kkpqfuj-amager.tripletex.dev/v2\",
      \"session_token\": \"<SESSION_TOKEN>\"
    }
  }"
```

### Concurrent requests (test asyncio stability)

Send multiple requests in parallel with `&` and `wait`:

```bash
curl -s -X POST "$URL" -H "..." -d '...' &
curl -s -X POST "$URL" -H "..." -d '...' &
curl -s -X POST "$URL" -H "..." -d '...' &
wait
```

## Read the Logs

All commands use the repo-local gcloud config. Prefix every `gcloud` call with `CLOUDSDK_CONFIG=.gcloud`.

### Find recent requests

```bash
CLOUDSDK_CONFIG=.gcloud gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="ai-accounting-agent"
   AND jsonPayload.event="request_received"' \
  --project=ai-nm26osl-1850 \
  --limit=10 \
  --format=json
```

### Find a specific run by trace token

```bash
CLOUDSDK_CONFIG=.gcloud gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="ai-accounting-agent"
   AND jsonPayload.prompt:"trace_token=YOUR_TOKEN"' \
  --project=ai-nm26osl-1850 \
  --limit=5 \
  --format=json
```

This returns the `request_received` entry. Extract the `run_id` from `jsonPayload.run_id`.

### Get full trace for a run

```bash
CLOUDSDK_CONFIG=.gcloud gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="ai-accounting-agent"
   AND jsonPayload.run_id="<RUN_ID>"' \
  --project=ai-nm26osl-1850 \
  --limit=200 \
  --format=json
```

### Analyze a run with the helper script

```bash
python3 src/ai_accounting_agent/tests/_analyze_logs.py <RUN_ID>
```

This prints: prompt, status, tool sequence, HTTP call count, errors, search queries, and output.

You can pass multiple run IDs:

```bash
python3 src/ai_accounting_agent/tests/_analyze_logs.py <RUN_ID_1> <RUN_ID_2> <RUN_ID_3>
```

### Find errors only

```bash
CLOUDSDK_CONFIG=.gcloud gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="ai-accounting-agent"
   AND severity>=ERROR
   AND timestamp>="2026-03-20T17:00:00Z"' \
  --project=ai-nm26osl-1850 \
  --limit=20 \
  --format=json
```

### Find task failures

```bash
CLOUDSDK_CONFIG=.gcloud gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="ai-accounting-agent"
   AND jsonPayload.event="task_error"
   AND timestamp>="2026-03-20T17:00:00Z"' \
  --project=ai-nm26osl-1850 \
  --limit=10 \
  --format=json
```

### Key log events to look for

| Event | Meaning |
|---|---|
| `request_received` | Incoming `/solve` request with prompt and file metadata |
| `agent_step` | Agent called `announce_step` — shows planned tools |
| `tool_call` | Agent called a tool — shows tool name and arguments |
| `tool_result` | Tool returned — shows result and duration |
| `tool_error` | Tool raised an error — shows error type and message |
| `tripletex_http_request` | Outgoing HTTP call to Tripletex — shows method, path, params |
| `tripletex_http_response` | Tripletex response — shows status code, duration, response body |
| `agent_messages` | Full agent transcript with thinking parts |
| `task_complete` | Run finished successfully — shows output and usage |
| `task_error` | Run failed — shows error type and message |

### Useful jq/python one-liners

```bash
# List recent prompts with timestamps
CLOUDSDK_CONFIG=.gcloud gcloud logging read '...' --format=json | \
  python3 -c "
import json, sys
for e in json.load(sys.stdin):
    jp = e.get('jsonPayload', {})
    print(f'{e[\"timestamp\"][:19]} {jp.get(\"prompt\",\"\")[:120]}')
"

# Extract tool sequence from a run
CLOUDSDK_CONFIG=.gcloud gcloud logging read '...run_id...' --format=json | \
  python3 -c "
import json, sys
entries = json.load(sys.stdin)
tools = [e['jsonPayload']['tool'] for e in entries
         if e.get('jsonPayload',{}).get('event') == 'tool_call']
print(' -> '.join(tools))
"

# Count HTTP errors in a run
CLOUDSDK_CONFIG=.gcloud gcloud logging read '...run_id...' --format=json | \
  python3 -c "
import json, sys
entries = json.load(sys.stdin)
errors = [f'{e[\"jsonPayload\"][\"method\"]} {e[\"jsonPayload\"][\"path\"]} -> {e[\"jsonPayload\"][\"status_code\"]}'
          for e in entries
          if e.get('jsonPayload',{}).get('event') == 'tripletex_http_response'
          and e['jsonPayload'].get('status_code', 0) >= 400]
print(f'{len(errors)} errors:', errors)
"
```

## Local Development

### Run locally

```bash
uv sync --dev
uv run fastapi dev
```

Server starts at `http://localhost:8000`. Send requests to `http://localhost:8000/solve`.

### Run tests

```bash
# Unit tests (fast, no external calls)
uv run pytest src/ai_accounting_agent/tests -v

# Live agent tests against Tripletex sandbox (slow, needs GEMINI_API_KEY)
RUN_LOCAL_AGENT_TESTS=1 uv run pytest src/ai_accounting_agent/tests/test_local_agent.py -v -s

# Live API tests against deployed Cloud Run (needs all credentials)
RUN_LIVE_API_TESTS=1 uv run pytest -m live_api src/ai_accounting_agent/tests/test_live_api.py -v -s
```

### Lint and format

```bash
uv run ruff check --fix --extend-select=I src/ai_accounting_agent/
uv run ruff format src/ai_accounting_agent/
```
