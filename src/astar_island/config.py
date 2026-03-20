from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


DEFAULT_BASE_URL = "https://api.ainm.no/astar-island"
DEFAULT_DATA_DIR = Path("data") / "astar_island"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / "ai_accounting_agent" / ".env"


@dataclass(slots=True)
class AstarIslandSettings:
    base_url: str = DEFAULT_BASE_URL
    access_token: str = ""
    data_dir: Path = DEFAULT_DATA_DIR
    gcs_bucket: str = ""
    gcs_prefix: str = ""

    @property
    def has_live_api_access(self) -> bool:
        return bool(self.access_token)

    @property
    def gcs_enabled(self) -> bool:
        return bool(self.gcs_bucket)


def load_settings() -> AstarIslandSettings:
    env_values: dict[str, str] = {}
    if DEFAULT_ENV_PATH.exists():
        env_values = {
            key: value
            for key, value in dotenv_values(DEFAULT_ENV_PATH).items()
            if value is not None
        }
    merged = {**env_values, **os.environ}

    data_dir_value = merged.get("ASTAR_ISLAND_DATA_DIR", "").strip()
    data_dir = Path(data_dir_value) if data_dir_value else DEFAULT_DATA_DIR
    return AstarIslandSettings(
        base_url=merged.get("ASTAR_ISLAND_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        access_token=merged.get("ASTAR_ISLAND_ACCESS_TOKEN", "").strip(),
        data_dir=data_dir,
        gcs_bucket=merged.get("ASTAR_ISLAND_GCS_BUCKET", "").strip(),
        gcs_prefix=merged.get("ASTAR_ISLAND_GCS_PREFIX", "").strip().strip("/"),
    )
