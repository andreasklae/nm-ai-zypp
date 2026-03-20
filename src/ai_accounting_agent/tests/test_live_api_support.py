from __future__ import annotations

from pathlib import Path

from ai_accounting_agent.tests.live_api_support import (
    REALISTIC_SCENARIO_DIR,
    ScenarioSpec,
    cleanup_realistic_scenarios,
    sanitize_for_artifact,
    summarize_observed_behavior,
)


class FakeResponse:
    def __init__(self, status_code: int, body: dict[str, object]) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict[str, object]:
        return self._body


def test_sanitize_for_artifact_redacts_sensitive_values() -> None:
    payload = sanitize_for_artifact(
        {
            "session_token": "secret",
            "x-api-key": "token",
            "nested": {"content_base64": "abc"},
        }
    )

    assert payload["session_token"] == "[redacted]"
    assert payload["x-api-key"] == "[redacted]"
    assert payload["nested"]["content_base64"] == "[redacted]"


def test_summarize_observed_behavior_extracts_expected_checks() -> None:
    scenario = ScenarioSpec(
        scenario_id="demo",
        category="demo",
        prompt_template="trace_token={token}",
        expected_behavior=["demo"],
        success_criteria=["demo"],
        score_focus=["demo"],
        expected_tools=["create_customer"],
        expect_tripletex_write=True,
    )
    response = FakeResponse(200, {"status": "completed"})
    cloud_logs = {
        "status": "ok",
        "entries": [
            {"timestamp": "2026-03-20T12:00:00Z", "jsonPayload": {"event": "request_received", "run_id": "run-1"}},
            {"timestamp": "2026-03-20T12:00:01Z", "jsonPayload": {"event": "agent_step", "run_id": "run-1"}},
            {
                "timestamp": "2026-03-20T12:00:02Z",
                "jsonPayload": {
                    "event": "tool_call",
                    "run_id": "run-1",
                    "tool": "announce_step",
                    "arguments": {"kwargs": {}},
                },
            },
            {
                "timestamp": "2026-03-20T12:00:03Z",
                "jsonPayload": {
                    "event": "tool_call",
                    "run_id": "run-1",
                    "tool": "create_customer",
                    "arguments": {"kwargs": {"payload": {"name": "Demo"}}},
                },
            },
            {
                "timestamp": "2026-03-20T12:00:04Z",
                "jsonPayload": {
                    "event": "tripletex_http_request",
                    "run_id": "run-1",
                    "method": "POST",
                },
            },
            {
                "timestamp": "2026-03-20T12:00:05Z",
                "jsonPayload": {
                    "event": "tripletex_http_response",
                    "run_id": "run-1",
                    "status_code": 200,
                },
            },
            {
                "timestamp": "2026-03-20T12:00:06Z",
                "jsonPayload": {
                    "event": "agent_messages",
                    "run_id": "run-1",
                    "messages": [
                        {
                            "parts": [
                                {"part_kind": "thinking", "content": "plan"},
                                {"part_kind": "text", "content": "done"},
                            ]
                        }
                    ],
                },
            },
            {"timestamp": "2026-03-20T12:00:07Z", "jsonPayload": {"event": "task_complete", "run_id": "run-1"}},
        ],
    }

    observed = summarize_observed_behavior(
        scenario=scenario,
        trace_token="abc123",
        response=response,
        cloud_logs=cloud_logs,
    )

    assert observed["run_id"] == "run-1"
    assert observed["tool_sequence"] == ["announce_step", "create_customer"]
    assert observed["tripletex_write_count"] == 1
    assert observed["provider_thoughts_present"] is True
    assert observed["checks"]["announce_step_before_tools"] is True
    assert observed["checks"]["required_tools_present"] is True
    assert observed["all_checks_passed"] is True


def test_cleanup_realistic_scenarios_removes_previous_artifacts(tmp_path, monkeypatch) -> None:
    fake_root = tmp_path / "realistic_scenarios"
    fake_root.mkdir(parents=True, exist_ok=True)
    artifact = fake_root / "demo.json"
    artifact.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("ai_accounting_agent.tests.live_api_support.REALISTIC_SCENARIO_DIR", fake_root)

    cleanup_realistic_scenarios()

    assert not Path(fake_root).exists()
