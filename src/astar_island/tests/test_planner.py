from __future__ import annotations

from astar_island.models import ObservationSample, PlannedQuery, SettlementObservation, SimulationResult, Viewport
from astar_island.planner import build_observation_collection, build_phase1_observation_plan, build_phase2_observation_plan
from astar_island.predictor import BaselinePredictor
from astar_island.tests.test_support import build_competition_round_detail


def _phase1_observations(variant: str):
    round_detail = build_competition_round_detail()
    plan = build_phase1_observation_plan(round_detail)
    samples: list[ObservationSample] = []
    for query in plan.queries:
        grid = [[11 for _ in range(query.viewport_w)] for _ in range(query.viewport_h)]
        mid_x = query.viewport_w // 2
        mid_y = query.viewport_h // 2
        settlements = []
        if query.seed_index == 0:
            if variant == "volatile" and query.repeat_index % 2 == 0:
                grid[mid_y][mid_x] = 3
                grid[mid_y][max(0, mid_x - 1)] = 3
                settlements = [
                    SettlementObservation(
                        x=query.viewport_x + mid_x,
                        y=query.viewport_y + mid_y,
                        has_port=False,
                        alive=True,
                        owner_id=1,
                        population=7,
                        food=2,
                        wealth=2,
                        defense=1,
                        tech_level=2,
                    ),
                    SettlementObservation(
                        x=query.viewport_x + max(0, mid_x - 1),
                        y=query.viewport_y + mid_y,
                        has_port=False,
                        alive=True,
                        owner_id=2,
                        population=6,
                        food=1.5,
                        wealth=2,
                        defense=1,
                        tech_level=2,
                    ),
                ]
            else:
                grid[mid_y][mid_x] = 1
                grid[mid_y][max(0, mid_x - 1)] = 1
                settlements = [
                    SettlementObservation(
                        x=query.viewport_x + mid_x,
                        y=query.viewport_y + mid_y,
                        has_port=False,
                        alive=True,
                        owner_id=1,
                        population=14,
                        food=10,
                        wealth=7,
                        defense=6,
                        tech_level=6,
                    ),
                    SettlementObservation(
                        x=query.viewport_x + max(0, mid_x - 1),
                        y=query.viewport_y + mid_y,
                        has_port=False,
                        alive=True,
                        owner_id=1,
                        population=12,
                        food=9,
                        wealth=6,
                        defense=5,
                        tech_level=5,
                    ),
                ]
        else:
            grid[mid_y][mid_x] = 1 if query.seed_index % 2 == 0 else 2
            settlements = [
                SettlementObservation(
                    x=query.viewport_x + mid_x,
                    y=query.viewport_y + mid_y,
                    has_port=query.seed_index % 2 == 1,
                    alive=True,
                    owner_id=query.seed_index + 1,
                    population=16,
                    food=11,
                    wealth=9,
                    defense=7,
                    tech_level=7,
                ),
            ]
        samples.append(
            ObservationSample(
                planned_query=PlannedQuery.model_validate(query.model_dump()),
                result=SimulationResult(
                    grid=grid,
                    settlements=settlements,
                    viewport=Viewport(x=query.viewport_x, y=query.viewport_y, w=query.viewport_w, h=query.viewport_h),
                ),
            )
        )
    return round_detail, plan, build_observation_collection(round_detail.id, samples)


def test_phase1_plan_allocates_four_queries_per_seed() -> None:
    round_detail = build_competition_round_detail()

    phase1 = build_phase1_observation_plan(round_detail)

    assert phase1.budget == 20
    assert len(phase1.queries) == 20
    assert all(seed_plan.allocated_queries == 4 for seed_plan in phase1.seed_plans)
    for seed_plan in phase1.seed_plans:
        assert len(seed_plan.cluster_windows) == 2
        assert any(reason in {"trade", "winter", "reclaim", "coast"} for window in seed_plan.cluster_windows for reason in window.selection_reasons)


def test_phase2_plan_changes_with_phase1_uncertainty() -> None:
    predictor = BaselinePredictor()
    round_detail, _, stable_observations = _phase1_observations("stable")
    _, _, volatile_observations = _phase1_observations("volatile")

    stable_predictions, stable_diagnostics = predictor.predict_with_diagnostics(round_detail, stable_observations)
    volatile_predictions, volatile_diagnostics = predictor.predict_with_diagnostics(round_detail, volatile_observations)

    stable_phase2 = build_phase2_observation_plan(
        round_detail,
        phase1_observations=stable_observations,
        provisional_predictions=stable_predictions,
        uncertainty_summaries=stable_diagnostics.uncertainty_summaries,
        total_queries=30,
    )
    volatile_phase2 = build_phase2_observation_plan(
        round_detail,
        phase1_observations=volatile_observations,
        provisional_predictions=volatile_predictions,
        uncertainty_summaries=volatile_diagnostics.uncertainty_summaries,
        total_queries=30,
    )

    assert stable_phase2.budget == 30
    assert len(stable_phase2.queries) == 30
    assert len(volatile_phase2.queries) == 30
    stable_seed0 = next(seed_plan for seed_plan in stable_phase2.seed_plans if seed_plan.seed_index == 0)
    volatile_seed0 = next(seed_plan for seed_plan in volatile_phase2.seed_plans if seed_plan.seed_index == 0)
    assert volatile_seed0.allocated_queries >= stable_seed0.allocated_queries
    assert min(seed_plan.allocated_queries for seed_plan in stable_phase2.seed_plans) >= 2
    assert volatile_phase2.queries != stable_phase2.queries
    assert max(query.repeat_index for query in stable_phase2.queries) >= 2
    assert any("stats" in query.selection_reason or "trade" in query.selection_reason or "winter" in query.selection_reason for query in volatile_phase2.queries)
