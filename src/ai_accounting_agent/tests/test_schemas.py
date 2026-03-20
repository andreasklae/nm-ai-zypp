from __future__ import annotations

from ai_accounting_agent.schemas import CreateCustomerInput, CreateSupplierInput, normalize_organization_number


def test_normalize_organization_number_strips_formatting_and_mva_suffix() -> None:
    assert normalize_organization_number("998 877 665 MVA") == "998877665"
    assert normalize_organization_number("998.877.665") == "998877665"


def test_normalize_organization_number_returns_none_for_invalid_values() -> None:
    assert normalize_organization_number("not-a-valid-org-number") is None
    assert normalize_organization_number("12345") is None


def test_customer_and_supplier_inputs_omit_invalid_organization_numbers() -> None:
    customer = CreateCustomerInput(name="Customer", organization_number="abc")
    supplier = CreateSupplierInput(name="Supplier", organization_number="org number unknown")

    assert customer.organization_number is None
    assert supplier.organization_number is None
