from __future__ import annotations

import json

from astar_island.backtest import _load_local_round_artifacts
from astar_island.calibration import build_similarity_calibration
from astar_island.storage import save_observations, save_round_artifacts
from astar_island.tests.test_support import build_mock_observations, load_sample_round_detail


def _write_analysis_cache(cache_dir, round_detail, distribution: list[float]) -> None:
    for seed_index, state in enumerate(round_detail.initial_states):
        payload = {
            "initial_grid": state.grid,
            "ground_truth": [[list(distribution) for _ in row] for row in state.grid],
        }
        path = cache_dir / f"round_{round_detail.round_number}_seed_{seed_index}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_similarity_calibration_prefers_closest_historical_round(tmp_path) -> None:
    base_detail = load_sample_round_detail()
    growth_detail = base_detail.model_copy(update={"id": 201, "round_number": 201})
    ruin_detail = base_detail.model_copy(update={"id": 202, "round_number": 202})
    current_detail = base_detail.model_copy(update={"id": 203, "round_number": 203})

    growth_dir = tmp_path / "round_201" / "best"
    ruin_dir = tmp_path / "round_202" / "best"
    save_round_artifacts(growth_dir, None, growth_detail)
    save_round_artifacts(ruin_dir, None, ruin_detail)
    save_observations(growth_dir, build_mock_observations(growth_detail, variant="growth_heavy"))
    save_observations(ruin_dir, build_mock_observations(ruin_detail, variant="ruin_heavy"))

    cache_dir = tmp_path / "analysis_cache"
    _write_analysis_cache(cache_dir, growth_detail, [0.22, 0.58, 0.05, 0.04, 0.11, 0.0])
    _write_analysis_cache(cache_dir, ruin_detail, [0.56, 0.08, 0.02, 0.24, 0.10, 0.0])

    adaptive = build_similarity_calibration(
        current_detail,
        build_mock_observations(current_detail, variant="growth_heavy"),
        _load_local_round_artifacts(tmp_path),
        analysis_cache_dir=cache_dir,
        top_k=2,
        temperature=1.5,
    )

    assert adaptive is not None
    assert adaptive.rounds_used[:2] == [growth_detail.round_number, ruin_detail.round_number]
    plains_distribution = adaptive.get_distribution("plains", coastal=False, frontier=False, dist_band="1_2")
    assert plains_distribution is not None
    assert plains_distribution[1] > plains_distribution[3]


def test_similarity_calibration_returns_none_without_observations(tmp_path) -> None:
    detail = load_sample_round_detail().model_copy(update={"id": 204, "round_number": 204})

    adaptive = build_similarity_calibration(
        detail,
        None,
        _load_local_round_artifacts(tmp_path),
        analysis_cache_dir=tmp_path / "analysis_cache",
    )

    assert adaptive is None
