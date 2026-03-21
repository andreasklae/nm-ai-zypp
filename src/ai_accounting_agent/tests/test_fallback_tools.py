"""Tests for the fallback tool system: ApiIndex, find_api, and raw_api_call."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.exceptions import ModelRetry

from ai_accounting_agent.api_index import ApiIndex, _format_tag_groups, _tokenize, get_api_index
from ai_accounting_agent.schemas import RawApiCallInput
from ai_accounting_agent.tripletex_client import TripletexApiError
from ai_accounting_agent.tripletex_tools import StepState, TripletexService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    *,
    step_announced: bool = True,
    get_responses: dict[str, Any] | None = None,
    request_response: Any = None,
    request_side_effect: Exception | None = None,
) -> tuple[TripletexService, MagicMock]:
    """Create a TripletexService with a mocked client."""
    client = MagicMock()
    client.cache = {}

    if get_responses:
        def fake_get(path, params=None, cache_key=None):
            for key, val in get_responses.items():
                if path.startswith(key):
                    return val
            raise AssertionError(f"Unexpected GET: {path}")
        client.get = MagicMock(side_effect=fake_get)

    if request_side_effect:
        client.request = MagicMock(side_effect=request_side_effect)
    elif request_response is not None:
        client.request = MagicMock(return_value=request_response)

    ref_index = MagicMock()
    ref_index.search.return_value = []

    step_state = StepState()
    if step_announced:
        step_state.has_announced_step = True

    service = TripletexService(
        client=client,
        run_id="test-run-123",
        reference_index=ref_index,
        step_state=step_state,
    )
    return service, client


# ---------------------------------------------------------------------------
# ApiIndex unit tests
# ---------------------------------------------------------------------------

class TestApiIndex:
    """Tests for the ApiIndex search and formatting."""

    def test_load_default_returns_populated_index(self) -> None:
        index = get_api_index()
        assert len(index.data) > 100, "Should have 100+ tag groups from the full spec"
        assert "customer" in index.data
        assert "invoice" in index.data
        assert "ledger/voucher" in index.data

    def test_search_voucher_returns_voucher_tags(self) -> None:
        index = get_api_index()
        tags, docs = index.search("reverse a posted voucher")
        assert len(tags) > 0
        assert any("voucher" in t for t in tags), f"Expected voucher in tags, got {tags}"
        assert len(docs) > 0, "Should return formatted docs"

    def test_search_invoice_returns_invoice_tags(self) -> None:
        index = get_api_index()
        tags, docs = index.search("register payment on invoice")
        assert any("invoice" in t for t in tags), f"Expected invoice in tags, got {tags}"

    def test_search_bank_reconciliation(self) -> None:
        index = get_api_index()
        tags, docs = index.search("create a bank reconciliation")
        assert any("reconciliation" in t for t in tags), f"Expected reconciliation in tags, got {tags}"

    def test_search_employee_returns_employee_tags(self) -> None:
        index = get_api_index()
        tags, docs = index.search("create employee with employment")
        assert any("employee" in t for t in tags), f"Expected employee in tags, got {tags}"

    def test_search_travel_expense(self) -> None:
        index = get_api_index()
        tags, docs = index.search("add per diem to travel expense")
        assert any("travel" in t.lower() for t in tags), f"Expected travel in tags, got {tags}"

    def test_search_salary(self) -> None:
        index = get_api_index()
        tags, docs = index.search("run salary transaction payroll")
        assert any("salary" in t for t in tags), f"Expected salary in tags, got {tags}"

    def test_search_supplier_invoice(self) -> None:
        index = get_api_index()
        tags, docs = index.search("create supplier invoice voucher ledger")
        assert any("ledger" in t or "supplier" in t for t in tags), f"Expected ledger/supplier in tags, got {tags}"

    def test_search_empty_query_returns_empty(self) -> None:
        index = get_api_index()
        tags, docs = index.search("")
        assert tags == []
        assert docs == ""

    def test_search_gibberish_returns_empty(self) -> None:
        index = get_api_index()
        tags, docs = index.search("xyzzy foobar baz")
        # Might match something or not, but shouldn't crash
        assert isinstance(tags, list)
        assert isinstance(docs, str)

    def test_search_max_groups_limits_results(self) -> None:
        index = get_api_index()
        tags, _ = index.search("invoice customer order payment", max_groups=2)
        assert len(tags) <= 2

    def test_formatted_docs_contain_endpoint_info(self) -> None:
        index = get_api_index()
        tags, docs = index.search("create customer")
        assert "POST" in docs, "Formatted docs should contain HTTP methods"
        assert "/customer" in docs, "Formatted docs should contain API paths"

    def test_formatted_docs_contain_schema_fields(self) -> None:
        index = get_api_index()
        tags, docs = index.search("create customer")
        # Customer schema should have fields like name, organizationNumber
        assert "name" in docs.lower()


class TestTokenize:
    """Tests for the _tokenize helper."""

    def test_basic_tokenization(self) -> None:
        assert _tokenize("create a customer") == ["create", "customer"]

    def test_filters_short_tokens(self) -> None:
        tokens = _tokenize("I am a big fan of AI")
        assert "big" in tokens
        assert "fan" in tokens
        assert "am" not in tokens  # too short
        assert "a" not in tokens   # too short

    def test_handles_special_chars(self) -> None:
        tokens = _tokenize("ledger/voucher reverse_payment 123")
        assert "ledger" in tokens
        assert "voucher" in tokens
        assert "reverse" in tokens
        assert "payment" in tokens
        assert "123" in tokens


class TestFormatTagGroups:
    """Tests for _format_tag_groups."""

    def test_formats_operations(self) -> None:
        data = {
            "customer": [
                {
                    "method": "POST",
                    "path": "/customer",
                    "summary": "Create customer.",
                    "parameters": [
                        {"name": "fields", "type": "string", "description": "Fields to return"}
                    ],
                    "request_body": {
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                        }
                    },
                }
            ]
        }
        result = _format_tag_groups(data, ["customer"])
        assert "## Resource: customer" in result
        assert "### POST /customer" in result
        assert "Create customer." in result
        assert "name: string" in result

    def test_formats_multiple_tags(self) -> None:
        data = {
            "customer": [{"method": "GET", "path": "/customer", "summary": "List"}],
            "invoice": [{"method": "POST", "path": "/invoice", "summary": "Create"}],
        }
        result = _format_tag_groups(data, ["customer", "invoice"])
        assert "customer" in result
        assert "invoice" in result
        assert "---" in result  # separator between tag groups

    def test_empty_tag_returns_empty(self) -> None:
        result = _format_tag_groups({}, ["nonexistent"])
        assert result == ""


# ---------------------------------------------------------------------------
# raw_api_call unit tests
# ---------------------------------------------------------------------------

class TestRawApiCall:
    """Tests for the raw_api_call tool."""

    def test_raw_get_call(self) -> None:
        service, client = _make_service(
            request_response={"value": {"id": 1, "name": "Test"}}
        )
        payload = RawApiCallInput(method="GET", path="/customer/1")
        result = service.raw_api_call(payload)
        client.request.assert_called_once_with(
            method="GET", path="/customer/1", params=None, json_body=None
        )
        assert result == {"value": {"id": 1, "name": "Test"}}

    def test_raw_post_call_with_body(self) -> None:
        service, client = _make_service(
            request_response={"value": {"id": 99, "name": "New"}}
        )
        payload = RawApiCallInput(
            method="POST",
            path="/customer",
            body={"name": "New Customer"},
            query_params={"fields": "id,name"},
        )
        result = service.raw_api_call(payload)
        client.request.assert_called_once_with(
            method="POST",
            path="/customer",
            params={"fields": "id,name"},
            json_body={"name": "New Customer"},
        )

    def test_raw_put_call(self) -> None:
        service, client = _make_service(
            request_response={"value": {"id": 1, "version": 2}}
        )
        payload = RawApiCallInput(
            method="PUT",
            path="/customer/1",
            body={"id": 1, "version": 1, "name": "Updated"},
        )
        result = service.raw_api_call(payload)
        client.request.assert_called_once()

    def test_raw_delete_call(self) -> None:
        service, client = _make_service(request_response=None)
        payload = RawApiCallInput(method="DELETE", path="/customer/1")
        service.raw_api_call(payload)
        client.request.assert_called_once_with(
            method="DELETE", path="/customer/1", params=None, json_body=None
        )

    def test_raw_call_normalizes_path_without_leading_slash(self) -> None:
        service, client = _make_service(request_response={"value": {}})
        payload = RawApiCallInput(method="GET", path="customer/1")
        service.raw_api_call(payload)
        client.request.assert_called_once_with(
            method="GET", path="/customer/1", params=None, json_body=None
        )

    def test_raw_call_requires_announce_step(self) -> None:
        service, _ = _make_service(step_announced=False, request_response={})
        payload = RawApiCallInput(method="GET", path="/customer")
        with pytest.raises(ModelRetry, match="announce_step"):
            service.raw_api_call(payload)

    def test_raw_call_raises_model_retry_on_tripletex_validation_error(self) -> None:
        error = TripletexApiError(
            message="Tripletex API returned 422",
            status_code=422,
            response_body={"message": "organizationNumber: Ugyldig verdi"},
            response_headers={},
            request_id=None,
        )
        service, _ = _make_service(request_side_effect=error)
        payload = RawApiCallInput(method="POST", path="/customer", body={"name": "Test"})
        with pytest.raises(ModelRetry, match="rejected"):
            service.raw_api_call(payload)


# ---------------------------------------------------------------------------
# find_api unit tests (mocking the sub-agent)
# ---------------------------------------------------------------------------

class TestFindApi:
    """Tests for the find_api tool with mocked sub-agent."""

    def test_find_api_requires_announce_step(self) -> None:
        service, _ = _make_service(step_announced=False)
        with pytest.raises(ModelRetry, match="announce_step"):
            asyncio.run(service.find_api("create customer"))

    def test_find_api_returns_no_match_for_gibberish(self) -> None:
        service, _ = _make_service()
        result = asyncio.run(
            service.find_api("xyzzy_nomatches_atall_qqq")
        )
        assert "No matching" in result["guidance"]
        assert result["searched_tags"] == []
        assert result["subagent_duration_ms"] == 0

    def test_find_api_spawns_subagent_and_returns_guidance(self) -> None:
        """Test that find_api searches the index, spawns a sub-agent, and returns its output."""
        service, _ = _make_service()

        mock_result = MagicMock()
        mock_result.output = "ENDPOINT: PUT /ledger/voucher/{id}/:reverse\nREQUIRED FIELDS:\n  - date (string)"
        mock_result.usage.return_value = {"total_tokens": 100}

        mock_agent_instance = MagicMock()
        mock_agent_instance.run = AsyncMock(return_value=mock_result)

        with patch("ai_accounting_agent.tripletex_tools.Agent", return_value=mock_agent_instance) as mock_agent_cls:
            with patch("ai_accounting_agent.gemini.build_google_model") as mock_model:
                mock_model.return_value = "fake-model"
                result = asyncio.run(
                    service.find_api("I need to reverse a posted voucher")
                )

        # Verify sub-agent was created with the specialist prompt
        mock_agent_cls.assert_called_once()
        call_kwargs = mock_agent_cls.call_args
        assert call_kwargs[1]["instructions"].startswith("You are a Tripletex API specialist")
        assert call_kwargs[1]["output_type"] is str

        # Verify sub-agent.run was called with relevant docs
        mock_agent_instance.run.assert_called_once()
        prompt_arg = mock_agent_instance.run.call_args[0][0]
        assert "voucher" in prompt_arg.lower()
        assert "reverse" in prompt_arg.lower() or "What I need" in prompt_arg

        # Verify return structure
        assert "guidance" in result
        assert "ENDPOINT: PUT /ledger/voucher" in result["guidance"]
        assert "searched_tags" in result
        assert len(result["searched_tags"]) > 0
        assert any("voucher" in t for t in result["searched_tags"])
        assert "subagent_duration_ms" in result
        assert isinstance(result["subagent_duration_ms"], int)

    def test_find_api_passes_error_context_to_subagent(self) -> None:
        """When find_api is called with an error message, it should reach the sub-agent."""
        service, _ = _make_service()

        mock_result = MagicMock()
        mock_result.output = "ENDPOINT: POST /customer\nFix: use organizationNumber not orgNumber"
        mock_result.usage.return_value = {}

        mock_agent_instance = MagicMock()
        mock_agent_instance.run = AsyncMock(return_value=mock_result)

        with patch("ai_accounting_agent.tripletex_tools.Agent", return_value=mock_agent_instance):
            with patch("ai_accounting_agent.gemini.build_google_model"):
                result = asyncio.run(
                    service.find_api(
                        "I tried POST /customer with {orgNumber: '123'} and got 422: "
                        "unknown field 'orgNumber'. What is the correct field name?"
                    )
                )

        # The error context should be in the sub-agent prompt
        prompt_arg = mock_agent_instance.run.call_args[0][0]
        assert "422" in prompt_arg
        assert "orgNumber" in prompt_arg

    def test_find_api_searches_correct_tags_for_various_needs(self) -> None:
        """Verify the index search returns relevant tags for different API needs."""
        service, _ = _make_service()
        index = ApiIndex.load_default()

        test_cases = [
            ("create a new invoice and send it", "invoice"),
            ("look up employee by email", "employee"),
            ("post a salary transaction", "salary"),
            ("add mileage allowance to travel expense", "travelExpense"),
            ("create accounting dimension values", "accountingDimensionValue"),
            ("find open ledger postings", "ledger"),
            ("create a project with hourly rates", "project"),
            ("register supplier invoice", "supplier"),
        ]

        for need, expected_substring in test_cases:
            tags, docs = index.search(need, max_groups=5)
            tag_str = " ".join(tags).lower()
            assert expected_substring.lower() in tag_str, (
                f"Need '{need}': expected '{expected_substring}' in tags, got {tags}"
            )


# ---------------------------------------------------------------------------
# find_api live integration test (hits real Gemini API — skipped by default)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    True,  # Change to False to run manually
    reason="Live sub-agent test — requires GEMINI_API_KEY and costs money",
)
class TestFindApiLive:
    """Live integration tests that actually spawn the Gemini sub-agent."""

    def test_live_find_api_voucher_reversal(self) -> None:
        service, _ = _make_service()
        result = asyncio.run(
            service.find_api("I need to reverse a posted voucher in Tripletex")
        )
        guidance = result["guidance"]
        assert "reverse" in guidance.lower()
        assert "/ledger/voucher" in guidance or "voucher" in guidance.lower()
        assert result["subagent_duration_ms"] > 0

    def test_live_find_api_invoice_payment(self) -> None:
        service, _ = _make_service()
        result = asyncio.run(
            service.find_api("I need to register a payment on an invoice")
        )
        guidance = result["guidance"]
        assert "payment" in guidance.lower()
        assert "invoice" in guidance.lower()
