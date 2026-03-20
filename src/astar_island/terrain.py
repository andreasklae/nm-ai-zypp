from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import inf, log

from astar_island.models import SeedInitialState

TERRAIN_TO_CLASS = {
    10: 0,  # ocean
    11: 0,  # plains
    0: 0,  # empty
    1: 1,  # settlement
    2: 2,  # port
    3: 3,  # ruin
    4: 4,  # forest
    5: 5,  # mountain
}

OCEAN_CODES = {10}
VACANT_CODES = {0, 11}
EMPTY_CODES = {0, 10, 11}  # legacy alias including ocean; prefer VACANT_CODES for non-ocean empty
LAND_CODES = {0, 1, 2, 3, 4, 5, 11}
FOREST_CODES = {4}
MOUNTAIN_CODES = {5}
INITIAL_OCCUPIED_CODES = {1, 2}


def terrain_code_to_class_index(code: int) -> int:
    if code not in TERRAIN_TO_CLASS:
        raise ValueError(f"Unknown terrain code: {code}")
    return TERRAIN_TO_CLASS[code]


def clamp_viewport_coordinate(position: int, viewport_size: int, world_size: int) -> int:
    if world_size <= viewport_size:
        return 0
    return max(0, min(position, world_size - viewport_size))


def normalize_distribution(values: Iterable[float], *, floor: float = 0.01) -> list[float]:
    raw = [max(float(value), 0.0) for value in values]
    if not raw:
        return []
    class_count = len(raw)
    if floor * class_count >= 1.0:
        raise ValueError("floor is too large for the distribution size")
    total = sum(raw)
    if total <= 0:
        uniform = 1.0 / class_count
        if uniform < floor:
            raise ValueError("cannot satisfy floor with a uniform fallback")
        return [uniform] * class_count
    remaining_mass = 1.0 - floor * class_count
    return [floor + remaining_mass * (value / total) for value in raw]


def iter_cells(state: SeedInitialState):
    for y, row in enumerate(state.grid):
        for x, code in enumerate(row):
            yield x, y, code


def adjacent_coordinates(width: int, height: int, x: int, y: int):
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx = x + dx
        ny = y + dy
        if 0 <= nx < width and 0 <= ny < height:
            yield nx, ny


def adjacent_terrain_count(grid: list[list[int]], x: int, y: int, target_codes: set[int]) -> int:
    height = len(grid)
    width = len(grid[0])
    return sum(1 for nx, ny in adjacent_coordinates(width, height, x, y) if grid[ny][nx] in target_codes)


def is_coastal_land(grid: list[list[int]], x: int, y: int) -> bool:
    height = len(grid)
    width = len(grid[0])
    code = grid[y][x]
    if code in OCEAN_CODES or code in MOUNTAIN_CODES:
        return False
    return any(grid[ny][nx] in OCEAN_CODES for nx, ny in adjacent_coordinates(width, height, x, y))


def adjacent_forest_count(grid: list[list[int]], x: int, y: int) -> int:
    return adjacent_terrain_count(grid, x, y, FOREST_CODES)


def adjacent_mountain_count(grid: list[list[int]], x: int, y: int) -> int:
    return adjacent_terrain_count(grid, x, y, MOUNTAIN_CODES)


def nearby_terrain_score(grid: list[list[int]], x: int, y: int, target_codes: set[int], *, radius: int = 3) -> float:
    height = len(grid)
    width = len(grid[0])
    if radius <= 0:
        return 0.0
    raw = 0.0
    max_raw = 0.0
    for ny in range(max(0, y - radius), min(height, y + radius + 1)):
        for nx in range(max(0, x - radius), min(width, x + radius + 1)):
            distance = abs(nx - x) + abs(ny - y)
            if distance == 0 or distance > radius:
                continue
            weight = (radius + 1 - distance) / (radius + 1)
            max_raw += weight
            if grid[ny][nx] in target_codes:
                raw += weight
    if max_raw <= 0:
        return 0.0
    return raw / max_raw


def nearby_ocean_score(grid: list[list[int]], x: int, y: int, *, radius: int = 3) -> float:
    return nearby_terrain_score(grid, x, y, OCEAN_CODES, radius=radius)


def nearby_forest_score(grid: list[list[int]], x: int, y: int, *, radius: int = 3) -> float:
    return nearby_terrain_score(grid, x, y, FOREST_CODES, radius=radius)


def coastal_corridor_score(grid: list[list[int]], x: int, y: int, *, radius: int = 3) -> float:
    code = grid[y][x]
    if code in OCEAN_CODES or code in MOUNTAIN_CODES:
        return 0.0
    adjacent_ocean = adjacent_terrain_count(grid, x, y, OCEAN_CODES) / 4.0
    nearby_ocean = nearby_ocean_score(grid, x, y, radius=radius)
    return min(1.0, adjacent_ocean * 0.65 + nearby_ocean * 0.65)


def distance_to_nearest_settlement(state: SeedInitialState, x: int, y: int) -> int:
    if not state.settlements:
        return 999
    best = inf
    for settlement in state.settlements:
        if not settlement.alive:
            continue
        best = min(best, abs(settlement.x - x) + abs(settlement.y - y))
    return int(best if best is not inf else 999)


def settlement_density(state: SeedInitialState, x: int, y: int, *, radius: int = 4) -> int:
    count = 0
    for settlement in state.settlements:
        if not settlement.alive:
            continue
        if abs(settlement.x - x) + abs(settlement.y - y) <= radius:
            count += 1
    return count


def frontier_score(state: SeedInitialState, x: int, y: int) -> int:
    code = state.grid[y][x]
    if code not in {0, 3, 4, 11}:
        return 0
    distance = distance_to_nearest_settlement(state, x, y)
    if distance > 4:
        return 0
    return max(0, 5 - distance)


def combined_settlement_support_score(state: SeedInitialState, x: int, y: int) -> float:
    code = state.grid[y][x]
    if code in OCEAN_CODES or code in MOUNTAIN_CODES:
        return 0.0
    water_score = max(coastal_corridor_score(state.grid, x, y), nearby_ocean_score(state.grid, x, y, radius=3))
    forest_score = nearby_forest_score(state.grid, x, y, radius=3)
    density_score = min(1.0, settlement_density(state, x, y, radius=4) / 3.0)
    distance_score = max(0.0, 1.0 - min(distance_to_nearest_settlement(state, x, y), 8) / 8.0)
    frontier_value = min(1.0, frontier_score(state, x, y) / 4.0)
    mountain_penalty = adjacent_mountain_count(state.grid, x, y) / 4.0
    raw = (
        water_score * 0.34
        + forest_score * 0.23
        + density_score * 0.14
        + distance_score * 0.19
        + frontier_value * 0.18
        - mountain_penalty * 0.18
    )
    if code in INITIAL_OCCUPIED_CODES:
        raw += 0.12
    if code in FOREST_CODES:
        raw -= 0.10
    return max(0.0, min(1.0, raw))


def ruin_rebuild_support_score(state: SeedInitialState, x: int, y: int) -> float:
    code = state.grid[y][x]
    if code in OCEAN_CODES or code in MOUNTAIN_CODES:
        return 0.0
    support = combined_settlement_support_score(state, x, y)
    coastal_bonus = 0.25 if is_coastal_land(state.grid, x, y) else 0.0
    frontier_bonus = min(0.2, frontier_score(state, x, y) * 0.04)
    ruin_bonus = 0.12 if code == 3 else 0.0
    return max(0.0, min(1.0, support * 0.7 + coastal_bonus + frontier_bonus + ruin_bonus))


def prediction_entropy(cell: list[float]) -> float:
    return -sum(value * log(value) for value in cell if value > 0)


def normalize_metric(values: list[float], value: float | None) -> float:
    if value is None or not values:
        return 0.5
    lower = min(values)
    upper = max(values)
    if lower == upper:
        return 0.5
    return max(0.0, min(1.0, (value - lower) / (upper - lower)))


@dataclass(slots=True)
class SeedFeatureGrid:
    height: int
    width: int
    coastal: list[list[bool]]
    ocean_score: list[list[float]]
    forest_score: list[list[float]]
    corridor_score: list[list[float]]
    density: list[list[int]]
    distance: list[list[int]]
    frontier: list[list[int]]
    support: list[list[float]]
    rebuild_support: list[list[float]]
    adj_forest: list[list[int]]
    adj_mountain: list[list[int]]
    dynamic_strength: list[list[float]]


def build_feature_grid(state: SeedInitialState) -> SeedFeatureGrid:
    grid = state.grid
    height = len(grid)
    width = len(grid[0])

    coastal_grid = [[False] * width for _ in range(height)]
    ocean_grid = [[0.0] * width for _ in range(height)]
    forest_grid = [[0.0] * width for _ in range(height)]
    corridor_grid = [[0.0] * width for _ in range(height)]
    density_grid = [[0] * width for _ in range(height)]
    distance_grid = [[999] * width for _ in range(height)]
    frontier_grid = [[0] * width for _ in range(height)]
    adj_forest_grid = [[0] * width for _ in range(height)]
    adj_mountain_grid = [[0] * width for _ in range(height)]

    for y in range(height):
        for x in range(width):
            coastal_grid[y][x] = is_coastal_land(grid, x, y)
            ocean_grid[y][x] = nearby_ocean_score(grid, x, y, radius=3)
            forest_grid[y][x] = nearby_forest_score(grid, x, y, radius=3)
            corridor_grid[y][x] = coastal_corridor_score(grid, x, y, radius=3)
            density_grid[y][x] = settlement_density(state, x, y, radius=4)
            distance_grid[y][x] = distance_to_nearest_settlement(state, x, y)
            adj_forest_grid[y][x] = adjacent_forest_count(grid, x, y)
            adj_mountain_grid[y][x] = adjacent_mountain_count(grid, x, y)

    for y in range(height):
        for x in range(width):
            code = grid[y][x]
            if code not in {0, 3, 4, 11}:
                frontier_grid[y][x] = 0
            elif distance_grid[y][x] > 4:
                frontier_grid[y][x] = 0
            else:
                frontier_grid[y][x] = max(0, 5 - distance_grid[y][x])

    support_grid = [[0.0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            code = grid[y][x]
            if code in OCEAN_CODES or code in MOUNTAIN_CODES:
                continue
            water = max(corridor_grid[y][x], ocean_grid[y][x])
            forest = forest_grid[y][x]
            dens = min(1.0, density_grid[y][x] / 3.0)
            dist = max(0.0, 1.0 - min(distance_grid[y][x], 8) / 8.0)
            front = min(1.0, frontier_grid[y][x] / 4.0)
            mtn = adj_mountain_grid[y][x] / 4.0
            raw = water * 0.34 + forest * 0.23 + dens * 0.14 + dist * 0.19 + front * 0.18 - mtn * 0.18
            if code in INITIAL_OCCUPIED_CODES:
                raw += 0.12
            if code in FOREST_CODES:
                raw -= 0.10
            support_grid[y][x] = max(0.0, min(1.0, raw))

    rebuild_grid = [[0.0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            code = grid[y][x]
            if code in OCEAN_CODES or code in MOUNTAIN_CODES:
                continue
            coastal_bonus = 0.25 if coastal_grid[y][x] else 0.0
            frontier_bonus = min(0.2, frontier_grid[y][x] * 0.04)
            ruin_bonus = 0.12 if code == 3 else 0.0
            rebuild_grid[y][x] = max(
                0.0, min(1.0, support_grid[y][x] * 0.7 + coastal_bonus + frontier_bonus + ruin_bonus)
            )

    dyn_grid = [[0.0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            code = grid[y][x]
            if code in OCEAN_CODES or code in MOUNTAIN_CODES:
                continue
            strength = 0.25 + support_grid[y][x] * 1.35 + rebuild_grid[y][x] * 0.45
            if code in INITIAL_OCCUPIED_CODES:
                strength += 1.20
            if frontier_grid[y][x] > 0:
                strength += 0.75
            if coastal_grid[y][x]:
                strength += 0.40
            if code in FOREST_CODES or code == 3:
                strength += 0.35
            dyn_grid[y][x] = strength

    return SeedFeatureGrid(
        height=height,
        width=width,
        coastal=coastal_grid,
        ocean_score=ocean_grid,
        forest_score=forest_grid,
        corridor_score=corridor_grid,
        density=density_grid,
        distance=distance_grid,
        frontier=frontier_grid,
        support=support_grid,
        rebuild_support=rebuild_grid,
        adj_forest=adj_forest_grid,
        adj_mountain=adj_mountain_grid,
        dynamic_strength=dyn_grid,
    )
