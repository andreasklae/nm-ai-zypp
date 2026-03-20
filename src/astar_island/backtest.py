from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import log
from pathlib import Path

from astar_island.batch import GcpBatchBackend
from astar_island.client import AstarIslandClient
from astar_island.cloud import sync_directory_to_gcs
from astar_island.config import AstarIslandSettings
from astar_island.delivery import validate_prediction_bundle
from astar_island.models import (
    CLASS_LABELS,
    BacktestSummary,
    DeliveryRunManifest,
    EvaluationReport,
    HoldoutMetrics,
    ModelComparisonSummary,
    ObservationCollection,
    ObservationSample,
    PredictorParameters,
    RoundDetail,
    RoundId,
    RoundSummary,
    ScoreSnapshot,
    utc_now_iso,
)
from astar_island.planner import build_observation_collection
from astar_island.predictor import ArchiveRound, BaselinePredictor, LegacyMechanicsPredictor
from astar_island.storage import (
    load_observations,
    load_round_detail,
    load_run_manifest,
    save_backtest_summary,
    save_evaluation_report,
    save_predictions,
    save_round_artifacts,
    save_run_manifest,
    save_score_snapshot,
)
from astar_island.terrain import terrain_code_to_class_index


@dataclass(slots=True)
class _RoundArtifact:
    artifact_dir: Path
    round_detail: RoundDetail
    observations: ObservationCollection | None
    round_summary: RoundSummary | None = None
    score_snapshot: ScoreSnapshot | None = None


def _matches_round_identifier(
    *, round_id: RoundId, candidate_id: RoundId, candidate_round_number: int | None = None
) -> bool:
    if candidate_id == round_id or str(candidate_id) == str(round_id):
        return True
    if candidate_round_number is not None:
        if candidate_round_number == round_id:
            return True
        if str(candidate_round_number) == str(round_id):
            return True
    return False


def _backtest_root(settings: AstarIslandSettings, explicit_dir: Path | None) -> Path:
    if explicit_dir is not None:
        return explicit_dir
    timestamp = utc_now_iso().replace(":", "").replace("-", "").replace("+", "")
    return settings.data_dir / "backtests" / timestamp


def _round_backtest_dir(root_dir: Path, round_id: RoundId) -> Path:
    return root_dir / f"round_{round_id}"


def _discover_local_round_artifacts(base_dir: Path) -> list[Path]:
    artifact_dirs: list[Path] = []
    for round_detail_path in base_dir.rglob("round_detail.json"):
        artifact_dir = round_detail_path.parent
        if "backtests" in artifact_dir.parts:
            continue
        artifact_dirs.append(artifact_dir)
    return sorted(set(artifact_dirs))


def _load_local_round_artifacts(base_dir: Path) -> list[_RoundArtifact]:
    artifacts: list[_RoundArtifact] = []
    for artifact_dir in _discover_local_round_artifacts(base_dir):
        try:
            round_detail = load_round_detail(artifact_dir)
        except Exception:
            continue
        artifacts.append(
            _RoundArtifact(
                artifact_dir=artifact_dir,
                round_detail=round_detail,
                observations=load_observations(artifact_dir),
            )
        )
    return artifacts


def _best_local_artifact_for_round(base_dir: Path, round_id: RoundId) -> _RoundArtifact | None:
    candidates = [
        artifact
        for artifact in _load_local_round_artifacts(base_dir)
        if _matches_round_identifier(
            round_id=round_id,
            candidate_id=artifact.round_detail.id,
            candidate_round_number=artifact.round_detail.round_number,
        )
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.observations.total_queries if item.observations is not None else -1,
            item.artifact_dir.stat().st_mtime,
        ),
    )


def _group_repeated_samples(observations: ObservationCollection) -> list[list[ObservationSample]]:
    grouped: dict[tuple[object, ...], list[ObservationSample]] = defaultdict(list)
    for sample in observations.samples:
        key = (
            sample.planned_query.phase,
            sample.planned_query.seed_index,
            sample.planned_query.viewport_x,
            sample.planned_query.viewport_y,
            sample.planned_query.viewport_w,
            sample.planned_query.viewport_h,
        )
        grouped[key].append(sample)
    return [
        sorted(samples, key=lambda item: (item.planned_query.repeat_index, item.planned_query.query_index))
        for samples in grouped.values()
    ]


def split_observations_for_holdout(
    observations: ObservationCollection,
) -> tuple[ObservationCollection, ObservationCollection | None]:
    train_samples: list[ObservationSample] = []
    test_samples: list[ObservationSample] = []
    for samples in _group_repeated_samples(observations):
        if len(samples) >= 2:
            train_samples.extend(samples[:-1])
            test_samples.append(samples[-1])
        else:
            train_samples.extend(samples)
    train = build_observation_collection(observations.round_id, train_samples)
    if not test_samples:
        return train, None
    return train, build_observation_collection(observations.round_id, test_samples)


def _observed_coverage_fraction(round_detail: RoundDetail, observations: ObservationCollection | None) -> float:
    if observations is None or not observations.samples:
        return 0.0
    unique_cells: set[tuple[int, int, int]] = set()
    for sample in observations.samples:
        seed_index = sample.planned_query.seed_index
        viewport = sample.result.viewport
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, _ in enumerate(row):
                unique_cells.add((seed_index, viewport.x + col_offset, viewport.y + row_offset))
    total_cells = round_detail.map_width * round_detail.map_height * round_detail.seeds_count
    return len(unique_cells) / total_cells if total_cells else 0.0


def _cell_mode_from_observations(observations: ObservationCollection) -> dict[tuple[int, int, int], int]:
    counts: dict[tuple[int, int, int], Counter[int]] = defaultdict(Counter)
    for sample in observations.samples:
        seed_index = sample.planned_query.seed_index
        viewport = sample.result.viewport
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, code in enumerate(row):
                key = (seed_index, viewport.x + col_offset, viewport.y + row_offset)
                counts[key][terrain_code_to_class_index(code)] += 1
    return {key: counter.most_common(1)[0][0] for key, counter in counts.items() if counter}


def evaluate_holdout_metrics(
    *,
    predictions,
    train_observations: ObservationCollection,
    test_observations: ObservationCollection,
) -> HoldoutMetrics:
    train_modes = _cell_mode_from_observations(train_observations)
    total_nll = 0.0
    total_count = 0
    changed_nll = 0.0
    changed_count = 0
    class_probabilities: dict[str, list[float]] = defaultdict(list)
    class_nll: dict[str, list[float]] = defaultdict(list)

    for sample in test_observations.samples:
        seed_index = sample.planned_query.seed_index
        viewport = sample.result.viewport
        tensor = predictions.seeds[seed_index].prediction
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, code in enumerate(row):
                class_index = terrain_code_to_class_index(code)
                world_x = viewport.x + col_offset
                world_y = viewport.y + row_offset
                probability = max(predictions.floor, tensor[world_y][world_x][class_index])
                nll = -log(probability)
                total_nll += nll
                total_count += 1
                label = CLASS_LABELS[class_index]
                class_probabilities[label].append(probability)
                class_nll[label].append(nll)
                train_mode = train_modes.get((seed_index, world_x, world_y))
                if train_mode is not None and train_mode != class_index:
                    changed_nll += nll
                    changed_count += 1

    class_average_probability = {
        label: round(sum(values) / len(values), 6) for label, values in class_probabilities.items() if values
    }
    class_average_nll = {label: round(sum(values) / len(values), 6) for label, values in class_nll.items() if values}
    return HoldoutMetrics(
        overall_nll=round(total_nll / max(1, total_count), 6),
        changed_cell_nll=round(changed_nll / changed_count, 6) if changed_count else None,
        observed_cell_count=total_count,
        changed_cell_count=changed_count,
        class_average_probability=class_average_probability,
        class_average_nll=class_average_nll,
    )


def _candidate_parameter_sets() -> list[PredictorParameters]:
    return [
        PredictorParameters(),
        PredictorParameters(
            prior_weight=0.26,
            exact_weight=0.24,
            relaxed_weight=0.12,
            round_transition_weight=0.20,
            transition_weight=0.28,
            nearest_weight=0.06,
            transition_alpha=1.6,
            round_transition_alpha=0.75,
            latent_proxy_strength=0.14,
            smoothing_base=0.022,
            smoothing_dynamic_scale=0.013,
        ),
        PredictorParameters(
            prior_weight=0.30,
            exact_weight=0.24,
            relaxed_weight=0.14,
            round_transition_weight=0.18,
            transition_weight=0.24,
            nearest_weight=0.06,
            transition_alpha=1.6,
            round_transition_alpha=0.75,
            latent_proxy_strength=0.12,
            smoothing_base=0.025,
            smoothing_dynamic_scale=0.015,
        ),
        PredictorParameters(
            prior_weight=0.34,
            exact_weight=0.22,
            relaxed_weight=0.14,
            round_transition_weight=0.12,
            transition_weight=0.16,
            nearest_weight=0.10,
            transition_alpha=1.8,
            round_transition_alpha=0.90,
            latent_proxy_strength=0.10,
            smoothing_base=0.03,
            smoothing_dynamic_scale=0.012,
        ),
        PredictorParameters(
            prior_weight=0.24,
            exact_weight=0.22,
            relaxed_weight=0.12,
            round_transition_weight=0.24,
            transition_weight=0.30,
            nearest_weight=0.05,
            transition_alpha=1.4,
            round_transition_alpha=0.65,
            latent_proxy_strength=0.10,
            smoothing_base=0.02,
            smoothing_dynamic_scale=0.012,
        ),
    ]


def _artifacts_with_holdout(local_artifacts: list[_RoundArtifact]) -> list[_RoundArtifact]:
    output: list[_RoundArtifact] = []
    for artifact in local_artifacts:
        if artifact.observations is None:
            continue
        _, holdout = split_observations_for_holdout(artifact.observations)
        if holdout is not None and holdout.samples:
            output.append(artifact)
    return output


def _archive_rounds_from_artifacts(
    artifacts: list[_RoundArtifact], *, exclude_round_id: RoundId | None = None
) -> list[ArchiveRound]:
    archive_rounds: list[ArchiveRound] = []
    for artifact in artifacts:
        if exclude_round_id is not None and artifact.round_detail.id == exclude_round_id:
            continue
        if artifact.observations is None:
            continue
        archive_rounds.append((artifact.round_detail, artifact.observations))
    return archive_rounds


def fit_predictor_parameters(local_artifacts: list[_RoundArtifact]) -> PredictorParameters:
    return _fit_predictor_parameters(local_artifacts)


def _evaluate_parameters(
    parameters: PredictorParameters,
    holdout_artifacts: list[_RoundArtifact],
    candidate_artifacts: list[_RoundArtifact],
) -> float | None:
    scores: list[float] = []
    for artifact in holdout_artifacts:
        train_observations, test_observations = split_observations_for_holdout(artifact.observations)
        if test_observations is None:
            continue
        archive_rounds = _archive_rounds_from_artifacts(candidate_artifacts, exclude_round_id=artifact.round_detail.id)
        predictor = BaselinePredictor(parameters=parameters, archive_rounds=archive_rounds)
        predictions = predictor.predict(artifact.round_detail, train_observations)
        metrics = evaluate_holdout_metrics(
            predictions=predictions,
            train_observations=train_observations,
            test_observations=test_observations,
        )
        scores.append(metrics.changed_cell_nll or metrics.overall_nll)
    if not scores:
        return None
    return sum(scores) / len(scores)


_PERTURBABLE_FIELDS = [
    "direct_observation_weight",
    "prior_weight",
    "exact_weight",
    "relaxed_weight",
    "round_transition_weight",
    "transition_weight",
    "nearest_weight",
    "cross_seed_nearest_weight",
    "transition_alpha",
    "round_transition_alpha",
    "latent_proxy_strength",
    "smoothing_base",
    "smoothing_dynamic_scale",
]


def _perturb_parameters(base: PredictorParameters, *, rng: random.Random, scale: float = 0.20) -> PredictorParameters:
    updates: dict[str, float] = {}
    for field in _PERTURBABLE_FIELDS:
        value = getattr(base, field)
        factor = 1.0 + rng.uniform(-scale, scale)
        updates[field] = max(0.0, value * factor)
    return base.model_copy(update=updates)


def _fit_predictor_parameters(
    local_artifacts: list[_RoundArtifact],
    *,
    exclude_round_id: RoundId | None = None,
) -> PredictorParameters:
    candidate_artifacts = [
        artifact
        for artifact in local_artifacts
        if exclude_round_id is None or artifact.round_detail.id != exclude_round_id
    ]
    holdout_artifacts = _artifacts_with_holdout(candidate_artifacts)
    if not holdout_artifacts:
        return PredictorParameters()

    best_parameters = PredictorParameters()
    best_score = float("inf")
    scored_candidates: list[tuple[float, PredictorParameters]] = []
    for parameters in _candidate_parameter_sets():
        score = _evaluate_parameters(parameters, holdout_artifacts, candidate_artifacts)
        if score is None:
            continue
        scored_candidates.append((score, parameters))
        if score < best_score:
            best_score = score
            best_parameters = parameters

    scored_candidates.sort(key=lambda item: item[0])
    top_bases = [params for _, params in scored_candidates[:2]]
    if not top_bases:
        return best_parameters

    rng = random.Random(42)
    for base in top_bases:
        for _ in range(15):
            perturbed = _perturb_parameters(base, rng=rng)
            score = _evaluate_parameters(perturbed, holdout_artifacts, candidate_artifacts)
            if score is not None and score < best_score:
                best_score = score
                best_parameters = perturbed
    return best_parameters


def _fetch_round_resources(
    *,
    client: AstarIslandClient | None,
    round_summary: RoundSummary | None,
    round_id: RoundId,
) -> tuple[RoundSummary | None, RoundDetail | None, ScoreSnapshot | None]:
    if client is None:
        return round_summary, None, None
    if round_summary is None:
        round_summary = next(
            (
                item
                for item in client.list_rounds()
                if _matches_round_identifier(
                    round_id=round_id,
                    candidate_id=item.id,
                    candidate_round_number=item.round_number,
                )
            ),
            None,
        )
    if round_summary is None:
        return None, None, None
    score_snapshot = client.fetch_score_snapshot(round_id)
    return round_summary, client.get_round(round_id), score_snapshot


def _save_round_context(
    artifact_dir: Path,
    *,
    round_summary: RoundSummary | None,
    round_detail: RoundDetail,
    score_snapshot: ScoreSnapshot | None,
) -> None:
    save_round_artifacts(artifact_dir, round_summary, round_detail)
    if score_snapshot is not None:
        save_score_snapshot(artifact_dir, score_snapshot)


def _evaluation_mode(holdout_metrics: HoldoutMetrics | None, score_snapshot: ScoreSnapshot | None) -> str:
    if score_snapshot is not None and any(
        probe.status_code == 200 and "truth" in probe.path for probe in score_snapshot.probes
    ):
        return "truth"
    if score_snapshot is not None and any(
        probe.status_code == 200 and "score" in probe.path for probe in score_snapshot.probes
    ):
        return "score_only"
    if holdout_metrics is not None:
        return "holdout_only"
    return "metadata_only"


def _score_metadata(snapshot: ScoreSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "leaderboard_entry_count": len(snapshot.leaderboard),
        "probe_statuses": {probe.path: probe.status_code for probe in snapshot.probes},
    }


def _model_comparison(
    candidate_metrics: HoldoutMetrics | None, legacy_metrics: HoldoutMetrics | None, model_name: str, baseline_name: str
) -> ModelComparisonSummary | None:
    if candidate_metrics is None or legacy_metrics is None:
        return None
    changed_delta = None
    if candidate_metrics.changed_cell_nll is not None and legacy_metrics.changed_cell_nll is not None:
        changed_delta = round(candidate_metrics.changed_cell_nll - legacy_metrics.changed_cell_nll, 6)
    overall_delta = round(candidate_metrics.overall_nll - legacy_metrics.overall_nll, 6)
    return ModelComparisonSummary(
        candidate_model_name=model_name,
        baseline_model_name=baseline_name,
        overall_nll_delta=overall_delta,
        changed_cell_nll_delta=changed_delta,
        candidate_better=(changed_delta if changed_delta is not None else overall_delta) < 0,
    )


def backtest_round(
    *,
    target_dir: Path,
    round_summary: RoundSummary | None,
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
    score_snapshot: ScoreSnapshot | None,
    calibrated_parameters: PredictorParameters,
    archive_rounds: list[ArchiveRound],
) -> EvaluationReport:
    predictor = BaselinePredictor(parameters=calibrated_parameters, archive_rounds=archive_rounds)
    legacy_predictor = LegacyMechanicsPredictor(archive_rounds=archive_rounds)

    train_observations = observations
    test_observations = None
    holdout_metrics = None
    legacy_holdout_metrics = None
    if observations is not None:
        train_observations, test_observations = split_observations_for_holdout(observations)

    predictions = predictor.predict(round_detail, train_observations)
    save_predictions(target_dir, predictions)
    validation = validate_prediction_bundle(
        predictions,
        expected_seed_count=round_detail.seeds_count,
        map_height=round_detail.map_height,
        map_width=round_detail.map_width,
    )

    if train_observations is not None and test_observations is not None:
        holdout_metrics = evaluate_holdout_metrics(
            predictions=predictions,
            train_observations=train_observations,
            test_observations=test_observations,
        )
        legacy_predictions = legacy_predictor.predict(round_detail, train_observations)
        legacy_holdout_metrics = evaluate_holdout_metrics(
            predictions=legacy_predictions,
            train_observations=train_observations,
            test_observations=test_observations,
        )

    comparison = _model_comparison(
        holdout_metrics, legacy_holdout_metrics, predictions.model_name, legacy_predictor.model_name
    )
    report = EvaluationReport(
        round_id=round_detail.id,
        round_number=round_detail.round_number or (round_summary.round_number if round_summary is not None else None),
        artifact_dir=str(target_dir),
        model_name=predictions.model_name,
        evaluation_mode=_evaluation_mode(holdout_metrics, score_snapshot),
        official_round_status=round_detail.status,
        official_score_metadata=_score_metadata(score_snapshot),
        prediction_validation=validation,
        holdout_metrics=holdout_metrics,
        legacy_holdout_metrics=legacy_holdout_metrics,
        model_comparison_summary=comparison,
        query_budget_used=observations.total_queries if observations is not None else 0,
        observed_coverage_fraction=round(_observed_coverage_fraction(round_detail, train_observations), 6),
        calibrated_parameters=calibrated_parameters,
    )
    save_evaluation_report(target_dir, report)

    manifest = load_run_manifest(target_dir) or DeliveryRunManifest(
        round_id=round_detail.id,
        round_number=report.round_number,
        active_round_status=round_detail.status or (round_summary.status if round_summary is not None else "unknown"),
        artifact_dir=str(target_dir),
        status="backtested",
    )
    manifest = manifest.model_copy(
        update={
            "updated_at": utc_now_iso(),
            "status": "backtested",
            "official_score_metadata": report.official_score_metadata,
            "evaluation_mode": report.evaluation_mode,
            "model_comparison_summary": report.model_comparison_summary,
            "prediction_model_name": predictions.model_name,
            "prediction_created_at": predictions.created_at,
            "prediction_validation": validation,
        }
    )
    save_run_manifest(target_dir, manifest)
    return report


def _aggregate_reports(
    model_name: str, calibrated_parameters: PredictorParameters, reports: list[EvaluationReport]
) -> BacktestSummary:
    aggregate_metrics: dict[str, float] = {}
    holdout_reports = [report for report in reports if report.holdout_metrics is not None]
    if holdout_reports:
        aggregate_metrics["mean_overall_nll"] = round(
            sum(report.holdout_metrics.overall_nll for report in holdout_reports if report.holdout_metrics is not None)
            / len(holdout_reports),
            6,
        )
        changed_values = [
            report.holdout_metrics.changed_cell_nll
            for report in holdout_reports
            if report.holdout_metrics is not None and report.holdout_metrics.changed_cell_nll is not None
        ]
        if changed_values:
            aggregate_metrics["mean_changed_cell_nll"] = round(sum(changed_values) / len(changed_values), 6)
    return BacktestSummary(
        model_name=model_name,
        calibrated_parameters=calibrated_parameters,
        reports=reports,
        aggregate_metrics=aggregate_metrics,
    )


def run_backtests(
    *,
    settings: AstarIslandSettings,
    round_id: RoundId | None = None,
    all_completed: bool = False,
    artifact_dir: Path | None = None,
    use_gcp: bool = False,
) -> tuple[Path, BacktestSummary]:
    root_dir = _backtest_root(settings, artifact_dir)
    root_dir.mkdir(parents=True, exist_ok=True)

    client = AstarIslandClient(settings=settings) if settings.access_token else None
    local_artifacts = _load_local_round_artifacts(settings.data_dir)
    global_calibrated_parameters = fit_predictor_parameters(local_artifacts)

    rounds_to_process: list[tuple[RoundSummary | None, RoundId]] = []
    if round_id is not None:
        rounds_to_process.append((None, round_id))
    elif all_completed and client is not None:
        rounds_to_process.extend((summary, summary.id) for summary in client.get_completed_rounds())
    else:
        seen: set[RoundId] = set()
        for artifact in local_artifacts:
            if artifact.round_detail.id in seen:
                continue
            seen.add(artifact.round_detail.id)
            rounds_to_process.append((artifact.round_summary, artifact.round_detail.id))

    reports: list[EvaluationReport] = []
    for round_summary, current_round_id in rounds_to_process:
        local_source = _best_local_artifact_for_round(settings.data_dir, current_round_id)
        fetched_summary, fetched_detail, score_snapshot = _fetch_round_resources(
            client=client,
            round_summary=round_summary,
            round_id=current_round_id,
        )
        if local_source is None and fetched_detail is None:
            continue
        round_detail = fetched_detail or local_source.round_detail
        observations = local_source.observations if local_source is not None else None
        round_dir = _round_backtest_dir(root_dir, round_detail.id)
        round_dir.mkdir(parents=True, exist_ok=True)
        _save_round_context(
            round_dir, round_summary=fetched_summary, round_detail=round_detail, score_snapshot=score_snapshot
        )

        archive_rounds = _archive_rounds_from_artifacts(local_artifacts, exclude_round_id=current_round_id)
        calibrated_parameters = _fit_predictor_parameters(local_artifacts, exclude_round_id=current_round_id)
        if calibrated_parameters == PredictorParameters() and global_calibrated_parameters != PredictorParameters():
            calibrated_parameters = global_calibrated_parameters
        report = backtest_round(
            target_dir=round_dir,
            round_summary=fetched_summary,
            round_detail=round_detail,
            observations=observations,
            score_snapshot=score_snapshot,
            calibrated_parameters=calibrated_parameters,
            archive_rounds=archive_rounds,
        )
        reports.append(report)

    summary = _aggregate_reports(
        BaselinePredictor(parameters=global_calibrated_parameters).model_name,
        global_calibrated_parameters,
        reports,
    )
    save_backtest_summary(root_dir, summary)

    if use_gcp and settings.gcs_enabled and GcpBatchBackend().available():
        sync_directory_to_gcs(settings, root_dir)

    return root_dir, summary
