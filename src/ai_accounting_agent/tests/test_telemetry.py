from __future__ import annotations

import asyncio

import pytest

from ai_accounting_agent import telemetry


def test_serialize_for_logging_redacts_sensitive_values() -> None:
    payload = telemetry.serialize_for_logging(
        {
            "session_token": "secret",
            "Authorization": "Bearer token",
            "prompt": "hello",
            "nested": {"x-api-key": "abc"},
        }
    )

    assert payload["session_token"] == "[REDACTED]"
    assert payload["Authorization"] == "[REDACTED]"
    assert payload["prompt"] == "hello"
    assert payload["nested"]["x-api-key"] == "[REDACTED]"


def test_log_tool_logs_success_and_error(monkeypatch) -> None:
    events = []

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        events.append({"event": event, "severity": severity, **payload})

    class FakeDeps:
        run_id = "run-123"

    class FakeCtx:
        deps = FakeDeps()

    monkeypatch.setattr(telemetry, "log_event", fake_log_event)

    @telemetry.log_tool
    async def successful_tool(ctx, invoice_id: int) -> str:
        return f"invoice-{invoice_id}"

    @telemetry.log_tool
    async def failing_tool(ctx, invoice_id: int) -> str:
        raise ValueError("bad tool")

    assert asyncio.run(successful_tool(FakeCtx(), invoice_id=7)) == "invoice-7"

    with pytest.raises(ValueError):
        asyncio.run(failing_tool(FakeCtx(), invoice_id=8))

    assert [event["event"] for event in events] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_error",
    ]
    assert {event["run_id"] for event in events} == {"run-123"}
