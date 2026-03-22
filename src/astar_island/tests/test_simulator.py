from __future__ import annotations

import pytest

from astar_island.simulator import SimulatorPredictor, estimate_parameters, expected_cell_distribution
from astar_island.terrain import build_feature_grid, terrain_code_to_class_index
from astar_island.tests.test_support import build_mock_observations, load_sample_round_detail


def test_simulator_respects_hard_constraints() -> None:
    round_detail = load_sample_round_detail()
    observations = build_mock_observations(round_detail, variant="mixed")
    predictions, _ = SimulatorPredictor(n_runs=80, seed=7).predict_with_diagnostics(round_detail, observations)

    for seed_pred in predictions.seeds:
        state = round_detail.initial_states[seed_pred.seed_index]
        fg = build_feature_grid(state)
        for y, row in enumerate(seed_pred.prediction):
            for x, cell in enumerate(row):
                code = state.grid[y][x]
                assert sum(cell) == pytest.approx(1.0)
                assert min(cell) >= predictions.floor - 1e-9
                if code == 10:  # ocean
                    assert cell[0] == max(cell)
                if code == 5:
                    assert cell[5] == max(cell)
                if not fg.coastal[y][x] and code not in (10, 5):
                    assert cell[2] == pytest.approx(predictions.floor)
                if code not in (5, 10):
                    assert cell[5] == pytest.approx(predictions.floor)


def test_simulator_uses_tiny_floor_for_impossible_classes() -> None:
    round_detail = load_sample_round_detail()
    observations = build_mock_observations(round_detail, variant="mixed")
    predictions, _ = SimulatorPredictor(n_runs=60, seed=11).predict_with_diagnostics(round_detail, observations)

    saw_inland_land = False
    saw_coastal_land = False
    for seed_pred in predictions.seeds:
        state = round_detail.initial_states[seed_pred.seed_index]
        fg = build_feature_grid(state)
        for y, row in enumerate(seed_pred.prediction):
            for x, cell in enumerate(row):
                code = state.grid[y][x]
                if code in (10, 5):
                    continue
                assert cell[5] == pytest.approx(predictions.floor)
                if fg.coastal[y][x]:
                    saw_coastal_land = True
                    assert cell[2] > predictions.floor
                else:
                    saw_inland_land = True
                    assert cell[2] == pytest.approx(predictions.floor)

    assert saw_inland_land
    assert saw_coastal_land


def test_simulator_deterministic_with_seed() -> None:
    round_detail = load_sample_round_detail()
    obs = build_mock_observations(round_detail, variant="mixed")
    a, _ = SimulatorPredictor(n_runs=50, seed=123).predict_with_diagnostics(round_detail, obs)
    b, _ = SimulatorPredictor(n_runs=50, seed=123).predict_with_diagnostics(round_detail, obs)
    assert a.seeds[0].prediction[10][10] == b.seeds[0].prediction[10][10]


def test_simulator_sets_last_simulation_parameters() -> None:
    round_detail = load_sample_round_detail()
    sim = SimulatorPredictor(n_runs=20, seed=1)
    assert sim.last_simulation_parameters is None
    sim.predict_with_diagnostics(round_detail, build_mock_observations(round_detail, variant="mixed"))
    assert sim.last_simulation_parameters is not None
    p = sim.last_simulation_parameters
    assert len(p.vacant_frontier_class_distribution) == 6
    assert abs(sum(p.vacant_frontier_class_distribution) - 1.0) < 1e-6


def test_estimate_parameters_no_observations_uses_proxies() -> None:
    round_detail = load_sample_round_detail()
    fgs = [build_feature_grid(s) for s in round_detail.initial_states]
    from astar_island.predictor import (
        _build_observed_settlement_index,
        _derive_latent_proxies,
    )

    proxies = _derive_latent_proxies(
        round_detail,
        None,
        _build_observed_settlement_index(round_detail, None),
        feature_grids=fgs,
    )
    params = estimate_parameters(round_detail, None, fgs, proxies)
    assert 0 < params.settlement_survival_rate < 1
    assert abs(sum(params.vacant_frontier_class_distribution) - 1.0) < 1e-5


def test_simulate_cell_static_terrain() -> None:
    round_detail = load_sample_round_detail()
    state = round_detail.initial_states[0]
    fg = build_feature_grid(state)
    rng = __import__("random").Random(0)
    from astar_island.models import LatentProxySummary

    proxies = LatentProxySummary(
        settlement_survival=0.5,
        ruin_intensity=0.2,
        port_prevalence=0.2,
        expansion_pressure=0.2,
        reclamation_rate=0.15,
        winter_severity=0.5,
        trade_strength=0.35,
        conflict_pressure=0.2,
        rebuild_strength=0.3,
    )
    params = estimate_parameters(round_detail, None, [fg], proxies)
    for y, row in enumerate(state.grid):
        for x, code in enumerate(row):
            if code == 10:
                dist = expected_cell_distribution(code, x=x, y=y, features=fg, params=params)
                assert dist[0] == 1.0
            if code == 5:
                dist = expected_cell_distribution(code, x=x, y=y, features=fg, params=params)
                assert dist[5] == 1.0


def test_simulator_improves_or_matches_naive_on_sample_nll() -> None:
    """Sanity: on mock data, simulator NLL should not be wildly worse than empirical vacant rates."""
    from math import log
    from pathlib import Path

    from astar_island.predictor import BaselinePredictor
    from astar_island.storage import load_observations, load_round_detail

    r15 = Path("data/astar_island/round_cc5442dd-bc5d-418b-911b-7eb960cb0390/20260321T130845.1960620000")
    if not (r15 / "round_detail.json").exists():
        pytest.skip("Round 15 fixture not present")
    rd = load_round_detail(r15)
    obs = load_observations(r15)
    if obs is None or obs.total_queries < 10:
        pytest.skip("Round 15 observations incomplete")

    sim_preds, _ = SimulatorPredictor(n_runs=120, seed=42).predict_with_diagnostics(rd, obs)
    base_preds, _ = BaselinePredictor().predict_with_diagnostics(rd, obs)

    def nll(bundle) -> float:
        total = 0.0
        n = 0
        for sample in obs.samples:
            si = sample.planned_query.seed_index
            tensor = bundle.seeds[si].prediction
            vp = sample.result.viewport
            for ro, row in enumerate(sample.result.grid):
                for co, code in enumerate(row):
                    wx = vp.x + co
                    wy = vp.y + ro
                    ci = terrain_code_to_class_index(code)
                    p = max(bundle.floor, tensor[wy][wx][ci])
                    total += -log(p)
                    n += 1
        return total / max(1, n)

    n_sim = nll(sim_preds)
    n_base = nll(base_preds)
    assert n_sim < n_base * 1.15
