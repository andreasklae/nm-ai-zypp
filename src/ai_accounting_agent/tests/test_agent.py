from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.exceptions import ModelRetry

from ai_accounting_agent.agent import AgentExecutionResult, AgentTaskError, execute_agent
from ai_accounting_agent.schemas import PreparedAttachment, SolveRequest


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
        logged_messages.append(
            {"run_id": run_id, "model": model, "messages": list(messages), "usage": usage}
        )

    monkeypatch.setattr("ai_accounting_agent.agent._build_agent", lambda model: FakeAgent())
    monkeypatch.setattr("ai_accounting_agent.agent.log_agent_messages", fake_log_agent_messages)

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
    assert captured["content"][0] == "Les vedlegget og fullfor."
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
        logged_messages.append(
            {"run_id": run_id, "model": model, "messages": list(messages), "usage": usage}
        )

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
