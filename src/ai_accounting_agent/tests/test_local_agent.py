"""Local integration tests that run execute_agent against the Tripletex sandbox.

These tests verify that the agent picks correct tool chains for representative
task types after the tool-description improvements. They call the real Gemini
model and the real Tripletex sandbox, so they are slow and gated behind
RUN_LOCAL_AGENT_TESTS=1.

Usage:
    RUN_LOCAL_AGENT_TESTS=1 uv run pytest src/ai_accounting_agent/tests/test_local_agent.py -v -s
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from ai_accounting_agent.agent import AgentExecutionResult, execute_agent
from ai_accounting_agent.schemas import SolveRequest, TripletexCredentials

DEFAULT_ENV_PATH = Path("src/ai_accounting_agent/.env")


def _load_env() -> dict[str, str]:
    settings: dict[str, str] = {}
    if DEFAULT_ENV_PATH.exists():
        for key, value in dotenv_values(DEFAULT_ENV_PATH).items():
            if value is not None:
                settings[key] = value
    for key in ("TRIPLETEX_API_URL", "TRIPLETEX_SESSION_TOKEN", "GEMINI_API_KEY", "RUN_LOCAL_AGENT_TESTS"):
        value = os.environ.get(key)
        if value:
            settings[key] = value
    return settings


ENV = _load_env()
RUN = ENV.get("RUN_LOCAL_AGENT_TESTS", "").lower() in {"1", "true", "yes"}
TRIPLETEX_API_URL = ENV.get("TRIPLETEX_API_URL", "")
TRIPLETEX_SESSION_TOKEN = ENV.get("TRIPLETEX_SESSION_TOKEN", "")

pytestmark = [
    pytest.mark.local_agent,
    pytest.mark.skipif(not RUN, reason="Set RUN_LOCAL_AGENT_TESTS=1 to run."),
    pytest.mark.skipif(
        not TRIPLETEX_API_URL or not TRIPLETEX_SESSION_TOKEN,
        reason="Tripletex credentials missing.",
    ),
]


def _run_agent(prompt: str) -> AgentExecutionResult:
    """Run the agent with a prompt against the sandbox."""
    import asyncio

    request = SolveRequest(
        prompt=prompt,
        files=[],
        tripletex_credentials=TripletexCredentials(
            base_url=TRIPLETEX_API_URL,
            session_token=TRIPLETEX_SESSION_TOKEN,
        ),
    )
    run_id = f"local-test-{uuid4().hex[:8]}"
    return asyncio.run(execute_agent(request=request, attachments=[], run_id=run_id))


def _extract_tool_sequence(messages: list[Any]) -> list[str]:
    """Extract the sequence of tool names called from agent messages.

    Only counts tool-call parts (not tool-return parts) to avoid duplicates.
    """
    tools: list[str] = []
    for msg in messages:
        if hasattr(msg, "parts"):
            for part in msg.parts:
                if hasattr(part, "part_kind") and part.part_kind == "tool-call":
                    tools.append(part.tool_name)
    return tools


def _print_result(label: str, result: AgentExecutionResult, tool_seq: list[str]) -> None:
    """Print a summary for manual inspection."""
    print(f"\n{'='*60}")
    print(f"SCENARIO: {label}")
    print(f"Tool sequence: {tool_seq}")
    print(f"Output: {result.output[:200]}")
    print(f"{'='*60}\n")


class TestEmployeeCreation:
    """Employee creation should use create_employee + grant_employee_privileges."""

    def test_employee_admin(self) -> None:
        token = uuid4().hex[:8]
        prompt = (
            f"Opprett en ansatt med fornavn Test og etternavn Agent {token}. "
            f"E-post: test-agent-{token}@example.org. "
            f"Personen skal være kontoadministrator. trace_token={token}"
        )
        result = _run_agent(prompt)
        tools = _extract_tool_sequence(result.messages)
        _print_result("employee_admin", result, tools)

        assert "announce_step" in tools, f"announce_step missing. Tools: {tools}"
        assert "create_employee" in tools, f"create_employee missing. Tools: {tools}"
        assert "grant_employee_privileges" in tools, f"grant_employee_privileges missing. Tools: {tools}"
        # Should NOT use generic REST for this
        assert "tripletex_post" not in tools, f"Should not use generic tripletex_post. Tools: {tools}"


class TestCustomerCreation:
    """Simple customer creation — single tool after announce_step."""

    def test_customer(self) -> None:
        token = uuid4().hex[:8]
        prompt = (
            f"Create a customer named Test Agent Customer {token} "
            f"with email test-{token}@example.org. Do only what is necessary. trace_token={token}"
        )
        result = _run_agent(prompt)
        tools = _extract_tool_sequence(result.messages)
        _print_result("customer_creation", result, tools)

        assert "announce_step" in tools, f"announce_step missing. Tools: {tools}"
        assert "create_customer" in tools, f"create_customer missing. Tools: {tools}"
        # Should NOT use search_tripletex_reference for something this basic
        assert "search_tripletex_reference" not in tools, f"Unnecessary search. Tools: {tools}"


class TestSupplierVoucher:
    """Supplier invoice booking should use create_supplier + create_voucher."""

    def test_supplier_voucher(self) -> None:
        token = uuid4().hex[:8]
        prompt = (
            f"Opprett en leverandør med navn Test Supplier {token} og bokfør en leverandørfaktura "
            f"på 5000 NOK for kontorrekvisita. Fakturanummer er INV-{token}. "
            f"Bruk {token} i beskrivelsen. trace_token={token}"
        )
        result = _run_agent(prompt)
        tools = _extract_tool_sequence(result.messages)
        _print_result("supplier_voucher", result, tools)

        assert "announce_step" in tools, f"announce_step missing. Tools: {tools}"
        assert "create_supplier" in tools, f"create_supplier missing. Tools: {tools}"
        assert "create_voucher" in tools, f"create_voucher missing. Tools: {tools}"
        # Should NOT try to POST /supplierInvoice via generic tools
        assert "tripletex_post" not in tools, f"Should not use generic tripletex_post. Tools: {tools}"


class TestOrderInvoiceFlow:
    """Invoice flow should follow create_customer → create_order → create_invoice."""

    def test_invoice_flow(self) -> None:
        token = uuid4().hex[:8]
        prompt = (
            f"Opprett kunde Test Invoice Customer {token}, et produkt Test Product {token} "
            f"med pris 1000 NOK, en ordre og en faktura. Ikke registrer betaling. trace_token={token}"
        )
        result = _run_agent(prompt)
        tools = _extract_tool_sequence(result.messages)
        _print_result("order_invoice_flow", result, tools)

        assert "announce_step" in tools, f"announce_step missing. Tools: {tools}"
        assert "create_customer" in tools, f"create_customer missing. Tools: {tools}"
        assert "create_order" in tools, f"create_order missing. Tools: {tools}"
        assert "create_invoice" in tools, f"create_invoice missing. Tools: {tools}"
        # Should NOT register payment
        assert "register_invoice_payment" not in tools, f"Should not register payment. Tools: {tools}"


class TestTimesheetFlow:
    """Timesheet should use create_project → get_timesheet_activities → create_timesheet_entry."""

    def test_timesheet(self) -> None:
        token = uuid4().hex[:8]
        prompt = (
            f"Opprett prosjekt Test Timesheet {token}, finn en gyldig aktivitet, "
            f"og før 3 timer i dag. trace_token={token}"
        )
        result = _run_agent(prompt)
        tools = _extract_tool_sequence(result.messages)
        _print_result("timesheet_flow", result, tools)

        assert "announce_step" in tools, f"announce_step missing. Tools: {tools}"
        assert "create_project" in tools, f"create_project missing. Tools: {tools}"
        assert "get_timesheet_activities" in tools, f"get_timesheet_activities missing. Tools: {tools}"
        assert "create_timesheet_entry" in tools, f"create_timesheet_entry missing. Tools: {tools}"


class TestFixedPriceProjectBilling:
    """Portuguese evaluator query: fixed-price project + milestone invoice.

    Previously failed because the agent searched 5x, put params in path, and probed /order?count=1.
    """

    def test_fixed_price_billing_pt(self) -> None:
        token = uuid4().hex[:8]
        prompt = (
            f'Defina um preço fixo de 122800 NOK no projeto "Segurança de dados {token}" '
            f"para Luz do Sol {token} Lda (org. nº 861443299). "
            f"O gestor de projeto é o utilizador atual. "
            f"Fature ao cliente 75 % do preço fixo como pagamento por etapa. "
            f"trace_token={token}"
        )
        result = _run_agent(prompt)
        tools = _extract_tool_sequence(result.messages)
        _print_result("fixed_price_billing_pt", result, tools)

        assert "announce_step" in tools, f"announce_step missing. Tools: {tools}"
        assert "create_customer" in tools, f"create_customer missing. Tools: {tools}"
        assert "create_project" in tools, f"create_project missing. Tools: {tools}"
        assert "configure_project_billing" in tools, f"configure_project_billing missing. Tools: {tools}"
        assert "create_order" in tools, f"create_order missing. Tools: {tools}"
        assert "create_invoice" in tools, f"create_invoice missing. Tools: {tools}"
        # Should NOT probe /order schema or use excessive search loops
        assert tools.count("search_tripletex_reference") <= 1, (
            f"Too many search_tripletex_reference calls ({tools.count('search_tripletex_reference')}). Tools: {tools}"
        )


class TestCancelPayment:
    """French evaluator query: cancel a returned bank payment via credit note.

    Previously failed because agent didn't know the approach and tried GET /invoice without date filters.
    This test creates the full prerequisite chain (customer→order→invoice→payment) then asks to cancel.
    """

    def test_cancel_payment_fr(self) -> None:
        token = uuid4().hex[:8]
        # The prompt mirrors the evaluator: payment returned by bank, cancel it
        prompt = (
            f"Le paiement de Test Client {token} pour la facture a été retourné par la banque. "
            f"Créez d'abord un client Test Client {token}, un produit Formation {token} à 19650 NOK, "
            f"une commande, une facture, puis enregistrez un paiement. "
            f"Ensuite, annulez le paiement en créant une note de crédit. "
            f"trace_token={token}"
        )
        result = _run_agent(prompt)
        tools = _extract_tool_sequence(result.messages)
        _print_result("cancel_payment_fr", result, tools)

        assert "announce_step" in tools, f"announce_step missing. Tools: {tools}"
        assert "create_credit_note" in tools, f"create_credit_note missing. Tools: {tools}"
        # Should NOT crash with errors — verify it completed
        assert result.output, f"Agent produced no output. Tools: {tools}"
