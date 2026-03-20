from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from astar_island.cloud import detect_gcloud_status


@dataclass(slots=True)
class BatchJobHandle:
    backend: str
    status: str
    job_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class BatchBackend(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def submit_prediction_batch(self, artifact_dir: Path, *, runs_per_seed: int) -> BatchJobHandle:
        ...


class LocalBatchBackend:
    name = "local"

    def available(self) -> bool:
        return True

    def submit_prediction_batch(self, artifact_dir: Path, *, runs_per_seed: int) -> BatchJobHandle:
        return BatchJobHandle(
            backend=self.name,
            status="not_started",
            metadata={
                "artifact_dir": str(artifact_dir),
                "runs_per_seed": str(runs_per_seed),
                "note": "Local execution seam only; the full simulator batch worker is a later milestone.",
            },
        )


class GcpBatchBackend:
    name = "gcp"

    def available(self) -> bool:
        status = detect_gcloud_status()
        return status.active_account is not None or status.has_adc

    def submit_prediction_batch(self, artifact_dir: Path, *, runs_per_seed: int) -> BatchJobHandle:
        if not self.available():
            raise RuntimeError("No active gcloud account or ADC credentials found.")
        return BatchJobHandle(
            backend=self.name,
            status="stub",
            metadata={
                "artifact_dir": str(artifact_dir),
                "runs_per_seed": str(runs_per_seed),
                "note": "GCP batch seam is defined, but real Monte Carlo fan-out is intentionally deferred.",
            },
        )
