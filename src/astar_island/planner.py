from __future__ import annotations

from collections import defaultdict

from astar_island.models import (
    ObservationCollection,
    ObservationPhasePlan,
    ObservationPlan,
    ObservationSample,
    PlannedQuery,
    PlannedWindow,
    PredictionBundle,
    RoundDetail,
    SeedInitialState,
    SeedObservationPlan,
    SeedObservationSummary,
    SeedUncertaintySummary,
)
from astar_island.terrain import (
    INITIAL_OCCUPIED_CODES,
    clamp_viewport_coordinate,
    coastal_corridor_score,
    combined_settlement_support_score,
    frontier_score,
    is_coastal_land,
    nearby_forest_score,
    normalize_metric,
    prediction_entropy,
    ruin_rebuild_support_score,
    settlement_density,
    terrain_code_to_class_index,
)


def _cluster_settlements(state: SeedInitialState, *, threshold: int = 8) -> list[list[tuple[int, int]]]:
    settlements = [(item.x, item.y) for item in state.settlements if item.alive]
    clusters: list[list[tuple[int, int]]] = []
    seen: set[int] = set()
    for index, settlement in enumerate(settlements):
        if index in seen:
            continue
        stack = [index]
        cluster: list[tuple[int, int]] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            point = settlements[current]
            cluster.append(point)
            for other_index, other_point in enumerate(settlements):
                if other_index in seen:
                    continue
                if abs(point[0] - other_point[0]) + abs(point[1] - other_point[1]) <= threshold:
                    stack.append(other_index)
        clusters.append(cluster)
    return clusters or [[]]


def _winter_candidate_score(state: SeedInitialState, x: int, y: int) -> float:
    code = state.grid[y][x]
    if code in {10, 5}:
        return 0.0
    support = combined_settlement_support_score(state, x, y)
    density = settlement_density(state, x, y, radius=4)
    isolation = max(0.0, 1.0 - min(density, 2) / 2.0)
    occupied_bonus = 0.25 if code in INITIAL_OCCUPIED_CODES else 0.10
    inland_bonus = 0.20 if not is_coastal_land(state.grid, x, y) else 0.05
    return max(0.0, min(1.0, (1.0 - support) * 0.60 + isolation * 0.20 + occupied_bonus + inland_bonus))


def _trade_candidate_score(state: SeedInitialState, x: int, y: int) -> float:
    if state.grid[y][x] in {10, 5}:
        return 0.0
    base = coastal_corridor_score(state.grid, x, y, radius=3)
    if state.grid[y][x] in INITIAL_OCCUPIED_CODES:
        base += 0.20
    return max(0.0, min(1.0, base))


def _reclaim_candidate_score(state: SeedInitialState, x: int, y: int) -> float:
    if state.grid[y][x] in {10, 5}:
        return 0.0
    base = ruin_rebuild_support_score(state, x, y) * 0.75
    if state.grid[y][x] == 3:
        base += 0.20
    if nearby_forest_score(state.grid, x, y, radius=2) > 0.20:
        base += 0.10
    return max(0.0, min(1.0, base))


def _dynamic_cell_weight(state: SeedInitialState, x: int, y: int) -> float:
    code = state.grid[y][x]
    if code in {10, 5}:
        return 0.0
    weight = 0.30 + combined_settlement_support_score(state, x, y) * 1.15
    if code in INITIAL_OCCUPIED_CODES:
        weight += 1.85
    if code in {3, 4}:
        weight += 0.65
    weight += _trade_candidate_score(state, x, y) * 0.70
    weight += _winter_candidate_score(state, x, y) * 0.55
    weight += _reclaim_candidate_score(state, x, y) * 0.65
    weight += frontier_score(state, x, y) * 0.45
    return weight


def _candidate_centers(
    state: SeedInitialState,
    *,
    viewport_size: int,
    extra_centers: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    height = len(state.grid)
    width = len(state.grid[0])
    centers: list[tuple[int, int]] = []
    for cluster in _cluster_settlements(state):
        if cluster:
            centers.extend(cluster)
            centers.append(
                (
                    round(sum(x for x, _ in cluster) / len(cluster)),
                    round(sum(y for _, y in cluster) / len(cluster)),
                )
            )
    step = max(3, viewport_size // 2)
    for y in range(viewport_size // 2, height, step):
        for x in range(viewport_size // 2, width, step):
            if _dynamic_cell_weight(state, x, y) > 1.05:
                centers.append((x, y))
    if extra_centers:
        centers.extend(extra_centers)
    deduped: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for center_x, center_y in centers:
        viewport_x = clamp_viewport_coordinate(center_x - viewport_size // 2, viewport_size, width)
        viewport_y = clamp_viewport_coordinate(center_y - viewport_size // 2, viewport_size, height)
        normalized = (viewport_x + viewport_size // 2, viewport_y + viewport_size // 2)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    if not deduped:
        deduped.append((width // 2, height // 2))
    return deduped


def _build_window(
    state: SeedInitialState,
    *,
    center_x: int,
    center_y: int,
    viewport_size: int,
    cluster_id: int,
    entropy_map: list[list[float]] | None = None,
    observed_counts: dict[tuple[int, int], int] | None = None,
    hotspot_scores: dict[tuple[int, int], float] | None = None,
) -> PlannedWindow:
    height = len(state.grid)
    width = len(state.grid[0])
    viewport_x = clamp_viewport_coordinate(center_x - viewport_size // 2, viewport_size, width)
    viewport_y = clamp_viewport_coordinate(center_y - viewport_size // 2, viewport_size, height)
    viewport_w = min(viewport_size, width)
    viewport_h = min(viewport_size, height)
    settlements = 0
    coastline_cells = 0
    frontier_cells = 0
    support_sum = 0.0
    trade_sum = 0.0
    winter_sum = 0.0
    reclaim_sum = 0.0
    expected_information_gain = 0.0
    overlap_penalty = 0.0
    reasons: set[str] = set()
    for y in range(viewport_y, viewport_y + viewport_h):
        for x in range(viewport_x, viewport_x + viewport_w):
            code = state.grid[y][x]
            if code in INITIAL_OCCUPIED_CODES:
                settlements += 1
                reasons.add("occupied")
            if is_coastal_land(state.grid, x, y):
                coastline_cells += 1
                reasons.add("coast")
            frontier = frontier_score(state, x, y)
            frontier_cells += frontier
            if frontier:
                reasons.add("frontier")
            support_value = combined_settlement_support_score(state, x, y)
            trade_value = _trade_candidate_score(state, x, y)
            winter_value = _winter_candidate_score(state, x, y)
            reclaim_value = _reclaim_candidate_score(state, x, y)
            support_sum += support_value
            trade_sum += trade_value
            winter_sum += winter_value
            reclaim_sum += reclaim_value
            if trade_value > 0.40:
                reasons.add("trade")
            if winter_value > 0.45:
                reasons.add("winter")
            if reclaim_value > 0.40:
                reasons.add("reclaim")
            if entropy_map is not None:
                entropy = entropy_map[y][x]
                under_sample_bonus = 1.0
                if observed_counts is not None:
                    under_sample_bonus = 1.0 / (1.0 + observed_counts.get((x, y), 0) * 0.75)
                    overlap_penalty += max(0, observed_counts.get((x, y), 0) - 1) * 0.03
                expected_information_gain += entropy * _dynamic_cell_weight(state, x, y) * under_sample_bonus
            if hotspot_scores is not None:
                bonus = hotspot_scores.get((x, y), 0.0)
                if bonus > 0.0:
                    expected_information_gain += bonus
                    reasons.add("stats")
    base_score = (
        settlements * 6.6
        + coastline_cells * 0.05
        + frontier_cells * 0.45
        + support_sum * 0.65
        + trade_sum * 0.55
        + winter_sum * 0.45
        + reclaim_sum * 0.50
    )
    total_score = base_score + expected_information_gain - overlap_penalty
    if expected_information_gain > 0.0:
        reasons.add("uncertainty")
    return PlannedWindow(
        viewport_x=viewport_x,
        viewport_y=viewport_y,
        viewport_w=viewport_w,
        viewport_h=viewport_h,
        score=round(total_score, 4),
        settlement_count=settlements,
        coastline_cells=coastline_cells,
        frontier_cells=frontier_cells,
        cluster_id=cluster_id,
        expected_information_gain=round(expected_information_gain, 4),
        overlap_penalty=round(overlap_penalty, 4),
        selection_reasons=sorted(reasons),
    )


def _overlap_ratio(left: PlannedWindow, right: PlannedWindow) -> float:
    x_overlap = max(
        0,
        min(left.viewport_x + left.viewport_w, right.viewport_x + right.viewport_w)
        - max(left.viewport_x, right.viewport_x),
    )
    y_overlap = max(
        0,
        min(left.viewport_y + left.viewport_h, right.viewport_y + right.viewport_h)
        - max(left.viewport_y, right.viewport_y),
    )
    intersection = x_overlap * y_overlap
    if not intersection:
        return 0.0
    return intersection / (left.viewport_w * left.viewport_h)


def _candidate_windows(
    state: SeedInitialState,
    *,
    viewport_size: int,
    extra_centers: list[tuple[int, int]] | None = None,
    entropy_map: list[list[float]] | None = None,
    observed_counts: dict[tuple[int, int], int] | None = None,
    hotspot_scores: dict[tuple[int, int], float] | None = None,
) -> list[PlannedWindow]:
    candidates: dict[tuple[int, int], PlannedWindow] = {}
    cluster_lookup: dict[tuple[int, int], int] = {}
    for cluster_id, cluster in enumerate(_cluster_settlements(state)):
        for point in cluster:
            cluster_lookup[point] = cluster_id
    for center_x, center_y in _candidate_centers(state, viewport_size=viewport_size, extra_centers=extra_centers):
        cluster_id = cluster_lookup.get((center_x, center_y), 0)
        window = _build_window(
            state,
            center_x=center_x,
            center_y=center_y,
            viewport_size=viewport_size,
            cluster_id=cluster_id,
            entropy_map=entropy_map,
            observed_counts=observed_counts,
            hotspot_scores=hotspot_scores,
        )
        key = (window.viewport_x, window.viewport_y)
        existing = candidates.get(key)
        if existing is None or window.score > existing.score:
            candidates[key] = window
    return sorted(candidates.values(), key=lambda item: item.score, reverse=True)


def _seed_diversity_score(state: SeedInitialState) -> float:
    clusters = _cluster_settlements(state)
    coastal_settlements = sum(
        1
        for settlement in state.settlements
        if settlement.alive and is_coastal_land(state.grid, settlement.x, settlement.y)
    )
    inland_isolated = sum(
        1
        for settlement in state.settlements
        if settlement.alive
        and not is_coastal_land(state.grid, settlement.x, settlement.y)
        and settlement_density(state, settlement.x, settlement.y) <= 1
    )
    return round(
        1.0 + len(clusters) * 1.4 + len(state.settlements) * 0.30 + coastal_settlements * 0.70 + inland_isolated * 0.50,
        4,
    )


def _flatten_phase_queries(phases: list[ObservationPhasePlan]) -> list[PlannedQuery]:
    queries = [query for phase in phases for query in phase.queries]
    for index, query in enumerate(queries, start=1):
        query.query_index = index
    return queries


def build_phase1_observation_plan(round_detail: RoundDetail, *, viewport_size: int = 15) -> ObservationPhasePlan:
    seed_plans: list[SeedObservationPlan] = []
    queries: list[PlannedQuery] = []
    query_index = 0
    for seed_index, state in enumerate(round_detail.initial_states):
        windows = []
        for candidate in _candidate_windows(state, viewport_size=viewport_size):
            if all(_overlap_ratio(candidate, existing) <= 0.25 for existing in windows):
                windows.append(candidate)
            if len(windows) == 2:
                break
        if len(windows) < 2:
            windows = _candidate_windows(state, viewport_size=viewport_size)[:2]
        seed_queries: list[PlannedQuery] = []
        for repeat_index in range(2):
            for cluster_rank, window in enumerate(windows, start=1):
                query_index += 1
                seed_queries.append(
                    PlannedQuery(
                        query_index=query_index,
                        seed_index=seed_index,
                        viewport_x=window.viewport_x,
                        viewport_y=window.viewport_y,
                        viewport_w=window.viewport_w,
                        viewport_h=window.viewport_h,
                        repeat_index=repeat_index + 1,
                        cluster_rank=cluster_rank,
                        purpose=f"phase1 sample seed {seed_index} cluster {cluster_rank}",
                        phase="phase1",
                        selection_reason="mechanics-aware discovery window repeated for distribution sampling",
                        expected_information_gain=window.expected_information_gain,
                    )
                )
        seed_plans.append(
            SeedObservationPlan(
                phase="phase1",
                seed_index=seed_index,
                diversity_score=_seed_diversity_score(state),
                allocated_queries=4,
                cluster_windows=windows,
                queries=seed_queries,
                selection_reason="two mechanics-aware discovery windows repeated twice",
            )
        )
        queries.extend(seed_queries)
    return ObservationPhasePlan(
        phase="phase1",
        description="Discovery sampling with two top non-overlapping trade, winter, and reclaim windows per seed, each repeated twice.",
        budget=len(queries),
        seed_plans=seed_plans,
        queries=queries,
    )


def _entropy_maps(bundle: PredictionBundle) -> list[list[list[float]]]:
    return [[[prediction_entropy(cell) for cell in row] for row in seed.prediction] for seed in bundle.seeds]


def _top_entropy_centers(
    entropy_map: list[list[float]],
    *,
    limit: int = 12,
    spacing: int = 5,
) -> list[tuple[int, int]]:
    ranked = sorted(
        ((entropy_map[y][x], x, y) for y in range(len(entropy_map)) for x in range(len(entropy_map[0]))),
        reverse=True,
    )
    centers: list[tuple[int, int]] = []
    for _, x, y in ranked:
        if all(abs(x - other_x) + abs(y - other_y) >= spacing for other_x, other_y in centers):
            centers.append((x, y))
        if len(centers) == limit:
            break
    return centers


def build_cell_observation_counts(
    round_detail: RoundDetail, observations: ObservationCollection
) -> list[dict[tuple[int, int], int]]:
    counts = [defaultdict(int) for _ in round_detail.initial_states]
    for sample in observations.samples:
        seed_index = sample.planned_query.seed_index
        viewport = sample.result.viewport
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, _ in enumerate(row):
                counts[seed_index][(viewport.x + col_offset, viewport.y + row_offset)] += 1
    return counts


_normalized_metric = normalize_metric


def _phase2_hotspot_data(
    round_detail: RoundDetail,
    observations: ObservationCollection,
) -> tuple[list[list[tuple[int, int]]], list[dict[tuple[int, int], float]]]:
    extra_centers: list[list[tuple[int, int]]] = [[] for _ in round_detail.initial_states]
    hotspot_scores: list[dict[tuple[int, int], float]] = [defaultdict(float) for _ in round_detail.initial_states]
    food_values: list[float] = []
    wealth_values: list[float] = []
    defense_values: list[float] = []
    tech_values: list[float] = []

    for sample in observations.samples:
        for settlement in sample.result.settlements:
            if settlement.food is not None:
                food_values.append(settlement.food)
            if settlement.wealth is not None:
                wealth_values.append(settlement.wealth)
            if settlement.defense is not None:
                defense_values.append(settlement.defense)
            if settlement.tech_level is not None:
                tech_values.append(settlement.tech_level)

    owner_positions: list[list[tuple[int, int, int]]] = [[] for _ in round_detail.initial_states]
    for sample in observations.samples:
        seed_index = sample.planned_query.seed_index
        state = round_detail.initial_states[seed_index]
        for settlement in sample.result.settlements:
            food = _normalized_metric(food_values, settlement.food)
            wealth = _normalized_metric(wealth_values, settlement.wealth)
            defense = _normalized_metric(defense_values, settlement.defense)
            tech = _normalized_metric(tech_values, settlement.tech_level)
            coastal = is_coastal_land(state.grid, settlement.x, settlement.y)
            stress = (1.0 - food) * 0.55 + (1.0 - defense) * 0.45
            prosperity = wealth * 0.45 + tech * 0.35 + food * 0.20
            if coastal and settlement.has_port:
                hotspot_scores[seed_index][(settlement.x, settlement.y)] += 0.60 + prosperity
                extra_centers[seed_index].append((settlement.x, settlement.y))
            elif coastal and prosperity > 0.60:
                hotspot_scores[seed_index][(settlement.x, settlement.y)] += 0.35 + prosperity * 0.60
                extra_centers[seed_index].append((settlement.x, settlement.y))
            if stress > 0.55:
                hotspot_scores[seed_index][(settlement.x, settlement.y)] += 0.45 + stress
                extra_centers[seed_index].append((settlement.x, settlement.y))
            if settlement.owner_id is not None:
                owner_positions[seed_index].append((settlement.x, settlement.y, settlement.owner_id))

        viewport = sample.result.viewport
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, code in enumerate(row):
                world_x = viewport.x + col_offset
                world_y = viewport.y + row_offset
                if code != 3:
                    continue
                coastal_bonus = 0.45 if is_coastal_land(state.grid, world_x, world_y) else 0.20
                hotspot_scores[seed_index][(world_x, world_y)] += (
                    coastal_bonus + ruin_rebuild_support_score(state, world_x, world_y) * 0.60
                )
                extra_centers[seed_index].append((world_x, world_y))

    for seed_index, positions in enumerate(owner_positions):
        for index, (left_x, left_y, left_owner) in enumerate(positions):
            for right_x, right_y, right_owner in positions[index + 1 :]:
                if left_owner == right_owner:
                    continue
                distance = abs(left_x - right_x) + abs(left_y - right_y)
                if distance > 14:
                    continue
                midpoint = (round((left_x + right_x) / 2), round((left_y + right_y) / 2))
                hotspot_scores[seed_index][midpoint] += 0.90
                extra_centers[seed_index].append(midpoint)
    return extra_centers, hotspot_scores


def build_phase2_observation_plan(
    round_detail: RoundDetail,
    *,
    phase1_observations: ObservationCollection,
    provisional_predictions: PredictionBundle,
    uncertainty_summaries: list[SeedUncertaintySummary],
    viewport_size: int = 15,
    total_queries: int = 30,
) -> ObservationPhasePlan:
    entropy_maps = _entropy_maps(provisional_predictions)
    observed_counts = build_cell_observation_counts(round_detail, phase1_observations)
    diversity_scores = [_seed_diversity_score(state) for state in round_detail.initial_states]
    uncertainty_by_seed = {summary.seed_index: summary for summary in uncertainty_summaries}
    hotspot_centers, hotspot_scores = _phase2_hotspot_data(round_detail, phase1_observations)

    candidate_windows_by_seed: list[list[PlannedWindow]] = []
    for seed_index, state in enumerate(round_detail.initial_states):
        candidate_windows_by_seed.append(
            _candidate_windows(
                state,
                viewport_size=viewport_size,
                extra_centers=_top_entropy_centers(entropy_maps[seed_index]) + hotspot_centers[seed_index],
                entropy_map=entropy_maps[seed_index],
                observed_counts=observed_counts[seed_index],
                hotspot_scores=hotspot_scores[seed_index],
            )
        )

    selected_queries_by_seed: list[list[PlannedQuery]] = [[] for _ in round_detail.initial_states]
    selected_windows_by_seed: list[list[PlannedWindow]] = [[] for _ in round_detail.initial_states]
    usage_counts: list[dict[tuple[int, int], int]] = [defaultdict(int) for _ in round_detail.initial_states]
    window_ranks: list[dict[tuple[int, int], int]] = [dict() for _ in round_detail.initial_states]

    query_index = 0
    baseline_queries_per_seed = 2
    for seed_index, state in enumerate(round_detail.initial_states):
        candidate_windows = candidate_windows_by_seed[seed_index]
        baseline_windows: list[PlannedWindow] = []
        for candidate in candidate_windows:
            if all(_overlap_ratio(candidate, existing) <= 0.30 for existing in baseline_windows):
                baseline_windows.append(candidate)
            if len(baseline_windows) == baseline_queries_per_seed:
                break
        if len(baseline_windows) < baseline_queries_per_seed:
            baseline_windows = candidate_windows[:baseline_queries_per_seed]
        for window in baseline_windows:
            key = (window.viewport_x, window.viewport_y)
            usage_counts[seed_index][key] += 1
            if key not in window_ranks[seed_index]:
                window_ranks[seed_index][key] = len(window_ranks[seed_index]) + 1
                selected_windows_by_seed[seed_index].append(window)
            query_index += 1
            selected_queries_by_seed[seed_index].append(
                PlannedQuery(
                    query_index=query_index,
                    seed_index=seed_index,
                    viewport_x=window.viewport_x,
                    viewport_y=window.viewport_y,
                    viewport_w=window.viewport_w,
                    viewport_h=window.viewport_h,
                    repeat_index=usage_counts[seed_index][key],
                    cluster_rank=window_ranks[seed_index][key],
                    purpose=f"phase2 baseline seed {seed_index}",
                    phase="phase2",
                    selection_reason=", ".join(window.selection_reasons) or "mechanics-aware adaptive window",
                    expected_information_gain=window.expected_information_gain,
                )
            )

    remaining = total_queries - baseline_queries_per_seed * len(round_detail.initial_states)
    while remaining > 0:
        best_seed: int | None = None
        best_window: PlannedWindow | None = None
        best_score = float("-inf")
        for seed_index, candidate_windows in enumerate(candidate_windows_by_seed):
            seed_multiplier = (
                1.0 + uncertainty_by_seed[seed_index].uncertainty_score * 0.25 + diversity_scores[seed_index] * 0.04
            )
            for candidate in candidate_windows:
                key = (candidate.viewport_x, candidate.viewport_y)
                current_repeats = usage_counts[seed_index][key]
                if current_repeats >= 4:
                    continue
                repeat_penalty = current_repeats * 0.95
                score = candidate.score * seed_multiplier - repeat_penalty
                if score > best_score:
                    best_score = score
                    best_seed = seed_index
                    best_window = candidate
        if best_seed is None or best_window is None:
            break
        key = (best_window.viewport_x, best_window.viewport_y)
        usage_counts[best_seed][key] += 1
        if key not in window_ranks[best_seed]:
            window_ranks[best_seed][key] = len(window_ranks[best_seed]) + 1
            selected_windows_by_seed[best_seed].append(best_window)
        query_index += 1
        selected_queries_by_seed[best_seed].append(
            PlannedQuery(
                query_index=query_index,
                seed_index=best_seed,
                viewport_x=best_window.viewport_x,
                viewport_y=best_window.viewport_y,
                viewport_w=best_window.viewport_w,
                viewport_h=best_window.viewport_h,
                repeat_index=usage_counts[best_seed][key],
                cluster_rank=window_ranks[best_seed][key],
                purpose=f"phase2 adaptive seed {best_seed}",
                phase="phase2",
                selection_reason=", ".join(best_window.selection_reasons) or "adaptive uncertainty window",
                expected_information_gain=best_window.expected_information_gain,
            )
        )
        remaining -= 1

    seed_plans: list[SeedObservationPlan] = []
    queries: list[PlannedQuery] = []
    for seed_index in range(len(round_detail.initial_states)):
        seed_queries = selected_queries_by_seed[seed_index]
        seed_plans.append(
            SeedObservationPlan(
                phase="phase2",
                seed_index=seed_index,
                diversity_score=diversity_scores[seed_index],
                allocated_queries=len(seed_queries),
                cluster_windows=selected_windows_by_seed[seed_index],
                queries=seed_queries,
                uncertainty_score=uncertainty_by_seed[seed_index].uncertainty_score,
                selection_reason="adaptive repeats ranked by entropy, trade, winter stress, conflict, and ruin branching",
            )
        )
        queries.extend(seed_queries)
    return ObservationPhasePlan(
        phase="phase2",
        description="Adaptive repeated sampling using entropy, observed settlement stats, and reclaim/trade/winter hotspots after phase1.",
        budget=total_queries,
        seed_plans=seed_plans,
        queries=queries,
    )


def build_two_phase_observation_plan(
    phase1: ObservationPhasePlan, phase2: ObservationPhasePlan, *, round_id: int, viewport_size: int = 15
) -> ObservationPlan:
    phases = [phase1, phase2]
    return ObservationPlan(
        round_id=round_id,
        viewport_size=viewport_size,
        max_queries=sum(phase.budget for phase in phases),
        phases=phases,
        queries=_flatten_phase_queries(phases),
    )


def build_observation_collection(round_id: int, samples: list[ObservationSample]) -> ObservationCollection:
    per_seed_class_counts: dict[int, list[int]] = defaultdict(lambda: [0] * 6)
    observed_cells: dict[int, int] = defaultdict(int)
    unique_cells: dict[int, set[tuple[int, int]]] = defaultdict(set)
    query_counts: dict[int, int] = defaultdict(int)
    phase_query_counts: dict[str, int] = defaultdict(int)
    seed_phase_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sample in samples:
        seed_index = sample.planned_query.seed_index
        query_counts[seed_index] += 1
        phase_query_counts[sample.planned_query.phase] += 1
        seed_phase_counts[seed_index][sample.planned_query.phase] += 1
        viewport = sample.result.viewport
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, code in enumerate(row):
                world_x = viewport.x + col_offset
                world_y = viewport.y + row_offset
                per_seed_class_counts[seed_index][terrain_code_to_class_index(code)] += 1
                observed_cells[seed_index] += 1
                unique_cells[seed_index].add((world_x, world_y))
    summaries = [
        SeedObservationSummary(
            seed_index=seed_index,
            query_count=query_counts[seed_index],
            observed_cells=observed_cells[seed_index],
            unique_observed_cells=len(unique_cells[seed_index]),
            class_counts=per_seed_class_counts[seed_index],
            phase_query_counts=dict(seed_phase_counts[seed_index]),
        )
        for seed_index in sorted({sample.planned_query.seed_index for sample in samples})
    ]
    return ObservationCollection(
        round_id=round_id,
        total_queries=len(samples),
        samples=samples,
        per_seed=summaries,
        phase_query_counts=dict(phase_query_counts),
    )
