from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from astar_island.config import AstarIslandSettings


@dataclass(slots=True)
class GcloudStatus:
    active_account: str | None
    active_project: str | None
    has_adc: bool


def _run_command(command: list[str]) -> tuple[bool, str]:
    if not shutil.which(command[0]):
        return False, ""
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        return False, completed.stderr.strip() or completed.stdout.strip()
    return True, completed.stdout.strip()


def detect_gcloud_status() -> GcloudStatus:
    account_ok, account_output = _run_command(
        ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"]
    )
    project_ok, project_output = _run_command(["gcloud", "config", "get-value", "project"])
    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    has_adc = bool(adc_path and Path(adc_path).exists())
    return GcloudStatus(
        active_account=account_output if account_ok and account_output else None,
        active_project=project_output if project_ok and project_output and project_output != "(unset)" else None,
        has_adc=has_adc,
    )


def gcs_uri(settings: AstarIslandSettings, artifact_dir: Path) -> str:
    if not settings.gcs_bucket:
        raise RuntimeError("ASTAR_ISLAND_GCS_BUCKET is required for GCS sync.")
    prefix = settings.gcs_prefix.strip("/")
    suffix = artifact_dir.as_posix().lstrip("./")
    if prefix:
        return f"gs://{settings.gcs_bucket}/{prefix}/{suffix}"
    return f"gs://{settings.gcs_bucket}/{suffix}"


def sync_directory_to_gcs(settings: AstarIslandSettings, artifact_dir: Path) -> str:
    uri = gcs_uri(settings, artifact_dir)
    ok, output = _run_command(["gcloud", "storage", "cp", "--recursive", str(artifact_dir), uri])
    if not ok:
        raise RuntimeError(output or "Failed to sync artifacts to GCS.")
    return uri


def sync_directory_from_gcs(settings: AstarIslandSettings, artifact_dir: Path) -> str:
    uri = gcs_uri(settings, artifact_dir)
    ok, output = _run_command(["gcloud", "storage", "cp", "--recursive", uri, str(artifact_dir)])
    if not ok:
        raise RuntimeError(output or "Failed to sync artifacts from GCS.")
    return uri
