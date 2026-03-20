from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from ai_accounting_agent.tests.evaluator_scenarios import SCENARIOS
from ai_accounting_agent.tests.live_api_support import (
    DEFAULT_ENV_PATH,
    REALISTIC_SCENARIO_DIR,
    ScenarioSpec,
    build_request_body,
    cleanup_realistic_scenarios,
    fetch_cloud_logs,
    load_live_api_settings,
    send_solve_request,
    summarize_observed_behavior,
    write_scenario_artifacts,
)
from ai_accounting_agent.tripletex_client import TripletexClient


def _run_live_flag() -> bool:
    env_settings = {}
    if DEFAULT_ENV_PATH.exists():
        env_settings = {key: value for key, value in dotenv_values(DEFAULT_ENV_PATH).items() if value is not None}
    value = os.environ.get("RUN_LIVE_API_TESTS", env_settings.get("RUN_LIVE_API_TESTS", ""))
    return value.lower() in {"1", "true", "yes"}


SETTINGS = load_live_api_settings()
RUN_LIVE_API_TESTS = _run_live_flag()

pytestmark = [
    pytest.mark.live_api,
    pytest.mark.skipif(
        not RUN_LIVE_API_TESTS,
        reason="Set RUN_LIVE_API_TESTS=1 to run evaluator-like Cloud Run integration tests.",
    ),
    pytest.mark.skipif(
        not SETTINGS.api_url or not SETTINGS.tripletex_api_url or not SETTINGS.tripletex_session_token,
        reason="Live API URL or Tripletex sandbox credentials are missing.",
    ),
]


@pytest.fixture(scope="session", autouse=True)
def _fresh_realistic_artifact_root() -> None:
    cleanup_realistic_scenarios()


def _verification_client() -> TripletexClient:
    return TripletexClient(
        base_url=SETTINGS.tripletex_api_url,
        session_token=SETTINGS.tripletex_session_token,
        run_id="evaluator-suite-verifier",
    )


def _list_values(client: TripletexClient, path: str, *, fields: str, count: int = 200, **params: object) -> list[dict[str, object]]:
    payload = client.get(path, params={"fields": fields, "count": count, **params})
    if isinstance(payload, dict) and "values" in payload:
        return payload["values"]
    if isinstance(payload, list):
        return payload
    return []


def _find_match(items: list[dict[str, object]], key: str, expected: str) -> bool:
    expected_normalized = expected.lower()
    for item in items:
        value = str(item.get(key, "")).lower()
        if expected_normalized in value:
            return True
    return False


def _outcome_checks(scenario: ScenarioSpec, token: str, observed_summary: dict[str, object]) -> dict[str, dict[str, object]]:
    client = _verification_client()
    checks: dict[str, dict[str, object]] = {}

    def add(name: str, passed: bool, details: str) -> None:
        checks[name] = {"passed": passed, "details": details}

    tool_sequence = observed_summary.get("tool_sequence", [])
    tripletex_errors = observed_summary.get("tripletex_http_error_statuses", [])
    add(
        "no_tripletex_http_errors",
        not tripletex_errors,
        f"Observed Tripletex error statuses: {tripletex_errors}",
    )

    if scenario.scenario_id == "read_only_proxy_verification":
        add(
            "no_tripletex_writes",
            observed_summary.get("tripletex_write_count") == 0,
            f"tripletex_write_count={observed_summary.get('tripletex_write_count')}",
        )
    elif scenario.scenario_id == "attachment_understanding_no_write":
        add(
            "no_tripletex_writes",
            observed_summary.get("tripletex_write_count") == 0,
            f"tripletex_write_count={observed_summary.get('tripletex_write_count')}",
        )
    elif scenario.scenario_id == "employee_admin_creation":
        employees = _list_values(client, "/employee", fields="id,firstName,lastName,email", count=200)
        add(
            "employee_created",
            _find_match(employees, "email", f"eval-employee-{token}@example.org"),
            "Expected employee email present in /employee listing.",
        )
    elif scenario.scenario_id == "customer_creation_en":
        customers = _list_values(client, "/customer", fields="id,name,email", count=200)
        add(
            "customer_created",
            _find_match(customers, "email", f"eval-customer-{token}@example.org"),
            "Expected customer email present in /customer listing.",
        )
    elif scenario.scenario_id == "supplier_voucher_booking":
        suppliers = _list_values(client, "/supplier", fields="id,name,email", count=200)
        add(
            "supplier_created",
            _find_match(suppliers, "name", f"Eval Supplier {token}"),
            "Expected supplier name present in /supplier listing.",
        )
    elif scenario.scenario_id == "product_creation_de":
        products = _list_values(client, "/product", fields="id,name,number", count=200)
        add(
            "product_created",
            _find_match(products, "name", f"Eval Produkt {token}"),
            "Expected product name present in /product listing.",
        )
    elif scenario.scenario_id == "project_creation_es":
        projects = _list_values(client, "/project", fields="id,name,number,startDate", count=200)
        add(
            "project_created",
            _find_match(projects, "name", f"Eval Proyecto {token}"),
            "Expected project name present in /project listing.",
        )
    elif scenario.scenario_id == "fixed_price_project_billing_pt":
        projects = _list_values(
            client,
            "/project",
            fields="id,name,isFixedPrice,fixedprice,customer(id,name)",
            count=200,
        )
        add(
            "fixed_price_project_configured",
            any(
                f"Segurança de dados {token}".lower() in str(project.get("name", "")).lower()
                and bool(project.get("isFixedPrice"))
                and float(project.get("fixedprice", 0) or 0) == 122800.0
                for project in projects
            ),
            "Expected fixed-price project with the requested amount present in /project listing.",
        )
        add(
            "project_billing_tools_present",
            all(
                tool in tool_sequence
                for tool in ("configure_project_billing", "create_order", "create_invoice")
            ),
            f"tool_sequence={tool_sequence}",
        )
    elif scenario.scenario_id == "order_invoice_flow":
        add(
            "invoice_tools_present",
            all(tool in tool_sequence for tool in ("create_customer", "create_product", "create_order", "create_invoice")),
            f"tool_sequence={tool_sequence}",
        )
    elif scenario.scenario_id == "invoice_payment_flow":
        add(
            "payment_tool_present",
            "register_invoice_payment" in tool_sequence,
            f"tool_sequence={tool_sequence}",
        )
    elif scenario.scenario_id == "travel_expense_from_receipt":
        travel_expenses = _list_values(client, "/travelExpense", fields="id,title", count=100)
        add(
            "travel_expense_created",
            _find_match(travel_expenses, "title", f"Eval Travel {token}"),
            "Expected travel expense title present in /travelExpense listing.",
        )
    elif scenario.scenario_id == "timesheet_entry_flow":
        projects = _list_values(client, "/project", fields="id,name,number,startDate", count=200)
        add(
            "timesheet_project_created",
            _find_match(projects, "name", f"Eval Timesheet Project {token}"),
            "Expected timesheet project present in /project listing.",
        )
        add(
            "timesheet_tool_present",
            "create_timesheet_entry" in tool_sequence,
            f"tool_sequence={tool_sequence}",
        )
    elif scenario.scenario_id == "voucher_reversal_flow":
        add(
            "reverse_tool_present",
            "reverse_voucher" in tool_sequence,
            f"tool_sequence={tool_sequence}",
        )

    return checks


def _assert_observed_summary(observed_summary_path: Path) -> None:
    observed = json.loads(observed_summary_path.read_text(encoding="utf-8"))
    assert observed["all_checks_passed"], json.dumps(observed, indent=2, ensure_ascii=True)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.scenario_id for scenario in SCENARIOS])
def test_live_evaluator_like_scenarios(scenario: ScenarioSpec) -> None:
    token = uuid4().hex[:10]
    prompt = scenario.prompt_template.format(token=token)
    body = build_request_body(
        SETTINGS,
        prompt=prompt,
        attachment_filenames=scenario.attachment_filenames,
    )
    response = send_solve_request(SETTINGS, body=body)
    cloud_logs = fetch_cloud_logs(SETTINGS, token)
    observed_summary = summarize_observed_behavior(
        scenario=scenario,
        trace_token=token,
        response=response,
        cloud_logs=cloud_logs,
    )
    observed_summary["outcome_checks"] = _outcome_checks(scenario, token, observed_summary)
    observed_summary["all_checks_passed"] = observed_summary["all_checks_passed"] and all(
        check["passed"] for check in observed_summary["outcome_checks"].values()
    )
    artifacts = write_scenario_artifacts(
        scenario=scenario,
        trace_token=token,
        request_payload=body,
        response=response,
        cloud_logs=cloud_logs,
        observed_summary=observed_summary,
    )

    assert response.status_code == 200
    assert response.json() == {"status": "completed"}
    assert cloud_logs["status"] == "ok"
    _assert_observed_summary(artifacts["observed_summary"])
    assert artifacts["scenario_spec"].exists()
    assert artifacts["request_response"].exists()
    assert artifacts["cloud_logging"].exists()
    assert artifacts["observed_summary"].exists()
    assert REALISTIC_SCENARIO_DIR.exists()
