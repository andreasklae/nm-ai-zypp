from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import ToolDefinition

from ai_accounting_agent.schemas import (
    ConfigureProjectBillingInput,
    CreateOrderInput,
    CreateSupplierInput,
    CreateTimesheetEntryInput,
    CreateVoucherInput,
    GetTimesheetActivitiesInput,
    OrderLineInput,
    ReferenceLookupInput,
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
        if path == "/ledger/voucher/321":
            return {"value": {"id": 321, "description": "Voucher"}}
        if path == "/department":
            return {"values": [{"id": 1, "name": "Avdeling", "departmentNumber": "1"}]}
        if path == "/token/session/>whoAmI":
            return {"value": {"employeeId": 77}}
        if path == "/activity/>forTimeSheet":
            return {"values": [{"id": 555, "name": "Fakturerbart arbeid"}]}
        if path == "/supplier":
            return {"values": []}
        if path == "/customer":
            return {"values": []}
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
        if path == "/supplier":
            return {"value": {"id": 88, "name": json_body["name"], "organizationNumber": json_body.get("organizationNumber")}}
        if path == "/order":
            return {"value": {"id": 999, "customer": json_body["customer"], "project": json_body.get("project")}}
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


def test_get_reference_data_requires_activity_filters_with_retry_message() -> None:
    service = _service()

    with pytest.raises(ModelRetry) as exc_info:
        service.get_reference_data(ReferenceLookupInput(reference="activities_for_timesheet"))

    assert "projectId" in str(exc_info.value)
    assert "date" in str(exc_info.value)


def test_get_timesheet_activities_defaults_employee_to_whoami() -> None:
    client = FakeClient()
    service = _service(client=client)

    result = service.get_timesheet_activities(
        GetTimesheetActivitiesInput(project_id=456, date="2026-03-20")
    )

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
    ctx = SimpleNamespace(deps=SimpleNamespace(step_state=StepState(has_announced_step=False)))
    tool_defs = [
        ToolDefinition(name="announce_step"),
        ToolDefinition(name="search_tripletex_reference"),
        ToolDefinition(name="create_supplier"),
    ]

    prepared = asyncio.run(prepare_tripletex_tools(ctx, tool_defs))

    assert [tool.name for tool in prepared] == ["announce_step", "search_tripletex_reference"]


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
        service.create_supplier(
            CreateSupplierInput(name="Supplier", organization_number="998 877 665 MVA")
        )

    assert "998877665" in str(exc_info.value)


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
