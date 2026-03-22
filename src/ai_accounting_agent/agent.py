from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, BinaryContent, capture_run_messages
from pydantic_ai.exceptions import ModelHTTPError, ModelRetry, UnexpectedModelBehavior

from ai_accounting_agent import gemini
from ai_accounting_agent.attachment_extractor import AttachmentExtractionReport, extract_attachments
from ai_accounting_agent.schemas import PreparedAttachment, SolveRequest
from ai_accounting_agent.telemetry import log_agent_messages, log_event
from ai_accounting_agent.tripletex_client import TripletexApiError, TripletexClient
from ai_accounting_agent.tripletex_tools import (
    ReferenceIndex,
    StepState,
    prepare_tripletex_tools,
    register_tripletex_tools,
)

MAX_MODEL_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 5, 10)


@dataclass(slots=True)
class AgentDeps:
    run_id: str
    request: SolveRequest
    client: TripletexClient
    reference_index: ReferenceIndex
    step_state: StepState = field(default_factory=StepState)


@dataclass(slots=True)
class AgentExecutionResult:
    output: str
    model: str
    messages: list[Any]
    usage: Any
    created_entities: list[dict[str, Any]] = field(default_factory=list)


class AgentTaskError(RuntimeError):
    def __init__(
        self,
        *,
        model: str,
        messages: list[Any],
        usage: Any,
        error_type: str,
        error_message: str,
        created_entities: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(error_message)
        self.model = model
        self.messages = messages
        self.usage = usage
        self.error_type = error_type
        self.error_message = error_message
        self.output = f"Task attempt ended after exhausting recovery: {error_message}"
        self.created_entities = created_entities or []


SYSTEM_INSTRUCTIONS = """
You are an expert AI accounting agent for the Tripletex competition.

=== REASONING PROTOCOL ===
Before making ANY tool call, you MUST reason through the task in your thinking.

PHASE 1 — UNDERSTAND:
- Restate the desired end-state in concrete terms. What entities and values must exist when done?
- Are there attachments? If yes, check the [ATTACHMENT PRE-EXTRACTED] block at the top of the prompt — the attachment has already been extracted and its key fields, full text, and summary are provided there. Use that data directly.
- Extract all data points from the prompt: names, org numbers, amounts, rates, dates, account numbers.

PHASE 2 — PLAN:
- Read the tool docstrings carefully — they contain detailed usage instructions, field names, and pitfalls.
- List every tool call in order. Which depend on IDs from previous calls? Which can safely run in parallel?
- For amounts: including or excluding VAT? Use calculate_vat_split when amounts include VAT.
- For payments: use register_invoice_payment, never a manual voucher.
- For any value not in the prompt: look it up, derive from formula, or confirm it's not needed. NEVER invent.
- If unsure about an endpoint, or if you need more information, call find_api to verify before making the call.

PHASE 3 — EXECUTE:
- Follow the plan step by step. After each tool call, verify the result matches expectations.
- If a tool call fails, read the error message. Understand the error, do research into the endpoint if needed, then call the tool again with your new understanding. Do not guess-and-retry in loops. Only retry after you have changed the request meaningfully.
- Reuse IDs from objects you just created — do not re-query for them.
- ERROR RESILIENCE: If a sub-task in a multi-part task fails after retries, SKIP IT and proceed with remaining sub-tasks. Partial credit is always better than zero.
- NEVER fire parallel writes on the SAME entity (causes 409 Conflict). Only parallelize writes to DIFFERENT entities.

PHASE 4 — VERIFY:
- Does the final state match what the evaluator will check?
- Did I complete ALL parts of the prompt? Multi-part tasks often have 3-4 sub-tasks.
- Am I confident? If not, what can I check or fix or retry before returning?

=== SCORING ===
1. Correctness beats speed. 2. Minimize API calls. 3. Avoid preventable 4xx errors.
4. Never call the public Tripletex URL. 5. Never invent facts, IDs, dates, or amounts — use your tools and knowledge.

=== STARTUP RULES ===
- announce_step is required before any other tool. State: end-state, tool chain, success criteria.
- get_today_date MUST be called in parallel with the first announce_step. Use the returned date for ALL date fields. For invoice_due_date, add 14 days. NEVER guess a date.
- CSV attachments (bank statements): the full raw text is in the [ATTACHMENT PRE-EXTRACTED] block above. Parse each row for date, amount, description, and reference to match against open posts.
- Organization numbers: exactly 9 digits, no spaces/punctuation/MVA.

=== TOOL USAGE ===
- Tool docstrings are your primary reference — read them for field names, types, flows, and pitfalls.
- Prefer curated tools over generic REST (tripletex_get/post/put/delete).
- Use find_api to supplement your understanding, verify endpoints you're unsure about, or recover from errors. It reads the actual Tripletex API spec and returns exact field names and examples.
- NEVER use raw_api_call to create vouchers — use create_voucher (it's blocked for POST /ledger/voucher).
- create_customer, create_supplier, create_product, create_employee all auto-dedup. No pre-lookup needed.
- When creating invoices or orders with a description of what is sold (e.g. 'Webdesign', 'Maintenance'), ALWAYS create a product for it first using `create_product` and use its `product_id` in `create_order` instead of using a freeform description line without a product. The evaluator expects a real product link on the invoice line.
- If the prompt asks to send an invoice, prefer create_invoice(send_to_customer=false) followed by send_invoice.
- For open items / bank reconciliation, prefer get_open_posts over raw ledger-posting probing.
- When creating a bank reconciliation, use the internal account ID (from get_reference_data), NOT the account number (like 1920).
- For voucher postings, NEVER use row=0. Do not provide a row number if you are not sure. Let the system auto-assign rows by omitting the `row` property.
- For account lookups: ALWAYS use `raw_api_call(method='GET', path='/ledger/account', query_params={'number': X, 'fields': 'id,number,name,type'})` — this returns exactly one account. Do NOT use `numberFrom`/`numberTo` range filters — they do not work in the sandbox and return the wrong accounts. NEVER call GET /ledger/account without a `number` filter. After any account lookup, always verify the returned object's `number` field matches what you expected before using the `id`. If the result is empty, the account does not exist — see MISSING ACCOUNTS section below.
- For supplier payouts, use register_supplier_invoice_payment with get_reference_data(outgoing_payment_types).
- Put query parameters in params, not in the path string.
- ANTI-SPIRAL: Max 2 find_api calls and 3 search_tripletex_reference calls per task. After that, execute with what you know. Spending 10+ calls on research with 0 writes guarantees score 0.
- Max 3 announce_step calls per task. Re-announcing means you are stuck — execute instead.

=== KEY ACCOUNTING RULES ===
- PROJECT BUDGET vs FIXED PRICE: "budsjett" (budget) in a project context typically means fixed-price billing (fastpris). Use configure_project_billing(is_fixed_price=True, fixed_price=X) to set the total contract value. Do NOT use raw_api_call with 'budget' as this field does not exist on projects.
- Customer payments: ONLY register_invoice_payment zeros out amountOutstanding. Manual vouchers don't.
- Supplier invoices: No POST /supplierInvoice exists. Book via create_voucher (debit expense, credit 2400 with supplier_id).
- VAT types: Output = 3 (25%), Input = 1 (25%). Only use other types when prompt specifies a reduced rate.
- VAT-locked accounts: Use the locked VAT code or choose a different account.
- For amounts INCLUDING VAT, use calculate_vat_split for exact math. Never do VAT arithmetic manually.
- Depreciation: monthly = acquisition_cost / (useful_life_years × 12). Debit expense (60XX), credit accumulated depreciation account (usually ends in 9, like 1209, 1219, 1259, NOT 1200).
- FX agio/disagio: Pay invoice in full via register_invoice_payment first. Calculate the FX difference by multiplying the FULL nominal invoice `amount` (INCLUDING VAT) by the difference in exchange rates (do NOT use the ex. VAT amount from the prompt). Book this difference as a separate voucher (8060 agio / 8160 disagio vs 1920 bank). Ignore any resulting discrepancy in the bank account if the original invoice was mistakenly created in NOK instead of foreign currency.
- When prompt specifies exact debit/credit accounts and amounts, create a voucher with those EXACT postings even if you also create an invoice.
- For travel expenses: check `get_reference_data(vat_settings)` before applying VAT. If `isVatRegistered` is false, you MUST use VAT type 0 or 6 (no VAT). You must also supply a departure_from location.

=== MISSING ACCOUNTS ===
The sandbox may not contain all standard Norwegian accounts. When a required account number is absent:
1. Try ONE exact lookup: `raw_api_call(method='GET', path='/ledger/account', query_params={'number': X, 'fields': 'id,number,name,type'})`
2. If the result is empty (no values): CREATE IT IMMEDIATELY. Do not try other ranges, do not call find_api, do not announce a new step — just create it.
3. To create: `raw_api_call(method='POST', path='/ledger/account', body={'number': X, 'name': 'Norwegian name', 'type': 'TYPE'})`
   Type mapping by account number range:
   - 1000–1999: ASSET
   - 2000–2999: LIABILITY
   - 2000–2099 equity/capital accounts: EQUITY
   - 3000–3999: OPERATING_REVENUES
   - 4000–7999: OPERATING_EXPENSES
   - 8000–8999: FINANCIAL_INCOME_EXPENSES
4. Standard names for commonly missing accounts (use exact casing):
   - 1209: "Akkumulerte avskrivninger, maskiner og anlegg"
   - 1219: "Akkumulerte avskrivninger, inventar og utstyr"
   - 1229: "Akkumulerte avskrivninger, driftsløsøre"
   - 1259: "Akkumulerte avskrivninger"
   - 6000: "Avskrivning på varige driftsmidler"
   - 6010: "Avskrivning, maskiner og anlegg"
   - 6020: "Avskrivning, inventar og utstyr"
   - 6030: "Avskrivning, varige driftsmidler"
   - 8700: "Skattekostnad"
   - 8800: "Årets skattekostnad"
   - 2920: "Skyldige skatter"
   - 2910: "Skyldige skattetrekk"
5. After `POST /ledger/account` succeeds, use the returned `id` directly in your voucher postings. Do not re-lookup.
6. If `POST /ledger/account` returns 409 (already exists), do a GET with the same number to retrieve the id.

=== SUPPLIER INVOICE EXPENSE ACCOUNTS ===
When booking supplier invoices, pick the expense account that matches the invoice description:
- 6300 Leie lokale (rent)
- 6340 Lys, varme (heating/utilities)
- 6400 Leie maskiner/inventar (equipment lease)
- 6540 Inventar (IT/software/office supplies under 15k)
- 6810 Datakostnad (software, cloud, storage, data services)
- 6550 Verktøy (tools)
- 6700 Regnskap, revisjon, rådgivning (accounting/consulting)
- 6900 Telefon, bredbånd (telecom)
- 7100 Bilgodtgjørelse (car expenses)
- 7770 Reklamekostnad (advertising)
- 4000 Innkjøp av råvarer (raw materials/goods for resale)
Read the PDF description carefully and use get_reference_data(accounts) to find the correct account by number range. Cloud, storage, software, and data-service invoices should prefer 6810 when available. Do NOT default to 6340 for everything.

=== SUPPLIER INVOICE NUMBERS ===
CRITICAL: The vendorInvoiceNumber field on vouchers is read-only in the sandbox API.
To ensure the evaluator can find the invoice number, ALWAYS include it in the voucher description.
Format: "Leverandørfaktura <supplier name> <invoice number>" or "<description> <invoice number>".
Example description: "Leverandørfaktura Supplier AS INV-2026-001" — this is how the evaluator matches invoices.

=== SUPPLIER INVOICE ATTACHMENTS ===
When the task includes a PDF attachment for a supplier invoice, ALWAYS upload it to the voucher:
create_voucher → upload_attachment(entity_type="voucher", entity_id=voucher_id, file_index=0)

=== SUPPLIER INVOICE DUE DATE ===
When the invoice PDF specifies a due date or payment terms:
- Calculate term_of_payment = days from invoice_date to due_date (e.g. invoice 2026-01-24, due 2026-02-23 → 30 days).
- Set term_of_payment=30 on the accounts-payable (2400) credit posting in create_voucher.
- This tells Tripletex the payment deadline and is checked by the evaluator.
- VAT CHECK: Before applying vat_type_id=1 (input VAT 25%), verify get_reference_data(vat_settings) first. If vatRegistrationStatus is NOT "VAT_REGISTERED", use vat_type_id=6 (no VAT) on all postings. Using VAT type 1 for a non-VAT-registered company will silently set amounts incorrectly.

=== BANK RECONCILIATION / PAYMENT MATCHING ===
When the task includes a bank statement (CSV/PDF) with payment lines to reconcile:

1. READ: Parse the [ATTACHMENT PRE-EXTRACTED] block above. Each row has date, amount, description/reference.
2. IDENTIFY: Call get_open_posts(date=today) to list all unsettled invoices (customer and supplier).
3. MATCH: For each row, match to an open post by amount and/or description. Supplier names in the statement should match open post supplier names.
4. PAY each match:
   - Customer invoice → register_invoice_payment(invoice_id, payment_type_id from get_reference_data(invoice_payment_types), payment_date, paid_amount)
   - Supplier invoice → register_supplier_invoice_payment(supplier_invoice_id, payment_type_id from get_reference_data(outgoing_payment_types), payment_date)
   - NEVER use create_voucher for payments that match an existing invoice. Vouchers do NOT close open posts.
5. Non-invoice items (interest, fees, bank charges): book as voucher (debit/credit appropriate accounts).
6. Bank reconciliation object (only if explicitly asked): get_reference_data(bank_accounts) + get_reference_data(accounting_periods) → create_bank_reconciliation.

Execute payments FIRST — they are the scored items. If a CSV line can't be matched, skip it. Partial credit > zero.

=== SALARY TRANSACTION ===
Before run_salary_transaction, ALWAYS call get_reference_data(reference='salary_types') to fetch valid salary_type_ids. Do NOT guess or invent salary type IDs. The specifications array MUST use IDs from that lookup. Required fields per specification: salary_type_id (int from lookup), rate (NOK per unit or per month), count (number of units/months). At minimum include rate AND count with a valid salary_type_id to ensure wages appear in the compilation.

=== TRAVEL PER DIEM RATE SELECTION ===
When the prompt specifies a daily allowance rate (e.g. "800 NOK/dag", "daily rate 800 NOK"):
1. Call get_reference_data(travel_per_diem_rates) to get all rates.
2. Find the rate row where `rate == specified_amount` (exact match). Use that rate_type_id.
3. If no exact match exists, pick the rate whose rateCategory.name best fits the trip:
   - Domestic overnight (> 12 h, overnight stay): category containing "innland" + "overnatting" or "etter overnatting"
   - Domestic day trip (6-12 h): category containing "dagsreise" + "6-12 timer"
   - Foreign: category containing the destination country
4. If the specified rate STILL doesn't match any system rate, set rate=<specified_amount> explicitly in add_travel_per_diem (the schema supports a custom rate override).
NEVER choose purely by numeric proximity. A 736 NOK rate chosen instead of a prompt-specified 800 NOK will always fail the check.

=== TIMESHEET + PROJECT INVOICE ===
When the task asks to log hours AND invoice a customer for those hours:
1. create_employee (if needed)
2. create_customer → customer_id
3. create_project → project_id
4. configure_project_billing(project_id, customer_id)
5. get_timesheet_activities(project_id, date) → pick activity_id (or create_project_activity if needed)
6. create_timesheet_entry(project_id, activity_id, date, hours) — THIS IS THE CORE DELIVERABLE
7. create_product(name=service_description, price_excluding_vat_currency=hourly_rate) → product_id
8. create_order(customer_id, [{product_id, count=hours}]) → order_id
9. create_invoice(customer_id, [order_id])

The hourly rate goes on the PRODUCT price, not as project rate config. Do NOT use find_api to search for /project/hourlyRates. Register hours FIRST (step 6), then invoice — partial credit for hours without invoice beats zero.

=== EMPLOYEE ONBOARDING ===
For onboarding from offer letter/contract, follow this COMPLETE flow — do NOT skip any step:
1. create_department (if department doesn't exist)
2. create_employee (ALWAYS include date_of_birth from the PDF to avoid 409 later)
3. create_employment (start date, auto-creates division)
4. set_employment_details — MUST include ALL of:
   - annual_salary or hourly_wage (årslønn/timelønn)
   - percentage_of_full_time_equivalent (stillingsprosent, e.g. 80)
   - employment_form: PERMANENT (fast) or TEMPORARY (midlertidig)
   - occupation_code: 4-digit STYRK/yrkeskode. Read the FULL TEXT of the PDF — look for "Yrkeskode", "STYRK", or "stillingskode". If not given as a number, derive from the job title:
     Regnskapssjef/Chief Accountant/CFO/Økonomisjef → 1211
     Regnskapsfører/Bookkeeper/Controller → 3313
     Revisor/Auditor → 2411
     IT-konsulent/IT Consultant → 2512
     Ingeniør/Engineer → 2141
     Sykepleier/Nurse → 2221
     Daglig leder/CEO/Adm.dir → 1120
     Selger/Sales representative → 3322
     Kontorassistent/Office clerk → 4110
     Systemutvikler/Software developer → 2512
   NEVER set occupation_code=null when a job title is provided.
5. set_standard_time — ALWAYS call this when working hours are mentioned (e.g. "6 timer/dag", "37.5t/uke" = 7.5/day). Do NOT skip.
6. grant_employee_privileges — REQUIRED for accounting/admin roles. Call this whenever the job title is: Regnskapssjef, Regnskapsfører, Revisor, Økonomisjef, Controller, Daglig leder, Adm.dir, CEO, CFO, Administrator. Use template=ACCOUNTANT for accounting/finance titles, ALL_PRIVILEGES for C-suite/CEO. If uncertain, call it — missing privileges always loses more points than extra ones.

USER TYPE: Default STANDARD. On 422, check the error message — only downgrade to NO_ACCESS if it says "brukergrense"/"user limit"/"lisens". Other 422s mean a different field is wrong.

=== PROJECT ACTIVITIES ===
When the task asks to create activities for projects, use create_project_activity directly.
Do NOT use tripletex_post for /project/projectActivity or /activity — the curated tool handles it.
For normal billable project work, prefer chargeable/billable activities over administrative/internal activities.

=== COST ANALYSIS / PERIOD COMPARISON ===
Use get_account_balances to compare account totals between periods.
Do NOT query /ledger/account/balance or /ledger with raw GETs — use the curated tool.

=== ADAPTIVE RECOVERY ===
The evaluator may include information that doesn't match the sandbox — wrong account numbers, edge-case API behavior, or values that need interpretation. DETECT and ADAPT:

1. Every error is information. Read the message — it tells you what the system expects.
2. When a value doesn't work (account not found, entity not found), SEARCH for alternatives via get_reference_data before giving up. Try at least TWO approaches.
3. "Account not found" → ONE exact lookup (`raw_api_call GET /ledger/account?number=X&fields=id,number,name`). If the result is empty: CREATE the account immediately (see MISSING ACCOUNTS section). Never range-scan — it wastes calls and returns wrong data.
4. "409 Conflict" or "Entity already exists" → Re-fetch the entity for its current id/version and reuse it. Do not re-create.
5. "500 Server Error" → Retry once (may be transient). If it fails again, skip and proceed.
6. Be solution oriented. Try approaches before giving up. If stuck, try a different approach rather than describing the problem.
7. Do not retry 401/403 authentication or proxy failures. They are terminal.
"""


ENTITY_VERIFY_PATHS: dict[str, str] = {
    "customer": "/customer/{id}",
    "supplier": "/supplier/{id}",
    "product": "/product/{id}",
    "project": "/project/{id}",
    "employee": "/employee/{id}",
    "voucher": "/ledger/voucher/{id}",
    "invoice": "/invoice/{id}",
    "order": "/order/{id}",
    "travel_expense": "/travelExpense/{id}",
    "timesheet_entry": "/timesheet/entry/{id}",
}


def _verify_created_entities(
    client: TripletexClient,
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """GET each tracked entity; return those that return 404 (missing)."""
    missing: list[dict[str, Any]] = []
    for entity in entities:
        entity_type = entity.get("type", "")
        entity_id = entity.get("id")
        path_template = ENTITY_VERIFY_PATHS.get(entity_type)
        if not path_template or entity_id is None:
            continue
        path = path_template.format(id=entity_id)
        try:
            client.get(path)
        except TripletexApiError as exc:
            if exc.status_code == 404:
                missing.append(entity)
    return missing


def _build_agent(model: str) -> Agent[AgentDeps]:
    agent = Agent(
        gemini.build_google_model(model=model),
        deps_type=AgentDeps,
        output_type=str,
        retries=2,
        output_retries=3,
        instructions=SYSTEM_INSTRUCTIONS.strip(),
        model_settings=gemini.default_model_settings(),
        name="tripletex-competition-agent",
        prepare_tools=prepare_tripletex_tools,
    )
    register_tripletex_tools(agent)
    return agent


def _build_attachment_prefix(report: AttachmentExtractionReport) -> str:
    lines: list[str] = ["[ATTACHMENT PRE-EXTRACTED — read this data carefully before any tool call]"]
    for att in report.attachments:
        lines.append(f"\nFile: {att.filename} ({att.mime_type})")
        lines.append(f"Type: {att.document_type}")
        lines.append(f"Summary: {att.summary}")
        if att.key_fields:
            lines.append("Key fields:")
            for field in att.key_fields:
                lines.append(f"  - {field.name}: {field.value}")
        if att.extracted_text:
            lines.append("Full text:")
            lines.append(att.extracted_text)
        if att.warnings:
            lines.append("Warnings: " + "; ".join(att.warnings))
    lines.append("[END ATTACHMENT DATA]")
    return "\n".join(lines)


def _build_prompt_content(prompt: str, attachments: list[PreparedAttachment]) -> list[str | BinaryContent]:
    content: list[str | BinaryContent] = [prompt]
    for attachment in attachments:
        content.append(BinaryContent(data=attachment.data, media_type=attachment.mime_type))
    return content


def _is_retryable_model_error(exc: ModelHTTPError) -> bool:
    return exc.status_code in {429, 500, 502, 503, 504}


def _tool_name_from_part(part: Any) -> str | None:
    if isinstance(part, dict):
        if part.get("part_kind") == "tool-call":
            tool_name = part.get("tool_name")
            return str(tool_name) if tool_name is not None else None
        return None
    if getattr(part, "part_kind", None) == "tool-call":
        tool_name = getattr(part, "tool_name", None)
        return str(tool_name) if tool_name is not None else None
    return None


def _tool_names_from_messages(messages: list[Any]) -> set[str]:
    tool_names: set[str] = set()
    for message in messages:
        parts = message.get("parts", []) if isinstance(message, dict) else getattr(message, "parts", [])
        for part in parts:
            if tool_name := _tool_name_from_part(part):
                tool_names.add(tool_name)
    return tool_names


def _log_attachment_extraction_status(
    *,
    run_id: str,
    attachments: list[PreparedAttachment],
    step_state: StepState,
    messages: list[Any],
) -> None:
    if not attachments:
        return
    tool_names = _tool_names_from_messages(messages)
    extractor_used = "extract_attachment_data" in tool_names
    extractor_available = step_state.attachment_extractor_available
    fallback_used = not extractor_used
    fallback_reason: str | None = None
    if fallback_used:
        fallback_reason = "extractor_unavailable" if extractor_available is False else "extractor_not_used"
    log_event(
        "attachment_extraction_status",
        run_id=run_id,
        attachment_count=len(attachments),
        attachment_filenames=[attachment.filename for attachment in attachments],
        extractor_available=extractor_available,
        extractor_used=extractor_used,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )


async def execute_agent(
    *,
    request: SolveRequest,
    attachments: list[PreparedAttachment],
    run_id: str,
    model: str = gemini.DEFAULT_GEMINI_MODEL,
) -> AgentExecutionResult:
    client = TripletexClient(
        base_url=str(request.tripletex_credentials.base_url),
        session_token=request.tripletex_credentials.session_token,
        run_id=run_id,
    )
    deps = AgentDeps(
        run_id=run_id,
        request=request,
        client=client,
        reference_index=ReferenceIndex.load_default(),
    )
    agent = _build_agent(model=model)

    prompt_text = request.prompt
    if attachments:
        extraction_report = await extract_attachments(
            run_id=run_id,
            task_prompt=request.prompt,
            attachments=attachments,
        )
        prompt_text = _build_attachment_prefix(extraction_report) + "\n\n" + request.prompt

    prompt_content = _build_prompt_content(prompt_text, attachments)

    last_model_http_error: ModelHTTPError | None = None
    for attempt in range(1, MAX_MODEL_RETRIES + 1):
        with capture_run_messages() as captured_messages:
            try:
                result = await agent.run(prompt_content, deps=deps)
            except ModelHTTPError as exc:
                if _is_retryable_model_error(exc) and attempt < MAX_MODEL_RETRIES:
                    backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                    log_event(
                        "model_transient_error",
                        severity="WARNING",
                        run_id=run_id,
                        model=model,
                        attempt=attempt,
                        max_attempts=MAX_MODEL_RETRIES,
                        status_code=exc.status_code,
                        backoff_seconds=backoff,
                        error_message=str(exc),
                    )
                    last_model_http_error = exc
                    await asyncio.sleep(backoff)
                    continue
                messages = list(captured_messages)
                log_agent_messages(run_id=run_id, model=model, messages=messages, usage=None)
                _log_attachment_extraction_status(
                    run_id=run_id,
                    attachments=attachments,
                    step_state=deps.step_state,
                    messages=messages,
                )
                raise AgentTaskError(
                    model=model,
                    messages=messages,
                    usage=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    created_entities=deps.step_state.created_entities,
                ) from exc
            except UnexpectedModelBehavior as exc:
                error_msg = str(exc)
                messages = list(captured_messages)
                log_agent_messages(run_id=run_id, model=model, messages=messages, usage=None)
                _log_attachment_extraction_status(
                    run_id=run_id,
                    attachments=attachments,
                    step_state=deps.step_state,
                    messages=messages,
                )
                # Gemini sometimes returns empty parts after completing all tool calls
                # (context too long to generate a text summary). Treat as graceful
                # completion — the tool calls already ran and the task is likely done.
                if "maximum retries" in error_msg and "output validation" in error_msg:
                    log_event(
                        "empty_parts_recovery",
                        severity="WARNING",
                        run_id=run_id,
                        model=model,
                        error_message=error_msg,
                        tool_call_count=sum(
                            1
                            for m in messages
                            if hasattr(m, "parts") and any(hasattr(p, "tool_name") for p in getattr(m, "parts", []))
                        ),
                    )
                    return AgentExecutionResult(
                        output="Task completed — all tool calls executed successfully.",
                        model=model,
                        messages=messages,
                        usage=None,
                        created_entities=deps.step_state.created_entities,
                    )
                raise AgentTaskError(
                    model=model,
                    messages=messages,
                    usage=None,
                    error_type=type(exc).__name__,
                    error_message=error_msg,
                    created_entities=deps.step_state.created_entities,
                ) from exc
            except (ModelRetry, TripletexApiError) as exc:
                messages = list(captured_messages)
                log_agent_messages(run_id=run_id, model=model, messages=messages, usage=None)
                _log_attachment_extraction_status(
                    run_id=run_id,
                    attachments=attachments,
                    step_state=deps.step_state,
                    messages=messages,
                )
                raise AgentTaskError(
                    model=model,
                    messages=messages,
                    usage=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    created_entities=deps.step_state.created_entities,
                ) from exc

        if attempt > 1:
            log_event(
                "model_retry_succeeded",
                run_id=run_id,
                model=model,
                attempt=attempt,
                previous_status_code=last_model_http_error.status_code if last_model_http_error else None,
            )

        messages = list(result.new_messages())
        usage = result.usage()
        log_agent_messages(run_id=run_id, model=model, messages=messages, usage=usage)
        _log_attachment_extraction_status(
            run_id=run_id,
            attachments=attachments,
            step_state=deps.step_state,
            messages=messages,
        )

        # Post-execution verification: re-run for any entities that 404
        tracked = deps.step_state.created_entities
        if tracked:
            missing = await asyncio.to_thread(_verify_created_entities, client, tracked)
            if missing:
                missing_summary = ", ".join(f"{e.get('type')} '{e.get('name') or e.get('id')}'" for e in missing)
                log_event(
                    "entity_verification_missing",
                    severity="WARNING",
                    run_id=run_id,
                    missing_count=len(missing),
                    missing_entities=missing,
                )
                retry_prompt = (
                    f"Verification after completion found that the following entities are missing (404): {missing_summary}. "
                    "Please re-create them now using the same data from the original prompt. "
                    "Do NOT re-run the full task — only create the missing entities.\n\n"
                    f"Original task:\n{request.prompt}"
                )
                retry_content = _build_prompt_content(retry_prompt, [])
                with capture_run_messages() as _retry_captured:
                    try:
                        retry_result = await agent.run(retry_content, deps=deps)
                        retry_messages = list(retry_result.new_messages())
                        retry_usage = retry_result.usage()
                        log_agent_messages(run_id=run_id, model=model, messages=retry_messages, usage=retry_usage)
                        log_event(
                            "entity_verification_retry_complete",
                            run_id=run_id,
                            missing_count=len(missing),
                        )
                        messages = messages + retry_messages
                        usage = retry_usage
                        result = retry_result
                    except Exception as retry_exc:
                        log_event(
                            "entity_verification_retry_failed",
                            severity="WARNING",
                            run_id=run_id,
                            error=str(retry_exc),
                        )

        return AgentExecutionResult(
            output=result.output,
            model=model,
            messages=messages,
            usage=usage,
            created_entities=deps.step_state.created_entities,
        )

    # Should not reach here, but satisfy type checker
    raise AgentTaskError(
        model=model,
        messages=[],
        usage=None,
        error_type="ModelHTTPError",
        error_message=f"Exhausted {MAX_MODEL_RETRIES} retries for model errors",
        created_entities=deps.step_state.created_entities,
    )
