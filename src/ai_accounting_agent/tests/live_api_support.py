from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


DEFAULT_ENV_PATH = Path("src/ai_accounting_agent/.env")
ATTACHMENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "attachments"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
REALISTIC_SCENARIO_DIR = PACKAGE_ROOT / "test_runs" / "realistic_scenarios"
DEFAULT_GCLOUD_CONFIG = REPO_ROOT / ".gcloud"
DEFAULT_GCP_PROJECT_ID = "ai-nm26osl-1850"
DEFAULT_CLOUD_RUN_SERVICE = "ai-accounting-agent"


@dataclass(slots=True)
class LiveApiSettings:
    api_url: str
    api_key: str
    tripletex_api_url: str
    tripletex_session_token: str
    gcp_project_id: str
    cloud_run_service_name: str
    cloudsdk_config_path: str


@dataclass(slots=True)
class ScenarioSpec:
    scenario_id: str
    category: str
    prompt_template: str
    expected_behavior: list[str]
    success_criteria: list[str]
    score_focus: list[str]
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    attachment_filenames: list[str] = field(default_factory=list)
    expect_tripletex_write: bool | None = None
    max_tolerated_4xx: int = 0
    expect_provider_thoughts: bool = True
    expected_log_events: list[str] = field(
        default_factory=lambda: [
            "request_received",
            "agent_messages",
            "task_complete",
        ]
    )


def load_live_api_settings() -> LiveApiSettings:
    settings: dict[str, str] = {}

    if DEFAULT_ENV_PATH.exists():
        for key, value in dotenv_values(DEFAULT_ENV_PATH).items():
            if value is not None:
                settings[key] = value

    for key in (
        "AI_ACCOUNTING_AGENT_API_URL",
        "AI_ACCOUNTING_AGENT_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GCP_PROJECT_ID",
        "CLOUD_RUN_SERVICE_NAME",
        "CLOUDSDK_CONFIG",
        "TRIPLETEX_API_URL",
        "TRIPLETEX_SESSION_TOKEN",
    ):
        value = os.environ.get(key)
        if value:
            settings[key] = value

    return LiveApiSettings(
        api_url=settings.get("AI_ACCOUNTING_AGENT_API_URL", "").rstrip("/"),
        api_key=settings.get("AI_ACCOUNTING_AGENT_API_KEY", ""),
        tripletex_api_url=settings.get("TRIPLETEX_API_URL", "").rstrip("/"),
        tripletex_session_token=settings.get("TRIPLETEX_SESSION_TOKEN", ""),
        gcp_project_id=settings.get("GOOGLE_CLOUD_PROJECT")
        or settings.get("GCP_PROJECT_ID", DEFAULT_GCP_PROJECT_ID),
        cloud_run_service_name=settings.get("CLOUD_RUN_SERVICE_NAME", DEFAULT_CLOUD_RUN_SERVICE),
        cloudsdk_config_path=settings.get("CLOUDSDK_CONFIG")
        or (str(DEFAULT_GCLOUD_CONFIG) if DEFAULT_GCLOUD_CONFIG.exists() else ""),
    )


def evaluator_headers(settings: LiveApiSettings) -> dict[str, str]:
    if not settings.api_key:
        return {}
    return {"x-api-key": settings.api_key}


def attachment_payload(filename: str) -> dict[str, str]:
    path = ATTACHMENTS_DIR / filename
    return {
        "filename": filename,
        "mime_type": "text/markdown",
        "content_base64": base64.b64encode(path.read_bytes()).decode(),
    }


def build_request_body(settings: LiveApiSettings, *, prompt: str, attachment_filenames: list[str]) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "files": [attachment_payload(filename) for filename in attachment_filenames],
        "tripletex_credentials": {
            "base_url": settings.tripletex_api_url,
            "session_token": settings.tripletex_session_token,
        },
    }


def send_solve_request(
    settings: LiveApiSettings,
    *,
    body: dict[str, Any],
    timeout: int = 300,
) -> requests.Response:
    return requests.post(
        f"{settings.api_url}/solve",
        headers=evaluator_headers(settings),
        json=body,
        timeout=timeout,
    )


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "test"


def sanitize_for_artifact(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in {"authorization", "api_key", "x-api-key", "session_token", "content_base64"}:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_for_artifact(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_artifact(item) for item in value]
    return value


def cleanup_realistic_scenarios() -> None:
    if REALISTIC_SCENARIO_DIR.exists():
        shutil.rmtree(REALISTIC_SCENARIO_DIR)


def gcloud_env(settings: LiveApiSettings) -> dict[str, str]:
    env = os.environ.copy()
    if settings.cloudsdk_config_path:
        env["CLOUDSDK_CONFIG"] = settings.cloudsdk_config_path
    return env


def _run_gcloud_logging_query(settings: LiveApiSettings, filter_expression: str) -> tuple[bool, list[dict[str, Any]], str]:
    command = [
        "gcloud",
        "logging",
        "read",
        filter_expression,
        f"--project={settings.gcp_project_id}",
        "--limit=400",
        "--format=json",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=gcloud_env(settings),
    )
    if completed.returncode != 0:
        return False, [], completed.stderr.strip() or completed.stdout.strip()
    try:
        return True, json.loads(completed.stdout or "[]"), ""
    except json.JSONDecodeError:
        return False, [], "gcloud logging read returned invalid JSON."


def fetch_cloud_logs(settings: LiveApiSettings, trace_token: str) -> dict[str, Any]:
    if not settings.gcp_project_id:
        return {"status": "skipped", "reason": "Missing GCP project id."}

    seed_filter = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{settings.cloud_run_service_name}" '
        f'AND jsonPayload.prompt:"trace_token={trace_token}"'
    )
    last_error = ""
    seed_entries: list[dict[str, Any]] = []
    run_id = ""

    for _ in range(8):
        ok, entries, error_message = _run_gcloud_logging_query(settings, seed_filter)
        if not ok:
            last_error = error_message
            time.sleep(2)
            continue
        seed_entries = entries
        run_id = next(
            (
                entry.get("jsonPayload", {}).get("run_id", "")
                for entry in seed_entries
                if entry.get("jsonPayload", {}).get("run_id")
            ),
            "",
        )
        if run_id:
            break
        time.sleep(2)

    if not run_id:
        return {
            "status": "error",
            "project_id": settings.gcp_project_id,
            "service_name": settings.cloud_run_service_name,
            "seed_filter": seed_filter,
            "error": last_error or "No matching log entries with a run_id were found before timeout.",
            "entries": seed_entries,
        }

    run_filter = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{settings.cloud_run_service_name}" '
        f'AND jsonPayload.run_id="{run_id}"'
    )
    run_entries: list[dict[str, Any]] = []
    for _ in range(10):
        ok, entries, error_message = _run_gcloud_logging_query(settings, run_filter)
        if not ok:
            last_error = error_message
            time.sleep(2)
            continue
        if entries:
            run_entries = entries
            if any(
                entry.get("jsonPayload", {}).get("event") in {"task_complete", "task_error"}
                for entry in run_entries
            ):
                break
        time.sleep(2)

    if not run_entries:
        return {
            "status": "error",
            "project_id": settings.gcp_project_id,
            "service_name": settings.cloud_run_service_name,
            "seed_filter": seed_filter,
            "run_filter": run_filter,
            "run_id": run_id,
            "error": last_error or "No matching run log entries were found before timeout.",
            "entries": seed_entries,
        }

    request_timestamp = next(
        (
            entry.get("timestamp")
            for entry in run_entries
            if entry.get("jsonPayload", {}).get("event") == "request_received"
        ),
        "",
    )
    completion_timestamp = next(
        (
            entry.get("timestamp")
            for entry in run_entries
            if entry.get("jsonPayload", {}).get("event") in {"task_complete", "task_error"}
        ),
        "",
    )

    if request_timestamp and completion_timestamp:
        start_dt = datetime.fromisoformat(request_timestamp.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(completion_timestamp.replace("Z", "+00:00")) + timedelta(seconds=5)
        window_filter = (
            'resource.type="cloud_run_revision" '
            f'AND resource.labels.service_name="{settings.cloud_run_service_name}" '
            f'AND timestamp>="{start_dt.isoformat().replace("+00:00", "Z")}" '
            f'AND timestamp<="{end_dt.isoformat().replace("+00:00", "Z")}"'
        )
        ok, window_entries, error_message = _run_gcloud_logging_query(settings, window_filter)
        if ok and window_entries:
            return {
                "status": "ok",
                "project_id": settings.gcp_project_id,
                "service_name": settings.cloud_run_service_name,
                "seed_filter": seed_filter,
                "run_filter": run_filter,
                "window_filter": window_filter,
                "run_id": run_id,
                "entries": window_entries,
            }
        if error_message:
            last_error = error_message

    return {
        "status": "ok",
        "project_id": settings.gcp_project_id,
        "service_name": settings.cloud_run_service_name,
        "seed_filter": seed_filter,
        "run_filter": run_filter,
        "run_id": run_id,
        "entries": run_entries,
        "warning": last_error or None,
    }


def summarize_observed_behavior(
    *,
    scenario: ScenarioSpec,
    trace_token: str,
    response: requests.Response,
    cloud_logs: dict[str, Any],
) -> dict[str, Any]:
    try:
        response_body = response.json()
    except ValueError:
        response_body = {"raw_text": response.text}

    entries = sorted(cloud_logs.get("entries", []), key=lambda entry: entry.get("timestamp", ""))
    structured_payloads = [
        entry["jsonPayload"]
        for entry in entries
        if isinstance(entry.get("jsonPayload"), dict)
    ]
    event_sequence = [
        payload.get("event") or entry.get("logName", "").split("/")[-1]
        for entry, payload in (
            (entry, entry.get("jsonPayload", {})) for entry in entries
        )
    ]
    tool_calls = [payload for payload in structured_payloads if payload.get("event") == "tool_call"]
    tool_sequence = [payload.get("tool", "") for payload in tool_calls]
    tool_argument_summary = [
        {
            "tool": payload.get("tool"),
            "arguments": sanitize_for_artifact(payload.get("arguments", {})),
        }
        for payload in tool_calls
    ]
    tripletex_requests = [payload for payload in structured_payloads if payload.get("event") == "tripletex_http_request"]
    tripletex_responses = [payload for payload in structured_payloads if payload.get("event") == "tripletex_http_response"]
    tripletex_error_statuses = [
        payload.get("status_code")
        for payload in tripletex_responses
        if isinstance(payload.get("status_code"), int) and payload["status_code"] >= 400
    ]
    provider_thoughts_present = False
    for payload in structured_payloads:
        if payload.get("event") != "agent_messages":
            continue
        for message in payload.get("messages", []):
            for part in message.get("parts", []):
                if part.get("part_kind") == "thinking":
                    provider_thoughts_present = True
                    break
            if provider_thoughts_present:
                break
        if provider_thoughts_present:
            break

    announce_seen = False
    announce_step_before_tools = True
    for payload in structured_payloads:
        event = payload.get("event")
        if event == "agent_step":
            announce_seen = True
        if event == "tool_call":
            tool_name = payload.get("tool")
            if tool_name == "announce_step":
                announce_seen = True
                continue
            if not announce_seen:
                announce_step_before_tools = False
                break

    tripletex_write_count = sum(
        1 for payload in tripletex_requests if payload.get("method") in {"POST", "PUT", "DELETE"}
    )
    run_id = next(
        (
            payload.get("run_id")
            for payload in structured_payloads
            if payload.get("run_id")
        ),
        cloud_logs.get("run_id"),
    )

    checks = {
        "response_completed": response.status_code == 200 and response_body == {"status": "completed"},
        "required_log_events_present": all(event in event_sequence for event in scenario.expected_log_events),
        "required_tools_present": all(tool in tool_sequence for tool in scenario.expected_tools),
        "forbidden_tools_absent": all(tool not in tool_sequence for tool in scenario.forbidden_tools),
        "announce_step_before_tools": announce_step_before_tools,
        "provider_thoughts_logged": (not scenario.expect_provider_thoughts) or provider_thoughts_present,
        "max_4xx_respected": len(tripletex_error_statuses) <= scenario.max_tolerated_4xx,
        "write_expectation_matches": (
            scenario.expect_tripletex_write is None
            or (scenario.expect_tripletex_write and tripletex_write_count > 0)
            or (scenario.expect_tripletex_write is False and tripletex_write_count == 0)
        ),
    }

    return {
        "scenario_id": scenario.scenario_id,
        "trace_token": trace_token,
        "run_id": run_id,
        "response_status_code": response.status_code,
        "response_body": sanitize_for_artifact(response_body),
        "event_sequence": event_sequence,
        "tool_sequence": tool_sequence,
        "tool_argument_summary": tool_argument_summary,
        "tripletex_http_call_count": len(tripletex_requests),
        "tripletex_write_count": tripletex_write_count,
        "tripletex_http_error_statuses": tripletex_error_statuses,
        "announce_step_before_tools": announce_step_before_tools,
        "provider_thoughts_present": provider_thoughts_present,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
    }


def write_scenario_artifacts(
    *,
    scenario: ScenarioSpec,
    trace_token: str,
    request_payload: dict[str, Any],
    response: requests.Response,
    cloud_logs: dict[str, Any],
    observed_summary: dict[str, Any],
) -> dict[str, Path]:
    scenario_dir = REALISTIC_SCENARIO_DIR / "evaluator_like" / slugify(scenario.category) / scenario.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = scenario_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    scenario_spec_path = run_dir / "scenario_spec.json"
    request_response_path = run_dir / "request_response.json"
    cloud_logging_path = run_dir / "cloud_logging.json"
    observed_summary_path = run_dir / "observed_summary.json"

    scenario_spec = {
        **sanitize_for_artifact(asdict(scenario)),
        "trace_token": trace_token,
        "attachment_manifest": [
            {"filename": filename, "mime_type": "text/markdown"}
            for filename in scenario.attachment_filenames
        ],
    }
    scenario_spec_path.write_text(json.dumps(scenario_spec, indent=2, ensure_ascii=True), encoding="utf-8")

    try:
        response_body: Any = response.json()
    except ValueError:
        response_body = {"raw_text": response.text}

    request_response = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": scenario.scenario_id,
        "trace_token": trace_token,
        "request": sanitize_for_artifact(request_payload),
        "response": {
            "status_code": response.status_code,
            "headers": sanitize_for_artifact(dict(response.headers)),
            "body": sanitize_for_artifact(response_body),
        },
    }
    request_response_path.write_text(json.dumps(request_response, indent=2, ensure_ascii=True), encoding="utf-8")
    cloud_logging_path.write_text(
        json.dumps(sanitize_for_artifact(cloud_logs), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    observed_summary_path.write_text(
        json.dumps(sanitize_for_artifact(observed_summary), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    return {
        "scenario_spec": scenario_spec_path,
        "request_response": request_response_path,
        "cloud_logging": cloud_logging_path,
        "observed_summary": observed_summary_path,
    }
