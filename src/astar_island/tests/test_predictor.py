from __future__ import annotations

import pytest

from astar_island.predictor import BaselinePredictor, _restore_observed_direct_posteriors
from astar_island.terrain import normalize_distribution
from astar_island.tests.test_support import build_mock_observations, load_sample_round_detail


def test_predictor_uses_hierarchical_empirical_signal_and_floor() -> None:
    round_detail = load_sample_round_detail()
    observations = build_mock_observations(round_detail, variant="mixed")

    predictions, diagnostics = BaselinePredictor().predict_with_diagnostics(round_detail, observations)

    observed_cell = predictions.seeds[0].prediction[6][6]
    neighbor_cell = predictions.seeds[1].prediction[6][9]
    ocean_cell = predictions.seeds[0].prediction[0][0]
    mountain_cell = predictions.seeds[0].prediction[8][9]
    inland_cell = predictions.seeds[0].prediction[10][12]
    barren_cell = predictions.seeds[0].prediction[17][17]

    assert observed_cell[3] > observed_cell[1]
    assert len({round(value, 4) for value in neighbor_cell}) > 2
    assert ocean_cell[0] == max(ocean_cell)
    assert mountain_cell[5] == max(mountain_cell)
    assert diagnostics.latent_proxies.ruin_intensity > 0
    assert diagnostics.latent_proxies.winter_severity > 0
    assert diagnostics.latent_proxies.trade_strength > 0
    assert diagnostics.latent_proxies.conflict_pressure > 0
    assert diagnostics.latent_proxies.rebuild_strength > 0
    assert inland_cell[2] <= 0.015
    assert barren_cell[0] > barren_cell[4]
    assert barren_cell[4] < 0.03
    for seed_prediction in predictions.seeds:
        for row in seed_prediction.prediction:
            for cell in row:
                assert sum(cell) == pytest.approx(1.0)
                assert min(cell) > 0.0


def test_latent_proxy_shift_changes_unobserved_frontier_cell() -> None:
    round_detail = load_sample_round_detail()
    ruin_predictions, ruin_diagnostics = BaselinePredictor().predict_with_diagnostics(
        round_detail,
        build_mock_observations(round_detail, variant="ruin_heavy"),
    )
    growth_predictions, growth_diagnostics = BaselinePredictor().predict_with_diagnostics(
        round_detail,
        build_mock_observations(round_detail, variant="growth_heavy"),
    )
    mixed_predictions, mixed_diagnostics = BaselinePredictor().predict_with_diagnostics(
        round_detail,
        build_mock_observations(round_detail, variant="mixed"),
    )

    ruin_cell = ruin_predictions.seeds[0].prediction[7][7]
    growth_cell = growth_predictions.seeds[0].prediction[7][7]
    ruin_trade_neighbor = ruin_predictions.seeds[1].prediction[6][7]
    growth_trade_neighbor = growth_predictions.seeds[1].prediction[6][7]
    mixed_conflict_cell = mixed_predictions.seeds[0].prediction[6][7]
    growth_conflict_cell = growth_predictions.seeds[0].prediction[6][7]

    assert ruin_diagnostics.latent_proxies.ruin_intensity > growth_diagnostics.latent_proxies.ruin_intensity
    assert growth_diagnostics.latent_proxies.expansion_pressure >= ruin_diagnostics.latent_proxies.expansion_pressure
    assert ruin_diagnostics.latent_proxies.winter_severity > growth_diagnostics.latent_proxies.winter_severity
    assert mixed_diagnostics.latent_proxies.conflict_pressure >= growth_diagnostics.latent_proxies.conflict_pressure
    assert growth_diagnostics.latent_proxies.trade_strength > ruin_diagnostics.latent_proxies.trade_strength
    assert ruin_cell[3] > growth_cell[3]
    assert growth_cell[1] > ruin_cell[1]
    assert growth_trade_neighbor[1] + growth_trade_neighbor[2] > ruin_trade_neighbor[1] + ruin_trade_neighbor[2]
    assert mixed_conflict_cell[3] > growth_conflict_cell[3]


def test_coastal_ruin_rebuilds_more_like_port_than_inland_ruin() -> None:
    round_detail = load_sample_round_detail()
    round_detail.initial_states[0].grid[10][1] = 3
    round_detail.initial_states[0].grid[10][12] = 3

    predictions, _ = BaselinePredictor().predict_with_diagnostics(
        round_detail,
        build_mock_observations(round_detail, variant="mixed"),
    )

    coastal_ruin = predictions.seeds[0].prediction[10][1]
    inland_ruin = predictions.seeds[0].prediction[10][12]

    assert coastal_ruin[2] > inland_ruin[2]
    assert coastal_ruin[1] + coastal_ruin[2] > inland_ruin[2]


def test_restore_observed_direct_posteriors_high_confidence() -> None:
    floor = 0.01
    prior = normalize_distribution([0.3, 0.3, 0.1, 0.1, 0.1, 0.1], floor=floor)
    confident_posterior = normalize_distribution([0.05, 0.85, 0.02, 0.02, 0.03, 0.03], floor=floor)
    tensor = [[prior[:], prior[:]], [prior[:], prior[:]]]

    posteriors = {(0, 0): (confident_posterior, 3)}
    result = _restore_observed_direct_posteriors(tensor, posteriors, floor=floor)

    assert result[0][0][1] > prior[1]
    assert sum(result[0][0]) == pytest.approx(1.0)
    assert result[0][1] == prior


def test_restore_observed_direct_posteriors_low_confidence_skipped() -> None:
    floor = 0.01
    prior = normalize_distribution([0.3, 0.3, 0.1, 0.1, 0.1, 0.1], floor=floor)
    low_conf = normalize_distribution([0.2, 0.2, 0.2, 0.15, 0.15, 0.1], floor=floor)
    tensor = [[prior[:], prior[:]], [prior[:], prior[:]]]

    posteriors = {(0, 0): (low_conf, 1)}
    result = _restore_observed_direct_posteriors(tensor, posteriors, floor=floor)

    assert result[0][0] == prior
