"""Tripletex MCP Server — exposes all Tripletex tools as MCP tools.

Credentials are read from environment variables:
  TRIPLETEX_API_URL       — base URL for the Tripletex API proxy
  TRIPLETEX_SESSION_TOKEN — session token used as Basic Auth password
  GEMINI_API_KEY          — required only for extract_attachment_data and find_api sub-agents
"""
from __future__ import annotations

import base64
import os
from datetime import date
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from ai_accounting_agent.schemas import (
    AddTravelExpenseCostInput,
    AddTravelMileageAllowanceInput,
    AddTravelPerDiemInput,
    CalculateVatSplitInput,
    ConfigureProjectBillingInput,
    CreateAccountingDimensionInput,
    CreateBankReconciliationInput,
    CreateContactInput,
    CreateCreditNoteInput,
    CreateCustomerInput,
    CreateDepartmentInput,
    CreateEmployeeInput,
    CreateEmploymentInput,
    CreateInvoiceInput,
    CreateOrderInput,
    CreateProductInput,
    CreateProjectActivityInput,
    CreateProjectInput,
    CreateSupplierInput,
    CreateTimesheetEntryInput,
    CreateTravelExpenseInput,
    CreateVoucherInput,
    CreateWebhookSubscriptionInput,
    FindApiInput,
    GetAccountBalancesInput,
    GetOpenPostsInput,
    GetTimesheetActivitiesInput,
    GrantEmployeePrivilegesInput,
    PreparedAttachment,
    RawApiCallInput,
    ReferenceLookupInput,
    RegisterInvoicePaymentInput,
    RegisterSupplierInvoicePaymentInput,
    ReverseVoucherInput,
    RunSalaryTransactionInput,
    SendInvoiceInput,
    SetEmploymentDetailsInput,
    SetStandardTimeInput,
    TransitionTravelExpenseInput,
    UpdateEmployeeInput,
    UploadAttachmentInput,
)
from ai_accounting_agent.tripletex_client import TripletexClient
from ai_accounting_agent.tripletex_tools import ReferenceIndex, StepState, TripletexService

mcp = FastMCP("Tripletex MCP Server", stateless_http=True)


def _make_service() -> TripletexService:
    """Create a fresh TripletexService instance from environment credentials."""
    run_id = uuid4().hex
    client = TripletexClient(
        base_url=os.environ["TRIPLETEX_API_URL"],
        session_token=os.environ["TRIPLETEX_SESSION_TOKEN"],
        run_id=run_id,
    )
    return TripletexService(
        client=client,
        run_id=run_id,
        reference_index=ReferenceIndex.load_default(),
        # has_announced_step=True skips the _require_step() gate (not needed in MCP)
        step_state=StepState(has_announced_step=True),
    )


# ---------------------------------------------------------------------------
# Utility tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_today_date() -> dict[str, str]:
    """Return today's date. Call this whenever you need a date for any field (invoice_date,
    order_date, voucher date, salary month/year, etc.). Never guess or assume the current date."""
    today = date.today()
    return {
        "today": today.isoformat(),
        "year": str(today.year),
        "month": str(today.month),
        "day": str(today.day),
    }


@mcp.tool()
def search_tripletex_reference(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the local Tripletex API reference docs for verified guidance.

    Use before making raw API calls to verify endpoint shape. Once you have a recipe, stop
    searching and execute. Avoid repeated search loops.
    """
    return _make_service().search_tripletex_reference(query=query, max_results=max_results)


@mcp.tool()
async def extract_attachment_data(
    task_prompt: str,
    files: list[dict[str, str]],
) -> dict[str, Any]:
    """Extract structured data from file attachments via a dedicated Gemini sub-agent.

    Each file in the list must be a dict with keys: filename, mime_type, content_base64.
    Returns one extracted entry per attachment with summary, extracted text, key fields, and warnings.
    Requires GEMINI_API_KEY in the environment.
    """
    attachments = [
        PreparedAttachment(
            filename=f["filename"],
            mime_type=f["mime_type"],
            data=base64.b64decode(f["content_base64"]),
        )
        for f in files
    ]
    return await _make_service().extract_attachment_data(task_prompt=task_prompt, files=attachments)


@mcp.tool()
async def find_api(need: str) -> dict[str, Any]:
    """Ask an API specialist sub-agent to find the right Tripletex endpoint.

    The sub-agent reads the actual Tripletex API specification and returns exact endpoint details
    with field names, types, pitfalls, and example bodies. Call this BEFORE raw_api_call when
    unsure about the correct endpoint. Requires GEMINI_API_KEY in the environment.
    """
    return await _make_service().find_api(need)


@mcp.tool()
def raw_api_call(payload: RawApiCallInput) -> dict[str, Any]:
    """Execute a Tripletex API call based on find_api guidance.

    Use after find_api has provided the endpoint, method, and required fields.
    Never POST to /ledger/voucher — use create_voucher instead.
    Returns the full API response body.
    """
    return _make_service().raw_api_call(payload)


@mcp.tool()
def calculate_vat_split(payload: CalculateVatSplitInput) -> dict[str, Any]:
    """Split a gross amount (including VAT) into net and VAT components.

    Pure calculation — no Tripletex API call is made.
    Example: amount_including_vat=61150, vat_percentage=25 → net=48920.0, vat=12230.0
    """
    return TripletexService.calculate_vat_split(payload)


# ---------------------------------------------------------------------------
# Reference / lookup tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_reference_data(payload: ReferenceLookupInput) -> dict[str, Any]:
    """Fetch common reference/lookup data on demand.

    Common patterns (no filters needed):
    - reference="whoami" → employeeId, companyId
    - reference="vat_settings" → company VAT registration status
    - reference="vat_types" → all VAT codes with id, name, percentage
    - reference="invoice_payment_types" → payment type IDs
    - reference="outgoing_payment_types" → payment type IDs for supplier invoices
    - reference="travel_cost_categories" / "travel_payment_types" / "travel_per_diem_rates" / "travel_mileage_rates"
    - reference="currencies" → currency IDs (NOK=1, SEK=2, EUR=5)
    - reference="salary_types" → salary type IDs
    - reference="departments", "accounting_periods", "bank_accounts", "events"
    - reference="accounts", "customers", "suppliers", "employees", "products", "projects"
    """
    return _make_service().get_reference_data(payload)


@mcp.tool()
def get_account_balances(payload: GetAccountBalancesInput) -> dict[str, Any]:
    """Get posting sums (balances) per account for a date range.

    REQUIRED: date_from, date_to (YYYY-MM-DD).
    Optional: account_number_from, account_number_to to filter by account range.
    """
    return _make_service().get_account_balances(payload)


@mcp.tool()
def get_open_posts(payload: GetOpenPostsInput) -> dict[str, Any]:
    """Fetch open (unsettled) postings.

    REQUIRED: date (YYYY-MM-DD).
    Optional: account_id, customer_id, supplier_id, employee_id, project_id.
    """
    return _make_service().get_open_posts(payload)


@mcp.tool()
def get_timesheet_activities(payload: GetTimesheetActivitiesInput) -> dict[str, Any]:
    """Fetch valid activity IDs for a project/date/employee combination.

    Call this before create_timesheet_entry — never invent activity IDs.
    REQUIRED: project_id, date (YYYY-MM-DD). employee_id defaults to logged-in employee.
    """
    return _make_service().get_timesheet_activities(payload)


# ---------------------------------------------------------------------------
# Generic Tripletex HTTP tools (last-resort)
# ---------------------------------------------------------------------------


@mcp.tool()
def tripletex_get(path: str, params: dict[str, Any] | None = None, cache_key: str | None = None) -> Any:
    """LAST RESORT: Call a Tripletex GET endpoint when no curated tool fits.

    Only use after verifying the endpoint via search_tripletex_reference.
    Put query parameters in params, not in the path string.
    """
    return _make_service().tripletex_get(path=path, params=params, cache_key=cache_key)


@mcp.tool()
def tripletex_post(path: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
    """LAST RESORT: Call a Tripletex POST endpoint when no curated tool fits.

    Only use after verifying the endpoint shape via search_tripletex_reference.
    """
    return _make_service().tripletex_post(path=path, body=body, params=params)


@mcp.tool()
def tripletex_put(path: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
    """LAST RESORT: Call a Tripletex PUT endpoint when no curated tool fits.

    PUT requires id + version for optimistic locking. GET the entity first to get the current version.
    """
    return _make_service().tripletex_put(path=path, body=body, params=params)


@mcp.tool()
def tripletex_delete(path: str, params: dict[str, Any] | None = None) -> Any:
    """LAST RESORT: Call a Tripletex DELETE endpoint when no curated tool fits."""
    return _make_service().tripletex_delete(path=path, params=params)


# ---------------------------------------------------------------------------
# Customer / supplier / product tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_customer(payload: CreateCustomerInput) -> dict[str, Any]:
    """Create a Tripletex customer.

    REQUIRED: name. Optional: organization_number, email, invoice_send_method,
    invoices_due_in + invoices_due_in_type, address fields.
    Returns the created customer with its id — reuse this id in subsequent tools.
    """
    return _make_service().create_customer(payload)


@mcp.tool()
def create_supplier(payload: CreateSupplierInput) -> dict[str, Any]:
    """Create a Tripletex supplier.

    REQUIRED: name. Optional: organization_number, email, invoice_email.
    Returns the created supplier with its id — reuse this id in create_voucher postings.
    """
    return _make_service().create_supplier(payload)


@mcp.tool()
def create_product(payload: CreateProductInput) -> dict[str, Any]:
    """Create a Tripletex product with a net price and optional VAT type.

    REQUIRED: name, price_excluding_vat_currency (net price).
    Auto-checks for existing products by number — no duplicate created if already exists.
    """
    return _make_service().create_product(payload)


@mcp.tool()
def create_contact(payload: CreateContactInput) -> dict[str, Any]:
    """Create a contact linked to a customer.

    REQUIRED: first_name, last_name, customer_id. Optional: email.
    Flow: create_customer → create_contact
    """
    return _make_service().create_contact(payload)


# ---------------------------------------------------------------------------
# Employee / HR tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_employee(payload: CreateEmployeeInput) -> dict[str, Any]:
    """Create a Tripletex employee.

    REQUIRED: first_name, last_name. user_type defaults to "STANDARD".
    If an employee with the given email already exists, returns the existing employee.
    To set a start date, call create_employment after this tool.
    To grant admin access, call grant_employee_privileges after this tool.
    """
    return _make_service().create_employee(payload)


@mcp.tool()
def update_employee(payload: UpdateEmployeeInput) -> dict[str, Any]:
    """Update an existing employee (versioned GET-then-PUT handled automatically).

    REQUIRED: employee_id. All other fields are optional — only changed fields need to be provided.
    Do NOT use tripletex_put for employee updates.
    """
    return _make_service().update_employee(payload)


@mcp.tool()
def grant_employee_privileges(payload: GrantEmployeePrivilegesInput) -> dict[str, Any]:
    """Grant employee entitlements using a Tripletex permission template.

    Templates: ALL_PRIVILEGES (admin), ACCOUNTANT, INVOICING_MANAGER, PERSONELL_MANAGER,
    AUDITOR, DEPARTMENT_LEADER, NONE_PRIVILEGES.
    """
    return _make_service().grant_employee_privileges(payload)


@mcp.tool()
def create_employment(payload: CreateEmploymentInput) -> dict[str, Any]:
    """Create an employment record for an employee (sets their start date and links to a division).

    REQUIRED: employee_id, start_date (YYYY-MM-DD).
    division_id is auto-resolved. Returns existing employment if one already exists (idempotent).
    Flow: create_employee → create_employment
    """
    return _make_service().create_employment(payload)


@mcp.tool()
def set_employment_details(payload: SetEmploymentDetailsInput) -> dict[str, Any]:
    """Set employment details (salary, percentage, occupation code) on an existing employment record.

    REQUIRED: employment_id (from create_employment result), date (effective date).
    Flow: create_employee → create_employment → set_employment_details
    """
    return _make_service().set_employment_details(payload)


@mcp.tool()
def set_standard_time(payload: SetStandardTimeInput) -> dict[str, Any]:
    """Set standard working hours per day for an employee.

    REQUIRED: employee_id, from_date (YYYY-MM-DD), hours_per_day.
    Common values: 7.5 (standard Norwegian full-time), 6.0, 8.0.
    """
    return _make_service().set_standard_time(payload)


@mcp.tool()
def create_department(payload: CreateDepartmentInput) -> dict[str, Any]:
    """Create a Tripletex department.

    REQUIRED: name. Optional: department_number.
    """
    return _make_service().create_department(payload)


@mcp.tool()
def run_salary_transaction(payload: RunSalaryTransactionInput) -> dict[str, Any]:
    """Run a salary/payroll transaction for one or more employees.

    REQUIRED: date, month, year, payslips (one per employee).
    Each payslip needs: employee_id, date, month, year, specifications.
    Each specification needs: salary_type_id (from get_reference_data), rate, count, amount.
    PREREQUISITE: Call get_reference_data(reference="salary_types") first to get valid salary_type_ids.
    """
    return _make_service().run_salary_transaction(payload)


# ---------------------------------------------------------------------------
# Project tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_project(payload: CreateProjectInput) -> dict[str, Any]:
    """Create a Tripletex project.

    REQUIRED: name, start_date (YYYY-MM-DD). Optional: number, end_date, is_internal.
    For fixed-price billing, call configure_project_billing after this.
    """
    return _make_service().create_project(payload)


@mcp.tool()
def configure_project_billing(payload: ConfigureProjectBillingInput) -> dict[str, Any]:
    """Update a project's billing settings (versioned GET-then-PUT handled automatically).

    For fixed-price projects, stage billing, or linking a customer to a project.
    Do NOT use tripletex_put for project updates.
    """
    return _make_service().configure_project_billing(payload)


@mcp.tool()
def create_project_activity(payload: CreateProjectActivityInput) -> dict[str, Any]:
    """Create a project-specific activity for an existing project.

    REQUIRED: project_id, activity_name.
    Do NOT use tripletex_post for /project/projectActivity.
    """
    return _make_service().create_project_activity(payload)


# ---------------------------------------------------------------------------
# Voucher / ledger tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_voucher(payload: CreateVoucherInput) -> dict[str, Any]:
    """Create and verify a balanced Tripletex ledger voucher.

    Use for supplier invoices, manual journal entries, and any balanced double-entry posting.
    There is NO POST /supplierInvoice — use this tool for vendor bills.

    CRITICAL: Postings must balance (signed amount_gross values sum to 0).
    SUPPLIER INVOICE PATTERN:
    - posting 1: account_id=<expense account>, amount_gross=+invoiceAmount (debit)
    - posting 2: account_id=<2400 Leverandørgjeld>, supplier_id=<id>, amount_gross=-invoiceAmount (credit)

    Look up account IDs first via get_reference_data(reference="accounts", filters={"number": 2400}).
    """
    return _make_service().create_voucher(payload)


@mcp.tool()
def reverse_voucher(payload: ReverseVoucherInput) -> dict[str, Any]:
    """Reverse an existing voucher.

    Use when the task asks to reverse/undo a voucher posting, or when a payment was returned
    by the bank — reverse the PAYMENT VOUCHER (not the invoice).
    REQUIRED: voucher_id, date (YYYY-MM-DD).
    """
    return _make_service().reverse_voucher(payload)


# ---------------------------------------------------------------------------
# Order / invoice tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_order(payload: CreateOrderInput) -> dict[str, Any]:
    """Create a Tripletex order (required before create_invoice).

    REQUIRED: customer_id, order_date, delivery_date (YYYY-MM-DD), at least one order line.
    delivery_date MUST be provided — omitting it gives 422.
    ORDER LINE: product-based (product_id + count) or freeform (description + unit_price + count).
    """
    return _make_service().create_order(payload)


@mcp.tool()
def create_invoice(payload: CreateInvoiceInput) -> dict[str, Any]:
    """Create and verify a Tripletex invoice from one or more existing orders.

    REQUIRED: customer_id, invoice_date, invoice_due_date (YYYY-MM-DD), order_ids.
    send_to_customer defaults to false. Use send_invoice separately to email the invoice.
    """
    return _make_service().create_invoice(payload)


@mcp.tool()
def send_invoice(payload: SendInvoiceInput) -> dict[str, Any]:
    """Send an existing invoice to the customer.

    REQUIRED: invoice_id. send_type defaults to EMAIL.
    Call this after create_invoice when the task explicitly asks to send/email the invoice.
    """
    return _make_service().send_invoice(payload)


@mcp.tool()
def register_invoice_payment(payload: RegisterInvoicePaymentInput) -> dict[str, Any]:
    """Register payment on an existing invoice.

    REQUIRED: invoice_id, payment_date (YYYY-MM-DD), payment_type_id, paid_amount.
    PREREQUISITE: get_reference_data(reference="invoice_payment_types") to find payment_type_id.
    """
    return _make_service().register_invoice_payment(payload)


@mcp.tool()
def register_supplier_invoice_payment(payload: RegisterSupplierInvoicePaymentInput) -> dict[str, Any]:
    """Register payment on an existing supplier invoice.

    REQUIRED: supplier_invoice_id, payment_type_id, payment_date.
    PREREQUISITE: get_reference_data(reference="outgoing_payment_types") to find payment_type_id.
    """
    return _make_service().register_supplier_invoice_payment(payload)


@mcp.tool()
def create_credit_note(payload: CreateCreditNoteInput) -> dict[str, Any]:
    """Create a credit note from an existing invoice.

    Use when the task asks to credit or cancel an invoice (kreditnota, credit note, Gutschrift).
    REQUIRED: invoice_id (the original invoice to credit), date (YYYY-MM-DD), comment.
    NOTE: To reverse a PAYMENT, use reverse_voucher instead.
    """
    return _make_service().create_credit_note(payload)


# ---------------------------------------------------------------------------
# Travel expense tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_travel_expense(payload: CreateTravelExpenseInput) -> dict[str, Any]:
    """Create a travel expense with nested travelDetails.

    REQUIRED: title, travel_details (nested: departure_date, return_date, destination, purpose,
    is_day_trip, is_foreign_travel). All travel detail fields go inside travel_details, NOT root level.

    FULL FLOW (all writes must be sequential):
    1. create_travel_expense → travel_expense_id
    2. If per-diem: get_reference_data(travel_per_diem_rates) → add_travel_per_diem
    3. If costs: get_reference_data(travel_cost_categories + travel_payment_types) → add_travel_expense_cost
    4. transition_travel_expense("deliver") → ("approve") → ("createVouchers")
    """
    return _make_service().create_travel_expense(payload)


@mcp.tool()
def add_travel_expense_cost(payload: AddTravelExpenseCostInput) -> dict[str, Any]:
    """Attach a cost line to an existing travel expense. Call once per receipt/cost item (sequentially).

    REQUIRED: travel_expense_id, cost_category_id, payment_type_id, date, amount_currency_inc_vat.
    PREREQUISITE: get_reference_data("travel_cost_categories") + get_reference_data("travel_payment_types").
    """
    return _make_service().add_travel_expense_cost(payload)


@mcp.tool()
def add_travel_mileage_allowance(payload: AddTravelMileageAllowanceInput) -> dict[str, Any]:
    """Add a mileage allowance to an existing travel expense.

    REQUIRED: travel_expense_id, rate_type_id, rate_category_id, date, departure_location, destination, km.
    PREREQUISITE: get_reference_data(reference="travel_mileage_rates", filters={"dateFrom": ..., "dateTo": ...}).
    """
    return _make_service().add_travel_mileage_allowance(payload)


@mcp.tool()
def add_travel_per_diem(payload: AddTravelPerDiemInput) -> dict[str, Any]:
    """Add per-diem (diett/kostgodtgjørelse) compensation to an existing travel expense.

    REQUIRED: travel_expense_id, rate_type_id, rate_category_id, location, count.
    PREREQUISITE: get_reference_data(reference="travel_per_diem_rates", filters={"dateFrom": ..., "dateTo": ...}).
    For multi-day trips with overnight stays: set overnight_accommodation="HOTEL".
    """
    return _make_service().add_travel_per_diem(payload)


@mcp.tool()
def transition_travel_expense(payload: TransitionTravelExpenseInput) -> dict[str, Any]:
    """Move a travel expense through its lifecycle.

    REQUIRED: travel_expense_id, action.
    Valid actions: "deliver", "approve", "unapprove", "undeliver", "createVouchers".
    """
    return _make_service().transition_travel_expense(payload)


# ---------------------------------------------------------------------------
# Timesheet tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_timesheet_entry(payload: CreateTimesheetEntryInput) -> dict[str, Any]:
    """Create a timesheet entry for hours worked on a project.

    REQUIRED: project_id, activity_id (from get_timesheet_activities), date (YYYY-MM-DD), hours.
    MANDATORY ORDER: create_project → get_timesheet_activities → create_timesheet_entry.
    Do NOT invent activity_id values.
    """
    return _make_service().create_timesheet_entry(payload)


# ---------------------------------------------------------------------------
# Bank / reconciliation tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_bank_reconciliation(payload: CreateBankReconciliationInput) -> dict[str, Any]:
    """Create a bank reconciliation entry.

    REQUIRED: account_id (internal ID from get_reference_data("bank_accounts")), accounting_period_id.
    PREREQUISITE: get_reference_data("accounting_periods") → accounting_period_id.
    """
    return _make_service().create_bank_reconciliation(payload)


# ---------------------------------------------------------------------------
# Misc tools
# ---------------------------------------------------------------------------


@mcp.tool()
def upload_attachment(
    entity_type: str,
    entity_id: int,
    filename: str,
    mime_type: str,
    file_data_base64: str,
) -> dict[str, Any]:
    """Upload a file attachment to a voucher, travel expense, or salary transaction.

    entity_type must be one of: "voucher", "travel_expense", "salary_transaction".
    file_data_base64: base64-encoded file content (PDF, PNG, JPEG, or TIFF).
    """

    class _File:
        pass

    f = _File()
    f.data = base64.b64decode(file_data_base64)  # type: ignore[attr-defined]
    f.filename = filename  # type: ignore[attr-defined]
    f.mime_type = mime_type  # type: ignore[attr-defined]
    payload = UploadAttachmentInput(entity_type=entity_type, entity_id=entity_id, file_index=0)  # type: ignore[arg-type]
    return _make_service().upload_attachment(payload, files=[f])


@mcp.tool()
def create_accounting_dimension(payload: CreateAccountingDimensionInput) -> dict[str, Any]:
    """Create a custom accounting dimension with optional initial values.

    REQUIRED: dimension_name (max 20 characters). Optional: description, values list.
    Use the returned value IDs in create_voucher postings via free_accounting_dimension_1/2/3_id.
    """
    return _make_service().create_accounting_dimension(payload)


@mcp.tool()
def create_webhook_subscription(payload: CreateWebhookSubscriptionInput) -> dict[str, Any]:
    """Create a webhook subscription for Tripletex events.

    REQUIRED: event (e.g. "customer.create"), target_url (absolute HTTPS URL).
    PREREQUISITE: get_reference_data(reference="events") to list available event keys.
    """
    return _make_service().create_webhook_subscription(payload)
