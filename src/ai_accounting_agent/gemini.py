from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.google import GoogleModelSettings
from pydantic_ai.providers.google import GoogleProvider

ENV_PATH = Path(__file__).with_name(".env")
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"

load_dotenv(ENV_PATH)


def build_google_model(model: str = DEFAULT_GEMINI_MODEL, api_key: str | None = None) -> GoogleModel:
    provider = GoogleProvider(api_key=api_key or os.environ["GEMINI_API_KEY"])
    return GoogleModel(model_name=model, provider=provider)


def default_model_settings() -> GoogleModelSettings:
    return GoogleModelSettings(
        parallel_tool_calls=False,
        google_thinking_config={
            "include_thoughts": True,
            "thinking_level": "HIGH",
        },
    )
