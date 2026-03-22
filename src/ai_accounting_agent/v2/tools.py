import re
from typing import Any

from pydantic_ai import RunContext

from ai_accounting_agent.v2.catalog import get_endpoint_catalog
from ai_accounting_agent.v2.learned_rules import get_learned_rules_for_endpoint
from ai_accounting_agent.tripletex_client import TripletexClient, TripletexApiError
from ai_accounting_agent.schemas import CalculateVatSplitInput

_BETA_PATH_PATTERN = re.compile(r"^/incomingInvoice", re.IGNORECASE)


def search_endpoints(ctx: RunContext[Any], query: str) -> list[dict[str, Any]]:
    """Search for relevant Tripletex API endpoints based on a natural language query.

    Use this to discover which API endpoints can fulfill your goal.
    Formulate a good query by combining the business object, the action, and any constraints
    (e.g., "find unpaid invoice for customer" or "create customer order").

    Args:
        query: The search string used to find endpoints. Use object + action + constraints. You MUST provide a specific string here, not an empty dict.

    Returns:
        A list of up to 15 matching endpoints containing `method`, `path_template`, and `summary`.
    """
    if not query or len(query.strip()) < 3 or query == "{}":
        return [{"error": "You must provide a descriptive search term (e.g. 'create project' or 'find customer'). Do not leave the query empty or too short."}]

    ctx.deps.step_state.search_call_count += 1
    if ctx.deps.step_state.search_call_count > 15:
        return [{"error": "Search limit exceeded. You have already searched 15 times in this run. Stop searching and use the endpoints you have already found. If an operation is blocked by missing configuration, report the error to the user instead of searching for a workaround."}]

    catalog = get_endpoint_catalog()
    results = catalog.search(query, top_k=15)

    formatted_results = []
    for r in results:
        entry = {
            "method": r.method,
            "path_template": r.path,
            "summary": r.summary,
        }
        if r.learned_rules:
            entry["learned_rules"] = r.learned_rules
        formatted_results.append(entry)

    return formatted_results


def get_endpoint_schema(ctx: RunContext[Any], method: str, path_template: str) -> dict[str, Any]:
    """Retrieve the full OpenAPI schema and parameter requirements for a specific endpoint.

    Before calling `call_tripletex_api`, you MUST use this tool to understand
    which parameters are required, what the request body expects, and any specific rules.
    You MUST pass the exact `path_template` returned by `search_endpoints`. Do not try to
    substitute path parameters or guess the URL at this stage.

    Args:
        method: HTTP method from the endpoint schema, e.g., GET, POST, PUT, DELETE.
        path_template: OpenAPI path template EXACTLY as returned by `search_endpoints` (e.g., `/invoice/{id}/:payment`).

    Returns:
        A dictionary containing the full OpenAPI schema, request body constraints, and any learned rules.
    """
    catalog = get_endpoint_catalog()
    record = catalog.get_exact_formatted(method, path_template)

    if not record:
        return {"error": f"Endpoint {method} {path_template} not found in catalog."}

    ctx.deps.step_state.inspected_endpoints.add((record["method"], record["path_template"]))

    return record


def call_tripletex_api(
    ctx: RunContext[Any],
    method: str,
    path_template: str,
    path_params: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
    body: Any | None = None,
    file_index: int | None = None,
) -> dict[str, Any]:
    """Execute one Tripletex API call after schema inspection.

    Use this only after `get_endpoint_schema`.
    Pass the OpenAPI path template exactly as returned by `search_endpoints`,
    then provide `path_params` to substitute placeholders like `{id}`.

    Args:
        method: HTTP method from the endpoint schema, e.g. GET, POST, PUT, DELETE.
        path_template: OpenAPI path template, e.g. `/invoice/{id}/:payment`.
        path_params: Dictionary of values used to replace `{...}` placeholders in `path_template`.
        query_params: Query-string parameters for filtering or field selection.
        body: Request body (can be a dictionary, list, etc.). Use internal Tripletex IDs for references to existing objects.
        file_index: If the endpoint requires file uploads (like attachments), provide the 0-based index of the file from the task's attachments array.

    Returns:
        A dictionary containing `status_code`, `ok` (boolean), and either `response` (JSON data/binary marker) or `error` (detailed reason for failure).
        An `ok=True` means the HTTP call succeeded; the caller must still verify business success.
    """
    client: TripletexClient = ctx.deps.client

    # Enforce schema inspection
    method_upper = method.upper()
    if (method_upper, path_template) not in ctx.deps.step_state.inspected_endpoints:
        # Only block write operations — GET calls are free for scoring and blocking wastes round trips
        if method_upper != "GET":
            return {
                "error": (
                    f"You must inspect the schema for {method_upper} {path_template} "
                    "using `get_endpoint_schema` BEFORE calling this tool."
                )
            }

    # Apply some of the most critical hard-coded rules directly to prevent loops.
    _rules = get_learned_rules_for_endpoint(method_upper, path_template)
    if _rules:
        pass # The model has already seen these via get_endpoint_schema
    def validate_date_range(params: dict[str, Any] | None, from_key: str, to_key: str, endpoint: str) -> None:
        if not params:
            return
        if from_key not in params or to_key not in params:
            raise ValueError(f"Learned Rule Violation: GET {endpoint} requires {from_key} and {to_key} query parameters. The 'To' date is exclusive.")
        if params[from_key] == params[to_key]:
            # Automatically increment to_key by 1 day if they are the same
            import datetime
            try:
                date_from = datetime.datetime.strptime(params[from_key], "%Y-%m-%d")
                date_to = date_from + datetime.timedelta(days=1)
                params[to_key] = date_to.strftime("%Y-%m-%d")
            except Exception:
                raise ValueError(f"Learned Rule Violation: For GET {endpoint}, '{to_key}' is exclusive. It must be strictly later than '{from_key}'.")

    # Hard-block BETA endpoints — they always return 403
    if _BETA_PATH_PATTERN.match(path_template):
        return {
            "error": (
                "BETA ENDPOINT BLOCKED: /incomingInvoice is a BETA-restricted API and always returns 403 Forbidden. "
                "Do NOT call this endpoint. To book a supplier invoice use POST /ledger/voucher instead."
            )
        }

    try:
        if path_template == "/order" and method_upper == "GET":
            validate_date_range(query_params, "orderDateFrom", "orderDateTo", "/order")

        if path_template == "/invoice" and method_upper == "GET":
            validate_date_range(query_params, "invoiceDateFrom", "invoiceDateTo", "/invoice")

        if path_template == "/ledger/voucher" and method_upper == "GET":
            validate_date_range(query_params, "dateFrom", "dateTo", "/ledger/voucher")

        if path_template == "/ledger/posting" and method_upper == "GET":
            validate_date_range(query_params, "dateFrom", "dateTo", "/ledger/posting")
    except ValueError as ve:
        return {"error": str(ve)}
            
    if path_template == "/supplier" and method_upper == "GET":
        if query_params and "name" in query_params:
            return {"error": "Learned Rule Violation: The API does not support partial text search on supplier names using the 'name' parameter. Filter by 'supplierNumber' or 'organizationNumber', or pull all active suppliers and filter locally."}
            
    if path_template == "/customer" and method_upper == "GET":
        if query_params and "name" in query_params:
            return {"error": "Learned Rule Violation: Filter customers by 'customerName', not 'name'."}
            
    if path_template == "/employee" and method_upper == "POST":
        if not body or "dateOfBirth" not in body:
            return {"error": "Learned Rule Violation: Always set 'dateOfBirth' when creating an employee. It is required before employment/salary can be created."}

    # Voucher must include postings
    if path_template == "/ledger/voucher" and method_upper == "POST":
        if not body or not body.get("postings"):
            return {"error": "Learned Rule Violation: POST /ledger/voucher requires a non-empty 'postings' array. A voucher without postings will always fail. Include at least one debit and one credit posting in the same request."}

        # Automatically set amountGrossCurrency = amountGross for all postings in a voucher if not explicitly provided
        if isinstance(body.get("postings"), list):
            for posting in body["postings"]:
                if isinstance(posting, dict) and "amountGross" in posting and "amountGrossCurrency" not in posting:
                    posting["amountGrossCurrency"] = posting["amountGross"]

    # Substitute path params
    resolved_path = path_template
    if path_params:
        for k, v in path_params.items():
            resolved_path = resolved_path.replace(f"{{{k}}}", str(v))

    if "{" in resolved_path or "}" in resolved_path:
        return {
            "error": f"Path '{resolved_path}' still contains unresolved placeholders. Make sure to provide all required `path_params`."
        }

    try:
        if file_index is not None:
            # Requires file payload
            # Accessing the prepared attachments list from the request
            attachments = ctx.deps.request.files
            if file_index < 0 or file_index >= len(attachments):
                return {"error": f"file_index {file_index} out of range (0-{len(attachments)-1})"}
            
            f = attachments[file_index]
            resp_data = client.upload(
                path=resolved_path,
                file_data=f.decoded_bytes(),
                filename=f.filename,
                mime_type=f.mime_type,
                params=query_params,
            )
        else:
            resp_data = client.request(
                method=method_upper,
                path=resolved_path,
                params=query_params,
                json_body=body,
            )
            
        return {
            "status_code": getattr(client, "_last_status_code", 200),  # Tracked on TripletexClient
            "ok": True,
            "response": resp_data,
        }
    except TripletexApiError as exc:
        if exc.status_code == 403:
            return {
                "status_code": 403,
                "ok": False,
                "error": (
                    f"403 Forbidden on {method_upper} {resolved_path}. "
                    "This is likely a BETA or restricted endpoint. "
                    "DO NOT retry this call. Stop and report to the user that this operation is not available."
                ),
            }
        if exc.status_code == 409 and "projectActivity" in resolved_path:
            return {
                "status_code": exc.status_code,
                "ok": False,
                "error": "A project activity with this name probably exists. Use get_endpoint_schema and then call GET /activity/>forTimeSheet with the projectId to find its ID instead of re-creating it."
            }
        if exc.status_code == 409 and "timesheet/entry" in resolved_path:
            return {
                "status_code": exc.status_code,
                "ok": False,
                "error": "A timesheet entry already exists. Use get_endpoint_schema and then call GET /timesheet/entry to fetch the existing entry and update it (PUT) instead of creating a new one."
            }
        return {
            "status_code": exc.status_code,
            "ok": False,
            "error": exc.response_body,
        }
    except Exception as e:
        return {"status_code": 0, "ok": False, "error": str(e)}


def calculate_vat_split(ctx: RunContext[Any], payload: CalculateVatSplitInput) -> dict[str, Any]:
    """Calculate the net amount and VAT amount from a single VAT-inclusive total.

    Use this helper instead of attempting VAT arithmetic yourself.
    This works for exactly ONE gross amount and ONE VAT rate at a time.
    For documents with mixed rates, you must separate them and call this tool individually.

    Args:
        payload: Contains the `amount_including_vat` and the `vat_percentage` (e.g. 25 for 25%).

    Returns:
        A dictionary containing exact mathematically precise splits: `amount_excluding_vat`, `vat_amount`, `amount_including_vat`, and `vat_percentage`.
    """
    gross = payload.amount_including_vat
    rate = payload.vat_percentage / 100
    net = round(gross / (1 + rate), 2)
    vat = round(gross - net, 2)
    # Adjust for any round-off discrepancies
    if abs((net + vat) - gross) > 0.001:
        vat = round(gross - net, 2)

    return {
        "amount_excluding_vat": net,
        "vat_amount": vat,
        "amount_including_vat": gross,
        "vat_percentage": payload.vat_percentage,
    }
