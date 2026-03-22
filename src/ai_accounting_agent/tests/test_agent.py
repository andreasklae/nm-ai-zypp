from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.exceptions import ModelRetry

from ai_accounting_agent.agent import AgentExecutionResult, AgentTaskError, execute_agent
from ai_accounting_agent.attachment_extractor import AttachmentExtractionReport, ExtractedAttachment
from ai_accounting_agent.schemas import PreparedAttachment, SolveRequest


def _fake_extraction_report() -> AttachmentExtractionReport:
    return AttachmentExtractionReport(
        summary="Extracted 1 attachment (invoice.pdf) via subagent.",
        extractor_model="gemini-2.5-flash",
        attachments=[
            ExtractedAttachment(
                filename="invoice.pdf",
                mime_type="application/pdf",
                extraction_method="subagent",
                document_type="invoice",
                summary="Supplier invoice for 5000 NOK.",
                extracted_text="Invoice #INV-001\nAmount: 5000 NOK",
                key_fields=[],
                warnings=[],
            )
        ],
    )


def test_execute_agent_builds_binary_prompt_content(monkeypatch) -> None:
    captured: dict[str, object] = {}
    logged_messages: list[dict[str, object]] = []

    class FakeRunResult:
        output = "done"

        def new_messages(self):
            return [{"kind": "response"}]

        def usage(self):
            return {"total_tokens": 77}

    class FakeAgent:
        async def run(self, content, deps):
            captured["content"] = content
            captured["deps"] = deps
            return FakeRunResult()

    def fake_log_agent_messages(*, run_id: str, model: str, messages, usage):
        logged_messages.append({"run_id": run_id, "model": model, "messages": list(messages), "usage": usage})

    async def fake_extract_attachments(*, run_id: str, task_prompt: str, attachments, model=None):
        return _fake_extraction_report()

    monkeypatch.setattr("ai_accounting_agent.agent._build_agent", lambda model: FakeAgent())
    monkeypatch.setattr("ai_accounting_agent.agent.log_agent_messages", fake_log_agent_messages)
    monkeypatch.setattr("ai_accounting_agent.agent.extract_attachments", fake_extract_attachments)

    request = SolveRequest.model_validate(
        {
            "prompt": "Les vedlegget og fullfor.",
            "files": [],
            "tripletex_credentials": {
                "base_url": "https://example.tripletex.dev/v2",
                "session_token": "token",
            },
        }
    )
    attachment = PreparedAttachment(filename="invoice.pdf", mime_type="application/pdf", data=b"%PDF-1.4")

    result = asyncio.run(execute_agent(request=request, attachments=[attachment], run_id="run-123"))

    assert isinstance(result, AgentExecutionResult)
    assert result.output == "done"
    # Prompt now starts with pre-extracted block and ends with the original prompt
    first_content = captured["content"][0]
    assert isinstance(first_content, str)
    assert "[ATTACHMENT PRE-EXTRACTED" in first_content
    assert "Les vedlegget og fullfor." in first_content
    assert isinstance(captured["content"][1], BinaryContent)
    assert logged_messages == [
        {
            "run_id": "run-123",
            "model": "gemini-3.1-pro-preview",
            "messages": [{"kind": "response"}],
            "usage": {"total_tokens": 77},
        }
    ]


def test_execute_agent_wraps_task_failures_and_logs_partial_messages(monkeypatch) -> None:
    logged_messages: list[dict[str, object]] = []

    class FakeAgent:
        async def run(self, content, deps):
            raise ModelRetry("retry with a better payload")

    @contextmanager
    def fake_capture_run_messages():
        yield [{"kind": "response", "parts": [{"part_kind": "thinking", "content": "plan"}]}]

    def fake_log_agent_messages(*, run_id: str, model: str, messages, usage):
        logged_messages.append({"run_id": run_id, "model": model, "messages": list(messages), "usage": usage})

    monkeypatch.setattr("ai_accounting_agent.agent._build_agent", lambda model: FakeAgent())
    monkeypatch.setattr("ai_accounting_agent.agent.capture_run_messages", fake_capture_run_messages)
    monkeypatch.setattr("ai_accounting_agent.agent.log_agent_messages", fake_log_agent_messages)

    request = SolveRequest.model_validate(
        {
            "prompt": "Prover a fullfor.",
            "files": [],
            "tripletex_credentials": {
                "base_url": "https://example.tripletex.dev/v2",
                "session_token": "token",
            },
        }
    )

    with pytest.raises(AgentTaskError) as exc_info:
        asyncio.run(execute_agent(request=request, attachments=[], run_id="run-456"))

    assert exc_info.value.error_type == "ModelRetry"
    assert "retry with a better payload" in exc_info.value.error_message
    assert logged_messages == [
        {
            "run_id": "run-456",
            "model": "gemini-3.1-pro-preview",
            "messages": [{"kind": "response", "parts": [{"part_kind": "thinking", "content": "plan"}]}],
            "usage": None,
        }
    ]


def test_execute_agent_logs_attachment_extractor_usage(monkeypatch) -> None:
    logged_events: list[dict[str, object]] = []

    class FakeRunResult:
        output = "done"

        def new_messages(self):
            return [{"kind": "response", "parts": [{"part_kind": "tool-call", "tool_name": "extract_attachment_data"}]}]

        def usage(self):
            return {"total_tokens": 12}

    class FakeAgent:
        async def run(self, content, deps):
            deps.step_state.attachment_extractor_available = True
            return FakeRunResult()

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        record = {"event": event, "severity": severity, **payload}
        logged_events.append(record)
        return record

    async def fake_extract_attachments(*, run_id: str, task_prompt: str, attachments, model=None):
        return _fake_extraction_report()

    monkeypatch.setattr("ai_accounting_agent.agent._build_agent", lambda model: FakeAgent())
    monkeypatch.setattr("ai_accounting_agent.agent.log_event", fake_log_event)
    monkeypatch.setattr("ai_accounting_agent.agent.extract_attachments", fake_extract_attachments)

    request = SolveRequest.model_validate(
        {
            "prompt": "Les vedlegget og fullfor.",
            "files": [],
            "tripletex_credentials": {
                "base_url": "https://example.tripletex.dev/v2",
                "session_token": "token",
            },
        }
    )
    attachment = PreparedAttachment(filename="invoice.pdf", mime_type="application/pdf", data=b"%PDF-1.4")

    asyncio.run(execute_agent(request=request, attachments=[attachment], run_id="run-attachment"))

    attachment_event = next(event for event in logged_events if event["event"] == "attachment_extraction_status")
    assert attachment_event["extractor_available"] is True
    assert attachment_event["extractor_used"] is True
    assert attachment_event["fallback_used"] is False


def test_execute_agent_logs_attachment_extractor_fallback(monkeypatch) -> None:
    logged_events: list[dict[str, object]] = []

    class FakeRunResult:
        output = "done"

        def new_messages(self):
            return [{"kind": "response", "parts": []}]

        def usage(self):
            return {"total_tokens": 12}

    class FakeAgent:
        async def run(self, content, deps):
            deps.step_state.attachment_extractor_available = False
            return FakeRunResult()

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        record = {"event": event, "severity": severity, **payload}
        logged_events.append(record)
        return record

    async def fake_extract_attachments(*, run_id: str, task_prompt: str, attachments, model=None):
        return _fake_extraction_report()

    monkeypatch.setattr("ai_accounting_agent.agent._build_agent", lambda model: FakeAgent())
    monkeypatch.setattr("ai_accounting_agent.agent.log_event", fake_log_event)
    monkeypatch.setattr("ai_accounting_agent.agent.extract_attachments", fake_extract_attachments)

    request = SolveRequest.model_validate(
        {
            "prompt": "Les vedlegget og fullfor.",
            "files": [],
            "tripletex_credentials": {
                "base_url": "https://example.tripletex.dev/v2",
                "session_token": "token",
            },
        }
    )
    attachment = PreparedAttachment(filename="invoice.pdf", mime_type="application/pdf", data=b"%PDF-1.4")

    asyncio.run(execute_agent(request=request, attachments=[attachment], run_id="run-fallback"))

    attachment_event = next(event for event in logged_events if event["event"] == "attachment_extraction_status")
    assert attachment_event["extractor_available"] is False
    assert attachment_event["extractor_used"] is False
    assert attachment_event["fallback_used"] is True
    assert attachment_event["fallback_reason"] == "extractor_unavailable"
