from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from astar_island.models import (
    BacktestSummary,
    DeliveryRunManifest,
    EvaluationReport,
    ObservationCollection,
    ObservationPlan,
    PredictionBundle,
    RoundId,
    RoundDetail,
    RoundSummary,
    ScoreSnapshot,
    SubmissionBundle,
)


ARTIFACT_FILENAMES = {
    "round_summary": "round_summary.json",
    "round_detail": "round_detail.json",
    "observation_plan": "observation_plan.json",
    "observations": "observations.json",
    "predictions": "predictions.json",
    "submission_receipts": "submission_receipts.json",
    "run_manifest": "run_manifest.json",
    "score_snapshot": "score_snapshot.json",
    "evaluation_report": "evaluation_report.json",
    "backtest_summary": "backtest_summary.json",
}

ModelT = TypeVar("ModelT", bound=BaseModel)


def round_artifact_dir(base_dir: Path, round_id: RoundId, timestamp: str) -> Path:
    return base_dir / f"round_{round_id}" / timestamp


def write_model(path: Path, model: BaseModel) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return path


def read_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def save_round_artifacts(artifact_dir: Path, summary: RoundSummary | None, detail: RoundDetail) -> dict[str, Path]:
    paths = {}
    if summary is not None:
        paths["round_summary"] = write_model(artifact_dir / ARTIFACT_FILENAMES["round_summary"], summary)
    paths["round_detail"] = write_model(artifact_dir / ARTIFACT_FILENAMES["round_detail"], detail)
    return paths


def load_round_detail(artifact_dir: Path) -> RoundDetail:
    return read_model(artifact_dir / ARTIFACT_FILENAMES["round_detail"], RoundDetail)


def save_observation_plan(artifact_dir: Path, plan: ObservationPlan) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["observation_plan"], plan)


def load_observation_plan(artifact_dir: Path) -> ObservationPlan:
    return read_model(artifact_dir / ARTIFACT_FILENAMES["observation_plan"], ObservationPlan)


def load_optional_observation_plan(artifact_dir: Path) -> ObservationPlan | None:
    path = artifact_dir / ARTIFACT_FILENAMES["observation_plan"]
    if not path.exists():
        return None
    return read_model(path, ObservationPlan)


def save_observations(artifact_dir: Path, observations: ObservationCollection) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["observations"], observations)


def load_observations(artifact_dir: Path) -> ObservationCollection | None:
    path = artifact_dir / ARTIFACT_FILENAMES["observations"]
    if not path.exists():
        return None
    return read_model(path, ObservationCollection)


def save_predictions(artifact_dir: Path, predictions: PredictionBundle) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["predictions"], predictions)


def load_predictions(artifact_dir: Path) -> PredictionBundle:
    return read_model(artifact_dir / ARTIFACT_FILENAMES["predictions"], PredictionBundle)


def save_submission_receipts(artifact_dir: Path, receipts: SubmissionBundle) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["submission_receipts"], receipts)


def load_submission_receipts(artifact_dir: Path) -> SubmissionBundle | None:
    path = artifact_dir / ARTIFACT_FILENAMES["submission_receipts"]
    if not path.exists():
        return None
    return read_model(path, SubmissionBundle)


def save_run_manifest(artifact_dir: Path, manifest: DeliveryRunManifest) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["run_manifest"], manifest)


def load_run_manifest(artifact_dir: Path) -> DeliveryRunManifest | None:
    path = artifact_dir / ARTIFACT_FILENAMES["run_manifest"]
    if not path.exists():
        return None
    return read_model(path, DeliveryRunManifest)


def save_score_snapshot(artifact_dir: Path, snapshot: ScoreSnapshot) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["score_snapshot"], snapshot)


def load_score_snapshot(artifact_dir: Path) -> ScoreSnapshot | None:
    path = artifact_dir / ARTIFACT_FILENAMES["score_snapshot"]
    if not path.exists():
        return None
    return read_model(path, ScoreSnapshot)


def save_evaluation_report(artifact_dir: Path, report: EvaluationReport) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["evaluation_report"], report)


def load_evaluation_report(artifact_dir: Path) -> EvaluationReport | None:
    path = artifact_dir / ARTIFACT_FILENAMES["evaluation_report"]
    if not path.exists():
        return None
    return read_model(path, EvaluationReport)


def save_backtest_summary(artifact_dir: Path, summary: BacktestSummary) -> Path:
    return write_model(artifact_dir / ARTIFACT_FILENAMES["backtest_summary"], summary)


def load_backtest_summary(artifact_dir: Path) -> BacktestSummary | None:
    path = artifact_dir / ARTIFACT_FILENAMES["backtest_summary"]
    if not path.exists():
        return None
    return read_model(path, BacktestSummary)


def dump_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return path
