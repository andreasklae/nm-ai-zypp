from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import ToolDefinition

from ai_accounting_agent.attachment_extractor import AttachmentExtractionReport, ExtractedAttachment
from ai_accounting_agent.schemas import (
    ConfigureProjectBillingInput,
    CreateOrderInput,
    CreateSupplierInput,
    CreateTimesheetEntryInput,
    CreateVoucherInput,
    GetOpenPostsInput,
    GetTimesheetActivitiesInput,
    OrderLineInput,
    ReferenceLookupInput,
    RegisterSupplierInvoicePaymentInput,
    RunSalaryTransactionInput,
    SalaryPayslipInput,
    SalarySpecificationInput,
    SendInvoiceInput,
    VoucherPostingInput,
)
from ai_accounting_agent.tripletex_client import TripletexApiError
from ai_accounting_agent.tripletex_tools import (
    ReferenceIndex,
    StepState,
    TripletexService,
    prepare_tripletex_tools,
)


class FakeClient:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict[str, object] | None, str | None]] = []
        self.post_calls: list[tuple[str, dict[str, object] | None, dict[str, object] | None]] = []
        self.put_calls: list[tuple[str, dict[str, object] | None, dict[str, object] | None]] = []

    def get(self, path: str, params=None, cache_key=None):
        self.get_calls.append((path, params, cache_key))
        if path == "/ledger/account/10":
            return {"value": {"id": 10, "number": 1920, "ledgerType": "GENERAL"}}
        if path == "/ledger/account/20":
            return {"value": {"id": 20, "number": 2400, "ledgerType": "VENDOR"}}
        if path == "/ledger/account/30":
            return {"value": {"id": 30, "number": 6810, "ledgerType": "GENERAL"}}
        if path == "/ledger/voucher/321":
            return {"value": {"id": 321, "description": "Voucher"}}
        if path == "/department":
            return {"values": [{"id": 1, "name": "Avdeling", "departmentNumber": "1"}]}
        if path == "/token/session/>whoAmI":
            return {"value": {"employeeId": 77}}
        if path == "/activity/>forTimeSheet":
            return {
                "values": [
                    {"id": 556, "name": "Prosjektadministrasjon"},
                    {"id": 555, "name": "Fakturerbart arbeid"},
                ]
            }
        if path == "/supplier":
            return {"values": []}
        if path == "/supplierInvoice/777":
            return {"value": {"id": 777, "invoiceNumber": "SUP-777"}}
        if path == "/customer":
            return {"values": []}
        if path == "/invoice/555":
            return {"value": {"id": 555, "invoiceNumber": "10055", "amountOutstanding": 0.0}}
        if path == "/invoice":
            return {"values": [{"id": 555, "invoiceNumber": "10055", "amountOutstanding": 0.0}]}
        if path == "/ledger/posting/openPost":
            return {
                "values": [
                    {
                        "id": 901,
                        "date": "2026-03-31",
                        "amount": 1250.0,
                        "account": {"id": 10, "number": 1500, "name": "Kundefordringer"},
                        "voucher": {"id": 444},
                        "invoice": {"id": 555, "invoiceNumber": "10055"},
                        "customer": {"id": 12, "name": "Customer"},
                    }
                ]
            }
        if path == "/ledger/paymentTypeOut":
            return {"values": [{"id": 88, "name": "Bank"}]}
        if path == "/employee/42":
            return {"value": {"id": 42, "version": 3, "dateOfBirth": "1991-02-03"}}
        if path == "/employee/employment":
            return {"values": []}
        if path == "/company/divisions":
            return {"values": [{"id": 12, "name": "Main"}]}
        if path == "/salary/payslip":
            return {
                "values": [
                    {
                        "id": 7001,
                        "date": "2026-03-21",
                        "employee": {"id": 42, "firstName": "Rafael", "lastName": "Silva"},
                        "grossAmount": 49650.0,
                    }
                ]
            }
        if path == "/salary/compilation":
            return {"value": {"employee": {"id": 42}, "year": 2026}}
        if path == "/project/123":
            fields = (params or {}).get("fields")
            if fields == "*":
                return {
                    "value": {
                        "id": 123,
                        "version": 0,
                        "name": "Project",
                        "customer": None,
                        "projectManager": {"id": 77},
                        "isFixedPrice": False,
                        "fixedprice": 0,
                        "invoicingPlan": [],
                    }
                }
            return {
                "value": {
                    "id": 123,
                    "version": 2,
                    "name": "Project",
                    "customer": {"id": 44, "name": "Customer"},
                    "projectManager": {"id": 77},
                    "isFixedPrice": True,
                    "fixedprice": 122800.0,
                    "invoicingPlan": [],
                }
            }
        raise AssertionError(f"Unexpected GET path {path}")

    def post(self, path: str, params=None, json_body=None):
        self.post_calls.append((path, params, json_body))
        if path == "/ledger/voucher":
            return {"value": {"id": 321, "description": json_body["description"]}}
        if path == "/timesheet/entry":
            return {"value": {"id": 654, "employee": json_body["employee"]}}
        if path == "/employee/employment":
            return {"value": {"id": 501, "employee": json_body["employee"], "startDate": json_body["startDate"]}}
        if path == "/employee/employment/details":
            return {"value": {"id": 502, "employment": json_body["employment"]}}
        if path == "/supplier":
            return {
                "value": {
                    "id": 88,
                    "name": json_body["name"],
                    "organizationNumber": json_body.get("organizationNumber"),
                }
            }
        if path == "/order":
            return {"value": {"id": 999, "customer": json_body["customer"], "project": json_body.get("project")}}
        if path == "/invoice":
            return {"value": {"id": 555, "invoiceNumber": "10055"}}
        if path == "/supplierInvoice/777/:addPayment":
            return {"value": {"ok": True}}
        if path == "/salary/transaction":
            return {"value": {"id": 991, "status": "POSTED"}}
        raise AssertionError(f"Unexpected POST path {path}")

    def put(self, path: str, params=None, json_body=None):
        self.put_calls.append((path, params, json_body))
        if path == "/project/123":
            return {
                "value": {
                    "id": 123,
                    "version": 2,
                    "customer": json_body.get("customer"),
                    "projectManager": json_body.get("projectManager"),
                    "isFixedPrice": json_body.get("isFixedPrice"),
                    "fixedprice": json_body.get("fixedprice"),
                }
            }
        return {"value": {"ok": True}}

    def delete(self, path: str, params=None):
        return {"deleted": True}


def _service(has_step: bool = True, client: FakeClient | None = None) -> TripletexService:
    return TripletexService(
        client=client or FakeClient(),
        run_id="run-1",
        reference_index=ReferenceIndex(documents=[]),
        step_state=StepState(has_announced_step=has_step),
    )


def test_tripletex_tools_require_announce_step() -> None:
    service = _service(has_step=False)

    with pytest.raises(ModelRetry) as exc_info:
        service.tripletex_get(path="/customer")

    assert "announce_step" in str(exc_info.value)


def test_tripletex_get_rejects_order_collection_reads_without_date_filters_before_http_call() -> None:
    client = FakeClient()
    service = _service(client=client)

    with pytest.raises(ModelRetry) as exc_info:
        service.tripletex_get(path="/order?count=1")

    assert "orderDateFrom" in str(exc_info.value)
    assert client.get_calls == []


@pytest.mark.parametrize(
    ("path", "params", "expected_fragment"),
    [
        ("/invoice", {"count": 1}, "invoiceDateFrom"),
        ("/ledger/voucher", {"count": 1}, "dateFrom"),
        ("/ledger/posting", {"count": 1}, "dateFrom"),
    ],
)
def test_tripletex_get_rejects_other_risky_collection_reads_before_http_call(
    path: str,
    params: dict[str, object],
    expected_fragment: str,
) -> None:
    client = FakeClient()
    service = _service(client=client)

    with pytest.raises(ModelRetry) as exc_info:
        service.tripletex_get(path=path, params=params)

    assert expected_fragment in str(exc_info.value)
    assert client.get_calls == []


def test_tripletex_get_rejects_supplier_name_filter_before_http_call() -> None:
    client = FakeClient()
    service = _service(client=client)

    with pytest.raises(ModelRetry) as exc_info:
        service.tripletex_get(path="/supplier", params={"name": "Supplier"})

    assert "/supplier" in str(exc_info.value)
    assert client.get_calls == []


def test_create_voucher_validates_balancing_and_vendor_postings() -> None:
    service = _service()
    payload = CreateVoucherInput(
        date="2026-03-20",
        description="Voucher",
        postings=[
            VoucherPostingInput(account_id=10, date="2026-03-20", amount_gross=1000.0),
            VoucherPostingInput(account_id=20, date="2026-03-20", amount_gross=-1000.0),
        ],
    )

    with pytest.raises(ModelRetry) as exc_info:
        service.create_voucher(payload)

    assert "requires supplier" in str(exc_info.value)


def test_create_voucher_assigns_rows_and_verifies_result() -> None:
    service = _service()
    payload = CreateVoucherInput(
        date="2026-03-20",
        description="Voucher",
        postings=[
            VoucherPostingInput(account_id=10, date="2026-03-20", amount_gross=1000.0),
            VoucherPostingInput(
                account_id=20,
                date="2026-03-20",
                amount_gross=-1000.0,
                supplier_id=99,
            ),
        ],
    )

    result = service.create_voucher(payload)
    path, params, body = service.client.post_calls[0]

    assert path == "/ledger/voucher"
    assert params == {"sendToLedger": "true"}
    assert body["postings"][0]["row"] == 1
    assert body["postings"][1]["row"] == 2
    assert body["postings"][0]["amountGrossCurrency"] == 1000.0
    assert body["postings"][1]["amountGrossCurrency"] == -1000.0
    assert result["voucher"]["id"] == 321
    assert result["verified_voucher"]["id"] == 321


def test_create_voucher_includes_project_on_posting() -> None:
    client = FakeClient()
    service = _service(client=client)
    payload = CreateVoucherInput(
        date="2026-03-20",
        description="Project voucher",
        postings=[
            VoucherPostingInput(account_id=10, date="2026-03-20", amount_gross=1000.0, project_id=123),
            VoucherPostingInput(account_id=30, date="2026-03-20", amount_gross=-1000.0),
        ],
    )

    service.create_voucher(payload)
    _path, _params, body = client.post_calls[0]

    assert body["postings"][0]["project"] == {"id": 123}


def test_configure_project_billing_uses_versioned_project_put_flow() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.configure_project_billing(
        ConfigureProjectBillingInput(
            project_id=123,
            customer_id=44,
            project_manager_id=77,
            is_fixed_price=True,
            fixed_price=122800.0,
        )
    )

    get_path, get_params, _ = client.get_calls[0]
    assert get_path == "/project/123"
    assert get_params == {"fields": "*"}

    put_path, put_params, put_body = client.put_calls[0]
    assert put_path == "/project/123"
    assert put_params is None
    assert put_body == {
        "id": 123,
        "version": 0,
        "customer": {"id": 44},
        "projectManager": {"id": 77},
        "isFixedPrice": True,
        "fixedprice": 122800.0,
    }
    assert result["project"]["fixedprice"] == 122800.0
    assert result["verified_project"]["isFixedPrice"] is True


def test_create_order_supports_project_linked_freeform_lines() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.create_order(
        CreateOrderInput(
            customer_id=44,
            project_id=123,
            order_date="2026-03-20",
            delivery_date="2026-03-31",
            order_lines=[
                OrderLineInput(
                    count=1,
                    description="Pagamento por etapa",
                    unit_price_excluding_vat_currency=92100.0,
                )
            ],
        )
    )

    path, params, body = client.post_calls[-1]
    assert path == "/order"
    assert params is None
    assert body["customer"] == {"id": 44}
    assert body["project"] == {"id": 123}
    assert body["orderLines"] == [
        {
            "count": 1.0,
            "description": "Pagamento por etapa",
            "unitPriceExcludingVatCurrency": 92100.0,
        }
    ]
    assert result["project"] == {"id": 123}


def test_send_invoice_calls_send_endpoint_and_verifies_invoice() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.send_invoice(SendInvoiceInput(invoice_id=555))

    path, params, _body = client.put_calls[0]
    assert path == "/invoice/555/:send"
    assert params == {"sendType": "EMAIL"}
    assert result["invoice"]["id"] == 555


def test_register_supplier_invoice_payment_uses_outgoing_payment_type() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.register_supplier_invoice_payment(
        RegisterSupplierInvoicePaymentInput(
            supplier_invoice_id=777,
            payment_type_id=88,
            payment_date="2026-03-21",
            amount=500.0,
            partial_payment=True,
        )
    )

    path, params, _body = client.post_calls[-1]
    assert path == "/supplierInvoice/777/:addPayment"
    assert params == {
        "paymentType": 88,
        "paymentDate": "2026-03-21",
        "partialPayment": True,
        "amount": 500.0,
    }
    assert result["supplier_invoice"]["id"] == 777


def test_get_open_posts_returns_normalized_rows() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.get_open_posts(GetOpenPostsInput(date="2026-03-31", customer_id=12))

    path, params, _cache_key = client.get_calls[-1]
    assert path == "/ledger/posting/openPost"
    assert params == {"date": "2026-03-31", "customerId": 12}
    assert result["normalized"][0]["invoice_id"] == 555
    assert result["normalized"][0]["customer_name"] == "Customer"


def test_get_reference_data_requires_activity_filters_with_retry_message() -> None:
    service = _service()

    with pytest.raises(ModelRetry) as exc_info:
        service.get_reference_data(ReferenceLookupInput(reference="activities_for_timesheet"))

    assert "projectId" in str(exc_info.value)
    assert "date" in str(exc_info.value)


def test_get_timesheet_activities_defaults_employee_to_whoami() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.get_timesheet_activities(GetTimesheetActivitiesInput(project_id=456, date="2026-03-20"))

    assert result["values"][0]["id"] == 555
    path, params, _cache_key = client.get_calls[-1]
    assert path == "/activity/>forTimeSheet"
    assert params == {"projectId": 456, "employeeId": 77, "date": "2026-03-20"}


def test_create_timesheet_entry_defaults_employee_to_whoami() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.create_timesheet_entry(
        CreateTimesheetEntryInput(project_id=456, activity_id=555, date="2026-03-20", hours=2.5)
    )

    assert result["id"] == 654
    path, _params, body = client.post_calls[-1]
    assert path == "/timesheet/entry"
    assert body["employee"] == {"id": 77}


def test_prepare_tripletex_tools_hides_tripletex_tools_until_step_announced() -> None:
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            run_id="run-1",
            request=SimpleNamespace(files=[]),
            step_state=StepState(has_announced_step=False),
        )
    )
    tool_defs = [
        ToolDefinition(name="announce_step"),
        ToolDefinition(name="search_tripletex_reference"),
        ToolDefinition(name="get_today_date"),
        ToolDefinition(name="create_supplier"),
    ]

    prepared = asyncio.run(prepare_tripletex_tools(ctx, tool_defs))

    assert [tool.name for tool in prepared] == ["announce_step", "search_tripletex_reference", "get_today_date"]


def test_prepare_tripletex_tools_allows_attachment_extractor_before_step(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        record = {"event": event, "severity": severity, **payload}
        events.append(record)
        return record

    monkeypatch.setattr("ai_accounting_agent.tripletex_tools.log_event", fake_log_event)
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            run_id="run-attach",
            request=SimpleNamespace(files=[SimpleNamespace(filename="invoice.pdf")]),
            step_state=StepState(has_announced_step=False),
        )
    )
    tool_defs = [
        ToolDefinition(name="announce_step"),
        ToolDefinition(name="get_today_date"),
        ToolDefinition(name="extract_attachment_data"),
        ToolDefinition(name="create_supplier"),
    ]

    prepared = asyncio.run(prepare_tripletex_tools(ctx, tool_defs))

    assert [tool.name for tool in prepared] == ["announce_step", "get_today_date", "extract_attachment_data"]
    assert events[-1]["event"] == "attachment_extractor_availability"
    assert events[-1]["extractor_available"] is True


def test_extract_attachment_data_service_delegates_to_attachment_extractor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_extract_attachments(*, run_id: str, task_prompt: str, attachments, model="unused"):
        captured["run_id"] = run_id
        captured["task_prompt"] = task_prompt
        captured["attachments"] = attachments
        return AttachmentExtractionReport(
            summary="Extracted 1 attachment.",
            extractor_model="gemini-2.5-flash",
            attachments=[
                ExtractedAttachment(
                    filename="invoice.pdf",
                    mime_type="application/pdf",
                    extraction_method="subagent",
                    document_type="invoice",
                    summary="Invoice extracted.",
                    extracted_text="Invoice Number: INV-42",
                )
            ],
        )

    monkeypatch.setattr("ai_accounting_agent.tripletex_tools.extract_attachments", fake_extract_attachments)

    service = _service()
    result = asyncio.run(
        service.extract_attachment_data(
            task_prompt="Read the attachment.",
            files=[SimpleNamespace(filename="invoice.pdf", mime_type="application/pdf", data=b"%PDF-1.4")],
        )
    )

    assert captured["run_id"] == "run-1"
    assert captured["task_prompt"] == "Read the attachment."
    attachments = captured["attachments"]
    assert len(attachments) == 1
    assert attachments[0].filename == "invoice.pdf"
    assert result["attachments"][0]["extraction_method"] == "subagent"


def test_create_supplier_wraps_fixable_tripletex_errors_as_model_retry() -> None:
    class ErroringClient(FakeClient):
        def post(self, path: str, params=None, json_body=None):
            if path == "/supplier":
                raise TripletexApiError(
                    message="Tripletex API returned 422 for POST /supplier",
                    status_code=422,
                    response_body={
                        "values": [
                            {
                                "field": "organizationNumber",
                                "message": "Organisasjonsnummeret må ha 9 siffer og kan ikke inneholde skilletegn.",
                            }
                        ]
                    },
                    response_headers={},
                    request_id="req-1",
                )
            return super().post(path, params=params, json_body=json_body)

    service = _service(client=ErroringClient())

    with pytest.raises(ModelRetry) as exc_info:
        service.create_supplier(CreateSupplierInput(name="Supplier", organization_number="998 877 665 MVA"))

    assert "998877665" in str(exc_info.value)


def test_tripletex_auth_failures_are_not_retried(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        record = {"event": event, "severity": severity, **payload}
        events.append(record)
        return record

    class ErroringClient(FakeClient):
        def post(self, path: str, params=None, json_body=None):
            if path == "/supplier":
                raise TripletexApiError(
                    message="Tripletex API returned 403 for POST /supplier",
                    status_code=403,
                    response_body={"error": "Invalid or expired proxy token."},
                    response_headers={},
                    request_id="req-auth",
                )
            return super().post(path, params=params, json_body=json_body)

    monkeypatch.setattr("ai_accounting_agent.tripletex_tools.log_event", fake_log_event)
    service = _service(client=ErroringClient())

    with pytest.raises(TripletexApiError):
        service.create_supplier(CreateSupplierInput(name="Supplier", organization_number="998877665"))

    retry_event = next(event for event in events if event["event"] == "tripletex_retry_decision")
    assert retry_event["retryable"] is False
    assert retry_event["retry_classification"] == "auth_failure"


def test_get_open_posts_wraps_invalid_endpoint_usage_as_model_retry() -> None:
    class ErroringClient(FakeClient):
        def get(self, path: str, params=None, cache_key=None):
            if path == "/ledger/posting/openPost":
                raise TripletexApiError(
                    message="Tripletex API returned 422 for GET /ledger/posting/openPost",
                    status_code=422,
                    response_body={"values": [{"field": "openPostings", "message": "Dato er ugyldig"}]},
                    response_headers={},
                    request_id="req-open-post",
                )
            return super().get(path, params=params, cache_key=cache_key)

    service = _service(client=ErroringClient())

    with pytest.raises(ModelRetry) as exc_info:
        service.get_open_posts(GetOpenPostsInput(date="2026-03-31"))

    assert "/ledger/posting/openPost" in str(exc_info.value)


def test_duplicate_entity_errors_are_retryable_with_reuse_hint() -> None:
    class ErroringClient(FakeClient):
        def post(self, path: str, params=None, json_body=None):
            if path == "/supplier":
                raise TripletexApiError(
                    message="Tripletex API returned 422 for POST /supplier",
                    status_code=422,
                    response_body={"values": [{"field": "email", "message": "Email already exists"}]},
                    response_headers={},
                    request_id="req-dup",
                )
            return super().post(path, params=params, json_body=json_body)

    service = _service(client=ErroringClient())

    with pytest.raises(ModelRetry) as exc_info:
        service.create_supplier(CreateSupplierInput(name="Supplier", email="supplier@example.org"))

    assert "already exists" in str(exc_info.value)
    assert "reuse its ID" in str(exc_info.value)


def test_vat_locked_errors_are_retryable_with_account_guidance() -> None:
    class ErroringClient(FakeClient):
        def post(self, path: str, params=None, json_body=None):
            if path == "/ledger/voucher":
                raise TripletexApiError(
                    message="Tripletex API returned 422 for POST /ledger/voucher",
                    status_code=422,
                    response_body={
                        "values": [{"field": "postings[0].vatType", "message": "Kontoen er låst til MVA-kode 3"}]
                    },
                    response_headers={},
                    request_id="req-vat",
                )
            return super().post(path, params=params, json_body=json_body)

    service = _service(client=ErroringClient())

    with pytest.raises(ModelRetry) as exc_info:
        service.create_voucher(
            CreateVoucherInput(
                date="2026-03-20",
                description="Voucher",
                postings=[
                    VoucherPostingInput(account_id=10, date="2026-03-20", amount_gross=1000.0),
                    VoucherPostingInput(account_id=30, date="2026-03-20", amount_gross=-1000.0),
                ],
            )
        )

    assert "locked" in str(exc_info.value).lower() or "låst" in str(exc_info.value).lower()


def test_not_found_errors_are_retryable_with_relookup_hint() -> None:
    class ErroringClient(FakeClient):
        def put(self, path: str, params=None, json_body=None):
            if path == "/invoice/555/:send":
                raise TripletexApiError(
                    message="Tripletex API returned 404 for PUT /invoice/555/:send",
                    status_code=404,
                    response_body={"message": "Invoice does not exist"},
                    response_headers={},
                    request_id="req-404",
                )
            return super().put(path, params=params, json_body=json_body)

    service = _service(client=ErroringClient())

    with pytest.raises(ModelRetry) as exc_info:
        service.send_invoice(SendInvoiceInput(invoice_id=555))

    assert "Re-look up the entity" in str(exc_info.value)


def test_get_reference_data_supports_outgoing_payment_types() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.get_reference_data(ReferenceLookupInput(reference="outgoing_payment_types"))

    assert result["values"][0]["id"] == 88
    path, params, _cache_key = client.get_calls[-1]
    assert path == "/ledger/paymentTypeOut"
    assert params == {"count": 20}


def test_run_salary_transaction_verifies_payslips_and_compilation() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.run_salary_transaction(
        RunSalaryTransactionInput(
            date="2026-03-21",
            month=3,
            year=2026,
            payslips=[
                SalaryPayslipInput(
                    employee_id=42,
                    date="2026-03-21",
                    month=3,
                    year=2026,
                    specifications=[
                        SalarySpecificationInput(
                            salary_type_id=2000,
                            rate=41150.0,
                            count=1,
                            amount=41150.0,
                        ),
                        SalarySpecificationInput(
                            salary_type_id=2002,
                            rate=8500.0,
                            count=1,
                            amount=8500.0,
                        ),
                    ],
                )
            ],
        )
    )

    assert result["transaction"]["id"] == 991
    assert result["verified_payslips"][0]["id"] == 7001
    assert result["salary_compilations"][0]["employee"]["id"] == 42


def test_reference_search_prioritizes_fixed_price_project_billing_section() -> None:
    index = ReferenceIndex(
        documents=[
            (
                "tripletex_api.md",
                "## Suggested Agent Playbook\n| Customer invoice | GET /customer, GET /product, GET /order |\n",
            ),
            (
                "tripletex_api.md",
                "## Project Billing / Fixed Price\nUse customer, projectManager, isFixedPrice, fixedprice, and invoicingPlan. "
                "For stage billing, create a project-linked order and then create the invoice.\n",
            ),
        ]
    )

    results = index.search("project fixed price milestone invoice")

    assert results[0]["heading"] == "Project Billing / Fixed Price"
