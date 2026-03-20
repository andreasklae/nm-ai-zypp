from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalize_organization_number(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    without_mva = re.sub(r"\bmva\b", "", cleaned, flags=re.IGNORECASE)
    digits = re.sub(r"\D", "", without_mva)
    if len(digits) == 9:
        return digits
    return None


class SolveFile(StrictBaseModel):
    filename: str = Field(min_length=1, description="Original attachment filename.")
    content_base64: str = Field(min_length=1, description="Base64-encoded attachment bytes.")
    mime_type: str = Field(min_length=1, description="Attachment MIME type such as application/pdf or image/png.")

    @field_validator("filename", "mime_type")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned

    def decoded_bytes(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class TripletexCredentials(StrictBaseModel):
    base_url: AnyHttpUrl = Field(description="Submission-specific Tripletex proxy base URL. Always use this URL.")
    session_token: str = Field(min_length=1, description="Tripletex session token used as the Basic Auth password.")


class SolveRequest(StrictBaseModel):
    prompt: str = Field(
        min_length=1,
        description="User task in natural language. The task may be written in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French.",
    )
    files: list[SolveFile] = Field(default_factory=list, description="Optional attachments that may ground the task.")
    tripletex_credentials: TripletexCredentials

    @field_validator("prompt")
    @classmethod
    def _validate_prompt(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("prompt must not be empty")
        return cleaned


class SolveResponse(StrictBaseModel):
    status: Literal["completed"] = "completed"


@dataclass(slots=True)
class PreparedAttachment:
    filename: str
    mime_type: str
    data: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class EntityRef(StrictBaseModel):
    id: int


class CreateEmployeeInput(StrictBaseModel):
    first_name: str = Field(min_length=1, description="Employee first name.")
    last_name: str = Field(min_length=1, description="Employee last name.")
    email: str | None = Field(default=None, description="Employee email address when the task explicitly provides one.")
    user_type: Literal["STANDARD", "EXTENDED", "NO_ACCESS"] = "STANDARD"
    department_id: int | None = Field(
        default=None, description="Department ID. Omit to let the service choose the default department."
    )


class GrantEmployeePrivilegesInput(StrictBaseModel):
    employee_id: int = Field(description="Existing employee ID that should receive entitlements.")
    template: Literal[
        "ALL_PRIVILEGES",
        "NONE_PRIVILEGES",
        "INVOICING_MANAGER",
        "PERSONELL_MANAGER",
        "ACCOUNTANT",
        "AUDITOR",
        "DEPARTMENT_LEADER",
    ] = "ALL_PRIVILEGES"


class CreateCustomerInput(StrictBaseModel):
    name: str = Field(min_length=1, description="Customer display name.")
    organization_number: str | None = Field(
        default=None,
        description="Optional Norwegian organization number. The service normalizes values like '998 877 665 MVA' to '998877665' and omits invalid values.",
    )
    email: str | None = Field(default=None, description="Customer email address when provided.")
    invoice_send_method: str | None = Field(default=None, description="Tripletex invoice send method such as EMAIL.")
    invoices_due_in: int | None = Field(
        default=None, description="Invoice due offset value when the task specifies payment terms."
    )
    invoices_due_in_type: str | None = Field(default=None, description="Unit for invoices_due_in, for example DAYS.")

    @field_validator("organization_number")
    @classmethod
    def _normalize_organization_number(cls, value: str | None) -> str | None:
        return normalize_organization_number(value)


class CreateSupplierInput(StrictBaseModel):
    name: str = Field(min_length=1, description="Supplier display name.")
    organization_number: str | None = Field(
        default=None,
        description="Optional Norwegian organization number. Send only 9 digits with no spaces or MVA suffix; invalid values are omitted.",
    )
    email: str | None = Field(default=None, description="Supplier email address when provided.")
    invoice_email: str | None = Field(
        default=None,
        description="Email for receiving invoices. Set this when an email is provided — Tripletex uses this field for EHF/invoice delivery.",
    )

    @field_validator("organization_number")
    @classmethod
    def _normalize_organization_number(cls, value: str | None) -> str | None:
        return normalize_organization_number(value)


class CreateProductInput(StrictBaseModel):
    name: str = Field(min_length=1, description="Product name.")
    number: str | None = Field(default=None, description="Optional product number or SKU.")
    price_excluding_vat_currency: float = Field(gt=0, description="Net sales price in company currency.")
    vat_type_id: int | None = Field(default=None, description="Optional Tripletex VAT type ID.")


class CreateProjectInput(StrictBaseModel):
    name: str = Field(min_length=1, description="Project name.")
    start_date: str = Field(min_length=1, description="Project start date in ISO format YYYY-MM-DD.")
    project_manager_id: int | None = Field(
        default=None,
        description="Project manager employee ID. Omit to default to the currently logged-in employee.",
    )
    number: str | None = Field(default=None, description="Optional project number.")
    end_date: str | None = Field(default=None, description="Optional project end date in ISO format YYYY-MM-DD.")
    is_internal: bool | None = Field(default=None, description="Whether the project should be marked as internal.")


class ConfigureProjectBillingInput(StrictBaseModel):
    project_id: int = Field(description="Existing project ID to update.")
    customer_id: int | None = Field(
        default=None,
        description="Customer ID that should be linked to the project for billing.",
    )
    project_manager_id: int | None = Field(
        default=None,
        description="Project manager employee ID to set on the project.",
    )
    is_fixed_price: bool | None = Field(
        default=None,
        description="Whether the project should be marked as fixed price.",
    )
    fixed_price: float | None = Field(
        default=None,
        gt=0,
        description="Fixed project price in company currency. Use the full project amount, not the milestone amount.",
    )


class VoucherPostingInput(StrictBaseModel):
    account_id: int = Field(description="Tripletex ledger account ID for this posting.")
    date: str = Field(min_length=1, description="Posting date in ISO format YYYY-MM-DD.")
    amount_gross: float = Field(description="Signed gross amount. Positive debits, negative credits.")
    amount_gross_currency: float | None = Field(
        default=None,
        description="Signed amount in company currency. Omit to reuse amount_gross.",
    )
    description: str | None = Field(default=None, description="Optional posting description.")
    row: int | None = Field(default=None, description="Optional posting row number. Omit to auto-number from 1.")
    vat_type_id: int | None = Field(default=None, description="Optional VAT type ID for VAT-locked accounts.")
    supplier_id: int | None = Field(
        default=None, description="Supplier ID required when the account ledger type is VENDOR."
    )
    customer_id: int | None = Field(
        default=None, description="Customer ID required when the account ledger type is CUSTOMER."
    )
    employee_id: int | None = Field(
        default=None, description="Employee ID required when the account ledger type is EMPLOYEE."
    )
    free_accounting_dimension_1_id: int | None = Field(
        default=None,
        description="ID of an accounting dimension value to link to this posting as freeAccountingDimension1.",
    )
    free_accounting_dimension_2_id: int | None = Field(
        default=None,
        description="ID of an accounting dimension value to link to this posting as freeAccountingDimension2.",
    )
    free_accounting_dimension_3_id: int | None = Field(
        default=None,
        description="ID of an accounting dimension value to link to this posting as freeAccountingDimension3.",
    )


class CreateVoucherInput(StrictBaseModel):
    date: str = Field(min_length=1, description="Voucher date in ISO format YYYY-MM-DD.")
    description: str = Field(min_length=1, description="Voucher description visible in Tripletex.")
    postings: list[VoucherPostingInput] = Field(
        min_length=2,
        description="Balanced voucher postings. The signed amountGross values must sum to zero.",
    )
    send_to_ledger: bool = Field(default=True, description="When true, post the voucher directly to the ledger.")
    vendor_invoice_number: str | None = Field(
        default=None,
        description="Supplier invoice number from the attachment or user prompt when booking a supplier invoice.",
    )


class OrderLineInput(StrictBaseModel):
    count: float = Field(gt=0, description="Quantity for the order line.")
    product_id: int | None = Field(default=None, description="Existing product ID for this line.")
    description: str | None = Field(default=None, description="Optional freeform order line description.")
    unit_price_excluding_vat_currency: float | None = Field(default=None, gt=0, description="Optional net unit price.")


class CreateOrderInput(StrictBaseModel):
    customer_id: int = Field(description="Existing customer ID for the order.")
    project_id: int | None = Field(
        default=None,
        description="Optional project ID. Use this for project-linked milestone or stage billing orders.",
    )
    order_date: str = Field(min_length=1, description="Order date in ISO format YYYY-MM-DD.")
    delivery_date: str = Field(min_length=1, description="Delivery date in ISO format YYYY-MM-DD.")
    order_lines: list[OrderLineInput] = Field(min_length=1, description="One or more order lines.")


class CreateInvoiceInput(StrictBaseModel):
    customer_id: int = Field(description="Existing customer ID on the invoice.")
    invoice_date: str = Field(min_length=1, description="Invoice date in ISO format YYYY-MM-DD.")
    invoice_due_date: str = Field(min_length=1, description="Invoice due date in ISO format YYYY-MM-DD.")
    order_ids: list[int] = Field(min_length=1, description="Existing order IDs that should be invoiced.")
    send_to_customer: bool = Field(
        default=False, description="Whether Tripletex should send the invoice to the customer."
    )


class RegisterInvoicePaymentInput(StrictBaseModel):
    invoice_id: int = Field(description="Existing invoice ID to register payment on.")
    payment_date: str = Field(min_length=1, description="Payment date in ISO format YYYY-MM-DD.")
    payment_type_id: int = Field(description="Tripletex invoice payment type ID.")
    paid_amount: float = Field(description="Paid amount in company currency.")
    paid_amount_currency: float | None = Field(default=None, description="Optional paid amount in invoice currency.")


class CreateCreditNoteInput(StrictBaseModel):
    invoice_id: int = Field(description="Existing invoice ID to credit.")
    date: str = Field(min_length=1, description="Credit note date in ISO format YYYY-MM-DD.")
    comment: str = Field(min_length=1, description="Comment passed to the Tripletex credit note action.")


class ReverseVoucherInput(StrictBaseModel):
    voucher_id: int = Field(description="Existing voucher ID to reverse.")
    date: str = Field(min_length=1, description="Reversal date in ISO format YYYY-MM-DD.")


class TravelDetailsInput(StrictBaseModel):
    departure_date: str = Field(min_length=1, description="Travel departure date in ISO format YYYY-MM-DD.")
    return_date: str = Field(min_length=1, description="Travel return date in ISO format YYYY-MM-DD.")
    departure_from: str = Field(min_length=1, description="Departure location.")
    destination: str = Field(min_length=1, description="Destination location.")
    purpose: str = Field(min_length=1, description="Trip purpose.")
    is_day_trip: bool = Field(default=True, description="Whether the trip starts and ends on the same day.")
    is_foreign_travel: bool = Field(default=False, description="Whether this is foreign travel.")
    departure_time: str | None = Field(default=None, description="Optional departure time, for example 08:00.")
    return_time: str | None = Field(default=None, description="Optional return time, for example 18:30.")


class CreateTravelExpenseInput(StrictBaseModel):
    title: str = Field(min_length=1, description="Travel expense title.")
    travel_details: TravelDetailsInput = Field(description="Nested travelDetails payload required by Tripletex.")
    employee_id: int | None = Field(default=None, description="Employee ID. Omit to default to the logged-in employee.")
    project_id: int | None = Field(default=None, description="Optional project ID linked to the trip.")
    department_id: int | None = Field(default=None, description="Optional department ID linked to the trip.")


class AddTravelExpenseCostInput(StrictBaseModel):
    travel_expense_id: int = Field(description="Existing travel expense ID.")
    cost_category_id: int = Field(description="Tripletex travel expense cost category ID.")
    payment_type_id: int = Field(description="Tripletex travel expense payment type ID.")
    date: str = Field(min_length=1, description="Cost date in ISO format YYYY-MM-DD.")
    amount_currency_inc_vat: float = Field(
        gt=0, description="Gross cost amount including VAT in the selected currency."
    )
    currency_id: int = Field(default=1, description="Tripletex currency ID. NOK is typically 1.")
    comments: str | None = Field(default=None, description="Optional receipt or cost note.")


class TransitionTravelExpenseInput(StrictBaseModel):
    travel_expense_id: int = Field(description="Existing travel expense ID.")
    action: Literal["deliver", "approve", "unapprove", "undeliver", "createVouchers"]


class CreateTimesheetEntryInput(StrictBaseModel):
    employee_id: int | None = Field(
        default=None,
        description="Employee ID for the timesheet entry. Omit to default to the logged-in employee.",
    )
    project_id: int = Field(description="Existing project ID.")
    activity_id: int = Field(description="Activity ID returned by get_timesheet_activities for this project/date.")
    date: str = Field(min_length=1, description="Timesheet entry date in ISO format YYYY-MM-DD.")
    hours: float = Field(gt=0, description="Number of hours to register.")
    comment: str | None = Field(default=None, description="Optional timesheet comment.")


class GetTimesheetActivitiesInput(StrictBaseModel):
    project_id: int = Field(description="Existing project ID to fetch valid timesheet activities for.")
    date: str = Field(min_length=1, description="Timesheet date in ISO format YYYY-MM-DD.")
    employee_id: int | None = Field(
        default=None,
        description="Employee ID used for the activity lookup. Omit to default to the logged-in employee.",
    )


class CreateContactInput(StrictBaseModel):
    first_name: str = Field(min_length=1, description="Contact first name.")
    last_name: str = Field(min_length=1, description="Contact last name.")
    customer_id: int = Field(description="Existing customer ID to link the contact to.")
    email: str | None = Field(default=None, description="Contact email address when provided.")


class UpdateEmployeeInput(StrictBaseModel):
    employee_id: int = Field(description="Existing employee ID to update.")
    first_name: str | None = Field(default=None, description="Updated first name.")
    last_name: str | None = Field(default=None, description="Updated last name.")
    email: str | None = Field(default=None, description="Updated email address.")
    date_of_birth: str | None = Field(
        default=None, description="Date of birth in ISO format YYYY-MM-DD. Tripletex requires this on every PUT."
    )
    user_type: Literal["STANDARD", "EXTENDED", "NO_ACCESS"] | None = Field(
        default=None, description="Updated user type."
    )
    department_id: int | None = Field(default=None, description="Updated department ID.")
    phone_number_mobile: str | None = Field(default=None, description="Updated mobile phone number.")
    phone_number_work: str | None = Field(default=None, description="Updated work phone number.")


class CreateDepartmentInput(StrictBaseModel):
    name: str = Field(min_length=1, description="Department name.")
    department_number: str | None = Field(default=None, description="Optional department number.")


class AddTravelMileageAllowanceInput(StrictBaseModel):
    travel_expense_id: int = Field(description="Existing travel expense ID.")
    rate_type_id: int = Field(description="Rate type ID from GET /travelExpense/rate?type=MILEAGE_ALLOWANCE.")
    rate_category_id: int = Field(description="Rate category ID from the same rate lookup.")
    date: str = Field(min_length=1, description="Mileage date in ISO format YYYY-MM-DD.")
    departure_location: str = Field(min_length=1, description="Start location of the drive.")
    destination: str = Field(min_length=1, description="End location of the drive.")
    km: float = Field(gt=0, description="Distance driven in kilometers.")


class AddTravelPerDiemInput(StrictBaseModel):
    travel_expense_id: int = Field(description="Existing travel expense ID.")
    rate_type_id: int = Field(description="The rate row's 'id' field from get_reference_data(travel_per_diem_rates).")
    rate_category_id: int = Field(description="The rate row's 'rateCategory.id' field from the same lookup.")
    location: str = Field(min_length=1, description="Location for per-diem compensation.")
    count: int = Field(gt=0, description="Number of per-diem units (days).")
    is_deduction_for_breakfast: bool = Field(default=False, description="Deduct breakfast from per-diem.")
    is_deduction_for_lunch: bool = Field(default=False, description="Deduct lunch from per-diem.")
    is_deduction_for_dinner: bool = Field(default=False, description="Deduct dinner from per-diem.")


class SalarySpecificationInput(StrictBaseModel):
    salary_type_id: int = Field(description="Tripletex salary type ID from GET /salary/type.")
    rate: float = Field(description="Rate or amount for this salary line.")
    count: float = Field(gt=0, description="Number of units (1 for monthly salary, hours for hourly).")
    amount: float = Field(description="Total amount (rate * count).")


class SalaryPayslipInput(StrictBaseModel):
    employee_id: int = Field(description="Existing employee ID.")
    date: str = Field(min_length=1, description="Payslip date in ISO format YYYY-MM-DD.")
    month: int = Field(ge=1, le=12, description="Payslip month (1-12).")
    year: int = Field(ge=2000, description="Payslip year.")
    specifications: list[SalarySpecificationInput] = Field(min_length=1, description="Salary line items.")


class RunSalaryTransactionInput(StrictBaseModel):
    date: str = Field(min_length=1, description="Transaction date in ISO format YYYY-MM-DD.")
    month: int = Field(ge=1, le=12, description="Salary month (1-12).")
    year: int = Field(ge=2000, description="Salary year.")
    payslips: list[SalaryPayslipInput] = Field(min_length=1, description="One payslip per employee.")
    generate_tax_deduction: bool = Field(default=True, description="Auto-calculate tax deduction.")


class UploadAttachmentInput(StrictBaseModel):
    entity_type: Literal["voucher", "travel_expense", "salary_transaction"] = Field(
        description="Type of entity to attach the file to."
    )
    entity_id: int = Field(description="ID of the voucher, travel expense, or salary transaction.")
    file_index: int = Field(default=0, description="Index into the request's files array (0-based).")


class CreateBankReconciliationInput(StrictBaseModel):
    account_id: int = Field(description="Bank ledger account ID (e.g. account 1920).")
    accounting_period_id: int = Field(description="Accounting period ID from GET /ledger/accountingPeriod.")
    type: str = Field(default="MANUAL", description="Reconciliation type. MANUAL is the default.")
    bank_account_closing_balance_currency: float = Field(default=0, description="Closing balance in account currency.")


class CreateWebhookSubscriptionInput(StrictBaseModel):
    event: str = Field(min_length=1, description="Event key from GET /event (e.g. 'customer.create').")
    target_url: str = Field(min_length=1, description="Absolute HTTPS callback URL.")
    fields: str | None = Field(default=None, description="Comma-separated field list for the webhook payload.")
    auth_header_name: str | None = Field(default=None, description="Optional auth header name (e.g. 'Authorization').")
    auth_header_value: str | None = Field(default=None, description="Optional auth header value (e.g. 'Bearer token').")


class AccountingDimensionValueInput(StrictBaseModel):
    display_name: str = Field(min_length=1, description="Dimension value display name.")
    number: str | None = Field(default=None, description="Optional dimension value number/code.")
    show_in_voucher_registration: bool = Field(default=True, description="Show in voucher registration UI.")
    active: bool = Field(default=True, description="Whether the value is active.")


class CreateAccountingDimensionInput(StrictBaseModel):
    dimension_name: str = Field(min_length=1, max_length=20, description="Dimension name (max 20 characters).")
    description: str | None = Field(default=None, description="Optional dimension description.")
    active: bool = Field(default=True, description="Whether the dimension is active.")
    values: list[AccountingDimensionValueInput] = Field(
        default_factory=list, description="Optional initial dimension values to create."
    )


class ReferenceLookupInput(StrictBaseModel):
    reference: Literal[
        "whoami",
        "vat_settings",
        "accounts",
        "vat_types",
        "voucher_types",
        "currencies",
        "employees",
        "departments",
        "customers",
        "suppliers",
        "products",
        "projects",
        "invoice_payment_types",
        "travel_cost_categories",
        "travel_payment_types",
        "travel_expenses",
        "activities_for_timesheet",
        "salary_types",
        "divisions",
        "travel_mileage_rates",
        "travel_per_diem_rates",
        "countries",
        "municipalities",
        "events",
        "accounting_periods",
        "bank_accounts",
    ]
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional raw Tripletex query filters for general reference lookups. Prefer dedicated tools for dependency-heavy lookups such as timesheet activities.",
    )
