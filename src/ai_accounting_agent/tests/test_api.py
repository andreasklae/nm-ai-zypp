from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from ai_accounting_agent.agent import AgentExecutionResult, AgentTaskError
from ai_accounting_agent.main import app


client = TestClient(app)


def _request_payload() -> dict[str, object]:
    return {
        "prompt": "Opprett ingenting. Bare fullfor testkallet.",
        "files": [],
        "tripletex_credentials": {
            "base_url": "https://example.tripletex.dev/v2",
            "session_token": "session-token",
        },
    }


def test_solve_returns_completed(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    monkeypatch.delenv("AI_ACCOUNTING_AGENT_API_KEY", raising=False)

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        events.append({"event": event, "severity": severity, **payload})

    async def fake_execute_agent(*, request, attachments, run_id: str, model: str):
        assert request.prompt == "Opprett ingenting. Bare fullfor testkallet."
        assert attachments == []
        assert run_id
        assert model == "gemini-3.1-pro-preview"
        return AgentExecutionResult(
            output="Task completed",
            model=model,
            messages=[{"kind": "response"}],
            usage={"total_tokens": 123},
        )

    monkeypatch.setattr("ai_accounting_agent.main.log_event", fake_log_event)
    monkeypatch.setattr("ai_accounting_agent.main.execute_agent", fake_execute_agent)

    response = client.post("/solve", json=_request_payload())

    assert response.status_code == 200
    assert response.json() == {"status": "completed"}
    assert [event["event"] for event in events] == ["request_received", "task_complete"]
    assert events[0]["prompt"] == "Opprett ingenting. Bare fullfor testkallet."
    assert events[1]["agent_output"] == "Task completed"


def test_solve_requires_api_key_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("AI_ACCOUNTING_AGENT_API_KEY", "secret-token")
    async def _fake(**kwargs):
        return AgentExecutionResult(output="ok", model="gemini-3.1-pro-preview", messages=[], usage=None)

    monkeypatch.setattr("ai_accounting_agent.main.execute_agent", _fake)

    unauthorized = client.post("/solve", json=_request_payload())
    wrong_token = client.post(
        "/solve",
        json=_request_payload(),
        headers={"x-api-key": "wrong-token"},
    )
    x_api_key_authorized = client.post(
        "/solve",
        json=_request_payload(),
        headers={"x-api-key": "secret-token"},
    )
    bearer_authorized = client.post(
        "/solve",
        json=_request_payload(),
        headers={"Authorization": "Bearer secret-token"},
    )

    assert unauthorized.status_code == 401
    assert wrong_token.status_code == 401
    assert x_api_key_authorized.status_code == 200
    assert x_api_key_authorized.json() == {"status": "completed"}
    assert bearer_authorized.status_code == 200
    assert bearer_authorized.json() == {"status": "completed"}


def test_solve_decodes_attachments_and_logs_metadata(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    monkeypatch.delenv("AI_ACCOUNTING_AGENT_API_KEY", raising=False)

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        events.append({"event": event, "severity": severity, **payload})

    async def fake_execute_agent(*, request, attachments, run_id: str, model: str):
        assert len(attachments) == 1
        assert attachments[0].filename == "receipt.txt"
        assert attachments[0].mime_type == "text/plain"
        assert attachments[0].data == b"hello from attachment"
        return AgentExecutionResult(output="done", model=model, messages=[], usage=None)

    monkeypatch.setattr("ai_accounting_agent.main.log_event", fake_log_event)
    monkeypatch.setattr("ai_accounting_agent.main.execute_agent", fake_execute_agent)

    payload = _request_payload()
    payload["files"] = [
        {
            "filename": "receipt.txt",
            "content_base64": base64.b64encode(b"hello from attachment").decode(),
            "mime_type": "text/plain",
        }
    ]

    response = client.post("/solve", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "completed"}
    assert events[0]["file_count"] == 1
    assert events[0]["files"] == [
        {
            "filename": "receipt.txt",
            "mime_type": "text/plain",
            "size_bytes": 21,
            "sha256": "d79147458348c797501ba4f6f3ad9c5c339834671276b4adea68b57bf7f9e649",
        }
    ]


def test_solve_logs_graceful_task_error_and_returns_completed(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    monkeypatch.delenv("AI_ACCOUNTING_AGENT_API_KEY", raising=False)

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        events.append({"event": event, "severity": severity, **payload})

    async def fake_execute_agent(**kwargs):
        raise AgentTaskError(
            model="gemini-3.1-pro-preview",
            messages=[{"kind": "response"}],
            usage=None,
            error_type="UnexpectedModelBehavior",
            error_message="boom",
        )

    error_client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr("ai_accounting_agent.main.log_event", fake_log_event)
    monkeypatch.setattr("ai_accounting_agent.main.execute_agent", fake_execute_agent)

    response = error_client.post("/solve", json=_request_payload())

    assert response.status_code == 200
    assert response.json() == {"status": "completed"}
    assert [event["event"] for event in events] == ["request_received", "task_error", "task_complete"]
    assert events[1]["error_type"] == "UnexpectedModelBehavior"
    assert events[1]["error_message"] == "boom"
    assert events[1]["graceful_completion"] is True
    assert events[2]["completed_with_recovery"] is True


def test_solve_keeps_500_for_unexpected_server_error(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    monkeypatch.delenv("AI_ACCOUNTING_AGENT_API_KEY", raising=False)

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        events.append({"event": event, "severity": severity, **payload})

    async def fake_execute_agent(**kwargs):
        raise RuntimeError("boom")

    error_client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr("ai_accounting_agent.main.log_event", fake_log_event)
    monkeypatch.setattr("ai_accounting_agent.main.execute_agent", fake_execute_agent)

    response = error_client.post("/solve", json=_request_payload())

    assert response.status_code == 500
    assert [event["event"] for event in events] == ["request_received", "task_error"]
    assert events[1]["error_type"] == "RuntimeError"
    assert events[1]["error_message"] == "boom"
