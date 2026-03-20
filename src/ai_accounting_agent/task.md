# Tripletex — AI Accounting Agent Task Specification

Build an AI agent that completes accounting tasks in Tripletex through a single HTTPS endpoint. The platform sends a prompt, optional file attachments, and Tripletex proxy credentials. Your agent must interpret the task, call the Tripletex API through the provided proxy, and finish within 5 minutes.

## Submission Contract

Your agent must expose exactly one HTTPS endpoint:

- Path: `/solve`
- Method: `POST`
- Content-Type: `application/json`
- Timeout budget: `300` seconds

The endpoint must return HTTP `200` with this JSON when the task is finished:

```json
{
  "status": "completed"
}
```

## Request Format

The platform sends a JSON body with the prompt, optional attachments, and Tripletex credentials:

```json
{
  "prompt": "Opprett en ansatt med navn Ola Nordmann, ola@example.org. Han skal være kontoadministrator.",
  "files": [
    {
      "filename": "faktura.pdf",
      "content_base64": "JVBERi0xLjQg...",
      "mime_type": "application/pdf"
    }
  ],
  "tripletex_credentials": {
    "base_url": "https://<provided-per-submission>/v2",
    "session_token": "abc123..."
  }
}
```

### Fields

| Field | Type | Description |
| --- | --- | --- |
| `prompt` | `string` | The task in Norwegian natural language. In practice, prompts may appear in `7` languages. |
| `files` | `array` | Attachments such as PDFs and images. May be empty. |
| `files[].filename` | `string` | Original filename. |
| `files[].content_base64` | `string` | Base64-encoded file content. |
| `files[].mime_type` | `string` | MIME type such as `application/pdf` or `image/png`. |
| `tripletex_credentials.base_url` | `string` | Proxy API URL for this submission. Use this instead of the standard Tripletex URL. |
| `tripletex_credentials.session_token` | `string` | Session token used for Tripletex authentication. |

## Authentication

There are two separate authentication layers:

### 1. Your endpoint authentication

If you configured an API key when submitting your agent, the platform sends:

```text
Authorization: Bearer <your-api-key>
```

Use this only to protect your own `/solve` endpoint from unauthorized callers.

### 2. Tripletex API authentication

All Tripletex API calls must use HTTP Basic Auth with:

- Username: `0`
- Password: the `session_token` from the request

Example:

```python
import requests

response = requests.get(
    f"{base_url}/employee",
    auth=("0", session_token),
    params={"fields": "id,firstName,lastName,email"},
)
```

Important:

- Always use the `base_url` provided in the request.
- Do not call the public Tripletex base URL directly.
- All calls must go through the submission-specific proxy.

## Functional Requirements

Your agent must:

- expose an HTTPS endpoint
- accept `POST /solve`
- parse the prompt and optional attachments
- authenticate to Tripletex using `auth=("0", session_token)`
- perform all API calls through the provided `base_url`
- finish within `300` seconds
- return `{"status": "completed"}` with HTTP `200`

## How the Challenge Works

1. You submit an HTTPS endpoint URL on the platform.
2. The platform provisions a fresh Tripletex sandbox account.
3. It sends one randomly selected task to your `/solve` endpoint.
4. Your agent interprets the prompt, optionally processes files, and executes the necessary Tripletex API calls.
5. After your endpoint returns, the platform verifies the resulting Tripletex state field by field.
6. Your leaderboard score updates based on correctness and efficiency.

Each submission starts from a fresh sandbox, so assume the environment is empty unless your current task creates prerequisite records first.

## Task Characteristics

| Category | Details |
| --- | --- |
| Task types | `30` accounting task types |
| Variants | `56` per task (`7` languages × `8` data sets) |
| Prompt languages | `nb`, `en`, `es`, `pt`, `nn`, `de`, `fr` |
| Timeout | `5` minutes |
| API | Tripletex `v2` through authenticated proxy |
| Files | Some tasks include PDFs or images |
| Best-score model | Your best historical score per task is kept |
| Max theoretical score | Up to `6.0` on Tier 3 with perfect efficiency |

## Standard Tripletex Endpoint Families

All standard Tripletex `v2` endpoints are available through the proxy. Common ones:

| Endpoint | Methods | Description |
| --- | --- | --- |
| `/employee` | `GET`, `POST`, `PUT` | Manage employees |
| `/customer` | `GET`, `POST`, `PUT` | Manage customers |
| `/product` | `GET`, `POST` | Manage products |
| `/invoice` | `GET`, `POST` | Create and query invoices |
| `/order` | `GET`, `POST` | Manage orders |
| `/travelExpense` | `GET`, `POST`, `PUT`, `DELETE` | Travel expense reports |
| `/project` | `GET`, `POST` | Manage projects |
| `/department` | `GET`, `POST` | Manage departments |
| `/ledger/account` | `GET` | Query chart of accounts |
| `/ledger/posting` | `GET` | Query ledger postings |
| `/ledger/voucher` | `GET`, `POST`, `DELETE` | Manage vouchers |

## API Usage Conventions

- Use `fields` to select only the properties you need: `?fields=id,firstName,lastName,*`
- Use `from` and `count` for pagination: `?from=0&count=100`
- `POST` and `PUT` requests send JSON bodies
- `DELETE` requests typically include the entity id in the URL path, such as `DELETE /employee/123`
- List responses are wrapped as:

```json
{
  "fullResultSize": 1,
  "values": [{ "...": "..." }]
}
```

- Single-object responses are typically wrapped as:

```json
{
  "value": { "...": "..." }
}
```

## Minimal `/solve` Example

```python
import base64
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.post("/solve")
async def solve(request: Request):
    body = await request.json()
    prompt = body["prompt"]
    files = body.get("files", [])
    creds = body["tripletex_credentials"]

    base_url = creds["base_url"]
    token = creds["session_token"]
    auth = ("0", token)

    for f in files:
        data = base64.b64decode(f["content_base64"])
        Path(f["filename"]).write_bytes(data)

    # TODO: Use an LLM to interpret the prompt and execute
    # the appropriate Tripletex API calls.

    return JSONResponse({"status": "completed"})
```

Run locally:

```bash
pip install fastapi uvicorn requests
uvicorn main:app --host 0.0.0.0 --port 8000
```

Expose locally over HTTPS for testing:

```bash
npx cloudflared tunnel --url http://localhost:8000
```

## Common API Examples

### List employees

```python
resp = requests.get(
    f"{base_url}/employee",
    auth=auth,
    params={"fields": "id,firstName,lastName,email"},
)
employees = resp.json()["values"]
```

### Create a customer

```python
resp = requests.post(
    f"{base_url}/customer",
    auth=auth,
    json={
        "name": "Acme AS",
        "email": "post@acme.no",
        "isCustomer": True,
    },
)
customer_id = resp.json()["value"]["id"]
```

### Create an invoice

```python
today = "2026-03-03"
resp = requests.post(
    f"{base_url}/invoice",
    auth=auth,
    json={
        "invoiceDate": today,
        "invoiceDueDate": today,
        "customer": {"id": customer_id},
        "orders": [{"id": order_id}],
    },
)
```

### Search for a specific entity

```python
resp = requests.get(
    f"{base_url}/customer",
    auth=auth,
    params={
        "name": "Acme",
        "fields": "id,name,email",
        "count": 10,
    },
)
matches = resp.json()["values"]
```

## Building an Effective Agent

Your agent should follow a tight loop:

1. Parse the prompt.
2. Extract task type, entities, values, and relationships.
3. Decode and inspect attached files when present.
4. Map the task to the smallest correct Tripletex API workflow.
5. Execute the workflow through the provided proxy.
6. Verify that the created or updated data matches the requested task.
7. Return `{"status": "completed"}`.

Practical guidance:

- prompts may be written in Norwegian, but production handling should cover all `7` supported languages
- some tasks require prerequisites such as customers, products, orders, or project setup
- attachments may contain invoices, contracts, or travel-expense details
- Tripletex error responses are useful; parse them and correct in as few retries as possible

## Common Task Patterns

| Pattern | Example | Typical API Flow |
| --- | --- | --- |
| Create single entity | "Create employee Ola Nordmann" | `POST /employee` |
| Create with linking | "Create invoice for customer" | `GET /customer` → `POST /order` → `POST /invoice` |
| Modify existing | "Add phone to contact" | `GET /customer` → `PUT /customer/{id}` |
| Delete or reverse | "Delete travel expense" | `GET /travelExpense` → `DELETE /travelExpense/{id}` |
| Multi-step setup | "Register payment" | `POST /customer` → `POST /invoice` → payment flow |

## Scoring

### Field-by-field correctness

After your agent responds, the platform queries Tripletex to verify what was created or modified. Each task has a list of checks with point values.

Example for a "Create employee" task:

| Check | Points |
| --- | --- |
| Employee found | `2` |
| Correct first name | `1` |
| Correct last name | `1` |
| Correct email | `1` |
| Administrator role assigned | `5` |

Correctness is normalized to `0.0`–`1.0`:

```text
correctness = points_earned / max_points
```

### Tier multiplier

Each task belongs to a difficulty tier:

| Tier | Multiplier | Example tasks |
| --- | --- | --- |
| Tier 1 | `×1` | Create employee, create customer |
| Tier 2 | `×2` | Create invoice, register payment |
| Tier 3 | `×3` | Complex multi-step workflows |

A perfect Tier 2 correctness score yields a base score of `2.0`.

### Efficiency bonus

If correctness is a perfect `1.0`, you may receive an efficiency bonus that can nearly double the tier score. The bonus is driven by:

- call efficiency: how many API calls you used compared to the best known solution
- error cleanliness: how many of your calls resulted in `4xx` errors

Interpretation:

| Scenario (Tier 2 task) | Score |
| --- | --- |
| Failed all checks | `0.0` |
| `80%` of checks passed | `1.6` |
| Perfect, but many errors and extra calls | `~2.1` |
| Perfect, efficient, a few errors | `~2.6` |
| Perfect, best-in-class efficiency, zero errors | `4.0` |

Important:

- the efficiency bonus only applies to perfect submissions
- benchmarks are recalculated periodically
- your best score per task is preserved; poor runs do not reduce it

## Best Score and Leaderboard

- Each of the `30` task types is tracked independently.
- Your score for a task is your all-time best score for that task.
- The leaderboard total is the sum of your best scores across task types.
- One excellent run is enough to lock in a strong score for a task.

## Task Assignment and Tier Release

Each submission receives one task, weighted toward tasks you have attempted less often. Over time you should encounter all task types.

Current tier availability:

- Tier 1: open
- Tier 2: open
- Tier 3: opens early Saturday; monitor the competition page for updates

## Rate Limits

| Limit | Verified teams | Unverified teams |
| --- | --- | --- |
| Concurrent submissions | `3` | `1` |
| Per task per day | `10` | `3` |

## Common Error Patterns

| Error | Cause | Fix |
| --- | --- | --- |
| `401 Unauthorized` | Wrong auth format | Use Basic Auth with username `0` and session token as password |
| `404 Not Found` | Wrong endpoint path | Check the Tripletex `v2` endpoint family and path |
| `422 Validation Error` | Missing required fields or invalid body/query | Read the response carefully and correct the payload |
| Empty `values` array | No result found | Broaden or adjust the search parameters |
| Timeout | Agent too slow | Reduce unnecessary calls and retries |

## Optimization Guidance

Higher scores come from correctness first, then efficiency. Design your agent to be deliberate:

- plan before calling: fully parse the prompt before making API requests
- avoid trial-and-error: every `4xx` hurts efficiency
- minimize lookups: do not fetch objects you already created and have ids for
- verify intelligently: confirm only the fields that matter for correctness
- batch when possible: prefer list-friendly operations where supported
- use error messages well: fix the issue in one retry, not several
- preserve UTF-8 text correctly: Norwegian characters such as `æ`, `ø`, and `å` are supported

## Implementation Checklist

- expose `POST /solve` over HTTPS
- validate optional Bearer auth if you configured an API key
- parse `prompt`, `files`, and `tripletex_credentials`
- base64-decode attachments and inspect them when relevant
- use `base_url` from the request for every Tripletex call
- authenticate with `auth=("0", session_token)`
- execute the correct Tripletex workflow
- keep API calls low and avoid `4xx` responses
- return HTTP `200` with `{"status": "completed"}`

## Sandbox Account

Every team gets a persistent Tripletex sandbox to explore the API and web UI before submitting.

### Getting your sandbox

1. Go to the Tripletex submission page on the platform
2. Click "Get Sandbox Account"
3. Your sandbox is provisioned instantly

You receive:

- **Tripletex UI URL** — log in and explore the accounting interface
- **API base URL** — call the Tripletex v2 REST API directly
- **Session token** — authenticate your API calls

### Logging into the web UI

1. Go to `https://kkpqfuj-amager.tripletex.dev`
2. Enter the email shown on your sandbox card
3. Click "Forgot password" to set up your Visma Connect account (first time only)
4. Set a password and log in

Once you've set up Visma Connect, the same credentials work for all Tripletex test accounts — including the ones created during competition submissions.

### Sandbox vs competition

| | Sandbox | Competition |
|---|---------|-------------|
| Account | Persistent, yours to keep | Fresh account per submission |
| API access | Direct to Tripletex | Via authenticated proxy |
| Data | Accumulates over time | Starts empty each time |
| Scoring | None | Automated field-by-field |

### Tips

- Create test data manually in the UI, then query via API to understand response formats
- Try the same operations your agent will need: creating employees, invoices, products, etc.
- The sandbox token expires `2026-03-31`
- Each team gets one sandbox — all team members share it
