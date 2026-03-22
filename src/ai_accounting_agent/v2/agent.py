from __future__ import annotations

import asyncio
import datetime
import logging
import dataclasses
from importlib import resources
from typing import Any

from pydantic_ai import Agent, Tool, capture_run_messages
from pydantic_ai.exceptions import ModelHTTPError, ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage

from ai_accounting_agent import gemini
from ai_accounting_agent.attachment_extractor import extract_attachments
from ai_accounting_agent.gemini import build_google_model, default_model_settings
from ai_accounting_agent.agent import _build_attachment_prefix, _build_prompt_content
from ai_accounting_agent.schemas import PreparedAttachment, SolveRequest
from ai_accounting_agent.telemetry import log_agent_messages, log_event
from ai_accounting_agent.tripletex_client import TripletexApiError, TripletexClient
from ai_accounting_agent.tripletex_tools import StepState
from ai_accounting_agent.v2.tools import (
    call_tripletex_api,
    get_endpoint_schema,
    search_endpoints,
    calculate_vat_split,
)

logger = logging.getLogger(__name__)

MAX_MODEL_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 5, 10)


def _is_retryable_model_error(exc: ModelHTTPError) -> bool:
    return exc.status_code in {429, 500, 502, 503, 504}


@dataclasses.dataclass(slots=True)
class AgentDependenciesV2:
    step_state: StepState
    run_id: str
    request: SolveRequest
    client: TripletexClient


@dataclasses.dataclass(slots=True)
class AgentExecutionResultV2:
    output: str
    messages: list[ModelMessage]
    usage: dict[str, Any]
    model: str


class AgentTaskErrorV2(Exception):
    def __init__(
        self,
        *,
        model: str,
        messages: list[ModelMessage],
        usage: dict[str, Any] | None,
        error_type: str,
        error_message: str,
        created_entities: dict[str, list[dict[str, Any]]],
    ) -> None:
        super().__init__(error_message)
        self.model = model
        self.messages = messages
        self.usage = usage
        self.error_type = error_type
        self.error_message = error_message
        self.created_entities = created_entities


V2_AGENT_SYSTEM_PROMPT = """You are an autonomous AI Accounting Agent specializing in the Tripletex API (version 2).

Instead of relying on pre-built, hardcoded workflows, you will discover the API dynamically.
Your source of truth is the OpenAPI specification, which you interact with via tools.

### YOUR WORKFLOW
1. **Understand the User Request**: Break down the user's intent into business objects (e.g., Customer, Invoice, Project, Employee) and actions (e.g., Search, Create, Update, Register Payment).
2. **Search for Endpoints**: Use `search_endpoints(query)` to find relevant operations. Do NOT guess paths. You must search first.
   - *Lookup before Action*: If you need to create something tied to a customer, look up the customer first to get their `id`.
   - *Payment Status*: For questions about payment status or accounting truth, consider ledger endpoints instead of assuming invoice endpoints are sufficient.
   - *Stop Searching*: DO NOT call `search_endpoints` repeatedly. Stop searching once you find the relevant endpoint.
3. **Inspect the Schema**: Before calling ANY endpoint, you MUST use `get_endpoint_schema(method, path)` EXACTLY ONCE for the specific endpoint you intend to use to understand its required parameters, request body structure, supplemental notes, and any crucial "learned rules" that apply to it.
4. **Plan Execution**: Formulate a step-by-step plan based on the endpoints you found.
   - E.g., "Step 1: GET /customer to resolve ID. Step 2: POST /invoice to create."
   - Multi-step reasoning: Most user intents require multiple steps (Lookup -> Retrieve/Create/Update -> Verify).
5. **Execute**: Use `call_tripletex_api` to execute your plan.
6. **Evaluate Responses**: Check the status code. If you receive a 400 or 422, read the error message carefully. Adjust your payload and retry.
   - If you see `code: 14000` (Conflict/Duplicate entry), DO NOT retry the POST. Instead, use a GET request to fetch the existing entity (e.g. GET /activity/>forTimeSheet or GET /timesheet/entry) and use its ID, or update it via PUT if needed.
   - If you receive **403 Forbidden**, DO NOT retry. This means the endpoint is restricted (BETA or requires a module you don't have). Stop and report the error to the user. Never attempt workarounds or alternative approaches that involve BETA endpoints.

### IMPORTANT RULES & GUARDRails
- **Learned Rules Overrule Everything**: When `get_endpoint_schema` returns `learned_rules`, those rules are absolute. If a rule says "Always set dateOfBirth on POST /employee", you MUST do it, even if the OpenAPI schema says it's optional.
- **IDs are Integers**: When linking entities (e.g., adding a customer to a project), use the Tripletex internal integer `id` returned from lookup calls, NEVER external strings like organization numbers.
- **Nested Objects**: Tripletex DTOs often look huge, but you usually only need `{"id": <number>}` when referencing an existing nested object. Do not reconstruct the entire object.
- **Filters & Fields**: Use narrow filters rather than broad scans. Use `fields=` aggressively to reduce payload sizes.
- **VAT Math**: You are bad at math. Whenever you need to split gross amounts into net/VAT amounts, use the `calculate_vat_split` tool. NEVER calculate VAT manually.
- **Dates**: Always use `YYYY-MM-DD` format. Today's date will be injected at the start of your task prompt — use it as the default for any required date field (like `startDate` on a project or `invoiceDate`) not specified by the user.
- **Scope Discipline**: Only perform the actions directly requested by the user. If an operation fails due to missing company configuration (e.g., "no bank account number registered", "missing company settings"), **stop and report the error clearly** — do NOT attempt to fix underlying company configuration as a side effect. Never modify company profiles, bank accounts, ledger account settings, or company-level configuration unless the user explicitly requested it.
- **Specificity**: Prefer the most semantically specific endpoint over broader alternatives.
- **Incremental Progress**: Prefer multiple smaller calls to one giant one. Lookup the customer ID first, THEN create the order.
"""


def _load_reference_docs() -> str:
    """Load supplemental markdown reference documents for the agent's context."""
    package = "ai_accounting_agent"
    docs = []

    # Base path assuming python package structure
    base_paths = [
        "v2/SKILL.md",
        "v2/references/endpoint-patterns.md",
        "v2/references/gotchas.md",
    ]

    for path in base_paths:
        try:
            text = resources.files(package).joinpath(path).read_text(encoding="utf-8")
            docs.append(f"\n--- BEGIN {path} ---\n{text}\n--- END {path} ---\n")
        except Exception as exc:
            logger.warning(f"Could not load reference doc {path}: {exc}")

    return "\n".join(docs)


def _build_agent_v2(model: str) -> Agent[AgentDependenciesV2, str]:
    full_system_prompt = V2_AGENT_SYSTEM_PROMPT + "\n\n### REFERENCE DOCUMENTS\n" + _load_reference_docs()
    return Agent(
        build_google_model(model),
        deps_type=AgentDependenciesV2,
        system_prompt=full_system_prompt,
        model_settings=default_model_settings(),
        tools=[
            Tool(search_endpoints, docstring_format="google", require_parameter_descriptions=True),
            Tool(get_endpoint_schema, docstring_format="google", require_parameter_descriptions=True),
            Tool(call_tripletex_api, docstring_format="google", require_parameter_descriptions=True, sequential=True),
            Tool(calculate_vat_split, docstring_format="google", require_parameter_descriptions=True),
        ],
        retries=3,
    )


async def execute_agent_v2(
    *,
    request: SolveRequest,
    attachments: list[PreparedAttachment],
    run_id: str,
    model: str = gemini.DEFAULT_GEMINI_MODEL,
) -> AgentExecutionResultV2:
    client = TripletexClient(
        base_url=str(request.tripletex_credentials.base_url),
        session_token=request.tripletex_credentials.session_token,
        run_id=run_id,
    )
    deps = AgentDependenciesV2(
        step_state=StepState(),
        run_id=run_id,
        request=request,
        client=client,
    )

    v2_agent = _build_agent_v2(model)

    import zoneinfo
    today = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Oslo")).date().isoformat()
    prompt_text = f"[Today's date: {today}]\n\n" + request.prompt
    if attachments:
        extraction_report = await extract_attachments(
            run_id=run_id,
            task_prompt=request.prompt,
            attachments=attachments,
        )
        prompt_text = f"[Today's date: {today}]\n\n" + _build_attachment_prefix(extraction_report) + "\n\n" + request.prompt

    prompt_content = _build_prompt_content(prompt_text, attachments)

    for attempt in range(1, MAX_MODEL_RETRIES + 1):
        with capture_run_messages() as captured_messages:
            try:
                result = await asyncio.wait_for(
                    v2_agent.run(prompt_content, deps=deps),
                    timeout=105.0,
                )
            except asyncio.TimeoutError:
                messages = list(captured_messages)
                log_agent_messages(run_id=run_id, model=model, messages=messages, usage=None)
                log_event(
                    "agent_timeout",
                    severity="WARNING",
                    run_id=run_id,
                    model=model,
                    attempt=attempt,
                    timeout_seconds=105.0,
                )
                raise AgentTaskErrorV2(
                    model=model,
                    messages=messages,
                    usage=None,
                    error_type="AgentTimeout",
                    error_message="Agent exceeded 105s time limit and was stopped to avoid cloudflared tunnel timeout.",
                    created_entities=deps.step_state.created_entities,
                )
            except ModelHTTPError as exc:
                if _is_retryable_model_error(exc) and attempt < MAX_MODEL_RETRIES:
                    backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                    log_event(
                        "model_transient_error",
                        severity="WARNING",
                        run_id=run_id,
                        model=model,
                        attempt=attempt,
                        max_attempts=MAX_MODEL_RETRIES,
                        status_code=exc.status_code,
                        backoff_seconds=backoff,
                        error_message=str(exc),
                    )
                    await asyncio.sleep(backoff)
                    continue
                messages = list(captured_messages)
                log_agent_messages(run_id=run_id, model=model, messages=messages, usage=None)
                raise AgentTaskErrorV2(
                    model=model,
                    messages=messages,
                    usage=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    created_entities=deps.step_state.created_entities,
                ) from exc
            except (ModelRetry, TripletexApiError, UnexpectedModelBehavior) as exc:
                messages = list(captured_messages)
                log_agent_messages(run_id=run_id, model=model, messages=messages, usage=None)
                raise AgentTaskErrorV2(
                    model=model,
                    messages=messages,
                    usage=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    created_entities=deps.step_state.created_entities,
                ) from exc

        messages = list(captured_messages)
        usage = dataclasses.asdict(result.usage()) if result.usage() else None

        log_agent_messages(run_id=run_id, model=model, messages=messages, usage=usage)

        output = ""
        if hasattr(result, "data") and isinstance(result.data, str):
            output = result.data
        elif hasattr(result, "all_messages"):
            output = result.all_messages()[-1].parts[-1].content

        return AgentExecutionResultV2(
            output=output,
            messages=messages,
            usage=usage,
            model=model,
        )

    # Should not reach here if exceptions are raised
    raise RuntimeError("Failed to execute agent V2.")
