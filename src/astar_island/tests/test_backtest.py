from __future__ import annotations

from pathlib import Path

import pytest

from astar_island.backtest import (
    evaluate_holdout_metrics,
    run_backtests,
    split_observations_for_holdout,
)
from astar_island.config import AstarIslandSettings
from astar_island.predictor import BaselinePredictor, LegacyMechanicsPredictor
from astar_island.storage import (
    load_backtest_summary,
    load_evaluation_report,
    load_run_manifest,
    save_observations,
    save_round_artifacts,
)
from astar_island.tests.test_support import build_mock_observations, load_sample_round_detail


def test_run_backtests_writes_holdout_report(tmp_path) -> None:
    detail = load_sample_round_detail()
    artifact_dir = tmp_path / "round_101" / "fixture"
    output_dir = tmp_path / "backtests" / "run"
    observations = build_mock_observations(detail, variant="mixed")
    save_round_artifacts(artifact_dir, None, detail)
    save_observations(artifact_dir, observations)

    resolved_dir, summary = run_backtests(
        settings=AstarIslandSettings(data_dir=tmp_path),
        round_id=detail.round_number,
        artifact_dir=output_dir,
    )

    assert resolved_dir == output_dir
    assert len(summary.reports) == 1
    report = summary.reports[0]
    assert report.evaluation_mode == "holdout_only"
    assert report.holdout_metrics is not None
    assert report.model_comparison_summary is not None
    saved_report = load_evaluation_report(output_dir / f"round_{detail.id}")
    assert saved_report is not None
    assert saved_report.round_id == detail.id
    saved_manifest = load_run_manifest(output_dir / f"round_{detail.id}")
    assert saved_manifest is not None
    assert saved_manifest.evaluation_mode == "holdout_only"
    saved_summary = load_backtest_summary(output_dir)
    assert saved_summary is not None
    assert saved_summary.reports


def test_empirical_transition_archive_changes_unobserved_frontier_cell() -> None:
    round_detail = load_sample_round_detail()
    no_archive_predictions, _ = BaselinePredictor().predict_with_diagnostics(round_detail, None)
    growth_archive_predictions, _ = BaselinePredictor(
        archive_rounds=[(round_detail, build_mock_observations(round_detail, variant="growth_heavy"))]
    ).predict_with_diagnostics(round_detail, None)
    ruin_archive_predictions, _ = BaselinePredictor(
        archive_rounds=[(round_detail, build_mock_observations(round_detail, variant="ruin_heavy"))]
    ).predict_with_diagnostics(round_detail, None)

    no_archive_cell = no_archive_predictions.seeds[0].prediction[7][7]
    growth_cell = growth_archive_predictions.seeds[0].prediction[7][7]
    ruin_cell = ruin_archive_predictions.seeds[0].prediction[7][7]

    assert growth_cell[1] + growth_cell[2] > no_archive_cell[1] + no_archive_cell[2]
    assert ruin_cell[3] + ruin_cell[4] > growth_cell[3] + growth_cell[4]


def test_empirical_model_beats_legacy_round8_holdout_if_artifact_available() -> None:
    artifact_dir = Path("data/astar_island/live_round8_20260320T171338Z")
    if not artifact_dir.exists():
        pytest.skip("round 8 live artifact is not available in this workspace")

    from astar_island.storage import load_observations, load_round_detail

    round_detail = load_round_detail(artifact_dir)
    observations = load_observations(artifact_dir)
    if observations is None:
        pytest.skip("round 8 artifact does not contain observations")

    train_observations, test_observations = split_observations_for_holdout(observations)
    if test_observations is None or not test_observations.samples:
        pytest.skip("round 8 artifact has no repeated-query holdout split")

    empirical_predictions = BaselinePredictor().predict(round_detail, train_observations)
    legacy_predictions = LegacyMechanicsPredictor().predict(round_detail, train_observations)
    empirical_metrics = evaluate_holdout_metrics(
        predictions=empirical_predictions,
        train_observations=train_observations,
        test_observations=test_observations,
    )
    legacy_metrics = evaluate_holdout_metrics(
        predictions=legacy_predictions,
        train_observations=train_observations,
        test_observations=test_observations,
    )

    empirical_score = empirical_metrics.changed_cell_nll or empirical_metrics.overall_nll
    legacy_score = legacy_metrics.changed_cell_nll or legacy_metrics.overall_nll
    assert empirical_score <= legacy_score
