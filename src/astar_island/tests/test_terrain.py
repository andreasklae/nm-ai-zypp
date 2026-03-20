from __future__ import annotations

import pytest

from astar_island.models import InitialSettlement, SeedInitialState
from astar_island.terrain import (
    adjacent_forest_count,
    coastal_corridor_score,
    combined_settlement_support_score,
    nearby_forest_score,
    nearby_ocean_score,
    normalize_distribution,
    ruin_rebuild_support_score,
    terrain_code_to_class_index,
)


@pytest.mark.parametrize(
    ("terrain_code", "expected_class"),
    [
        (10, 0),
        (11, 0),
        (0, 0),
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, 5),
    ],
)
def test_terrain_code_mapping(terrain_code: int, expected_class: int) -> None:
    assert terrain_code_to_class_index(terrain_code) == expected_class


def test_normalize_distribution_applies_floor_and_normalizes() -> None:
    values = normalize_distribution([1.0, 0.0, 0.0, 0.0, 0.0, 0.0], floor=0.01)

    assert len(values) == 6
    assert sum(values) == pytest.approx(1.0)
    assert min(values) >= 0.01 / 1.05


def test_support_and_rebuild_helpers_capture_water_forest_and_coast() -> None:
    grid = [
        [10, 10, 10, 10, 10, 10, 10],
        [10, 11, 11, 11, 11, 11, 10],
        [10, 11, 4, 4, 11, 11, 10],
        [10, 11, 11, 3, 11, 11, 10],
        [10, 11, 11, 11, 11, 5, 10],
        [10, 11, 11, 11, 11, 11, 10],
        [10, 10, 10, 10, 10, 10, 10],
    ]
    state = SeedInitialState(
        grid=grid,
        settlements=[InitialSettlement(x=3, y=3, has_port=False, alive=True)],
    )

    coastal_support = combined_settlement_support_score(state, 1, 3)
    inland_support = combined_settlement_support_score(state, 5, 1)
    assert nearby_ocean_score(grid, 1, 3, radius=3) > nearby_ocean_score(grid, 3, 3, radius=3)
    assert adjacent_forest_count(grid, 3, 3) > 0
    assert nearby_forest_score(grid, 3, 3, radius=3) > nearby_forest_score(grid, 5, 1, radius=3)
    assert coastal_corridor_score(grid, 1, 3, radius=3) > coastal_corridor_score(grid, 3, 3, radius=3)
    assert coastal_support > inland_support
    assert ruin_rebuild_support_score(state, 3, 3) > ruin_rebuild_support_score(state, 5, 1)
