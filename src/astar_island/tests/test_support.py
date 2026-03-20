from __future__ import annotations

from pathlib import Path

from astar_island.models import (
    InitialSettlement,
    ObservationCollection,
    ObservationSample,
    PlannedQuery,
    RoundDetail,
    SeedInitialState,
    SettlementObservation,
    SimulationResult,
    Viewport,
)
from astar_island.planner import build_observation_collection


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def load_sample_round_detail() -> RoundDetail:
    path = FIXTURE_DIR / "sample_round_detail.json"
    return RoundDetail.model_validate_json(path.read_text(encoding="utf-8"))


def build_competition_round_detail() -> RoundDetail:
    def build_seed(*settlements: tuple[int, int, bool]) -> SeedInitialState:
        size = 40
        grid = [[10 if x in {0, size - 1} or y in {0, size - 1} else 11 for x in range(size)] for y in range(size)]
        for x in range(15, 20):
            grid[18][x] = 5
        for x in range(26, 31):
            grid[28][x] = 4
        settlement_models = []
        for x, y, has_port in settlements:
            grid[y][x] = 2 if has_port else 1
            settlement_models.append(InitialSettlement(x=x, y=y, has_port=has_port, alive=True))
        return SeedInitialState(grid=grid, settlements=settlement_models)

    return RoundDetail(
        id=701,
        status="active",
        round_number=7,
        map_width=40,
        map_height=40,
        seeds_count=5,
        initial_states=[
            build_seed((6, 6, False), (30, 6, True), (18, 30, False)),
            build_seed((7, 8, False), (31, 7, True)),
            build_seed((8, 8, False), (28, 28, False), (31, 6, True)),
            build_seed((6, 30, False), (31, 7, True)),
            build_seed((9, 6, True), (28, 31, False)),
        ],
    )


def build_mock_observations(round_detail: RoundDetail, *, variant: str = "mixed") -> ObservationCollection:
    if variant == "ruin_heavy":
        observed_grids = [
            [[11, 3, 3, 11], [11, 3, 3, 11], [11, 1, 3, 11], [11, 11, 11, 11]],
            [[11, 3, 3, 11], [11, 3, 3, 11], [11, 3, 3, 11], [11, 11, 11, 11]],
            [[11, 3, 2, 11], [11, 3, 3, 11], [11, 1, 3, 11], [11, 11, 11, 11]],
        ]
    elif variant == "growth_heavy":
        observed_grids = [
            [[11, 1, 1, 11], [11, 1, 1, 11], [11, 1, 1, 11], [11, 11, 11, 11]],
            [[11, 1, 2, 11], [11, 1, 1, 11], [11, 1, 2, 11], [11, 11, 11, 11]],
            [[11, 1, 2, 11], [11, 1, 1, 11], [11, 1, 1, 11], [11, 11, 11, 11]],
        ]
    else:
        observed_grids = [
            [[11, 1, 1, 11], [11, 3, 1, 11], [11, 1, 3, 11], [11, 11, 11, 11]],
            [[11, 1, 1, 11], [11, 3, 3, 11], [11, 1, 3, 11], [11, 11, 11, 11]],
            [[11, 1, 2, 11], [11, 3, 1, 11], [11, 1, 3, 11], [11, 11, 11, 11]],
        ]

    samples: list[ObservationSample] = []
    seed0_settlement_variants = {
        "ruin_heavy": [
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=8, food=2, wealth=2, defense=1, tech_level=2),
                SettlementObservation(x=7, y=6, has_port=False, alive=True, owner_id=2, population=7, food=1, wealth=2, defense=1, tech_level=2),
            ],
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=7, food=1, wealth=2, defense=1, tech_level=2),
                SettlementObservation(x=7, y=6, has_port=False, alive=False, owner_id=2, population=6, food=0.5, wealth=1, defense=0.5, tech_level=1.5),
            ],
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=False, owner_id=1, population=6, food=0.5, wealth=1, defense=0.5, tech_level=1.5),
                SettlementObservation(x=7, y=6, has_port=True, alive=True, owner_id=2, population=8, food=2, wealth=3, defense=1.5, tech_level=2.5),
            ],
        ],
        "growth_heavy": [
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=18, food=12, wealth=9, defense=7, tech_level=7),
                SettlementObservation(x=7, y=6, has_port=False, alive=True, owner_id=1, population=15, food=10, wealth=8, defense=6, tech_level=6),
            ],
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=19, food=11, wealth=9, defense=7, tech_level=7),
                SettlementObservation(x=7, y=6, has_port=True, alive=True, owner_id=1, population=16, food=11, wealth=9, defense=6, tech_level=7),
            ],
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=20, food=12, wealth=10, defense=8, tech_level=7),
                SettlementObservation(x=7, y=6, has_port=True, alive=True, owner_id=1, population=17, food=12, wealth=10, defense=7, tech_level=8),
            ],
        ],
        "mixed": [
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=14, food=7, wealth=5, defense=4, tech_level=4),
                SettlementObservation(x=7, y=6, has_port=False, alive=True, owner_id=2, population=10, food=3, wealth=4, defense=2, tech_level=3),
            ],
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=13, food=6, wealth=5, defense=4, tech_level=4),
                SettlementObservation(x=7, y=6, has_port=False, alive=True, owner_id=2, population=9, food=2.5, wealth=3, defense=2, tech_level=3),
            ],
            [
                SettlementObservation(x=5, y=5, has_port=False, alive=True, owner_id=1, population=15, food=8, wealth=6, defense=5, tech_level=5),
                SettlementObservation(x=7, y=6, has_port=False, alive=True, owner_id=2, population=11, food=3, wealth=4, defense=2, tech_level=3),
            ],
        ],
    }
    seed1_settlement_variants = {
        "ruin_heavy": [
            SettlementObservation(x=8, y=6, has_port=True, alive=True, owner_id=3, population=11, food=4, wealth=5, defense=3, tech_level=4),
            SettlementObservation(x=9, y=7, has_port=False, alive=True, owner_id=3, population=9, food=3, wealth=4, defense=3, tech_level=3),
        ],
        "growth_heavy": [
            SettlementObservation(x=8, y=6, has_port=True, alive=True, owner_id=3, population=18, food=12, wealth=12, defense=8, tech_level=8),
            SettlementObservation(x=9, y=7, has_port=False, alive=True, owner_id=3, population=14, food=10, wealth=8, defense=6, tech_level=6),
        ],
        "mixed": [
            SettlementObservation(x=8, y=6, has_port=True, alive=True, owner_id=3, population=17, food=11, wealth=12, defense=7, tech_level=8),
            SettlementObservation(x=9, y=7, has_port=False, alive=True, owner_id=3, population=13, food=9, wealth=8, defense=6, tech_level=6),
        ],
    }
    for index, grid in enumerate(observed_grids, start=1):
        samples.append(
            ObservationSample(
                planned_query=PlannedQuery(
                    query_index=index,
                    seed_index=0,
                    viewport_x=5,
                    viewport_y=5,
                    viewport_w=4,
                    viewport_h=4,
                    repeat_index=index,
                    cluster_rank=1,
                    purpose="mock seed 0",
                    phase="phase1",
                    selection_reason="fixture",
                ),
                result=SimulationResult(
                    grid=grid,
                    settlements=seed0_settlement_variants[variant][index - 1],
                    viewport=Viewport(x=5, y=5, w=4, h=4),
                ),
            )
        )
    samples.append(
        ObservationSample(
            planned_query=PlannedQuery(
                query_index=4,
                seed_index=1,
                viewport_x=8,
                viewport_y=6,
                viewport_w=4,
                viewport_h=4,
                repeat_index=1,
                cluster_rank=1,
                purpose="mock seed 1",
                phase="phase1",
                selection_reason="fixture",
            ),
            result=SimulationResult(
                grid=[
                    [2, 1, 11, 11],
                    [1, 1, 11, 11],
                    [11, 3, 11, 11],
                    [11, 11, 11, 11],
                ],
                settlements=seed1_settlement_variants[variant],
                viewport=Viewport(x=8, y=6, w=4, h=4),
            ),
        )
    )
    return build_observation_collection(round_detail.id, samples)
