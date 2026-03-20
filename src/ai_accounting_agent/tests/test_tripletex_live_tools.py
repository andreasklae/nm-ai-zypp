from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from ai_accounting_agent.schemas import (
    ConfigureProjectBillingInput,
    CreateCustomerInput,
    CreateInvoiceInput,
    CreateOrderInput,
    CreateProductInput,
    CreateProjectInput,
    CreateSupplierInput,
    CreateTimesheetEntryInput,
    CreateTravelExpenseInput,
    CreateVoucherInput,
    GetTimesheetActivitiesInput,
    OrderLineInput,
    ReferenceLookupInput,
    RegisterInvoicePaymentInput,
    ReverseVoucherInput,
    TravelDetailsInput,
    VoucherPostingInput,
)
from ai_accounting_agent.tripletex_client import TripletexClient
from ai_accounting_agent.tripletex_tools import ReferenceIndex, StepState, TripletexService


DEFAULT_ENV_PATH = Path("src/ai_accounting_agent/.env")


def _load_settings() -> dict[str, str]:
    settings: dict[str, str] = {}
    if DEFAULT_ENV_PATH.exists():
        for key, value in dotenv_values(DEFAULT_ENV_PATH).items():
            if value is not None:
                settings[key] = value
    for key in ("TRIPLETEX_API_URL", "TRIPLETEX_SESSION_TOKEN", "RUN_LIVE_TRIPLETEX_TESTS"):
        value = os.environ.get(key)
        if value:
            settings[key] = value
    return settings


SETTINGS = _load_settings()
RUN_LIVE_TRIPLETEX_TESTS = SETTINGS.get("RUN_LIVE_TRIPLETEX_TESTS", "").lower() in {"1", "true", "yes"}
TRIPLETEX_API_URL = SETTINGS.get("TRIPLETEX_API_URL", "")
TRIPLETEX_SESSION_TOKEN = SETTINGS.get("TRIPLETEX_SESSION_TOKEN", "")

pytestmark = [
    pytest.mark.live_tripletex,
    pytest.mark.skipif(not RUN_LIVE_TRIPLETEX_TESTS, reason="Set RUN_LIVE_TRIPLETEX_TESTS=1 to run these tests."),
    pytest.mark.skipif(
        not TRIPLETEX_API_URL or not TRIPLETEX_SESSION_TOKEN,
        reason="Tripletex sandbox credentials are missing.",
    ),
]


def _service() -> TripletexService:
    run_id = uuid4().hex
    client = TripletexClient(
        base_url=TRIPLETEX_API_URL,
        session_token=TRIPLETEX_SESSION_TOKEN,
        run_id=run_id,
    )
    return TripletexService(
        client=client,
        run_id=run_id,
        reference_index=ReferenceIndex.load_default(),
        step_state=StepState(has_announced_step=True),
    )


def _unique(label: str) -> str:
    return f"{label}-{uuid4().hex[:8]}"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def test_live_create_customer_and_product_tools() -> None:
    service = _service()

    customer = service.create_customer(
        CreateCustomerInput(name=_unique("Kunde"), email=f"{uuid4().hex[:8]}@example.org")
    )
    product = service.create_product(
        CreateProductInput(name=_unique("Produkt"), price_excluding_vat_currency=1000.0)
    )

    assert customer["id"]
    assert product["id"]


def test_live_create_supplier_and_voucher_tools() -> None:
    service = _service()
    supplier = service.create_supplier(CreateSupplierInput(name=_unique("Leverandor")))
    expense_account = service._account_by_number(4000)
    payable_account = service._account_by_number(2400)

    voucher = service.create_voucher(
        CreateVoucherInput(
            date=_today(),
            description=_unique("Voucher"),
            vendor_invoice_number=_unique("INV"),
            postings=[
                VoucherPostingInput(
                    account_id=int(expense_account["id"]),
                    date=_today(),
                    amount_gross=500.0,
                ),
                VoucherPostingInput(
                    account_id=int(payable_account["id"]),
                    date=_today(),
                    amount_gross=-500.0,
                    supplier_id=int(supplier["id"]),
                ),
            ],
        )
    )

    assert voucher["voucher"]["id"]
    assert voucher["verified_voucher"]["id"] == voucher["voucher"]["id"]


def test_live_create_project_order_invoice_and_payment_tools() -> None:
    service = _service()
    customer = service.create_customer(CreateCustomerInput(name=_unique("InvoiceCustomer")))
    product = service.create_product(
        CreateProductInput(name=_unique("InvoiceProduct"), price_excluding_vat_currency=1200.0)
    )
    project = service.create_project(CreateProjectInput(name=_unique("Prosjekt"), start_date=_today()))
    order = service.create_order(
        CreateOrderInput(
            customer_id=int(customer["id"]),
            order_date=_today(),
            delivery_date=(date.fromisoformat(_today()) + timedelta(days=7)).isoformat(),
            order_lines=[
                OrderLineInput(
                    product_id=int(product["id"]),
                    count=2,
                    unit_price_excluding_vat_currency=1200.0,
                    description=f"Project delivery for {project['name']}",
                )
            ],
        )
    )
    invoice_date = _today()
    due_date = (date.fromisoformat(invoice_date) + timedelta(days=14)).isoformat()
    invoice = service.create_invoice(
        CreateInvoiceInput(
            customer_id=int(customer["id"]),
            invoice_date=invoice_date,
            invoice_due_date=due_date,
            order_ids=[int(order["id"])],
        )
    )
    payment_types = service.get_reference_data(
        ReferenceLookupInput(reference="invoice_payment_types", filters={"count": 10})
    )
    payment_type_id = int(payment_types["values"][0]["id"])
    payment = service.register_invoice_payment(
        RegisterInvoicePaymentInput(
            invoice_id=int(invoice["invoice"]["id"]),
            payment_date=invoice_date,
            payment_type_id=payment_type_id,
            paid_amount=2400.0,
        )
    )

    assert project["id"]
    assert invoice["invoice"]["id"]
    assert payment["payment_result"] is not None


def test_live_configure_fixed_price_project_billing_and_invoice_tools() -> None:
    service = _service()
    customer = service.create_customer(CreateCustomerInput(name=_unique("BillingCustomer")))
    project = service.create_project(CreateProjectInput(name=_unique("BillingProject"), start_date=_today()))
    configured = service.configure_project_billing(
        ConfigureProjectBillingInput(
            project_id=int(project["id"]),
            customer_id=int(customer["id"]),
            is_fixed_price=True,
            fixed_price=122800.0,
        )
    )
    order = service.create_order(
        CreateOrderInput(
            customer_id=int(customer["id"]),
            project_id=int(project["id"]),
            order_date=_today(),
            delivery_date=(date.fromisoformat(_today()) + timedelta(days=7)).isoformat(),
            order_lines=[
                OrderLineInput(
                    count=1,
                    description=_unique("Milestone"),
                    unit_price_excluding_vat_currency=92100.0,
                )
            ],
        )
    )
    invoice = service.create_invoice(
        CreateInvoiceInput(
            customer_id=int(customer["id"]),
            invoice_date=_today(),
            invoice_due_date=(date.fromisoformat(_today()) + timedelta(days=14)).isoformat(),
            order_ids=[int(order["id"])],
        )
    )

    assert configured["verified_project"]["isFixedPrice"] is True
    assert float(configured["verified_project"]["fixedprice"]) == 122800.0
    assert invoice["invoice"]["id"]


def test_live_create_travel_expense_tool() -> None:
    service = _service()
    travel = service.create_travel_expense(
        CreateTravelExpenseInput(
            title=_unique("Reise"),
            travel_details=TravelDetailsInput(
                departure_date=_today(),
                return_date=_today(),
                departure_from="Oslo",
                destination="Bergen",
                purpose="Kundemote",
                departure_time="08:00",
                return_time="18:00",
            ),
        )
    )

    assert travel["id"]


def test_live_reverse_voucher_tool() -> None:
    service = _service()
    supplier = service.create_supplier(CreateSupplierInput(name=_unique("ReverseSupplier")))
    expense_account = service._account_by_number(4000)
    payable_account = service._account_by_number(2400)

    voucher = service.create_voucher(
        CreateVoucherInput(
            date=_today(),
            description=_unique("ReverseVoucher"),
            postings=[
                VoucherPostingInput(
                    account_id=int(expense_account["id"]),
                    date=_today(),
                    amount_gross=500.0,
                ),
                VoucherPostingInput(
                    account_id=int(payable_account["id"]),
                    date=_today(),
                    amount_gross=-500.0,
                    supplier_id=int(supplier["id"]),
                ),
            ],
        )
    )
    reversed_voucher = service.reverse_voucher(
        ReverseVoucherInput(voucher_id=int(voucher["voucher"]["id"]), date=_today())
    )

    assert reversed_voucher["id"]


def test_live_timesheet_activity_lookup_and_entry_tools() -> None:
    service = _service()
    project = service.create_project(CreateProjectInput(name=_unique("TimesheetProject"), start_date=_today()))
    activities = service.get_timesheet_activities(
        GetTimesheetActivitiesInput(project_id=int(project["id"]), date=_today())
    )
    activity_id = int(activities["values"][0]["id"])
    entry = service.create_timesheet_entry(
        CreateTimesheetEntryInput(
            project_id=int(project["id"]),
            activity_id=activity_id,
            date=_today(),
            hours=2.5,
            comment=_unique("Timesheet"),
        )
    )

    assert entry["id"]
