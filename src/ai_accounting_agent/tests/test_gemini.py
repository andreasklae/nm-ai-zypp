from __future__ import annotations

from ai_accounting_agent.gemini import DEFAULT_GEMINI_MODEL, build_google_model, default_model_settings


def test_default_model_settings_enable_high_thinking() -> None:
    settings = default_model_settings()

    assert settings["parallel_tool_calls"] is False
    assert settings["google_thinking_config"]["include_thoughts"] is True
    assert settings["google_thinking_config"]["thinking_level"] == "HIGH"


def test_build_google_model_uses_requested_model() -> None:
    model = build_google_model(model=DEFAULT_GEMINI_MODEL, api_key="test-key")

    assert model.model_name == DEFAULT_GEMINI_MODEL
