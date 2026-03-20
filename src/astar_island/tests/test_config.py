from __future__ import annotations

from astar_island import config


def test_load_settings_reads_repo_dotenv_and_allows_env_override(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ASTAR_ISLAND_ACCESS_TOKEN=dotenv-token\nASTAR_ISLAND_BASE_URL=https://example.invalid/astar\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", env_path)
    monkeypatch.setenv("ASTAR_ISLAND_BASE_URL", "https://override.invalid/astar")

    settings = config.load_settings()

    assert settings.access_token == "dotenv-token"
    assert settings.base_url == "https://override.invalid/astar"
