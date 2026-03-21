from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import resources
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import ToolDefinition

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
    CreateProjectInput,
    CreateSupplierInput,
    CreateTimesheetEntryInput,
    CreateTravelExpenseInput,
    CreateVoucherInput,
    CreateWebhookSubscriptionInput,
    FindApiInput,
    GetTimesheetActivitiesInput,
    GrantEmployeePrivilegesInput,
    RawApiCallInput,
    ReferenceLookupInput,
    RegisterInvoicePaymentInput,
    ReverseVoucherInput,
    RunSalaryTransactionInput,
    TransitionTravelExpenseInput,
    UpdateEmployeeInput,
    UploadAttachmentInput,
)
from ai_accounting_agent.telemetry import log_event, log_tool
from ai_accounting_agent.tripletex_client import TripletexApiError, TripletexClient

DEFAULT_BANK_ACCOUNT_NUMBER = "86011117947"
PRE_ANNOUNCE_TOOL_NAMES = {"announce_step", "search_tripletex_reference", "get_today_date"}

FIND_API_SYSTEM_PROMPT = """\
You are a Tripletex API specialist. You receive the full API documentation and a request describing what the caller needs to accomplish.

Your job:
1. Find the correct endpoint(s) for the request.
2. Return the HTTP method, path, and ALL required fields with their exact names, types, and constraints.
3. If the task requires multiple sequential API calls, list them in order with dependencies noted.
4. Include a realistic example request body with plausible field values.
5. Flag common pitfalls: nested field structures, fields that look optional but are actually required, resources that must exist before this call works, non-obvious enum values.

Rules:
- Field names must exactly match the API spec. Do not paraphrase or rename them.
- If you are unsure whether a field is required, include it and mark it as "likely required".
- Do not explain what REST APIs are. Do not include generic HTTP instructions.
- Do not include optional fields unless they are relevant to the specific request.
- If the request is ambiguous, state your assumption and provide the answer for that interpretation.
- Be thorough. The caller will construct API calls directly from your response. Missing a required field means a failed call.

Return your answer in this format:
ENDPOINT: <METHOD> <path>
REQUIRED FIELDS:
  - <field> (<type>) — <note>
OPTIONAL BUT RELEVANT:
  - <field> (<type>) — <note>
PITFALLS:
  - <pitfall description>
EXAMPLE BODY:
{...}
MULTI-STEP NOTE: (only if applicable)
  <ordered steps with dependencies>
"""


@dataclass(slots=True)
class StepState:
    has_announced_step: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ReferenceIndex:
    documents: list[tuple[str, str]]

    @classmethod
    def load_default(cls) -> "ReferenceIndex":
        docs = []
        package = "ai_accounting_agent"
        for filename in ("tripletex_api.md", "task.md"):
            text = resources.files(package).joinpath(filename).read_text(encoding="utf-8")
            docs.append((filename, text))
        return cls(documents=docs)

    def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        lowered_query = query.lower()
        terms = self._expanded_terms(lowered_query)
        matches: list[tuple[int, dict[str, Any]]] = []

        for source, text in self.documents:
            sections = text.split("\n## ")
            for raw_section in sections:
                heading, _, body = raw_section.partition("\n")
                section_text = f"{heading}\n{body}"
                lowered = section_text.lower()
                score = sum(lowered.count(term) for term in terms)
                heading_lower = heading.lower()
                score += sum(4 for term in terms if term in heading_lower)
                if self._looks_like_project_billing_query(lowered_query) and self._is_project_billing_section(
                    heading_lower, lowered
                ):
                    score += 40
                if self._looks_like_project_billing_query(lowered_query) and heading_lower in {
                    "suggested agent playbook",
                    "common task workflows (competition patterns)",
                }:
                    score -= 8
                if score <= 0:
                    continue
                matches.append(
                    (
                        score,
                        {
                            "source": source,
                            "heading": heading.lstrip("# ").strip(),
                            "excerpt": section_text[:1500].strip(),
                        },
                    )
                )

        matches.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in matches[:max_results]]

    @staticmethod
    def _expanded_terms(lowered_query: str) -> list[str]:
        terms = {term for term in re.findall(r"[a-z0-9_]+", lowered_query) if len(term) > 2}
        if "fixed price" in lowered_query or "fixedprice" in lowered_query or "isfixedprice" in lowered_query:
            terms.update({"fixedprice", "isfixedprice", "invoicingplan", "project", "billing"})
        if any(
            phrase in lowered_query
            for phrase in ("milestone", "payment by stage", "pagamento por etapa", "invoice on account")
        ):
            terms.update({"project", "invoice", "order", "fixedprice", "billing"})
        if "project billing" in lowered_query:
            terms.update({"project", "billing", "fixedprice", "invoice"})
        return sorted(terms)

    @staticmethod
    def _looks_like_project_billing_query(lowered_query: str) -> bool:
        return any(
            phrase in lowered_query
            for phrase in (
                "fixed price",
                "fixedprice",
                "isfixedprice",
                "milestone",
                "project billing",
                "payment by stage",
                "pagamento por etapa",
                "invoice on account",
            )
        )

    @staticmethod
    def _is_project_billing_section(heading_lower: str, section_lower: str) -> bool:
        return (
            "project" in heading_lower
            and any(
                term in section_lower
                for term in ("fixedprice", "isfixedprice", "invoicingplan", "project-linked order")
            )
        ) or "project billing" in heading_lower


async def prepare_tripletex_tools(
    ctx: RunContext[Any],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition] | None:
    if ctx.deps.step_state.has_announced_step:
        return tool_defs
    return [tool_def for tool_def in tool_defs if tool_def.name in PRE_ANNOUNCE_TOOL_NAMES]


class TripletexService:
    def __init__(self, *, client: TripletexClient, run_id: str, reference_index: ReferenceIndex, step_state: StepState):
        self.client = client
        self.run_id = run_id
        self.reference_index = reference_index
        self.step_state = step_state

    def announce_step(
        self, *, task_understanding: str, planned_tools: list[str], success_criteria: str
    ) -> dict[str, Any]:
        self.step_state.has_announced_step = True
        payload = {
            "task_understanding": task_understanding,
            "planned_tools": planned_tools,
            "success_criteria": success_criteria,
        }
        self.step_state.history.append(payload)
        log_event("agent_step", run_id=self.run_id, **payload)
        return {"status": "announced", **payload}

    def search_tripletex_reference(self, *, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return self.reference_index.search(query, max_results=max_results)

    def _require_step(self) -> None:
        if not self.step_state.has_announced_step:
            raise ModelRetry(
                "Call announce_step before using Tripletex or workflow tools. "
                "Explain what you plan to do and what success looks like first."
            )

    @staticmethod
    def _value(payload: Any) -> Any:
        if isinstance(payload, dict) and "value" in payload:
            return payload["value"]
        return payload

    @staticmethod
    def _values(payload: Any) -> list[Any]:
        if isinstance(payload, dict) and "values" in payload:
            return payload["values"]
        if isinstance(payload, list):
            return payload
        raise ValueError("Expected a Tripletex list response")

    @staticmethod
    def _validation_entries(response_body: Any) -> list[tuple[str | None, str | None]]:
        entries: list[tuple[str | None, str | None]] = []
        if not isinstance(response_body, dict):
            return entries

        values = response_body.get("values")
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict):
                    continue
                field = item.get("field")
                message = item.get("message")
                if field is not None or message is not None:
                    entries.append(
                        (str(field) if field is not None else None, str(message) if message is not None else None)
                    )

        message = response_body.get("message")
        if isinstance(message, str) and not entries:
            field = response_body.get("field")
            entries.append((str(field) if field is not None else None, message))

        return entries

    @classmethod
    def _tripletex_retry_message(cls, *, operation: str, exc: TripletexApiError) -> str | None:
        if exc.status_code in {401, 403}:
            body_message = ""
            if isinstance(exc.response_body, dict):
                body_message = exc.response_body.get("error", "") or exc.response_body.get("message", "")
            return (
                f"Tripletex returned {exc.status_code} for {operation}. "
                f"Authentication failed: {body_message}. "
                "The session token may be expired or invalid. Do not retry — report this failure."
            )

        if exc.status_code not in {400, 409, 422}:
            return None

        entries = cls._validation_entries(exc.response_body)
        for field, message in entries:
            field_name = field or ""
            message_text = message or ""
            if field_name == "organizationNumber" or "Organisasjonsnummeret" in message_text:
                return (
                    "Tripletex rejected organizationNumber. Use exactly 9 digits with no spaces, punctuation, "
                    "or MVA suffix. Normalize examples like '998 877 665 MVA' to '998877665', or omit "
                    "organization_number and retry."
                )
            if "allerede en bruker" in message_text or "already exists" in message_text.lower():
                return (
                    f"Tripletex rejected {operation}: {message_text}. "
                    "An entity with this email/identifier already exists. "
                    "Use get_reference_data to look up the existing entity by email and reuse its ID."
                )
            if "arbeidsforhold" in message_text or "employment" in message_text.lower():
                return (
                    f"Tripletex rejected {operation}: {message_text}. "
                    "The employee needs an active employment record. The run_salary_transaction tool "
                    "handles this automatically — retry the same call."
                )
            if "låst til mva-kode" in message_text or "locked to vat" in message_text.lower():
                return (
                    f"Account is locked to a specific VAT code ({message_text}). "
                    "Use the locked vatType on this posting, or pick a different account. "
                    "Do NOT attempt to modify the ledger account itself. "
                    "To find an alternative account, use get_reference_data(accounts) and filter by number range "
                    "(e.g. accounts with number between 6000-6999 for expenses). Do NOT use 'id' filters with '>' syntax."
                )

        if entries:
            rendered = "; ".join(
                f"{field}: {message}" if field else str(message) for field, message in entries if field or message
            )
            return f"Tripletex rejected {operation}: {rendered}. Fix the payload and retry once."

        if isinstance(exc.response_body, dict):
            message = exc.response_body.get("message")
            if isinstance(message, str) and message.strip():
                return f"Tripletex rejected {operation}: {message}. Fix the payload and retry once."

        return None

    def _maybe_raise_tripletex_retry(self, *, operation: str, exc: TripletexApiError) -> None:
        retry_message = self._tripletex_retry_message(operation=operation, exc=exc)
        if retry_message is not None:
            raise ModelRetry(retry_message) from exc

    def _call_with_tripletex_retry_hint(self, *, operation: str, call: Any) -> Any:
        try:
            return call()
        except TripletexApiError as exc:
            self._maybe_raise_tripletex_retry(operation=operation, exc=exc)
            raise

    @staticmethod
    def calculate_vat_split(payload: CalculateVatSplitInput) -> dict[str, Any]:
        gross = payload.amount_including_vat
        rate = payload.vat_percentage / 100
        net = round(gross / (1 + rate), 2)
        vat = round(gross - net, 2)
        return {"gross": gross, "net": net, "vat": vat, "vat_percentage": payload.vat_percentage}

    @staticmethod
    def _normalize_generic_path_and_params(
        path: str,
        params: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        cleaned_path = path.strip()
        if not cleaned_path:
            raise ModelRetry("Provide a non-empty relative Tripletex path.")

        split = urlsplit(cleaned_path)
        if split.scheme or split.netloc:
            raise ModelRetry("Use a relative Tripletex path, not a full URL.")
        merged_params = dict(params or {})
        if split.query:
            for key, value in parse_qsl(split.query, keep_blank_values=True):
                if key in merged_params and str(merged_params[key]) != value:
                    raise ModelRetry(
                        f"Conflicting values for query parameter '{key}'. Put query parameters in params and retry."
                    )
                merged_params.setdefault(key, value)

        normalized_path = split.path or "/"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        return normalized_path, merged_params or None

    @staticmethod
    def _require_collection_filters(
        path: str, params: dict[str, Any] | None, required: tuple[str, ...], *, hint: str
    ) -> None:
        current_params = params or {}
        missing = [name for name in required if name not in current_params]
        if missing:
            rendered = ", ".join(missing)
            raise ModelRetry(f"{path} requires query params {rendered}. {hint}")

    def _validate_generic_get(self, *, path: str, params: dict[str, Any] | None) -> None:
        current_path = path.rstrip("/") or "/"
        current_params = params or {}

        if current_path == "/order":
            self._require_collection_filters(
                current_path,
                current_params,
                ("orderDateFrom", "orderDateTo"),
                hint=(
                    "Do not inspect /order without a date range. Use create_order or create_invoice for billing flows, "
                    "or pass both date filters in params for a real historical lookup."
                ),
            )
        if current_path == "/invoice":
            self._require_collection_filters(
                current_path,
                current_params,
                ("invoiceDateFrom", "invoiceDateTo"),
                hint="Use create_invoice for new invoices, or pass both invoiceDate filters for historical reads.",
            )
        if current_path == "/ledger/voucher":
            self._require_collection_filters(
                current_path,
                current_params,
                ("dateFrom", "dateTo"),
                hint="Use dateFrom/dateTo for voucher reads. Do not probe the voucher collection without a date range.",
            )
        if current_path == "/ledger/posting":
            self._require_collection_filters(
                current_path,
                current_params,
                ("dateFrom", "dateTo"),
                hint="Use dateFrom/dateTo for posting reads. Do not probe the posting collection without a date range.",
            )
        if current_path == "/supplier" and "name" in current_params:
            raise ModelRetry(
                "Do not filter /supplier by name. Use supplierNumber, organizationNumber, or /supplierCustomer/search instead."
            )
        if current_path == "/customer" and "name" in current_params and "customerName" not in current_params:
            raise ModelRetry("Do not filter /customer by name. Use customerName in params instead.")

    def tripletex_get(self, *, path: str, params: dict[str, Any] | None = None, cache_key: str | None = None) -> Any:
        self._require_step()
        normalized_path, normalized_params = self._normalize_generic_path_and_params(path, params)
        self._validate_generic_get(path=normalized_path, params=normalized_params)
        return self._call_with_tripletex_retry_hint(
            operation=f"GET {normalized_path}",
            call=lambda: self.client.get(normalized_path, params=normalized_params, cache_key=cache_key),
        )

    def tripletex_post(self, *, path: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        self._require_step()
        normalized_path, normalized_params = self._normalize_generic_path_and_params(path, params)
        return self._call_with_tripletex_retry_hint(
            operation=f"POST {normalized_path}",
            call=lambda: self.client.post(normalized_path, params=normalized_params, json_body=body),
        )

    def tripletex_put(self, *, path: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        self._require_step()
        normalized_path, normalized_params = self._normalize_generic_path_and_params(path, params)
        return self._call_with_tripletex_retry_hint(
            operation=f"PUT {normalized_path}",
            call=lambda: self.client.put(normalized_path, params=normalized_params, json_body=body),
        )

    def tripletex_delete(self, *, path: str, params: dict[str, Any] | None = None) -> Any:
        self._require_step()
        normalized_path, normalized_params = self._normalize_generic_path_and_params(path, params)
        return self._call_with_tripletex_retry_hint(
            operation=f"DELETE {normalized_path}",
            call=lambda: self.client.delete(normalized_path, params=normalized_params),
        )

    def _whoami(self) -> dict[str, Any]:
        response = self.client.get("/token/session/>whoAmI", cache_key="whoami")
        return self._value(response)

    def _default_department_id(self) -> int:
        departments = self._values(
            self.client.get(
                "/department",
                params={"count": 1, "fields": "id,name,departmentNumber"},
                cache_key="departments:first",
            )
        )
        if not departments:
            raise ValueError("No department found in the Tripletex account.")
        return int(departments[0]["id"])

    def _account_details_by_id(self, account_id: int) -> dict[str, Any]:
        return self._value(
            self.client.get(
                f"/ledger/account/{account_id}",
                params={"fields": "*"},
                cache_key=f"account:details:{account_id}",
            )
        )

    def _account_by_number(self, number: int) -> dict[str, Any]:
        response = self.client.get(
            "/ledger/account",
            params={"number": number, "count": 1, "fields": "id,number,name,type"},
            cache_key=f"account:number:{number}",
        )
        values = self._values(response)
        if not values:
            raise ValueError(f"No ledger account found for number {number}.")
        return values[0]

    def _ensure_bank_account_number(self) -> dict[str, Any]:
        account = self._account_by_number(1920)
        account_id = int(account["id"])
        details = self._account_details_by_id(account_id)
        if details.get("bankAccountNumber"):
            return details

        payload = {
            "id": details["id"],
            "version": details["version"],
            "bankAccountNumber": DEFAULT_BANK_ACCOUNT_NUMBER,
        }
        self.client.put(f"/ledger/account/{account_id}", json_body=payload)
        self.client.cache.pop(f"account:details:{account_id}", None)
        return self._account_details_by_id(account_id)

    def _validate_voucher_postings(self, postings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        total = 0.0
        for index, posting in enumerate(postings, start=1):
            amount_gross_currency = posting.get("amountGrossCurrency")
            if amount_gross_currency is None:
                amount_gross_currency = posting["amountGross"]
            normalized_posting = {
                "row": posting.get("row") or index,
                "date": posting["date"],
                "account": {"id": posting["account"]["id"]},
                "amountGross": posting["amountGross"],
                "amountGrossCurrency": amount_gross_currency,
            }
            if posting.get("description"):
                normalized_posting["description"] = posting["description"]
            if posting.get("vatType"):
                normalized_posting["vatType"] = posting["vatType"]
            if posting.get("supplier"):
                normalized_posting["supplier"] = posting["supplier"]
            if posting.get("customer"):
                normalized_posting["customer"] = posting["customer"]
            if posting.get("employee"):
                normalized_posting["employee"] = posting["employee"]
            for dim_key in ("freeAccountingDimension1", "freeAccountingDimension2", "freeAccountingDimension3"):
                if posting.get(dim_key):
                    normalized_posting[dim_key] = posting[dim_key]

            account_details = self._account_details_by_id(int(normalized_posting["account"]["id"]))

            locked_vat = account_details.get("vatType")
            if locked_vat and isinstance(locked_vat, dict) and locked_vat.get("id") is not None:
                locked_vat_id = int(locked_vat["id"])
                locked_vat_name = locked_vat.get("name", "")
                posting_vat = normalized_posting.get("vatType")
                if posting_vat and isinstance(posting_vat, dict):
                    posting_vat_id = int(posting_vat.get("id", 0))
                    if posting_vat_id != locked_vat_id:
                        raise ModelRetry(
                            f"Account {account_details.get('number')} is locked to vatType {locked_vat_id} "
                            f"({locked_vat_name}). Use vatType {locked_vat_id} on this posting, or choose a "
                            f"different account. Do NOT modify the ledger account."
                        )
                else:
                    normalized_posting["vatType"] = {"id": locked_vat_id}

            ledger_type = account_details.get("ledgerType")
            if ledger_type == "VENDOR" and "supplier" not in normalized_posting:
                raise ModelRetry(
                    f"Account {account_details.get('number')} requires supplier on the posting. "
                    "Add supplier_id for the payable posting and retry."
                )
            if ledger_type == "CUSTOMER" and "customer" not in normalized_posting:
                raise ModelRetry(
                    f"Account {account_details.get('number')} requires customer on the posting. "
                    "Add customer_id for that posting and retry."
                )
            if ledger_type == "EMPLOYEE" and "employee" not in normalized_posting:
                raise ModelRetry(
                    f"Account {account_details.get('number')} requires employee on the posting. "
                    "Add employee_id for that posting and retry."
                )

            normalized.append(normalized_posting)
            total += float(normalized_posting["amountGross"])

        if round(total, 2) != 0:
            raise ModelRetry("Voucher postings must balance so the signed amountGross values sum to exactly 0.")

        return normalized

    def create_employee(self, payload: CreateEmployeeInput) -> dict[str, Any]:
        self._require_step()
        if payload.email:
            existing = self._values(
                self.client.get(
                    "/employee",
                    params={
                        "email": payload.email,
                        "count": 1,
                        "fields": "id,firstName,lastName,email,employeeNumber,userType,version",
                    },
                )
            )
            if existing:
                return existing[0]
        body = {
            "firstName": payload.first_name,
            "lastName": payload.last_name,
            "userType": payload.user_type,
            "department": {"id": payload.department_id or self._default_department_id()},
        }
        if payload.email:
            body["email"] = payload.email
        response = self._call_with_tripletex_retry_hint(
            operation="employee creation",
            call=lambda: self.client.post("/employee", json_body=body),
        )
        return self._value(response)

    def grant_employee_privileges(self, payload: GrantEmployeePrivilegesInput) -> dict[str, Any]:
        self._require_step()
        self.client.put(
            "/employee/entitlement/:grantEntitlementsByTemplate",
            params={"employeeId": payload.employee_id, "template": payload.template},
        )
        response = self.client.get(
            "/employee/entitlement",
            params={"employeeId": payload.employee_id, "fields": "id,name"},
        )
        return {"employee_id": payload.employee_id, "entitlements": self._values(response)}

    def create_customer(self, payload: CreateCustomerInput) -> dict[str, Any]:
        self._require_step()
        if payload.organization_number:
            existing = self._values(
                self.client.get(
                    "/customer",
                    params={
                        "organizationNumber": payload.organization_number,
                        "count": 1,
                        "fields": "id,name,customerNumber,organizationNumber,email,version",
                    },
                )
            )
            if existing:
                return existing[0]
        body: dict[str, Any] = {"name": payload.name}
        if payload.organization_number:
            body["organizationNumber"] = payload.organization_number
        if payload.email:
            body["email"] = payload.email
        if payload.invoice_send_method:
            body["invoiceSendMethod"] = payload.invoice_send_method
        if payload.invoices_due_in is not None:
            body["invoicesDueIn"] = payload.invoices_due_in
        if payload.invoices_due_in_type:
            body["invoicesDueInType"] = payload.invoices_due_in_type
        if payload.address_line1 or payload.postal_code or payload.city:
            address: dict[str, Any] = {}
            if payload.address_line1:
                address["addressLine1"] = payload.address_line1
            if payload.postal_code:
                address["postalCode"] = payload.postal_code
            if payload.city:
                address["city"] = payload.city
            body["postalAddress"] = address
        response = self._call_with_tripletex_retry_hint(
            operation="customer creation",
            call=lambda: self.client.post("/customer", json_body=body),
        )
        return self._value(response)

    def create_supplier(self, payload: CreateSupplierInput) -> dict[str, Any]:
        self._require_step()
        if payload.organization_number:
            existing = self._values(
                self.client.get(
                    "/supplier",
                    params={
                        "organizationNumber": payload.organization_number,
                        "count": 1,
                        "fields": "id,name,supplierNumber,organizationNumber,email,version",
                    },
                )
            )
            if existing:
                return existing[0]
        body = {"name": payload.name}
        if payload.organization_number:
            body["organizationNumber"] = payload.organization_number
        if payload.email:
            body["email"] = payload.email
            if not payload.invoice_email:
                body["invoiceEmail"] = payload.email
        if payload.invoice_email:
            body["invoiceEmail"] = payload.invoice_email
        response = self._call_with_tripletex_retry_hint(
            operation="supplier creation",
            call=lambda: self.client.post("/supplier", json_body=body),
        )
        return self._value(response)

    def create_product(self, payload: CreateProductInput) -> dict[str, Any]:
        self._require_step()
        if payload.number:
            existing = self._values(
                self.client.get(
                    "/product",
                    params={
                        "number": payload.number,
                        "count": 1,
                        "fields": "id,name,number,priceExcludingVatCurrency,priceIncludingVatCurrency,vatType(id,percentage),version",
                    },
                )
            )
            if existing:
                return existing[0]
        body = {
            "name": payload.name,
            "priceExcludingVatCurrency": payload.price_excluding_vat_currency,
        }
        if payload.number:
            body["number"] = payload.number
        if payload.vat_type_id is not None:
            body["vatType"] = {"id": payload.vat_type_id}
        response = self._call_with_tripletex_retry_hint(
            operation="product creation",
            call=lambda: self.client.post("/product", json_body=body),
        )
        return self._value(response)

    def create_project(self, payload: CreateProjectInput) -> dict[str, Any]:
        self._require_step()
        project_manager_id = payload.project_manager_id or int(self._whoami()["employeeId"])
        body = {
            "name": payload.name,
            "projectManager": {"id": project_manager_id},
            "startDate": payload.start_date,
        }
        if payload.number:
            body["number"] = payload.number
        if payload.end_date:
            body["endDate"] = payload.end_date
        if payload.is_internal is not None:
            body["isInternal"] = payload.is_internal
        response = self._call_with_tripletex_retry_hint(
            operation="project creation",
            call=lambda: self.client.post("/project", json_body=body),
        )
        return self._value(response)

    def configure_project_billing(self, payload: ConfigureProjectBillingInput) -> dict[str, Any]:
        self._require_step()
        if (
            payload.customer_id is None
            and payload.project_manager_id is None
            and payload.is_fixed_price is None
            and payload.fixed_price is None
        ):
            raise ModelRetry(
                "configure_project_billing needs at least one field to update: customer_id, project_manager_id, is_fixed_price, or fixed_price."
            )

        current = self._value(self.client.get(f"/project/{payload.project_id}", params={"fields": "*"}))
        body: dict[str, Any] = {
            "id": current["id"],
            "version": current["version"],
        }
        if payload.customer_id is not None:
            body["customer"] = {"id": payload.customer_id}
        if payload.project_manager_id is not None:
            body["projectManager"] = {"id": payload.project_manager_id}
        if payload.is_fixed_price is not None:
            body["isFixedPrice"] = payload.is_fixed_price
        if payload.fixed_price is not None:
            body["fixedprice"] = payload.fixed_price

        project = self._value(
            self._call_with_tripletex_retry_hint(
                operation="project billing configuration",
                call=lambda: self.client.put(f"/project/{payload.project_id}", json_body=body),
            )
        )
        verified = self._value(
            self.client.get(
                f"/project/{payload.project_id}",
                params={
                    "fields": "id,version,name,customer(id,name),projectManager(id),isFixedPrice,fixedprice,invoicingPlan"
                },
            )
        )
        return {"project": project, "verified_project": verified}

    def create_voucher(self, payload: CreateVoucherInput) -> dict[str, Any]:
        self._require_step()
        body = {
            "date": payload.date,
            "description": payload.description,
            "postings": self._validate_voucher_postings(
                [
                    {
                        "row": posting.row,
                        "date": posting.date,
                        "description": posting.description,
                        "account": {"id": posting.account_id},
                        "amountGross": posting.amount_gross,
                        "amountGrossCurrency": posting.amount_gross_currency,
                        "vatType": {"id": posting.vat_type_id} if posting.vat_type_id is not None else None,
                        "supplier": {"id": posting.supplier_id} if posting.supplier_id is not None else None,
                        "customer": {"id": posting.customer_id} if posting.customer_id is not None else None,
                        "employee": {"id": posting.employee_id} if posting.employee_id is not None else None,
                        "freeAccountingDimension1": {"id": posting.free_accounting_dimension_1_id}
                        if posting.free_accounting_dimension_1_id is not None
                        else None,
                        "freeAccountingDimension2": {"id": posting.free_accounting_dimension_2_id}
                        if posting.free_accounting_dimension_2_id is not None
                        else None,
                        "freeAccountingDimension3": {"id": posting.free_accounting_dimension_3_id}
                        if posting.free_accounting_dimension_3_id is not None
                        else None,
                    }
                    for posting in payload.postings
                ]
            ),
        }
        if payload.vendor_invoice_number:
            body["vendorInvoiceNumber"] = payload.vendor_invoice_number
        response = self._call_with_tripletex_retry_hint(
            operation="voucher creation",
            call=lambda: self.client.post(
                "/ledger/voucher",
                params={"sendToLedger": str(payload.send_to_ledger).lower()},
                json_body=body,
            ),
        )
        voucher = self._value(response)
        verified = self._value(self.client.get(f"/ledger/voucher/{voucher['id']}", params={"fields": "*"}))
        return {"voucher": voucher, "verified_voucher": verified}

    def create_order(self, payload: CreateOrderInput) -> dict[str, Any]:
        self._require_step()
        order_lines = []
        for line in payload.order_lines:
            order_line: dict[str, Any] = {"count": line.count}
            if line.product_id is not None:
                order_line["product"] = {"id": line.product_id}
            if line.description:
                order_line["description"] = line.description
            if line.unit_price_excluding_vat_currency is not None:
                order_line["unitPriceExcludingVatCurrency"] = line.unit_price_excluding_vat_currency
            order_lines.append(order_line)

        body = {
            "customer": {"id": payload.customer_id},
            "orderDate": payload.order_date,
            "deliveryDate": payload.delivery_date,
            "orderLines": order_lines,
        }
        if payload.project_id is not None:
            body["project"] = {"id": payload.project_id}
        response = self._call_with_tripletex_retry_hint(
            operation="order creation",
            call=lambda: self.client.post("/order", json_body=body),
        )
        return self._value(response)

    def create_invoice(self, payload: CreateInvoiceInput) -> dict[str, Any]:
        self._require_step()
        bank_account = self._ensure_bank_account_number()
        invoice_date_to = (date.fromisoformat(payload.invoice_date) + timedelta(days=1)).isoformat()
        body = {
            "invoiceDate": payload.invoice_date,
            "invoiceDueDate": payload.invoice_due_date,
            "customer": {"id": payload.customer_id},
            "orders": [{"id": order_id} for order_id in payload.order_ids],
        }
        invoice = self._value(
            self._call_with_tripletex_retry_hint(
                operation="invoice creation",
                call=lambda: self.client.post(
                    "/invoice",
                    params={"sendToCustomer": str(payload.send_to_customer).lower()},
                    json_body=body,
                ),
            )
        )
        verified = self._values(
            self.client.get(
                "/invoice",
                params={
                    "invoiceDateFrom": payload.invoice_date,
                    "invoiceDateTo": invoice_date_to,
                    "count": 10,
                    "fields": "id,invoiceNumber,invoiceDate,invoiceDueDate,customer(id,name),amount,amountOutstanding,isCreditNote,version",
                },
            )
        )
        return {
            "invoice": invoice,
            "verified_invoices": verified,
            "bank_account": {
                "id": bank_account["id"],
                "bankAccountNumber": bank_account.get("bankAccountNumber"),
            },
        }

    def register_invoice_payment(self, payload: RegisterInvoicePaymentInput) -> dict[str, Any]:
        self._require_step()
        payment_date_to = (date.fromisoformat(payload.payment_date) + timedelta(days=1)).isoformat()
        params: dict[str, Any] = {
            "paymentDate": payload.payment_date,
            "paymentTypeId": payload.payment_type_id,
            "paidAmount": payload.paid_amount,
        }
        if payload.paid_amount_currency is not None:
            params["paidAmountCurrency"] = payload.paid_amount_currency
        result = self._call_with_tripletex_retry_hint(
            operation="invoice payment registration",
            call=lambda: self.client.put(f"/invoice/{payload.invoice_id}/:payment", params=params),
        )
        invoice = self._values(
            self.client.get(
                "/invoice",
                params={
                    "invoiceDateFrom": payload.payment_date,
                    "invoiceDateTo": payment_date_to,
                    "count": 20,
                    "fields": "id,invoiceNumber,amount,amountOutstanding,version",
                },
            )
        )
        return {"payment_result": result, "invoices": invoice}

    def create_credit_note(self, payload: CreateCreditNoteInput) -> dict[str, Any]:
        self._require_step()
        response = self._call_with_tripletex_retry_hint(
            operation="credit note creation",
            call=lambda: self.client.put(
                f"/invoice/{payload.invoice_id}/:createCreditNote",
                params={"date": payload.date, "comment": payload.comment},
            ),
        )
        return self._value(response)

    def reverse_voucher(self, payload: ReverseVoucherInput) -> dict[str, Any]:
        self._require_step()
        response = self._call_with_tripletex_retry_hint(
            operation="voucher reversal",
            call=lambda: self.client.put(
                f"/ledger/voucher/{payload.voucher_id}/:reverse", params={"date": payload.date}
            ),
        )
        return self._value(response)

    def create_travel_expense(self, payload: CreateTravelExpenseInput) -> dict[str, Any]:
        self._require_step()
        employee_id = payload.employee_id or int(self._whoami()["employeeId"])
        body: dict[str, Any] = {
            "employee": {"id": employee_id},
            "title": payload.title,
            "travelDetails": {
                "isForeignTravel": payload.travel_details.is_foreign_travel,
                "isDayTrip": payload.travel_details.is_day_trip,
                "departureDate": payload.travel_details.departure_date,
                "returnDate": payload.travel_details.return_date,
                "departureFrom": payload.travel_details.departure_from,
                "destination": payload.travel_details.destination,
                "purpose": payload.travel_details.purpose,
            },
        }
        if payload.department_id is not None:
            body["department"] = {"id": payload.department_id}
        if payload.project_id is not None:
            body["project"] = {"id": payload.project_id}
        if payload.travel_details.departure_time:
            body["travelDetails"]["departureTime"] = payload.travel_details.departure_time
        if payload.travel_details.return_time:
            body["travelDetails"]["returnTime"] = payload.travel_details.return_time
        response = self._call_with_tripletex_retry_hint(
            operation="travel expense creation",
            call=lambda: self.client.post("/travelExpense", json_body=body),
        )
        return self._value(response)

    def add_travel_expense_cost(self, payload: AddTravelExpenseCostInput) -> dict[str, Any]:
        self._require_step()
        body = {
            "travelExpense": {"id": payload.travel_expense_id},
            "costCategory": {"id": payload.cost_category_id},
            "paymentType": {"id": payload.payment_type_id},
            "date": payload.date,
            "amountCurrencyIncVat": payload.amount_currency_inc_vat,
            "currency": {"id": payload.currency_id},
        }
        if payload.comments:
            body["comments"] = payload.comments
        response = self._call_with_tripletex_retry_hint(
            operation="travel expense cost creation",
            call=lambda: self.client.post("/travelExpense/cost", json_body=body),
        )
        return self._value(response)

    def transition_travel_expense(self, payload: TransitionTravelExpenseInput) -> dict[str, Any]:
        self._require_step()
        result = self._call_with_tripletex_retry_hint(
            operation=f"travel expense transition {payload.action}",
            call=lambda: self.client.put(f"/travelExpense/:{payload.action}", params={"id": payload.travel_expense_id}),
        )
        return {"action": payload.action, "result": result}

    def create_timesheet_entry(self, payload: CreateTimesheetEntryInput) -> dict[str, Any]:
        self._require_step()
        employee_id = payload.employee_id or int(self._whoami()["employeeId"])
        body = {
            "employee": {"id": employee_id},
            "project": {"id": payload.project_id},
            "activity": {"id": payload.activity_id},
            "date": payload.date,
            "hours": payload.hours,
        }
        if payload.comment:
            body["comment"] = payload.comment
        response = self._call_with_tripletex_retry_hint(
            operation="timesheet entry creation",
            call=lambda: self.client.post("/timesheet/entry", json_body=body),
        )
        return self._value(response)

    def get_timesheet_activities(self, payload: GetTimesheetActivitiesInput) -> dict[str, Any]:
        self._require_step()
        employee_id = payload.employee_id or int(self._whoami()["employeeId"])
        params = {"projectId": payload.project_id, "employeeId": employee_id, "date": payload.date}
        response = self._call_with_tripletex_retry_hint(
            operation="timesheet activity lookup",
            call=lambda: self.client.get(
                "/activity/>forTimeSheet", params=params, cache_key=json.dumps(params, sort_keys=True)
            ),
        )
        return {"values": self._values(response)}

    def create_contact(self, payload: CreateContactInput) -> dict[str, Any]:
        self._require_step()
        body: dict[str, Any] = {
            "firstName": payload.first_name,
            "lastName": payload.last_name,
            "customer": {"id": payload.customer_id},
        }
        if payload.email:
            body["email"] = payload.email
        response = self._call_with_tripletex_retry_hint(
            operation="contact creation",
            call=lambda: self.client.post("/contact", json_body=body),
        )
        return self._value(response)

    def update_employee(self, payload: UpdateEmployeeInput) -> dict[str, Any]:
        self._require_step()
        current = self._value(self.client.get(f"/employee/{payload.employee_id}", params={"fields": "*"}))
        body: dict[str, Any] = {
            "id": current["id"],
            "version": current["version"],
        }
        body["firstName"] = payload.first_name or current.get("firstName", "")
        body["lastName"] = payload.last_name or current.get("lastName", "")
        body["dateOfBirth"] = payload.date_of_birth or current.get("dateOfBirth")
        if body["dateOfBirth"] is None:
            raise ModelRetry(
                "Tripletex requires dateOfBirth on every employee PUT. "
                "Provide date_of_birth in the payload (YYYY-MM-DD)."
            )
        if payload.email is not None:
            body["email"] = payload.email
        elif current.get("email"):
            body["email"] = current["email"]
        if payload.user_type is not None:
            body["userType"] = payload.user_type
        elif current.get("userType"):
            body["userType"] = current["userType"]
        if payload.department_id is not None:
            body["department"] = {"id": payload.department_id}
        elif current.get("department"):
            body["department"] = current["department"]
        if payload.phone_number_mobile is not None:
            body["phoneNumberMobile"] = payload.phone_number_mobile
        if payload.phone_number_work is not None:
            body["phoneNumberWork"] = payload.phone_number_work
        response = self._call_with_tripletex_retry_hint(
            operation="employee update",
            call=lambda: self.client.put(f"/employee/{payload.employee_id}", json_body=body),
        )
        return self._value(response)

    def create_department(self, payload: CreateDepartmentInput) -> dict[str, Any]:
        self._require_step()
        body: dict[str, Any] = {"name": payload.name}
        if payload.department_number:
            body["departmentNumber"] = payload.department_number
        response = self._call_with_tripletex_retry_hint(
            operation="department creation",
            call=lambda: self.client.post("/department", json_body=body),
        )
        return self._value(response)

    def add_travel_mileage_allowance(self, payload: AddTravelMileageAllowanceInput) -> dict[str, Any]:
        self._require_step()
        body = {
            "travelExpense": {"id": payload.travel_expense_id},
            "rateType": {"id": payload.rate_type_id},
            "rateCategory": {"id": payload.rate_category_id},
            "date": payload.date,
            "departureLocation": payload.departure_location,
            "destination": payload.destination,
            "km": payload.km,
        }
        response = self._call_with_tripletex_retry_hint(
            operation="travel mileage allowance creation",
            call=lambda: self.client.post("/travelExpense/mileageAllowance", json_body=body),
        )
        return self._value(response)

    def add_travel_per_diem(self, payload: AddTravelPerDiemInput) -> dict[str, Any]:
        self._require_step()
        body: dict[str, Any] = {
            "travelExpense": {"id": payload.travel_expense_id},
            "rateType": {"id": payload.rate_type_id},
            "rateCategory": {"id": payload.rate_category_id},
            "location": payload.location,
            "count": payload.count,
            "isDeductionForBreakfast": payload.is_deduction_for_breakfast,
            "isDeductionForLunch": payload.is_deduction_for_lunch,
            "isDeductionForDinner": payload.is_deduction_for_dinner,
        }
        if payload.overnight_accommodation is not None:
            body["overnightAccommodation"] = payload.overnight_accommodation
        response = self._call_with_tripletex_retry_hint(
            operation="travel per-diem compensation creation",
            call=lambda: self.client.post("/travelExpense/perDiemCompensation", json_body=body),
        )
        return self._value(response)

    def _ensure_division(self, start_date: str) -> int:
        """Find an existing division or create one."""
        divisions = self._values(self.client.get("/company/divisions", params={"count": 1}))
        if divisions:
            return int(divisions[0]["id"])
        whoami = self._whoami()
        company = self._value(
            self.client.get(f"/company/{whoami['companyId']}", params={"fields": "id,organizationNumber"})
        )
        # Tripletex rejects the parent company's own org number for divisions
        # ("Juridisk enhet kan ikke registreres som virksomhet/underenhet").
        # Derive a sub-unit number by changing the last digit.
        parent_org = company.get("organizationNumber", "000000000")
        last_digit = int(parent_org[-1]) if parent_org and parent_org[-1].isdigit() else 0
        sub_org = parent_org[:-1] + str((last_digit + 1) % 10)
        municipalities = self._values(self.client.get("/municipality", params={"count": 1, "fields": "id,name"}))
        municipality_id = municipalities[0]["id"] if municipalities else 262
        div_body = {
            "name": "Hovedvirksomhet",
            "startDate": start_date,
            "organizationNumber": sub_org,
            "municipalityDate": start_date,
            "municipality": {"id": municipality_id},
        }
        div_response = self._call_with_tripletex_retry_hint(
            operation="division creation",
            call=lambda: self.client.post("/division", json_body=div_body),
        )
        return int(self._value(div_response)["id"])

    def create_employment(self, payload: CreateEmploymentInput) -> dict[str, Any]:
        self._require_step()
        existing = self._values(
            self.client.get(
                "/employee/employment",
                params={"employeeId": payload.employee_id, "count": 1, "fields": "id,startDate,division(id,name)"},
            )
        )
        if existing:
            return existing[0]

        employee = self._value(
            self.client.get(f"/employee/{payload.employee_id}", params={"fields": "id,version,dateOfBirth"})
        )
        if not employee.get("dateOfBirth"):
            self.client.put(
                f"/employee/{payload.employee_id}",
                json_body={"id": employee["id"], "version": employee["version"], "dateOfBirth": "1990-01-01"},
            )

        division_id = payload.division_id or self._ensure_division(payload.start_date)

        emp_body: dict[str, Any] = {
            "employee": {"id": payload.employee_id},
            "startDate": payload.start_date,
            "division": {"id": division_id},
            "isMainEmployer": payload.is_main_employer,
        }
        response = self._call_with_tripletex_retry_hint(
            operation="employment creation",
            call=lambda: self.client.post("/employee/employment", json_body=emp_body),
        )
        return self._value(response)

    def _ensure_employee_employment(self, employee_id: int, start_date: str) -> None:
        employments = self._values(
            self.client.get(
                "/employee/employment", params={"employeeId": employee_id, "count": 1, "fields": "id,startDate"}
            )
        )
        if employments:
            return

        employee = self._value(self.client.get(f"/employee/{employee_id}", params={"fields": "id,version,dateOfBirth"}))
        if not employee.get("dateOfBirth"):
            self.client.put(
                f"/employee/{employee_id}",
                json_body={"id": employee["id"], "version": employee["version"], "dateOfBirth": "1990-01-01"},
            )

        division_id = self._ensure_division(start_date)

        emp_body: dict[str, Any] = {
            "employee": {"id": employee_id},
            "startDate": start_date,
            "division": {"id": division_id},
        }
        self.client.post("/employee/employment", json_body=emp_body)

    def run_salary_transaction(self, payload: RunSalaryTransactionInput) -> dict[str, Any]:
        self._require_step()
        for payslip in payload.payslips:
            self._ensure_employee_employment(payslip.employee_id, payslip.date)
        payslips = []
        for payslip in payload.payslips:
            specifications = [
                {
                    "salaryType": {"id": spec.salary_type_id},
                    "rate": spec.rate,
                    "count": spec.count,
                    "amount": spec.amount,
                }
                for spec in payslip.specifications
            ]
            payslips.append(
                {
                    "employee": {"id": payslip.employee_id},
                    "date": payslip.date,
                    "month": payslip.month,
                    "year": payslip.year,
                    "specifications": specifications,
                }
            )
        body = {
            "date": payload.date,
            "month": payload.month,
            "year": payload.year,
            "payslips": payslips,
        }
        response = self._call_with_tripletex_retry_hint(
            operation="salary transaction",
            call=lambda: self.client.post(
                "/salary/transaction",
                params={"generateTaxDeduction": str(payload.generate_tax_deduction).lower()},
                json_body=body,
            ),
        )
        return self._value(response)

    def upload_attachment(self, payload: UploadAttachmentInput, *, files: list[Any]) -> dict[str, Any]:
        self._require_step()
        if payload.file_index < 0 or payload.file_index >= len(files):
            raise ModelRetry(
                f"file_index {payload.file_index} is out of range. The request has {len(files)} file(s) (0-indexed)."
            )
        attachment = files[payload.file_index]
        path_map = {
            "voucher": f"/ledger/voucher/{payload.entity_id}/attachment",
            "travel_expense": f"/travelExpense/{payload.entity_id}/attachment",
            "salary_transaction": f"/salary/transaction/{payload.entity_id}/attachment",
        }
        path = path_map[payload.entity_type]
        response = self._call_with_tripletex_retry_hint(
            operation=f"attachment upload to {payload.entity_type}",
            call=lambda: self.client.upload(
                path,
                file_data=attachment.data,
                filename=attachment.filename,
                mime_type=attachment.mime_type,
            ),
        )
        return self._value(response)

    def create_bank_reconciliation(self, payload: CreateBankReconciliationInput) -> dict[str, Any]:
        self._require_step()
        body = {
            "account": {"id": payload.account_id},
            "accountingPeriod": {"id": payload.accounting_period_id},
            "type": payload.type,
            "bankAccountClosingBalanceCurrency": payload.bank_account_closing_balance_currency,
        }
        response = self._call_with_tripletex_retry_hint(
            operation="bank reconciliation creation",
            call=lambda: self.client.post("/bank/reconciliation", json_body=body),
        )
        return self._value(response)

    def create_webhook_subscription(self, payload: CreateWebhookSubscriptionInput) -> dict[str, Any]:
        self._require_step()
        body: dict[str, Any] = {
            "event": payload.event,
            "targetUrl": payload.target_url,
        }
        if payload.fields:
            body["fields"] = payload.fields
        if payload.auth_header_name:
            body["authHeaderName"] = payload.auth_header_name
        if payload.auth_header_value:
            body["authHeaderValue"] = payload.auth_header_value
        response = self._call_with_tripletex_retry_hint(
            operation="webhook subscription creation",
            call=lambda: self.client.post("/event/subscription", json_body=body),
        )
        return self._value(response)

    def create_accounting_dimension(self, payload: CreateAccountingDimensionInput) -> dict[str, Any]:
        self._require_step()
        dim_body: dict[str, Any] = {
            "dimensionName": payload.dimension_name,
            "active": payload.active,
        }
        if payload.description:
            dim_body["description"] = payload.description
        dim_response = self._call_with_tripletex_retry_hint(
            operation="accounting dimension creation",
            call=lambda: self.client.post("/ledger/accountingDimensionName", json_body=dim_body),
        )
        dimension = self._value(dim_response)
        dimension_index = dimension.get("dimensionIndex")

        created_values: list[dict[str, Any]] = []
        for val in payload.values:
            val_body: dict[str, Any] = {
                "dimensionIndex": dimension_index,
                "displayName": val.display_name,
                "showInVoucherRegistration": val.show_in_voucher_registration,
                "active": val.active,
            }
            if val.number:
                val_body["number"] = val.number
            val_response = self._call_with_tripletex_retry_hint(
                operation="accounting dimension value creation",
                call=lambda: self.client.post("/ledger/accountingDimensionValue", json_body=val_body),
            )
            created_values.append(self._value(val_response))

        return {"dimension": dimension, "values": created_values}

    @staticmethod
    def _sanitize_reference_filters(reference: str, filters: dict[str, Any]) -> dict[str, Any]:
        """Validate and sanitize filters before passing to Tripletex API."""
        sanitized = dict(filters)
        for key, value in list(sanitized.items()):
            str_value = str(value)
            if "%" in str_value or "*" in str_value or ">" in str_value or "<" in str_value:
                if key in ("numberFrom", "numberTo", "accountNumberFrom", "accountNumberTo"):
                    continue
                raise ModelRetry(
                    f"Filter '{key}={value}' contains wildcard/pattern characters. "
                    f"Tripletex filters require exact values. "
                    f"For account ranges, use numberFrom/numberTo with integer values "
                    f'(e.g. filters={{"numberFrom": 8000, "numberTo": 8999}}).'
                )
        if reference == "accounts" and "number" in sanitized:
            num_val = sanitized["number"]
            if not isinstance(num_val, int):
                try:
                    sanitized["number"] = int(str(num_val).strip())
                except (ValueError, TypeError):
                    raise ModelRetry(
                        f"Filter 'number={num_val}' is not a valid integer for account lookup. "
                        f"Use an exact account number (e.g. 2400) or use numberFrom/numberTo for ranges."
                    )
        return sanitized

    def get_reference_data(self, payload: ReferenceLookupInput) -> dict[str, Any]:
        self._require_step()
        filters = self._sanitize_reference_filters(payload.reference, dict(payload.filters))

        if payload.reference == "whoami":
            return self._whoami()
        if payload.reference == "vat_settings":
            return self._value(self.client.get("/ledger/vatSettings", cache_key="vat_settings"))
        if payload.reference == "accounts":
            params = {"count": 200, "fields": "id,number,name,type"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/ledger/account", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "vat_types":
            params = {"count": 60, "fields": "id,name,number,percentage"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/ledger/vatType", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "voucher_types":
            params = {"count": 20, "fields": "id,name,displayName"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/ledger/voucherType", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "currencies":
            params = {"count": 25, "fields": "id,code"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/currency", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "employees":
            params = {"count": 20, "fields": "id,firstName,lastName,email,employeeNumber,displayName,userType,version"}
            params.update(filters)
            return {"values": self._values(self.client.get("/employee", params=params))}
        if payload.reference == "departments":
            params = {"count": 20, "fields": "id,name,departmentNumber"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/department", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "customers":
            params = {"count": 20, "fields": "id,name,customerNumber,organizationNumber,email,version"}
            params.update(filters)
            return {"values": self._values(self.client.get("/customer", params=params))}
        if payload.reference == "suppliers":
            params = {"count": 20, "fields": "id,name,supplierNumber,organizationNumber,email,version"}
            params.update(filters)
            return {"values": self._values(self.client.get("/supplier", params=params))}
        if payload.reference == "products":
            params = {
                "count": 20,
                "fields": "id,name,number,priceExcludingVatCurrency,priceIncludingVatCurrency,vatType(id,percentage),version",
            }
            params.update(filters)
            return {"values": self._values(self.client.get("/product", params=params))}
        if payload.reference == "projects":
            params = {
                "count": 20,
                "fields": "id,name,number,startDate,endDate,version,customer(id,name),projectManager(id),isFixedPrice,fixedprice",
            }
            params.update(filters)
            return {"values": self._values(self.client.get("/project", params=params))}
        if payload.reference == "invoice_payment_types":
            params = {"count": 20}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/invoice/paymentType", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "travel_cost_categories":
            params = {"count": 25}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get(
                        "/travelExpense/costCategory", params=params, cache_key=json.dumps(params, sort_keys=True)
                    )
                )
            }
        if payload.reference == "travel_payment_types":
            params = {"count": 10}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get(
                        "/travelExpense/paymentType", params=params, cache_key=json.dumps(params, sort_keys=True)
                    )
                )
            }
        if payload.reference == "travel_expenses":
            params = {"count": 10, "fields": "id,title,date,state,employee(id,firstName,lastName),amount,version"}
            params.update(filters)
            return {"values": self._values(self.client.get("/travelExpense", params=params))}
        if payload.reference == "activities_for_timesheet":
            if "employeeId" not in filters:
                filters["employeeId"] = int(self._whoami()["employeeId"])
            required = {"projectId", "date"}
            if not required.issubset(filters):
                missing = ", ".join(sorted(required - set(filters)))
                raise ModelRetry(
                    "activities_for_timesheet requires filters "
                    f"{missing}. employeeId defaults to the current user if you omit it."
                )
            return {
                "values": self._values(
                    self.client.get(
                        "/activity/>forTimeSheet", params=filters, cache_key=json.dumps(filters, sort_keys=True)
                    )
                )
            }
        if payload.reference == "salary_types":
            params = {"count": 50, "fields": "id,number,name,description"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/salary/type", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "divisions":
            params = {"count": 50}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/company/divisions", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "travel_mileage_rates":
            params: dict[str, Any] = {"type": "MILEAGE_ALLOWANCE", "count": 20}
            params.update(filters)
            return {"values": self._values(self.client.get("/travelExpense/rate", params=params))}
        if payload.reference == "travel_per_diem_rates":
            params: dict[str, Any] = {
                "type": "PER_DIEM",
                "isDomestic": True,
                "count": 25,
                "fields": "id,rate,rateCategory(id,name),zone,breakfastDeductionRate,lunchDeductionRate,dinnerDeductionRate",
            }
            params.update(filters)
            if "dateFrom" not in params or "dateTo" not in params:
                params.setdefault("dateFrom", "2026-01-01")
                params.setdefault("dateTo", "2026-12-31")
            return {"values": self._values(self.client.get("/travelExpense/rate", params=params))}
        if payload.reference == "countries":
            params = {"count": 300, "fields": "id,code,name"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/country", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "municipalities":
            params = {"count": 500, "fields": "id,name,number"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/municipality", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }
        if payload.reference == "events":
            return self._value(self.client.get("/event", cache_key="events"))
        if payload.reference == "accounting_periods":
            params = {"count": 20, "fields": "id,start,end,isClosed"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get(
                        "/ledger/accountingPeriod", params=params, cache_key=json.dumps(params, sort_keys=True)
                    )
                )
            }
        if payload.reference == "bank_accounts":
            params = {"isBankAccount": True, "count": 20, "fields": "id,number,name,bankAccountNumber"}
            params.update(filters)
            return {
                "values": self._values(
                    self.client.get("/ledger/account", params=params, cache_key=json.dumps(params, sort_keys=True))
                )
            }

        raise ValueError(f"Unsupported reference lookup: {payload.reference}")

    async def find_api(self, need: str) -> dict[str, Any]:
        """Spawn a sub-agent to find the right Tripletex API endpoint for a given need."""
        self._require_step()

        from ai_accounting_agent import gemini
        from ai_accounting_agent.api_index import get_api_index

        index = get_api_index()
        matched_tags, relevant_docs = index.search(need, max_groups=5)

        log_event(
            "find_api_search",
            run_id=self.run_id,
            need=need,
            matched_tags=matched_tags,
            doc_chars=len(relevant_docs),
        )

        if not relevant_docs:
            return {
                "guidance": "No matching API endpoints found for this need. Try rephrasing or use search_tripletex_reference.",
                "searched_tags": [],
                "subagent_duration_ms": 0,
            }

        sub_agent: Agent[None] = Agent(
            gemini.build_google_model(),
            instructions=FIND_API_SYSTEM_PROMPT,
            output_type=str,
        )

        started = time.perf_counter()
        result = await sub_agent.run(f"Relevant API documentation:\n\n{relevant_docs}\n\n---\n\nWhat I need: {need}")
        duration_ms = round((time.perf_counter() - started) * 1000)

        guidance = result.output
        usage = result.usage()

        log_event(
            "find_api_subagent_result",
            run_id=self.run_id,
            need=need,
            matched_tags=matched_tags,
            duration_ms=duration_ms,
            guidance_length=len(guidance),
            guidance_preview=guidance[:2000],
            usage=usage,
        )

        return {
            "guidance": guidance,
            "searched_tags": matched_tags,
            "subagent_duration_ms": duration_ms,
        }

    def raw_api_call(self, payload: RawApiCallInput) -> dict[str, Any]:
        """Execute an arbitrary Tripletex API call."""
        self._require_step()
        path = payload.path
        if not path.startswith("/"):
            path = f"/{path}"

        return self._call_with_tripletex_retry_hint(
            operation=f"{payload.method} {path}",
            call=lambda: self.client.request(
                method=payload.method,
                path=path,
                params=payload.query_params,
                json_body=payload.body,
            ),
        )


def register_tripletex_tools(agent: Agent[Any]) -> None:
    def _service(ctx: RunContext[Any]) -> TripletexService:
        return TripletexService(
            client=ctx.deps.client,
            run_id=ctx.deps.run_id,
            reference_index=ctx.deps.reference_index,
            step_state=ctx.deps.step_state,
        )

    @agent.tool
    @log_tool
    def announce_step(
        ctx: RunContext[Any],
        task_understanding: str,
        planned_tools: list[str],
        success_criteria: str,
    ) -> dict[str, Any]:
        """Required first tool call before any Tripletex or workflow action.

        Call this before every logical cluster of work. Provide:
        - task_understanding: what the user wants (be specific about entities and values)
        - planned_tools: list of tool names you will call next (from the tool catalog)
        - success_criteria: what the Tripletex state should look like when done

        Call again when switching phases (e.g. from discovery to writes, or from creation to reversal).
        """
        return _service(ctx).announce_step(
            task_understanding=task_understanding,
            planned_tools=planned_tools,
            success_criteria=success_criteria,
        )

    @agent.tool
    @log_tool
    def get_today_date(ctx: RunContext[Any]) -> dict[str, str]:
        """Return today's date. Call this FIRST (alongside announce_step) whenever you need a date for any field (invoice_date, order_date, voucher date, salary month/year, etc.). Never guess or assume the current date."""
        today = date.today()
        return {
            "today": today.isoformat(),
            "year": str(today.year),
            "month": str(today.month),
            "day": str(today.day),
        }

    @agent.tool
    @log_tool
    def search_tripletex_reference(ctx: RunContext[Any], query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Search the local Tripletex API reference docs for verified guidance.

        WHEN TO USE:
        - Before generic REST calls (tripletex_get/post/put/delete) to verify endpoint shape
        - When unsure about VAT behavior, account ledger types, or rare field names
        - For endpoints not covered by curated tools (e.g. salary, balance sheet, contacts)

        DO NOT USE for basic tool usage — each curated tool's docstring has the info you need.
        Once you have a recipe, stop searching and execute. Avoid repeated search loops.
        """
        return _service(ctx).search_tripletex_reference(query=query, max_results=max_results)

    @agent.tool(retries=1)
    @log_tool
    def tripletex_get(
        ctx: RunContext[Any],
        path: str,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
    ) -> Any:
        """LAST RESORT: Call a Tripletex GET endpoint when no curated tool fits.

        Only use after verifying the endpoint via search_tripletex_reference.
        Put query parameters in params, not in the path string.

        Required date filters on collection endpoints (rejected without them):
        - /order → orderDateFrom + orderDateTo
        - /invoice → invoiceDateFrom + invoiceDateTo
        - /ledger/voucher → dateFrom + dateTo
        - /ledger/posting → dateFrom + dateTo
        - /supplier → no name filter; use supplierNumber or organizationNumber
        - /customer → use customerName, not name
        """
        return _service(ctx).tripletex_get(path=path, params=params, cache_key=cache_key)

    @agent.tool(retries=1)
    @log_tool
    def tripletex_post(
        ctx: RunContext[Any],
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        """LAST RESORT: Call a Tripletex POST endpoint when no curated tool fits.

        Only use after verifying the endpoint shape via search_tripletex_reference.
        Put query parameters in params, not in the path string.
        """
        return _service(ctx).tripletex_post(path=path, body=body, params=params)

    @agent.tool(retries=1)
    @log_tool
    def tripletex_put(
        ctx: RunContext[Any],
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        """LAST RESORT: Call a Tripletex PUT endpoint when no curated tool fits.

        Only use after verifying the endpoint shape via search_tripletex_reference.
        PUT requires id + version for optimistic locking. GET the entity first to get the current version.
        Put query parameters in params, not in the path string.
        """
        return _service(ctx).tripletex_put(path=path, body=body, params=params)

    @agent.tool(retries=1)
    @log_tool
    def tripletex_delete(ctx: RunContext[Any], path: str, params: dict[str, Any] | None = None) -> Any:
        """LAST RESORT: Call a Tripletex DELETE endpoint when no curated tool fits.

        Typical pattern: tripletex_get to find the entity id, then tripletex_delete with the path /resource/{id}.
        """
        return _service(ctx).tripletex_delete(path=path, params=params)

    @agent.tool(retries=1)
    @log_tool
    def create_employee(ctx: RunContext[Any], payload: CreateEmployeeInput) -> dict[str, Any]:
        """Create a Tripletex employee.

        WHEN TO USE: Task asks to create/register a new employee.
        REQUIRED: first_name, last_name. department_id defaults to the company's first department if omitted.
        user_type defaults to "STANDARD". Use "EXTENDED" for full access, "NO_ACCESS" for no login.

        If an employee with the given email already exists, returns the existing employee (no duplicate created).
        To set a start date for the employee, call create_employment after this tool.
        Start dates live on the employment record, not on the employee entity.
        To make the employee an admin, call grant_employee_privileges after this tool.
        """
        return _service(ctx).create_employee(payload)

    @agent.tool(retries=1)
    @log_tool
    def grant_employee_privileges(ctx: RunContext[Any], payload: GrantEmployeePrivilegesInput) -> dict[str, Any]:
        """Grant employee entitlements using a Tripletex permission template.

        WHEN TO USE: After create_employee, when the task says the employee should be admin/accountant/etc.
        Templates: ALL_PRIVILEGES (admin), ACCOUNTANT, INVOICING_MANAGER, PERSONELL_MANAGER, AUDITOR,
        DEPARTMENT_LEADER, NONE_PRIVILEGES. "kontoadministrator" / "administrator" → ALL_PRIVILEGES.
        """
        return _service(ctx).grant_employee_privileges(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_customer(ctx: RunContext[Any], payload: CreateCustomerInput) -> dict[str, Any]:
        """Create a Tripletex customer.

        WHEN TO USE: Before create_order/create_invoice, or when task asks to register a customer.
        REQUIRED: name. Optional: organization_number (auto-normalized to 9 digits), email,
        invoice_send_method (e.g. "EMAIL"), invoices_due_in + invoices_due_in_type (e.g. 14, "DAYS").
        ADDRESS: If the task provides an address, parse it into address_line1, postal_code, and city.
        Example: "Torggata 50, 9008 Tromsø" → address_line1="Torggata 50", postal_code="9008", city="Tromsø".
        Returns the created customer with its id — reuse this id in subsequent tools.
        """
        return _service(ctx).create_customer(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_supplier(ctx: RunContext[Any], payload: CreateSupplierInput) -> dict[str, Any]:
        """Create a Tripletex supplier.

        WHEN TO USE: Before create_voucher for supplier invoice booking.
        REQUIRED: name. Optional: organization_number (auto-normalized to 9 digits), email, invoice_email.
        When email is provided, it is automatically copied to invoice_email unless a separate invoice_email is given.
        Returns the created supplier with its id — reuse this id in create_voucher postings.
        """
        return _service(ctx).create_supplier(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_product(ctx: RunContext[Any], payload: CreateProductInput) -> dict[str, Any]:
        """Create a Tripletex product with a net price and optional VAT type.

        WHEN TO USE: Before create_order when the invoice needs a product line.
        REQUIRED: name, price_excluding_vat_currency (net price).
        Optional: number (SKU), vat_type_id (defaults to 6 = 0% in non-VAT companies).
        In non-VAT-registered companies only vat_type_id=6 is accepted.

        This tool auto-checks for existing products by number — if a product with the given number
        already exists, it returns the existing product without creating a duplicate. No pre-lookup needed.
        """
        return _service(ctx).create_product(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_project(ctx: RunContext[Any], payload: CreateProjectInput) -> dict[str, Any]:
        """Create a Tripletex project.

        WHEN TO USE: For timesheet, fixed-price billing, or travel expense linked to a project.
        REQUIRED: name, start_date (YYYY-MM-DD).
        project_manager_id defaults to the logged-in employee if omitted.
        Optional: number, end_date, is_internal.

        For fixed-price billing, call configure_project_billing after this to set customer and pricing.
        """
        return _service(ctx).create_project(payload)

    @agent.tool(retries=1)
    @log_tool
    def configure_project_billing(ctx: RunContext[Any], payload: ConfigureProjectBillingInput) -> dict[str, Any]:
        """Update a project's billing settings (versioned GET-then-PUT handled automatically).

        WHEN TO USE: For fixed-price projects, stage billing, or linking a customer to a project.
        Do NOT use tripletex_put for project updates — this tool handles version numbers automatically.

        TYPICAL FIXED-PRICE FLOW:
        1. create_customer → customer_id
        2. create_project → project_id
        3. configure_project_billing(project_id, customer_id, is_fixed_price=True, fixed_price=TOTAL_PROJECT_AMOUNT)
        4. create_order(customer_id, project_id, order_lines with milestone amount)
        5. create_invoice(customer_id, order_ids)

        IMPORTANT: fixed_price is the TOTAL project contract value, not the milestone amount.
        The milestone amount goes in the order line's unit_price_excluding_vat_currency.
        """
        return _service(ctx).configure_project_billing(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_voucher(ctx: RunContext[Any], payload: CreateVoucherInput) -> dict[str, Any]:
        """Create and verify a balanced Tripletex ledger voucher.

        WHEN TO USE: For supplier invoices, manual journal entries, and any balanced double-entry posting.
        There is NO POST /supplierInvoice — use this tool for vendor bills.

        CRITICAL RULES:
        - Postings must balance: signed amount_gross values MUST sum to exactly 0
        - Each posting needs its own date field (typically same as voucher date)
        - Rows auto-number from 1 if omitted
        - VAT-locked accounts require vat_type_id on the posting (e.g. account 3200 needs vat_type_id=6)
        - Accounts with ledgerType VENDOR require supplier_id on that posting
        - Accounts with ledgerType CUSTOMER require customer_id on that posting
        - Accounts with ledgerType EMPLOYEE require employee_id on that posting

        SUPPLIER INVOICE PATTERN (most common):
        - posting 1: account_id=<expense account e.g. 6300>, amount_gross=+invoiceAmount (debit)
        - posting 2: account_id=<2400 Leverandørgjeld>, supplier_id=<id>, amount_gross=-invoiceAmount (credit)
        - Set vendor_invoice_number when the invoice number is available

        Look up account IDs first: get_reference_data(reference="accounts", filters={"number": 2400})
        Common expense accounts: 4000 (purchases), 6300 (rent), 6900 (telecom), 7000 (depreciation).

        ACCOUNTING DIMENSIONS: To link a posting to a custom dimension, set
        free_accounting_dimension_1_id (or 2/3) to the dimension value ID
        returned by create_accounting_dimension.
        """
        return _service(ctx).create_voucher(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_order(ctx: RunContext[Any], payload: CreateOrderInput) -> dict[str, Any]:
        """Create a Tripletex order (required before create_invoice).

        WHEN TO USE: As a prerequisite for create_invoice. Invoices are always created from orders.
        REQUIRED: customer_id, order_date, delivery_date (both YYYY-MM-DD), at least one order line.
        delivery_date MUST be provided — omitting it gives 422 "deliveryDate: Kan ikke være null".

        ORDER LINE OPTIONS:
        - Product-based: set product_id and count. Price/VAT from product.
        - Freeform: set description, count=1, unit_price_excluding_vat_currency=amount. No product needed.

        For project milestone/stage billing: pass project_id and use a freeform line for the milestone amount.
        Do NOT GET /order to inspect schema — use this tool directly.
        """
        return _service(ctx).create_order(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_invoice(ctx: RunContext[Any], payload: CreateInvoiceInput) -> dict[str, Any]:
        """Create and verify a Tripletex invoice from one or more existing orders.

        WHEN TO USE: After create_order. This is the final step in the invoicing flow.
        REQUIRED: customer_id, invoice_date, invoice_due_date (YYYY-MM-DD), order_ids (list of order IDs).
        send_to_customer defaults to false. Set to TRUE when the prompt explicitly asks to
        send/envie/senden/envoyez/schicken/invia the invoice to the customer.

        PREREQUISITES (handled automatically by this tool):
        - Company must have a bank account number on account 1920 — this tool auto-sets it if missing.
        - At least one order must exist (use create_order first).
        """
        return _service(ctx).create_invoice(payload)

    @agent.tool(retries=1)
    @log_tool
    def register_invoice_payment(ctx: RunContext[Any], payload: RegisterInvoicePaymentInput) -> dict[str, Any]:
        """Register payment on an existing invoice.

        WHEN TO USE: After create_invoice, when the task asks to record a payment.
        REQUIRED: invoice_id, payment_date (YYYY-MM-DD), payment_type_id, paid_amount.

        PREREQUISITE: Look up payment_type_id first:
          get_reference_data(reference="invoice_payment_types")
        Common types: "Kontant" and "Betalt til bank" — IDs vary per sandbox, always look them up.
        """
        return _service(ctx).register_invoice_payment(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_credit_note(ctx: RunContext[Any], payload: CreateCreditNoteInput) -> dict[str, Any]:
        """Create a credit note from an existing invoice.

        WHEN TO USE:
        - When the task asks to credit or cancel an invoice itself (not a payment)
        - When the task says "kreditnota", "credit note", "nota de crédito", "Gutschrift", etc.
        NOTE: To reverse a PAYMENT (e.g. bank returned), use reverse_voucher on the payment voucher instead — a credit note does NOT restore the original invoice's outstanding balance.
        REQUIRED: invoice_id (the original invoice to credit), date (YYYY-MM-DD), comment.

        HOW TO FIND THE INVOICE:
        1. Find the customer: get_reference_data(reference="customers", filters={"organizationNumber": "..."})
        2. Find the invoice: tripletex_get(path="/invoice", params={"customerId": id, "invoiceDateFrom": "2020-01-01", "invoiceDateTo": "2030-12-31"})
        3. Use the invoice id from the result.

        Returns the new credit note invoice with creditedInvoice pointing to the original.
        """
        return _service(ctx).create_credit_note(payload)

    @agent.tool(retries=1)
    @log_tool
    def reverse_voucher(ctx: RunContext[Any], payload: ReverseVoucherInput) -> dict[str, Any]:
        """Reverse an existing voucher.

        WHEN TO USE:
        - When the task asks to reverse/undo a voucher posting
        - When a payment was returned by the bank — reverse the PAYMENT VOUCHER (not the invoice)

        FOR PAYMENT REVERSALS:
        1. Find customer: get_reference_data(customers, filters={"organizationNumber": "..."})
        2. Find invoice: tripletex_get("/invoice", params={"customerId": id, "invoiceDateFrom": ..., "invoiceDateTo": ...})
        3. Find payment voucher: tripletex_get("/ledger/posting", params={"invoiceId": invoice_id, "dateFrom": ..., "dateTo": ...}) — look for postings on account 1920 (bank)
        4. reverse_voucher(voucher_id=payment_voucher_id, date=today)
        This restores the original invoice's amountOutstanding.

        REQUIRED: voucher_id, date (YYYY-MM-DD).
        """
        return _service(ctx).reverse_voucher(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_travel_expense(ctx: RunContext[Any], payload: CreateTravelExpenseInput) -> dict[str, Any]:
        """Create a travel expense with nested travelDetails.

        WHEN TO USE: When the task asks to create/register a travel expense or business trip.
        REQUIRED: title, travel_details (nested object with departure_date, return_date, departure_from,
        destination, purpose, is_day_trip, is_foreign_travel).

        CRITICAL: All travel detail fields go inside travel_details, NOT at the root level.
        Putting them at root gives 422 "Feltet eksisterer ikke i objektet".
        employee_id defaults to the logged-in employee if omitted.

        FULL FLOW (all writes must be sequential, not parallel):
        1. create_travel_expense → travel_expense_id
        2. If per-diem: get_reference_data(travel_per_diem_rates) → add_travel_per_diem
        3. If costs: get_reference_data(travel_cost_categories) + get_reference_data(travel_payment_types) → add_travel_expense_cost (one call per cost line, sequentially)
        4. transition_travel_expense(action="deliver") to submit for approval
        """
        return _service(ctx).create_travel_expense(payload)

    @agent.tool(retries=1)
    @log_tool
    def add_travel_expense_cost(ctx: RunContext[Any], payload: AddTravelExpenseCostInput) -> dict[str, Any]:
        """Attach a cost line to an existing travel expense.

        WHEN TO USE: After create_travel_expense (and after add_travel_per_diem if applicable).
        Call once per receipt or cost item. MUST be called sequentially — never in parallel.
        REQUIRED: travel_expense_id, cost_category_id, payment_type_id, date, amount_currency_inc_vat.
        currency_id defaults to 1 (NOK).

        PREREQUISITE LOOKUPS (do these before calling):
          get_reference_data(reference="travel_cost_categories") → cost_category_id
          get_reference_data(reference="travel_payment_types") → payment_type_id
        Common categories: Fly, Hotell, Buss, Drivstoff, Bomavgift (IDs vary per sandbox).
        Common payment type: "Privat utlegg" (employee paid out of pocket).

        VAT WARNING: In non-VAT-registered companies (VAT_NOT_REGISTERED), cost categories that
        carry VAT will cause transition_travel_expense(deliver) to fail with "selskapet er ikke
        registrert i Merverdiavgiftsregisteret". Check get_reference_data(vat_settings) first.
        If VAT_NOT_REGISTERED, pick cost categories with vatType 0 or 6 (no VAT).
        """
        return _service(ctx).add_travel_expense_cost(payload)

    @agent.tool(retries=1)
    @log_tool
    def transition_travel_expense(ctx: RunContext[Any], payload: TransitionTravelExpenseInput) -> dict[str, Any]:
        """Move a travel expense through its lifecycle.

        WHEN TO USE: After add_travel_expense_cost, to submit/approve the expense.
        REQUIRED: travel_expense_id, action.

        Valid actions:
        - "deliver" → submit for approval (most common after adding costs)
        - "approve" → approve the expense
        - "unapprove" → revert approval
        - "undeliver" → return to draft
        - "createVouchers" → create the accounting voucher from the approved expense
        """
        return _service(ctx).transition_travel_expense(payload)

    @agent.tool(retries=1)
    @log_tool
    def get_timesheet_activities(ctx: RunContext[Any], payload: GetTimesheetActivitiesInput) -> dict[str, Any]:
        """Fetch valid activity IDs for a project/date/employee combination.

        WHEN TO USE: Before create_timesheet_entry — you MUST use an activity_id returned by this tool.
        Do NOT invent activity IDs. They are project-specific and must come from this lookup.
        REQUIRED: project_id, date (YYYY-MM-DD). employee_id defaults to logged-in employee.

        If the project doesn't exist yet, create it first with create_project.
        Returns activities like "Fakturerbart arbeid", "Prosjektadministrasjon" with their IDs.
        """
        return _service(ctx).get_timesheet_activities(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_timesheet_entry(ctx: RunContext[Any], payload: CreateTimesheetEntryInput) -> dict[str, Any]:
        """Create a timesheet entry for hours worked on a project.

        WHEN TO USE: When the task asks to register/log timesheet hours.
        REQUIRED: project_id, activity_id (from get_timesheet_activities), date (YYYY-MM-DD), hours.
        employee_id defaults to the logged-in employee if omitted.

        MANDATORY ORDER:
        1. create_project (if the project doesn't exist yet)
        2. get_timesheet_activities(project_id, date) → pick an activity_id from the results
        3. create_timesheet_entry(project_id, activity_id, date, hours)

        Do NOT invent activity_id values — they MUST come from get_timesheet_activities.
        """
        return _service(ctx).create_timesheet_entry(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_contact(ctx: RunContext[Any], payload: CreateContactInput) -> dict[str, Any]:
        """Create a contact linked to a customer.

        WHEN TO USE: When the task asks to create/register a contact person for a customer.
        REQUIRED: first_name, last_name, customer_id.
        Optional: email.

        FLOW:
        1. create_customer (or find existing) → customer_id
        2. create_contact(first_name, last_name, customer_id)
        """
        return _service(ctx).create_contact(payload)

    @agent.tool(retries=1)
    @log_tool
    def update_employee(ctx: RunContext[Any], payload: UpdateEmployeeInput) -> dict[str, Any]:
        """Update an existing employee (versioned GET-then-PUT handled automatically).

        WHEN TO USE: When the task asks to modify employee details (name, email, phone, department, etc.).
        REQUIRED: employee_id. All other fields are optional — only changed fields need to be provided.

        CRITICAL: Tripletex requires dateOfBirth on every employee PUT. This tool auto-fetches
        the current dateOfBirth if you don't provide one. If the employee has no dateOfBirth set,
        you MUST provide date_of_birth in the payload.

        Do NOT use tripletex_put for employee updates — this tool handles version numbers
        and the dateOfBirth requirement automatically.
        """
        return _service(ctx).update_employee(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_department(ctx: RunContext[Any], payload: CreateDepartmentInput) -> dict[str, Any]:
        """Create a Tripletex department.

        WHEN TO USE: When the task asks to create a new department, or before creating an employee
        in a specific department that doesn't exist yet.
        REQUIRED: name. Optional: department_number.
        """
        return _service(ctx).create_department(payload)

    @agent.tool(retries=1)
    @log_tool
    def add_travel_mileage_allowance(ctx: RunContext[Any], payload: AddTravelMileageAllowanceInput) -> dict[str, Any]:
        """Add a mileage allowance to an existing travel expense.

        WHEN TO USE: When the task asks for mileage compensation (kjøregodtgjørelse) on a trip.
        REQUIRED: travel_expense_id, rate_type_id, rate_category_id, date, departure_location, destination, km.

        PREREQUISITE LOOKUPS (do these before calling):
          get_reference_data(reference="travel_mileage_rates", filters={"dateFrom": "2026-03-01", "dateTo": "2026-04-01"})
        Use rateType.id and rateCategory.id from the returned rate rows.
        Amount is calculated automatically from km * rate.
        """
        return _service(ctx).add_travel_mileage_allowance(payload)

    @agent.tool(retries=1)
    @log_tool
    def add_travel_per_diem(ctx: RunContext[Any], payload: AddTravelPerDiemInput) -> dict[str, Any]:
        """Add per-diem (diett/kostgodtgjørelse) compensation to an existing travel expense.

        WHEN TO USE: When the task asks for per-diem or daily allowance on a trip.
        REQUIRED: travel_expense_id, rate_type_id, rate_category_id, location, count.

        OVERNIGHT ACCOMMODATION (critical for multi-day trips):
        - For multi-day trips with overnight stays: set overnight_accommodation="HOTEL" (or "NONE" if no accommodation provided)
        - For day trips: omit overnight_accommodation (leave as None)
        - If you omit this on a multi-day trip, transition_travel_expense(deliver) will FAIL with "Sone må fylles ut"

        PREREQUISITE LOOKUPS (do these before calling):
          get_reference_data(reference="travel_per_diem_rates", filters={"dateFrom": "2026-01-01", "dateTo": "2026-12-31"})
        Each returned row has: id (= rate_type_id), rate (daily NOK), rateCategory.id (= rate_category_id), rateCategory.name.

        FIELD MAPPING — from the returned rate rows:
          rate_type_id    ← the row's "id" field (e.g. 21669)
          rate_category_id ← the row's "rateCategory"."id" field (e.g. 663)

        RATE SELECTION — pick the row whose "rate" best matches the prompted daily amount:
        - For multi-day trips with overnight stays: prefer rateCategory whose name contains "Overnatting".
        - For day trips: prefer rateCategory whose name contains "Dagsreise".
        - If the prompted rate does not exactly match any row, pick the closest available rate. Do not search further.
        - Do NOT query /travelExpense/rate or /travelExpense/rateType directly — only use get_reference_data.

        Amount is calculated automatically from count * rate.
        Deduction flags default to false — set to true if meals were provided.

        IMPORTANT: Call this BEFORE add_travel_expense_cost, and call all travel writes sequentially (not in parallel).
        """
        return _service(ctx).add_travel_per_diem(payload)

    @agent.tool(retries=1)
    @log_tool
    def run_salary_transaction(ctx: RunContext[Any], payload: RunSalaryTransactionInput) -> dict[str, Any]:
        """Run a salary/payroll transaction for one or more employees.

        WHEN TO USE: When the task asks to run payroll, register salary, or create a salary transaction.
        REQUIRED: date, month, year, payslips (one per employee).

        Each payslip needs: employee_id, date, month, year, specifications.
        Each specification needs: salary_type_id, rate, count, amount (rate * count).

        PREREQUISITE LOOKUPS:
          get_reference_data(reference="salary_types") → find salary type IDs (e.g. Fastlønn=2000, Bonus=2002)
          get_reference_data(reference="employees") → find employee IDs

        PREREQUISITES (employee must have):
        1. dateOfBirth set on employee
        2. An employment linked to a division:
           - Find or create division: tripletex_post("/division", body)
           - Create employment: tripletex_post("/employee/employment", body with employee.id, division.id, startDate)
        3. generate_tax_deduction defaults to true (auto-calculates tax)

        CRITICAL: After looking up salary_types and employee, call this tool IMMEDIATELY.
        Do NOT browse /salary/transaction, /ledger/voucher, or other read endpoints first.
        Unnecessary GETs waste API calls and can hit Tripletex 500 errors.
        """
        return _service(ctx).run_salary_transaction(payload)

    @agent.tool(retries=1)
    @log_tool
    def upload_attachment(ctx: RunContext[Any], payload: UploadAttachmentInput) -> dict[str, Any]:
        """Upload a file attachment to a voucher, travel expense, or salary transaction.

        WHEN TO USE: When the task asks to attach a document/receipt/file to an entity.
        REQUIRED: entity_type ("voucher", "travel_expense", or "salary_transaction"), entity_id.
        file_index defaults to 0 (the first attachment in the request).

        The file data comes from the attachments provided in the original /solve request.
        Supported formats: PDF, PNG, JPEG, TIFF.
        """
        files = [
            att
            for solve_file in ctx.deps.request.files
            if (
                att := type(
                    "_Att",
                    (),
                    {
                        "data": solve_file.decoded_bytes(),
                        "filename": solve_file.filename,
                        "mime_type": solve_file.mime_type,
                    },
                )()
            )
        ]
        return _service(ctx).upload_attachment(payload, files=files)

    @agent.tool(retries=1)
    @log_tool
    def create_bank_reconciliation(ctx: RunContext[Any], payload: CreateBankReconciliationInput) -> dict[str, Any]:
        """Create a bank reconciliation entry.

        WHEN TO USE: When the task asks to reconcile a bank account for an accounting period.
        REQUIRED: account_id (bank account, typically 1920), accounting_period_id.

        PREREQUISITE LOOKUPS:
          get_reference_data(reference="bank_accounts") → find bank account ID
          get_reference_data(reference="accounting_periods") → find period ID
        type defaults to "MANUAL". bank_account_closing_balance_currency defaults to 0.
        """
        return _service(ctx).create_bank_reconciliation(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_webhook_subscription(ctx: RunContext[Any], payload: CreateWebhookSubscriptionInput) -> dict[str, Any]:
        """Create a webhook subscription for Tripletex events.

        WHEN TO USE: When the task asks to set up a webhook or event notification.
        REQUIRED: event (e.g. "customer.create"), target_url (absolute HTTPS URL).

        PREREQUISITE LOOKUPS:
          get_reference_data(reference="events") → list available event keys
        Optional: fields (comma-separated), auth_header_name, auth_header_value.
        """
        return _service(ctx).create_webhook_subscription(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_accounting_dimension(ctx: RunContext[Any], payload: CreateAccountingDimensionInput) -> dict[str, Any]:
        """Create a custom accounting dimension with optional initial values.

        WHEN TO USE: When the task asks to create a free accounting dimension for cost tracking.
        REQUIRED: dimension_name (max 20 characters).
        Optional: description, values (list of dimension values to create immediately).

        To use the dimension on voucher postings, set freeAccountingDimension1/2/3 to the value ID
        via create_voucher or tripletex_post.

        FLOW:
        1. create_accounting_dimension(dimension_name, values=[...]) → dimension with dimensionIndex and value IDs
        2. create_voucher with postings that reference freeAccountingDimension1: {"id": value_id}
        """
        return _service(ctx).create_accounting_dimension(payload)

    @agent.tool(retries=1)
    @log_tool
    def get_reference_data(ctx: RunContext[Any], payload: ReferenceLookupInput) -> dict[str, Any]:
        """Fetch common reference/lookup data on demand.

        COMMON PATTERNS (no filters needed):
        - get_reference_data(reference="whoami") → employeeId, companyId
        - get_reference_data(reference="vat_settings") → company VAT registration status
        - get_reference_data(reference="vat_types") → all VAT codes with id, name, percentage
        - get_reference_data(reference="invoice_payment_types") → payment type IDs for register_invoice_payment
        - get_reference_data(reference="travel_cost_categories") → cost category IDs
        - get_reference_data(reference="travel_payment_types") → payment type IDs
        - get_reference_data(reference="currencies") → currency IDs (NOK=1, SEK=2, EUR=5, etc.)
        - get_reference_data(reference="salary_types") → salary type IDs (Fastlønn=2000, Bonus=2002)
        - get_reference_data(reference="departments") → departments
        - get_reference_data(reference="accounting_periods") → accounting period IDs
        - get_reference_data(reference="bank_accounts") → bank account IDs with account numbers
        - get_reference_data(reference="events") → available webhook event keys

        WITH FILTERS — valid filter keys per reference type:
        - accounts: number (exact integer), numberFrom/numberTo (integer range), isBankAccount (bool)
          Examples: filters={"number": 2400} or filters={"numberFrom": 8000, "numberTo": 8999}
          Do NOT use wildcards (%) or string patterns — number must be an exact integer or use range.
        - customers: organizationNumber (exact 9 digits), customerName (string), email (string)
        - suppliers: organizationNumber (exact 9 digits), supplierNumber (integer)
          Note: /supplier has NO name filter — use organizationNumber or supplierNumber.
        - employees: email (string), firstName (string), lastName (string), departmentId (integer)
        - products: number (string, exact match), name (string)
        - projects: name (string), number (string), customerId (integer)
        - travel_per_diem_rates: dateFrom/dateTo (YYYY-MM-DD) — recommended to include date filters
        - travel_mileage_rates: dateFrom/dateTo (YYYY-MM-DD) — recommended to include date filters

        RULES:
        - All filter values must be exact matches (integers or strings). No wildcards, patterns, or SQL syntax.
        - To find accounts in a range (e.g. 8000-8999), use numberFrom + numberTo, NOT number with a pattern.
        - For timesheet activities, use the dedicated get_timesheet_activities tool instead.
        """
        return _service(ctx).get_reference_data(payload)

    @agent.tool
    @log_tool
    def calculate_vat_split(ctx: RunContext[Any], payload: CalculateVatSplitInput) -> dict[str, Any]:
        """Split a gross amount (including VAT) into net and VAT components.

        WHEN TO USE: When booking supplier invoices where the amount is given INCLUDING VAT.
        Example: amount_including_vat=61150, vat_percentage=25 → net=48920.0, vat=12230.0

        Use the returned 'net' for the expense posting and 'gross' for the supplier payable posting.
        When the account has a locked vatType, use amountGross=gross (Tripletex auto-extracts VAT).
        This is a pure calculation — no Tripletex API call is made.
        """
        return TripletexService.calculate_vat_split(payload)

    @agent.tool(retries=1)
    @log_tool
    def create_employment(ctx: RunContext[Any], payload: CreateEmploymentInput) -> dict[str, Any]:
        """Create an employment record for an employee (sets their start date and links to a division).

        WHEN TO USE: After create_employee, when the task specifies a start date for the employee.
        Start dates live on the EMPLOYMENT record, not on the employee entity.
        REQUIRED: employee_id, start_date (YYYY-MM-DD).
        division_id is auto-resolved: uses existing division or creates one.
        Returns existing employment if one already exists (idempotent).

        FLOW: create_employee → create_employment(employee_id, start_date)
        """
        return _service(ctx).create_employment(payload)

    @agent.tool(retries=1)
    @log_tool
    async def find_api(ctx: RunContext[Any], payload: FindApiInput) -> dict[str, Any]:
        """Ask an API specialist sub-agent to find the right Tripletex endpoint.

        The sub-agent reads the actual Tripletex API specification and returns exact
        endpoint details with field names, types, pitfalls, and example bodies.

        WHEN TO USE:
        - No curated tool exists for the action you need (e.g. creating accounts, approving invoices, closing periods)
        - You are UNCERTAIN about the correct endpoint, field names, or required parameters
        - A curated tool failed and you need to understand why
        - The task involves an unusual or complex API operation
        - You want to verify how an endpoint works BEFORE making the call

        WHEN NOT TO USE:
        - You are confident a curated tool handles the exact operation correctly
        - For simple reference lookups (use get_reference_data instead)

        Input: Natural language description of what you need. Include error messages from failed attempts if retrying.
        Output: Structured endpoint guidance with method, path, required fields, pitfalls, and example body.

        Always call this BEFORE raw_api_call. The sub-agent finds the endpoint, you execute it.
        When in doubt, use this tool — it's cheaper than a failed API call.
        """
        return await _service(ctx).find_api(payload.need)

    @agent.tool(retries=1)
    @log_tool
    def raw_api_call(ctx: RunContext[Any], payload: RawApiCallInput) -> dict[str, Any]:
        """Execute a Tripletex API call based on find_api guidance.

        WHEN TO USE: After find_api has provided the endpoint, method, and required fields.
        This is the execution partner for find_api — find_api discovers, raw_api_call executes.

        NEVER call this without first calling find_api to confirm the endpoint shape.
        If this returns a 4xx error, call find_api again with the error message included,
        then retry once with the corrected payload. Max 2 total attempts per endpoint.

        Returns the full API response body.
        """
        return _service(ctx).raw_api_call(payload)
