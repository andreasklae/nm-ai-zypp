from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from astar_island.models import (
    LatentProxySummary,
    ObservationCollection,
    PredictionBundle,
    PredictionDiagnostics,
    PredictorParameters,
    RoundDetail,
    SeedInitialState,
    SeedPrediction,
    SeedUncertaintySummary,
    SettlementObservation,
)
from astar_island.terrain import (
    EMPTY_CODES,
    FOREST_CODES,
    INITIAL_OCCUPIED_CODES,
    MOUNTAIN_CODES,
    OCEAN_CODES,
    VACANT_CODES,
    SeedFeatureGrid,
    build_feature_grid,
    coastal_corridor_score,
    normalize_distribution,
    normalize_metric,
    prediction_entropy,
    terrain_code_to_class_index,
)

ProbabilityTensor = list[list[list[float]]]
FloatGrid = list[list[float]]
ArchiveRound = tuple[RoundDetail, ObservationCollection]

SUPPORT_RADIUS = 4
WINTER_RADIUS = 5
TRADE_RADIUS = 7
REBUILD_RADIUS = 4
CONFLICT_RADIUS = 4


@dataclass(slots=True)
class _ObservedCell:
    counts: list[int] = field(default_factory=lambda: [0] * 6)
    samples: int = 0

    def add(self, class_index: int) -> None:
        self.counts[class_index] += 1
        self.samples += 1


@dataclass(slots=True)
class _ObservedSettlementAggregate:
    x: int
    y: int
    samples: int = 0
    alive_sum: float = 0.0
    alive_count: int = 0
    port_sum: float = 0.0
    port_count: int = 0
    population_sum: float = 0.0
    population_count: int = 0
    food_sum: float = 0.0
    food_count: int = 0
    wealth_sum: float = 0.0
    wealth_count: int = 0
    defense_sum: float = 0.0
    defense_count: int = 0
    tech_sum: float = 0.0
    tech_count: int = 0
    owner_counts: Counter[int] = field(default_factory=Counter)

    def add(self, settlement: SettlementObservation) -> None:
        self.samples += 1
        if settlement.alive is not None:
            self.alive_sum += float(settlement.alive)
            self.alive_count += 1
        if settlement.has_port is not None:
            self.port_sum += float(settlement.has_port)
            self.port_count += 1
        if settlement.population is not None:
            self.population_sum += settlement.population
            self.population_count += 1
        if settlement.food is not None:
            self.food_sum += settlement.food
            self.food_count += 1
        if settlement.wealth is not None:
            self.wealth_sum += settlement.wealth
            self.wealth_count += 1
        if settlement.defense is not None:
            self.defense_sum += settlement.defense
            self.defense_count += 1
        if settlement.tech_level is not None:
            self.tech_sum += settlement.tech_level
            self.tech_count += 1
        if settlement.owner_id is not None:
            self.owner_counts[settlement.owner_id] += 1


@dataclass(slots=True)
class _ObservedSettlementSummary:
    x: int
    y: int
    alive: float
    has_port: float
    population: float
    food: float
    wealth: float
    defense: float
    tech_level: float
    owner_id: int | None
    owner_diversity: float


@dataclass(slots=True)
class _InfluenceMaps:
    support_map: FloatGrid
    winter_risk_map: FloatGrid
    trade_map: FloatGrid
    rebuild_map: FloatGrid
    conflict_map: FloatGrid


class PredictionModel(Protocol):
    model_name: str

    def predict(
        self, round_detail: RoundDetail, observations: ObservationCollection | None = None
    ) -> PredictionBundle: ...


class BaselinePredictor:
    model_name = "hierarchical-empirical-v4-transition"

    def __init__(
        self,
        *,
        floor: float = 0.01,
        parameters: PredictorParameters | None = None,
        archive_rounds: list[ArchiveRound] | None = None,
    ) -> None:
        self.floor = floor
        self.parameters = parameters or PredictorParameters()
        self.archive_rounds = archive_rounds or []

    def predict(self, round_detail: RoundDetail, observations: ObservationCollection | None = None) -> PredictionBundle:
        bundle, _ = self.predict_with_diagnostics(round_detail, observations)
        return bundle

    def predict_with_diagnostics(
        self,
        round_detail: RoundDetail,
        observations: ObservationCollection | None = None,
    ) -> tuple[PredictionBundle, PredictionDiagnostics]:
        observed_cells = _build_observed_cell_index(round_detail, observations)
        observed_counts = _build_observed_count_index(round_detail, observations)
        observed_settlements = _build_observed_settlement_index(round_detail, observations)
        proxy_source = _phase1_only_observations(round_detail, observations)
        proxy_settlements = _build_observed_settlement_index(round_detail, proxy_source)

        feature_grids = [build_feature_grid(state) for state in round_detail.initial_states]

        latent_proxies = _derive_latent_proxies(
            round_detail,
            proxy_source,
            proxy_settlements,
            feature_grids=feature_grids,
            legacy_trade_proxy_mode=self.parameters.legacy_trade_proxy_mode,
        )
        exact_indexes, relaxed_indexes = _build_feature_indexes(round_detail, observed_cells, feature_grids)
        round_transition_indexes = _build_round_transition_indexes(round_detail, observed_cells, feature_grids)
        transition_indexes = _build_transition_indexes(round_detail, observed_cells, feature_grids)
        if self.archive_rounds:
            transition_indexes = _merge_transition_indexes(
                transition_indexes,
                _build_archive_transition_indexes(self.archive_rounds),
            )
        family_indexes = _build_family_indexes(round_detail, observed_cells, feature_grids)
        cross_seed_index = _build_cross_seed_family_index(round_detail, observed_cells, feature_grids)

        seeds: list[SeedPrediction] = []
        tensors: list[ProbabilityTensor] = []
        for seed_index, state in enumerate(round_detail.initial_states):
            features = feature_grids[seed_index]
            influence_maps = _build_influence_maps(state, observed_settlements[seed_index], latent_proxies, features)
            static_prior = self._build_static_prior(state, influence_maps, features)
            adjusted_prior = _apply_latent_proxy_shift(
                state,
                static_prior,
                latent_proxies,
                floor=self.floor,
                strength=self.parameters.latent_proxy_strength,
                features=features,
            )
            if self.parameters.use_local_influence_pass:
                adjusted_prior = _apply_local_influence_maps(
                    state,
                    adjusted_prior,
                    influence_maps,
                    latent_proxies,
                    floor=self.floor,
                    features=features,
                )
            adjusted_prior = _apply_transition_policy(
                state,
                adjusted_prior,
                influence_maps=influence_maps,
                floor=self.floor,
                aggressive=self.parameters.aggressive_transition_policy,
                features=features,
            )
            observed_direct_posteriors: dict[tuple[int, int], tuple[list[float], int]] = {}

            tensor: ProbabilityTensor = []
            for y in range(round_detail.map_height):
                row: list[list[float]] = []
                for x in range(round_detail.map_width):
                    code = state.grid[y][x]
                    prior = adjusted_prior[y][x]
                    if code in OCEAN_CODES or code in MOUNTAIN_CODES:
                        row.append(prior)
                        continue
                    direct = observed_cells[seed_index].get((x, y))
                    if direct is not None:
                        direct_posterior = _blend_distributions(
                            [
                                (
                                    direct_distribution(
                                        direct, prior, self.floor, alpha=self.parameters.transition_alpha
                                    ),
                                    self.parameters.direct_observation_weight,
                                ),
                                (prior, max(0.01, 1.0 - self.parameters.direct_observation_weight)),
                            ],
                            self.floor,
                        )
                        row.append(
                            _apply_transition_policy_to_cell(
                                state,
                                x=x,
                                y=y,
                                distribution=direct_posterior,
                                influence_maps=influence_maps,
                                floor=self.floor,
                                aggressive=self.parameters.aggressive_transition_policy,
                                features=features,
                            )
                        )
                        observed_direct_posteriors[(x, y)] = (row[-1], direct.samples)
                        continue
                    exact = _lookup_exact_distribution(
                        round_detail,
                        seed_index,
                        x,
                        y,
                        exact_indexes,
                        prior,
                        self.floor,
                        alpha=self.parameters.transition_alpha,
                        features=feature_grids,
                    )
                    relaxed = _lookup_relaxed_distribution(
                        round_detail,
                        seed_index,
                        x,
                        y,
                        relaxed_indexes,
                        prior,
                        self.floor,
                        alpha=self.parameters.transition_alpha,
                        features=feature_grids,
                    )
                    round_transition = _lookup_round_transition_distribution(
                        round_detail,
                        seed_index,
                        x,
                        y,
                        round_transition_indexes,
                        prior,
                        self.floor,
                        alpha=self.parameters.round_transition_alpha,
                        features=feature_grids,
                    )
                    transition = _lookup_transition_distribution(
                        round_detail,
                        seed_index,
                        x,
                        y,
                        transition_indexes,
                        prior,
                        self.floor,
                        alpha=self.parameters.transition_alpha,
                        features=feature_grids,
                    )
                    nearest = _nearest_family_distribution(
                        round_detail,
                        seed_index=seed_index,
                        x=x,
                        y=y,
                        family_indexes=family_indexes,
                        fallback=prior,
                        floor=self.floor,
                        alpha=self.parameters.transition_alpha,
                    )
                    cross_seed_nearest = (
                        _cross_seed_family_distribution(
                            round_detail,
                            seed_index=seed_index,
                            x=x,
                            y=y,
                            cross_seed_index=cross_seed_index,
                            fallback=prior,
                            floor=self.floor,
                            alpha=self.parameters.transition_alpha,
                            features=feature_grids,
                        )
                        if self.parameters.cross_seed_nearest_weight > 0
                        else None
                    )
                    components: list[tuple[list[float], float]] = [(prior, self.parameters.prior_weight)]
                    if exact is not None:
                        components.append((exact, self.parameters.exact_weight))
                    if relaxed is not None:
                        components.append((relaxed, self.parameters.relaxed_weight))
                    if round_transition is not None and self.parameters.round_transition_weight > 0.0:
                        local_round_weight = self.parameters.round_transition_weight
                        if code in INITIAL_OCCUPIED_CODES:
                            local_round_weight *= 1.25
                        elif features.frontier[y][x] > 0:
                            local_round_weight *= 1.10
                        components.append((round_transition, local_round_weight))
                    if transition is not None:
                        components.append((transition, self.parameters.transition_weight))
                    if nearest is not None:
                        components.append((nearest, self.parameters.nearest_weight))
                    if cross_seed_nearest is not None and self.parameters.cross_seed_nearest_weight > 0:
                        components.append((cross_seed_nearest, self.parameters.cross_seed_nearest_weight))
                    posterior = _blend_distributions(components, self.floor)
                    row.append(
                        _apply_transition_policy_to_cell(
                            state,
                            x=x,
                            y=y,
                            distribution=posterior,
                            influence_maps=influence_maps,
                            floor=self.floor,
                            aggressive=self.parameters.aggressive_transition_policy,
                            features=features,
                        )
                    )
                tensor.append(row)

            smoothed = _spatially_smooth_non_static(
                state,
                tensor,
                influence_maps=influence_maps,
                floor=self.floor,
                parameters=self.parameters,
                features=features,
            )
            if self.parameters.use_local_influence_pass:
                smoothed = _apply_local_influence_maps(
                    state,
                    smoothed,
                    influence_maps,
                    latent_proxies,
                    floor=self.floor,
                    features=features,
                )
            smoothed = _apply_transition_policy(
                state,
                smoothed,
                influence_maps=influence_maps,
                floor=self.floor,
                aggressive=self.parameters.aggressive_transition_policy,
                features=features,
            )
            smoothed = _restore_observed_direct_posteriors(
                smoothed,
                observed_direct_posteriors,
                floor=self.floor,
            )
            tensors.append(smoothed)
            seeds.append(
                SeedPrediction(
                    seed_index=seed_index,
                    height=round_detail.map_height,
                    width=round_detail.map_width,
                    prediction=smoothed,
                )
            )

        uncertainty_summaries = _build_uncertainty_summaries(round_detail, tensors, observed_counts, feature_grids)
        bundle = PredictionBundle(round_id=round_detail.id, model_name=self.model_name, floor=self.floor, seeds=seeds)
        diagnostics = PredictionDiagnostics(
            latent_proxies=latent_proxies,
            uncertainty_summaries=uncertainty_summaries,
        )
        return bundle, diagnostics

    def _build_static_prior(
        self, state: SeedInitialState, influence_maps: _InfluenceMaps, features: SeedFeatureGrid
    ) -> ProbabilityTensor:
        tensor: ProbabilityTensor = []
        for y, row in enumerate(state.grid):
            output_row: list[list[float]] = []
            for x, code in enumerate(row):
                output_row.append(
                    self._static_distribution_for_cell(
                        state,
                        x=x,
                        y=y,
                        code=code,
                        influence_maps=influence_maps,
                        features=features,
                    )
                )
            tensor.append(output_row)
        return tensor

    def _static_distribution_for_cell(
        self,
        state: SeedInitialState,
        *,
        x: int,
        y: int,
        code: int,
        influence_maps: _InfluenceMaps,
        features: SeedFeatureGrid,
    ) -> list[float]:
        coastal = features.coastal[y][x]
        support = influence_maps.support_map[y][x]
        water_score = max(features.ocean_score[y][x], features.corridor_score[y][x])
        forest_score = features.forest_score[y][x]
        rebuild = influence_maps.rebuild_map[y][x]
        winter = influence_maps.winter_risk_map[y][x]
        trade = influence_maps.trade_map[y][x]
        conflict = influence_maps.conflict_map[y][x]
        frontier = min(1.0, features.frontier[y][x] / 4.0)
        density = min(1.0, features.density[y][x] / 3.0)
        distance = max(0.0, 1.0 - min(features.distance[y][x], 8) / 8.0)
        mountain_penalty = features.adj_mountain[y][x] / 4.0
        forest_adj = features.adj_forest[y][x] / 4.0
        barren = max(0.0, 0.55 - support - forest_score * 0.15 - water_score * 0.10)

        if code in MOUNTAIN_CODES:
            return normalize_distribution([0.0, 0.0, 0.0, 0.0, 0.0, 1.0], floor=self.floor)
        if code in OCEAN_CODES:
            return normalize_distribution([1.0, 0.0, 0.0, 0.0, 0.0, 0.0], floor=self.floor)
        if code == 2:
            return normalize_distribution(
                [
                    0.08 + winter * 0.06,
                    0.18 + support * 0.10 + trade * 0.08,
                    0.46 + trade * 0.18 + water_score * 0.10,
                    0.14 + winter * 0.18 + conflict * 0.10,
                    0.10 + rebuild * 0.08,
                    0.0,
                ],
                floor=self.floor,
            )
        if code == 1:
            return normalize_distribution(
                [
                    0.18 + winter * 0.10,
                    0.28 + support * 0.18 + density * 0.06 + trade * 0.03,
                    0.00 + (0.10 if coastal else 0.0) + trade * (0.08 if coastal else 0.01),
                    0.20 + winter * 0.18 + conflict * 0.12,
                    0.18 + rebuild * 0.12 + forest_score * 0.04,
                    0.0,
                ],
                floor=self.floor,
            )
        if code == 3:
            return normalize_distribution(
                [
                    0.20 + winter * 0.06,
                    0.10 + support * 0.16 + rebuild * 0.24,
                    0.00
                    + (0.10 if coastal else 0.0)
                    + rebuild * (0.12 if coastal else 0.02)
                    + trade * (0.08 if coastal else 0.0),
                    0.22 + conflict * 0.12 + winter * 0.10,
                    0.26 + forest_score * 0.18 + rebuild * 0.08 + forest_adj * 0.06,
                    0.0,
                ],
                floor=self.floor,
            )
        if code in FOREST_CODES:
            return normalize_distribution(
                [
                    0.20 + (1.0 - support) * 0.08 + winter * 0.03,
                    0.02 + support * 0.05 + distance * 0.03,
                    0.00 + (0.02 if coastal else 0.0) + trade * 0.02,
                    0.03 + conflict * 0.04,
                    0.73 + forest_score * 0.16 + forest_adj * 0.08 + rebuild * 0.04,
                    0.0,
                ],
                floor=self.floor,
            )
        if code in EMPTY_CODES:
            return normalize_distribution(
                [
                    0.70 + barren * 0.18 + winter * 0.04 + mountain_penalty * 0.08,
                    0.05 + support * 0.20 + forest_score * 0.06 + water_score * 0.08 + density * 0.04 + distance * 0.04,
                    0.00 + (0.12 if coastal else 0.0) + trade * 0.10 + water_score * (0.05 if coastal else 0.0),
                    0.04 + winter * 0.12 + conflict * 0.10 + frontier * 0.04,
                    0.01 + forest_score * 0.16 + rebuild * 0.08 + forest_adj * 0.04,
                    0.0,
                ],
                floor=self.floor,
            )
        return normalize_distribution(
            [
                0.64 + barren * 0.10,
                0.10 + support * 0.14,
                0.00 + (0.06 if coastal else 0.0),
                0.08 + winter * 0.06 + conflict * 0.06,
                0.06 + forest_score * 0.08,
                0.0,
            ],
            floor=self.floor,
        )


class LegacyMechanicsPredictor(BaselinePredictor):
    model_name = "hierarchical-empirical-v3-mechanics"

    def __init__(self, *, floor: float = 0.01, archive_rounds: list[ArchiveRound] | None = None) -> None:
        super().__init__(
            floor=floor,
            archive_rounds=archive_rounds,
            parameters=PredictorParameters(
                direct_observation_weight=0.86,
                prior_weight=0.38,
                exact_weight=0.26,
                relaxed_weight=0.20,
                transition_weight=0.0,
                nearest_weight=0.16,
                transition_alpha=2.0,
                latent_proxy_strength=1.0,
                smoothing_base=0.05,
                smoothing_dynamic_scale=0.028,
                use_local_influence_pass=True,
                aggressive_transition_policy=True,
                legacy_trade_proxy_mode=True,
            ),
        )

    def _build_static_prior(
        self, state: SeedInitialState, influence_maps: _InfluenceMaps, features: SeedFeatureGrid
    ) -> ProbabilityTensor:
        tensor: ProbabilityTensor = []
        for y, row in enumerate(state.grid):
            output_row: list[list[float]] = []
            for x, code in enumerate(row):
                output_row.append(
                    self._static_distribution_for_cell(
                        state, x=x, y=y, code=code, influence_maps=influence_maps, features=features
                    )
                )
            tensor.append(output_row)
        return tensor

    def _static_distribution_for_cell(
        self,
        state: SeedInitialState,
        *,
        x: int,
        y: int,
        code: int,
        influence_maps: _InfluenceMaps,
        features: SeedFeatureGrid,
    ) -> list[float]:
        coastal = features.coastal[y][x]
        water_score = max(features.ocean_score[y][x], features.corridor_score[y][x])
        forest_score = features.forest_score[y][x]
        mountain_penalty = features.adj_mountain[y][x] / 4.0
        support = influence_maps.support_map[y][x]
        winter_risk = influence_maps.winter_risk_map[y][x]
        trade = influence_maps.trade_map[y][x]
        rebuild = influence_maps.rebuild_map[y][x]
        conflict = influence_maps.conflict_map[y][x]
        frontier = min(1.0, features.frontier[y][x] / 4.0)
        density = min(1.0, features.density[y][x] / 3.0)
        distance = max(0.0, 1.0 - min(features.distance[y][x], 8) / 8.0)

        if code in MOUNTAIN_CODES:
            return normalize_distribution([0.0, 0.0, 0.0, 0.0, 0.0, 1.0], floor=self.floor)
        if code in OCEAN_CODES:
            return normalize_distribution([1.0, 0.0, 0.0, 0.0, 0.0, 0.0], floor=self.floor)
        if code == 2:
            return normalize_distribution(
                [
                    0.04 + winter_risk * 0.03,
                    0.18 + support * 0.12 + trade * 0.08,
                    0.56 + trade * 0.20 + water_score * 0.08,
                    0.12 + winter_risk * 0.16 + conflict * 0.12,
                    0.02 + rebuild * 0.04,
                    0.0,
                ],
                floor=self.floor,
            )
        if code == 1:
            return normalize_distribution(
                [
                    0.05 + winter_risk * 0.05,
                    0.44 + support * 0.16 + trade * 0.05,
                    0.02 + (0.14 if coastal else 0.02) + trade * (0.12 if coastal else 0.02),
                    0.18 + winter_risk * 0.24 + conflict * 0.18,
                    0.03 + rebuild * 0.07,
                    0.0,
                ],
                floor=self.floor,
            )
        if code == 3:
            return normalize_distribution(
                [
                    0.18 + winter_risk * 0.06,
                    0.14 + support * 0.18 + rebuild * 0.24,
                    0.01
                    + (0.12 if coastal else 0.01)
                    + trade * (0.18 if coastal else 0.02)
                    + rebuild * (0.12 if coastal else 0.03),
                    0.26 + conflict * 0.16 + winter_risk * 0.10,
                    0.22 + forest_score * 0.16 + rebuild * 0.10,
                    0.0,
                ],
                floor=self.floor,
            )
        if code in FOREST_CODES:
            return normalize_distribution(
                [
                    0.12 + winter_risk * 0.02,
                    0.03 + support * 0.04,
                    0.00 + (0.03 if coastal else 0.0) + trade * 0.03,
                    0.04 + conflict * 0.04,
                    0.72 + forest_score * 0.22 + rebuild * 0.05,
                    0.0,
                ],
                floor=self.floor,
            )
        if code in EMPTY_CODES:
            barren = max(0.0, 0.45 - support)
            return normalize_distribution(
                [
                    0.74 - support * 0.16 - trade * 0.06 + barren * 0.18 + mountain_penalty * 0.10,
                    0.06 + support * 0.28 + forest_score * 0.12 + water_score * 0.12 + density * 0.05 + distance * 0.06,
                    0.00 + (0.14 if coastal else 0.0) + trade * 0.14 + water_score * (0.05 if coastal else 0.0),
                    0.03 + winter_risk * 0.12 + conflict * 0.10 + frontier * 0.06,
                    0.00 + rebuild * 0.06 + forest_score * 0.04,
                    0.0,
                ],
                floor=self.floor,
            )
        return normalize_distribution(
            [
                0.70,
                0.10 + support * 0.10,
                0.02 + (0.06 if coastal else 0.0),
                0.08 + conflict * 0.06 + winter_risk * 0.04,
                0.08 + forest_score * 0.05,
                0.0,
            ],
            floor=self.floor,
        )


def _average_or_none(total: float, count: int) -> float | None:
    if count <= 0:
        return None
    return total / count


def _build_observed_cell_index(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
) -> list[dict[tuple[int, int], _ObservedCell]]:
    seed_data: list[dict[tuple[int, int], _ObservedCell]] = [
        defaultdict(_ObservedCell) for _ in round_detail.initial_states
    ]
    if observations is None:
        return seed_data
    for sample in observations.samples:
        seed_index = sample.planned_query.seed_index
        viewport = sample.result.viewport
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, code in enumerate(row):
                world_x = viewport.x + col_offset
                world_y = viewport.y + row_offset
                seed_data[seed_index][(world_x, world_y)].add(terrain_code_to_class_index(code))
    return seed_data


def _build_observed_count_index(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
) -> list[dict[tuple[int, int], int]]:
    counts = [defaultdict(int) for _ in round_detail.initial_states]
    if observations is None:
        return counts
    for sample in observations.samples:
        viewport = sample.result.viewport
        seed_index = sample.planned_query.seed_index
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, _ in enumerate(row):
                counts[seed_index][(viewport.x + col_offset, viewport.y + row_offset)] += 1
    return counts


def _build_observed_settlement_index(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
) -> list[list[_ObservedSettlementSummary]]:
    seed_aggregates: list[dict[tuple[int, int], _ObservedSettlementAggregate]] = [
        {} for _ in round_detail.initial_states
    ]
    if observations is not None:
        for sample in observations.samples:
            seed_index = sample.planned_query.seed_index
            for settlement in sample.result.settlements:
                key = (settlement.x, settlement.y)
                aggregate = seed_aggregates[seed_index].get(key)
                if aggregate is None:
                    aggregate = _ObservedSettlementAggregate(x=settlement.x, y=settlement.y)
                    seed_aggregates[seed_index][key] = aggregate
                aggregate.add(settlement)

    population_values: list[float] = []
    food_values: list[float] = []
    wealth_values: list[float] = []
    defense_values: list[float] = []
    tech_values: list[float] = []
    raw_by_seed: list[list[_ObservedSettlementAggregate]] = []
    for aggregates in seed_aggregates:
        aggregate_list = list(aggregates.values())
        raw_by_seed.append(aggregate_list)
        for aggregate in aggregate_list:
            population = _average_or_none(aggregate.population_sum, aggregate.population_count)
            food = _average_or_none(aggregate.food_sum, aggregate.food_count)
            wealth = _average_or_none(aggregate.wealth_sum, aggregate.wealth_count)
            defense = _average_or_none(aggregate.defense_sum, aggregate.defense_count)
            tech = _average_or_none(aggregate.tech_sum, aggregate.tech_count)
            if population is not None:
                population_values.append(population)
            if food is not None:
                food_values.append(food)
            if wealth is not None:
                wealth_values.append(wealth)
            if defense is not None:
                defense_values.append(defense)
            if tech is not None:
                tech_values.append(tech)

    output: list[list[_ObservedSettlementSummary]] = []
    for aggregate_list in raw_by_seed:
        summaries: list[_ObservedSettlementSummary] = []
        for aggregate in aggregate_list:
            owner_total = sum(aggregate.owner_counts.values())
            dominant_owner = aggregate.owner_counts.most_common(1)[0][0] if aggregate.owner_counts else None
            owner_diversity = 0.0
            if owner_total > 0 and aggregate.owner_counts:
                owner_diversity = 1.0 - (aggregate.owner_counts.most_common(1)[0][1] / owner_total)
            summaries.append(
                _ObservedSettlementSummary(
                    x=aggregate.x,
                    y=aggregate.y,
                    alive=_average_or_none(aggregate.alive_sum, aggregate.alive_count) or 0.5,
                    has_port=_average_or_none(aggregate.port_sum, aggregate.port_count) or 0.5,
                    population=normalize_metric(
                        population_values, _average_or_none(aggregate.population_sum, aggregate.population_count)
                    ),
                    food=normalize_metric(food_values, _average_or_none(aggregate.food_sum, aggregate.food_count)),
                    wealth=normalize_metric(
                        wealth_values, _average_or_none(aggregate.wealth_sum, aggregate.wealth_count)
                    ),
                    defense=normalize_metric(
                        defense_values, _average_or_none(aggregate.defense_sum, aggregate.defense_count)
                    ),
                    tech_level=normalize_metric(
                        tech_values, _average_or_none(aggregate.tech_sum, aggregate.tech_count)
                    ),
                    owner_id=dominant_owner,
                    owner_diversity=max(0.0, min(1.0, owner_diversity)),
                )
            )
        output.append(summaries)
    return output


def _phase1_only_observations(
    round_detail: RoundDetail, observations: ObservationCollection | None
) -> ObservationCollection | None:
    if observations is None:
        return None
    phase1_samples = [sample for sample in observations.samples if sample.planned_query.phase == "phase1"]
    if not phase1_samples:
        return observations
    return ObservationCollection(
        round_id=round_detail.id,
        total_queries=len(phase1_samples),
        samples=phase1_samples,
        per_seed=[],
        phase_query_counts={"phase1": len(phase1_samples)},
    )


def _terrain_group(code: int) -> str:
    if code in OCEAN_CODES:
        return "ocean"
    if code in MOUNTAIN_CODES:
        return "mountain"
    if code == 3:
        return "ruin"
    if code == 2:
        return "port"
    if code == 1:
        return "settlement"
    if code in FOREST_CODES:
        return "forest"
    return "vacant"


def _distance_band(distance: int) -> str:
    if distance == 0:
        return "0"
    if distance <= 2:
        return "1_2"
    if distance <= 4:
        return "3_4"
    if distance <= 6:
        return "5_6"
    return "7_plus"


def _density_band(density: int) -> str:
    if density == 0:
        return "0"
    if density == 1:
        return "1"
    if density == 2:
        return "2"
    return "3_plus"


def _score_band(value: float) -> str:
    if value <= 0.15:
        return "low"
    if value <= 0.40:
        return "mid"
    if value <= 0.70:
        return "high"
    return "very_high"


def _feature_parts(
    state: SeedInitialState, x: int, y: int, features: SeedFeatureGrid | None = None
) -> tuple[str, str, str, str, str, str]:
    code = state.grid[y][x]
    if features is not None:
        return (
            _terrain_group(code),
            f"coast:{int(features.coastal[y][x])}",
            f"dist:{_distance_band(features.distance[y][x])}",
            f"density:{_density_band(features.density[y][x])}",
            f"frontier:{int(features.frontier[y][x] > 0)}",
            f"occupied:{int(code in INITIAL_OCCUPIED_CODES)}",
        )
    from astar_island.terrain import distance_to_nearest_settlement, frontier_score, is_coastal_land, settlement_density

    return (
        _terrain_group(code),
        f"coast:{int(is_coastal_land(state.grid, x, y))}",
        f"dist:{_distance_band(distance_to_nearest_settlement(state, x, y))}",
        f"density:{_density_band(settlement_density(state, x, y))}",
        f"frontier:{int(frontier_score(state, x, y) > 0)}",
        f"occupied:{int(code in INITIAL_OCCUPIED_CODES)}",
    )


def _relaxed_feature_keys(parts: tuple[str, str, str, str, str, str]) -> list[tuple[str, ...]]:
    terrain, coast, distance, density, frontier, occupied = parts
    return [
        parts,
        (terrain, coast, distance, density),
        (terrain, coast, distance),
        (terrain, coast),
        (terrain,),
        (terrain, coast, frontier, occupied),
    ]


def _transition_feature_parts(
    state: SeedInitialState, x: int, y: int, features: SeedFeatureGrid | None = None
) -> tuple[str, str, str, str, str, str, str]:
    if features is not None:
        return (
            _terrain_group(state.grid[y][x]),
            f"coast:{int(features.coastal[y][x])}",
            f"forest:{_score_band(features.forest_score[y][x])}",
            f"water:{_score_band(max(features.ocean_score[y][x], features.corridor_score[y][x]))}",
            f"dist:{_distance_band(features.distance[y][x])}",
            f"density:{_density_band(features.density[y][x])}",
            f"frontier:{int(features.frontier[y][x] > 0)}",
        )
    from astar_island.terrain import (
        coastal_corridor_score as _ccs,
    )
    from astar_island.terrain import (
        distance_to_nearest_settlement,
        frontier_score,
        is_coastal_land,
        nearby_forest_score,
        nearby_ocean_score,
        settlement_density,
    )

    return (
        _terrain_group(state.grid[y][x]),
        f"coast:{int(is_coastal_land(state.grid, x, y))}",
        f"forest:{_score_band(nearby_forest_score(state.grid, x, y, radius=3))}",
        f"water:{_score_band(max(nearby_ocean_score(state.grid, x, y, radius=3), _ccs(state.grid, x, y, radius=3)))}",
        f"dist:{_distance_band(distance_to_nearest_settlement(state, x, y))}",
        f"density:{_density_band(settlement_density(state, x, y))}",
        f"frontier:{int(frontier_score(state, x, y) > 0)}",
    )


def _transition_feature_keys(parts: tuple[str, str, str, str, str, str, str]) -> list[tuple[str, ...]]:
    terrain, coast, forest, water, distance, density, frontier = parts
    return [
        parts,
        (terrain, coast, forest, water, distance),
        (terrain, coast, forest, water),
        (terrain, coast, distance),
        (terrain, coast),
        (terrain,),
        (terrain, forest, water, frontier),
    ]


def _round_transition_feature_parts(
    state: SeedInitialState, x: int, y: int, features: SeedFeatureGrid | None = None
) -> tuple[str, str, str, str, str]:
    if features is not None:
        return (
            _terrain_group(state.grid[y][x]),
            f"coast:{int(features.coastal[y][x])}",
            f"frontier:{int(features.frontier[y][x] > 0)}",
            f"dist:{_distance_band(features.distance[y][x])}",
            f"density:{_density_band(features.density[y][x])}",
        )
    from astar_island.terrain import distance_to_nearest_settlement, frontier_score, is_coastal_land, settlement_density

    return (
        _terrain_group(state.grid[y][x]),
        f"coast:{int(is_coastal_land(state.grid, x, y))}",
        f"frontier:{int(frontier_score(state, x, y) > 0)}",
        f"dist:{_distance_band(distance_to_nearest_settlement(state, x, y))}",
        f"density:{_density_band(settlement_density(state, x, y))}",
    )


def _round_transition_feature_keys(parts: tuple[str, str, str, str, str]) -> list[tuple[str, ...]]:
    terrain, coast, frontier, distance, density = parts
    return [
        parts,
        (terrain, coast, frontier, distance),
        (terrain, coast, frontier),
        (terrain, coast),
        (terrain,),
        (terrain, frontier, distance),
    ]


def _build_feature_indexes(
    round_detail: RoundDetail,
    observed_cells: list[dict[tuple[int, int], _ObservedCell]],
    feature_grids: list[SeedFeatureGrid] | None = None,
) -> tuple[dict[tuple[str, ...], list[int]], list[dict[tuple[str, ...], list[int]]]]:
    exact: dict[tuple[str, ...], list[int]] = defaultdict(lambda: [0] * 6)
    relaxed: list[dict[tuple[str, ...], list[int]]] = [defaultdict(lambda: [0] * 6) for _ in range(5)]
    for seed_index, state in enumerate(round_detail.initial_states):
        fg = feature_grids[seed_index] if feature_grids else None
        for (x, y), observation in observed_cells[seed_index].items():
            keys = _relaxed_feature_keys(_feature_parts(state, x, y, fg))
            for class_index, count in enumerate(observation.counts):
                exact[keys[0]][class_index] += count
                for level, key in enumerate(keys[1:], start=0):
                    relaxed[level][key][class_index] += count
    return exact, relaxed


def _new_count_bucket() -> list[int]:
    return [0] * 6


def _build_transition_indexes(
    round_detail: RoundDetail,
    observed_cells: list[dict[tuple[int, int], _ObservedCell]],
    feature_grids: list[SeedFeatureGrid] | None = None,
) -> list[dict[tuple[str, ...], list[int]]]:
    indexes: list[dict[tuple[str, ...], list[int]]] = [defaultdict(_new_count_bucket) for _ in range(7)]
    for seed_index, state in enumerate(round_detail.initial_states):
        fg = feature_grids[seed_index] if feature_grids else None
        for (x, y), observation in observed_cells[seed_index].items():
            keys = _transition_feature_keys(_transition_feature_parts(state, x, y, fg))
            for class_index, count in enumerate(observation.counts):
                for level, key in enumerate(keys):
                    indexes[level][key][class_index] += count
    return indexes


def _build_round_transition_indexes(
    round_detail: RoundDetail,
    observed_cells: list[dict[tuple[int, int], _ObservedCell]],
    feature_grids: list[SeedFeatureGrid] | None = None,
) -> list[dict[tuple[str, ...], list[int]]]:
    indexes: list[dict[tuple[str, ...], list[int]]] = [defaultdict(_new_count_bucket) for _ in range(6)]
    for seed_index, state in enumerate(round_detail.initial_states):
        fg = feature_grids[seed_index] if feature_grids else None
        for (x, y), observation in observed_cells[seed_index].items():
            keys = _round_transition_feature_keys(_round_transition_feature_parts(state, x, y, fg))
            for class_index, count in enumerate(observation.counts):
                for level, key in enumerate(keys):
                    indexes[level][key][class_index] += count
    return indexes


def _build_archive_transition_indexes(archive_rounds: list[ArchiveRound]) -> list[dict[tuple[str, ...], list[int]]]:
    indexes: list[dict[tuple[str, ...], list[int]]] = [defaultdict(_new_count_bucket) for _ in range(7)]
    for round_detail, observations in archive_rounds:
        observed_cells = _build_observed_cell_index(round_detail, observations)
        round_indexes = _build_transition_indexes(round_detail, observed_cells)
        indexes = _merge_transition_indexes(indexes, round_indexes)
    return indexes


def _merge_transition_indexes(
    left: list[dict[tuple[str, ...], list[int]]],
    right: list[dict[tuple[str, ...], list[int]]],
) -> list[dict[tuple[str, ...], list[int]]]:
    output: list[dict[tuple[str, ...], list[int]]] = [
        defaultdict(_new_count_bucket) for _ in range(max(len(left), len(right)))
    ]
    for level, source in enumerate(left):
        for key, counts in source.items():
            for class_index, count in enumerate(counts):
                output[level][key][class_index] += count
    for level, source in enumerate(right):
        for key, counts in source.items():
            for class_index, count in enumerate(counts):
                output[level][key][class_index] += count
    return output


def _build_family_indexes(
    round_detail: RoundDetail,
    observed_cells: list[dict[tuple[int, int], _ObservedCell]],
    feature_grids: list[SeedFeatureGrid] | None = None,
) -> list[dict[tuple[str, bool], list[tuple[int, int, list[int]]]]]:
    output: list[dict[tuple[str, bool], list[tuple[int, int, list[int]]]]] = []
    for seed_index, state in enumerate(round_detail.initial_states):
        fg = feature_grids[seed_index] if feature_grids else None
        families: dict[tuple[str, bool], list[tuple[int, int, list[int]]]] = defaultdict(list)
        for (x, y), observation in observed_cells[seed_index].items():
            if fg is not None:
                key = (_terrain_group(state.grid[y][x]), fg.coastal[y][x])
            else:
                from astar_island.terrain import is_coastal_land

                key = (_terrain_group(state.grid[y][x]), is_coastal_land(state.grid, x, y))
            families[key].append((x, y, observation.counts))
        output.append(families)
    return output


def _build_cross_seed_family_index(
    round_detail: RoundDetail,
    observed_cells: list[dict[tuple[int, int], _ObservedCell]],
    feature_grids: list[SeedFeatureGrid] | None = None,
) -> dict[tuple[str, bool], list[tuple[int, int, list[int]]]]:
    families: dict[tuple[str, bool], list[tuple[int, int, list[int]]]] = defaultdict(list)
    for seed_index, state in enumerate(round_detail.initial_states):
        fg = feature_grids[seed_index] if feature_grids else None
        for (x, y), observation in observed_cells[seed_index].items():
            if fg is not None:
                key = (_terrain_group(state.grid[y][x]), fg.coastal[y][x])
            else:
                from astar_island.terrain import is_coastal_land

                key = (_terrain_group(state.grid[y][x]), is_coastal_land(state.grid, x, y))
            families[key].append((x, y, observation.counts))
    return families


def _cross_seed_family_distribution(
    round_detail: RoundDetail,
    *,
    seed_index: int,
    x: int,
    y: int,
    cross_seed_index: dict[tuple[str, bool], list[tuple[int, int, list[int]]]],
    fallback: list[float],
    floor: float,
    k: int = 8,
    alpha: float = 2.0,
    features: list[SeedFeatureGrid] | None = None,
) -> list[float] | None:
    state = round_detail.initial_states[seed_index]
    fg = features[seed_index] if features else None
    if fg is not None:
        family_key = (_terrain_group(state.grid[y][x]), fg.coastal[y][x])
    else:
        from astar_island.terrain import is_coastal_land

        family_key = (_terrain_group(state.grid[y][x]), is_coastal_land(state.grid, x, y))
    candidates = cross_seed_index.get(family_key, [])
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda item: abs(item[0] - x) + abs(item[1] - y))
    weighted = [0.0] * 6
    total_weight = 0.0
    for other_x, other_y, counts in ranked[:k]:
        distance = max(1, abs(other_x - x) + abs(other_y - y))
        distribution = _counts_to_distribution(counts, fallback, floor, alpha=alpha)
        if distribution is None:
            continue
        weight = 1.0 / distance
        total_weight += weight
        for index, value in enumerate(distribution):
            weighted[index] += value * weight
    if total_weight == 0:
        return None
    return normalize_distribution([value / total_weight for value in weighted], floor=floor)


def _counts_to_distribution(
    counts: list[int] | None,
    prior: list[float],
    floor: float,
    *,
    alpha: float = 2.0,
) -> list[float] | None:
    if counts is None or sum(counts) == 0:
        return None
    raw = [count + alpha * prior_value for count, prior_value in zip(counts, prior, strict=True)]
    return normalize_distribution(raw, floor=floor)


def direct_distribution(
    observation: _ObservedCell,
    prior: list[float],
    floor: float,
    *,
    alpha: float = 2.0,
) -> list[float]:
    return _counts_to_distribution(observation.counts, prior, floor, alpha=alpha) or prior


def _lookup_exact_distribution(
    round_detail: RoundDetail,
    seed_index: int,
    x: int,
    y: int,
    indexes: dict[tuple[str, ...], list[int]],
    prior: list[float],
    floor: float,
    *,
    alpha: float = 2.0,
    features: list[SeedFeatureGrid] | None = None,
) -> list[float] | None:
    state = round_detail.initial_states[seed_index]
    fg = features[seed_index] if features else None
    key = _relaxed_feature_keys(_feature_parts(state, x, y, fg))[0]
    return _counts_to_distribution(indexes.get(key), prior, floor, alpha=alpha)


def _lookup_relaxed_distribution(
    round_detail: RoundDetail,
    seed_index: int,
    x: int,
    y: int,
    indexes: list[dict[tuple[str, ...], list[int]]],
    prior: list[float],
    floor: float,
    *,
    alpha: float = 2.0,
    features: list[SeedFeatureGrid] | None = None,
) -> list[float] | None:
    state = round_detail.initial_states[seed_index]
    fg = features[seed_index] if features else None
    keys = _relaxed_feature_keys(_feature_parts(state, x, y, fg))[1:]
    for level, key in enumerate(keys):
        distribution = _counts_to_distribution(indexes[level].get(key), prior, floor, alpha=alpha)
        if distribution is not None:
            return distribution
    return None


def _lookup_transition_distribution(
    round_detail: RoundDetail,
    seed_index: int,
    x: int,
    y: int,
    indexes: list[dict[tuple[str, ...], list[int]]],
    prior: list[float],
    floor: float,
    *,
    alpha: float = 2.0,
    features: list[SeedFeatureGrid] | None = None,
) -> list[float] | None:
    state = round_detail.initial_states[seed_index]
    fg = features[seed_index] if features else None
    keys = _transition_feature_keys(_transition_feature_parts(state, x, y, fg))
    for level, key in enumerate(keys):
        distribution = _counts_to_distribution(indexes[level].get(key), prior, floor, alpha=alpha)
        if distribution is not None:
            return distribution
    return None


def _lookup_round_transition_distribution(
    round_detail: RoundDetail,
    seed_index: int,
    x: int,
    y: int,
    indexes: list[dict[tuple[str, ...], list[int]]],
    prior: list[float],
    floor: float,
    *,
    alpha: float = 0.9,
    features: list[SeedFeatureGrid] | None = None,
) -> list[float] | None:
    state = round_detail.initial_states[seed_index]
    fg = features[seed_index] if features else None
    keys = _round_transition_feature_keys(_round_transition_feature_parts(state, x, y, fg))
    for level, key in enumerate(keys):
        distribution = _counts_to_distribution(indexes[level].get(key), prior, floor, alpha=alpha)
        if distribution is not None:
            return distribution
    return None


def _nearest_family_distribution(
    round_detail: RoundDetail,
    *,
    seed_index: int,
    x: int,
    y: int,
    family_indexes: list[dict[tuple[str, bool], list[tuple[int, int, list[int]]]]],
    fallback: list[float],
    floor: float,
    k: int = 6,
    alpha: float = 2.0,
    features: list[SeedFeatureGrid] | None = None,
) -> list[float] | None:
    state = round_detail.initial_states[seed_index]
    fg = features[seed_index] if features else None
    if fg is not None:
        family_key = (_terrain_group(state.grid[y][x]), fg.coastal[y][x])
    else:
        from astar_island.terrain import is_coastal_land

        family_key = (_terrain_group(state.grid[y][x]), is_coastal_land(state.grid, x, y))
    candidates = family_indexes[seed_index].get(family_key, [])
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda item: abs(item[0] - x) + abs(item[1] - y))
    weighted = [0.0] * 6
    total_weight = 0.0
    for other_x, other_y, counts in ranked[:k]:
        distance = max(1, abs(other_x - x) + abs(other_y - y))
        distribution = _counts_to_distribution(counts, fallback, floor, alpha=alpha)
        if distribution is None:
            continue
        weight = 1.0 / distance
        total_weight += weight
        for index, value in enumerate(distribution):
            weighted[index] += value * weight
    if total_weight == 0:
        return None
    return normalize_distribution([value / total_weight for value in weighted], floor=floor)


def _blend_distributions(components: list[tuple[list[float], float]], floor: float) -> list[float]:
    total_weight = sum(weight for _, weight in components)
    raw = [0.0] * 6
    for values, weight in components:
        normalized_weight = weight / total_weight if total_weight else 0.0
        for index, value in enumerate(values):
            raw[index] += value * normalized_weight
    return normalize_distribution(raw, floor=floor)


def _average_metric(summaries: list[_ObservedSettlementSummary], accessor: str, *, default: float) -> float:
    if not summaries:
        return default
    values = [getattr(item, accessor) for item in summaries]
    return sum(values) / len(values)


def _derive_latent_proxies(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
    observed_settlements: list[list[_ObservedSettlementSummary]],
    *,
    feature_grids: list[SeedFeatureGrid] | None = None,
    legacy_trade_proxy_mode: bool = False,
) -> LatentProxySummary:
    if observations is None or not observations.samples:
        return LatentProxySummary(
            settlement_survival=0.50,
            ruin_intensity=0.18,
            port_prevalence=0.16,
            expansion_pressure=0.14,
            reclamation_rate=0.12,
            winter_severity=0.52,
            trade_strength=0.35,
            conflict_pressure=0.22,
            rebuild_strength=0.28,
        )

    occupied_total = occupied_alive = 0
    ruin_total = ruin_hits = 0
    coastal_dynamic_total = coastal_ports = 0
    expansion_total = expansion_hits = 0
    reclamation_total = reclamation_hits = 0
    for sample in observations.samples:
        seed_idx = sample.planned_query.seed_index
        state = round_detail.initial_states[seed_idx]
        fg = feature_grids[seed_idx] if feature_grids else None
        viewport = sample.result.viewport
        for row_offset, row in enumerate(sample.result.grid):
            for col_offset, code in enumerate(row):
                world_x = viewport.x + col_offset
                world_y = viewport.y + row_offset
                initial_code = state.grid[world_y][world_x]
                final_class = terrain_code_to_class_index(code)
                if initial_code in INITIAL_OCCUPIED_CODES:
                    occupied_total += 1
                    if final_class in {1, 2}:
                        occupied_alive += 1
                dyn_str = fg.dynamic_strength[world_y][world_x] if fg else _dynamic_strength(state, world_x, world_y)
                if dyn_str > 0.75:
                    ruin_total += 1
                    if final_class == 3:
                        ruin_hits += 1
                is_coast = (
                    fg.coastal[world_y][world_x] if fg else _is_coastal_land_fallback(state.grid, world_x, world_y)
                )
                if is_coast and initial_code not in (OCEAN_CODES | MOUNTAIN_CODES):
                    coastal_dynamic_total += 1
                    if final_class == 2:
                        coastal_ports += 1
                front = fg.frontier[world_y][world_x] if fg else _frontier_score_fallback(state, world_x, world_y)
                if initial_code in (EMPTY_CODES | FOREST_CODES) and front > 0:
                    expansion_total += 1
                    if final_class in {1, 2}:
                        expansion_hits += 1
                if initial_code in FOREST_CODES or initial_code == 3:
                    reclamation_total += 1
                    if final_class == 4:
                        reclamation_hits += 1

    settlement_summaries = [item for seed_items in observed_settlements for item in seed_items]
    mean_food = _average_metric(settlement_summaries, "food", default=0.5)
    mean_wealth = _average_metric(settlement_summaries, "wealth", default=0.5)
    mean_defense = _average_metric(settlement_summaries, "defense", default=0.5)
    mean_tech = _average_metric(settlement_summaries, "tech_level", default=0.5)
    mean_alive = _average_metric(settlement_summaries, "alive", default=0.5)
    mean_port = _average_metric(settlement_summaries, "has_port", default=0.5)
    mean_owner_diversity = _average_metric(settlement_summaries, "owner_diversity", default=0.2)
    port_like = [item for item in settlement_summaries if item.has_port > 0.35]
    port_signal = min(1.0, len(port_like) / 6.0)
    port_trade_quality = (
        sum(item.wealth * 0.45 + item.tech_level * 0.35 + item.food * 0.20 for item in port_like) / len(port_like)
        if port_like
        else (mean_wealth * 0.55 + mean_tech * 0.45)
    )

    settlement_survival = _safe_ratio(occupied_alive, occupied_total, default=0.50)
    ruin_intensity = _safe_ratio(ruin_hits, ruin_total, default=0.18)
    port_prevalence = _safe_ratio(coastal_ports, coastal_dynamic_total, default=0.16)
    expansion_pressure = _safe_ratio(expansion_hits, expansion_total, default=0.14)
    reclamation_rate = _safe_ratio(reclamation_hits, reclamation_total, default=0.12)

    winter_severity = max(
        0.01,
        min(
            0.99,
            (1.0 - mean_food) * 0.50
            + (1.0 - mean_defense) * 0.16
            + ruin_intensity * 0.18
            + (1.0 - settlement_survival) * 0.16,
        ),
    )
    if legacy_trade_proxy_mode:
        trade_strength = max(
            0.01,
            min(
                0.99,
                port_trade_quality * 0.55 + port_prevalence * 0.20 + mean_port * 0.15 + mean_alive * 0.10,
            ),
        )
    else:
        trade_strength = max(
            0.01,
            min(
                0.99,
                port_trade_quality * (0.10 + port_signal * 0.30)
                + port_prevalence * 0.30
                + mean_port * 0.20
                + mean_alive * 0.05,
            ),
        )
    conflict_pressure = max(
        0.01,
        min(
            0.99,
            mean_owner_diversity * 0.55 + ruin_intensity * 0.25 + (1.0 - mean_defense) * 0.20,
        ),
    )
    rebuild_strength = max(
        0.01,
        min(
            0.99,
            mean_wealth * 0.20
            + mean_tech * 0.20
            + settlement_survival * 0.18
            + expansion_pressure * 0.16
            + reclamation_rate * 0.12
            + trade_strength * 0.14,
        ),
    )
    return LatentProxySummary(
        settlement_survival=settlement_survival,
        ruin_intensity=ruin_intensity,
        port_prevalence=port_prevalence,
        expansion_pressure=expansion_pressure,
        reclamation_rate=reclamation_rate,
        winter_severity=winter_severity,
        trade_strength=trade_strength,
        conflict_pressure=conflict_pressure,
        rebuild_strength=rebuild_strength,
    )


def _is_coastal_land_fallback(grid: list[list[int]], x: int, y: int) -> bool:
    from astar_island.terrain import is_coastal_land

    return is_coastal_land(grid, x, y)


def _frontier_score_fallback(state: SeedInitialState, x: int, y: int) -> int:
    from astar_island.terrain import frontier_score

    return frontier_score(state, x, y)


def _support_fallback(state: SeedInitialState, x: int, y: int) -> float:
    from astar_island.terrain import combined_settlement_support_score

    return combined_settlement_support_score(state, x, y)


def _rebuild_support_fallback(state: SeedInitialState, x: int, y: int) -> float:
    from astar_island.terrain import ruin_rebuild_support_score

    return ruin_rebuild_support_score(state, x, y)


def _density_fallback(state: SeedInitialState, x: int, y: int) -> int:
    from astar_island.terrain import settlement_density

    return settlement_density(state, x, y)


def _ocean_score_fallback(grid: list[list[int]], x: int, y: int) -> float:
    from astar_island.terrain import nearby_ocean_score

    return nearby_ocean_score(grid, x, y, radius=3)


def _forest_score_fallback(grid: list[list[int]], x: int, y: int) -> float:
    from astar_island.terrain import nearby_forest_score

    return nearby_forest_score(grid, x, y, radius=3)


def _adj_forest_fallback(grid: list[list[int]], x: int, y: int) -> int:
    from astar_island.terrain import adjacent_forest_count

    return adjacent_forest_count(grid, x, y)


def _safe_ratio(numerator: int, denominator: int, *, default: float) -> float:
    if denominator <= 0:
        return default
    return max(0.01, min(0.99, numerator / denominator))


def _blank_grid(state: SeedInitialState, *, fill: float = 0.0) -> FloatGrid:
    return [[fill for _ in row] for row in state.grid]


def _observed_trade_strength(summary: _ObservedSettlementSummary) -> float:
    return max(
        0.0, min(1.0, summary.has_port * 0.35 + summary.wealth * 0.30 + summary.tech_level * 0.20 + summary.food * 0.15)
    )


def _observed_expansion_strength(summary: _ObservedSettlementSummary) -> float:
    return max(
        0.0,
        min(1.0, summary.food * 0.32 + summary.wealth * 0.20 + summary.population * 0.24 + summary.tech_level * 0.24),
    )


def _observed_winter_fragility(summary: _ObservedSettlementSummary) -> float:
    return max(
        0.0, min(1.0, (1.0 - summary.food) * 0.46 + (1.0 - summary.defense) * 0.30 + (1.0 - summary.alive) * 0.24)
    )


def _observed_rebuild_strength(summary: _ObservedSettlementSummary) -> float:
    return max(
        0.0,
        min(
            1.0,
            summary.food * 0.20
            + summary.wealth * 0.25
            + summary.tech_level * 0.20
            + summary.defense * 0.15
            + summary.alive * 0.20,
        ),
    )


def _spread_influence(
    grid: FloatGrid,
    *,
    center_x: int,
    center_y: int,
    radius: int,
    amplitude: float,
    state: SeedInitialState,
    cell_scale: Callable[[int, int], float] | None = None,
) -> None:
    height = len(state.grid)
    width = len(state.grid[0])
    if amplitude <= 0.0 or radius <= 0:
        return
    for y in range(max(0, center_y - radius), min(height, center_y + radius + 1)):
        for x in range(max(0, center_x - radius), min(width, center_x + radius + 1)):
            distance = abs(x - center_x) + abs(y - center_y)
            if distance > radius:
                continue
            decay = (radius + 1 - distance) / (radius + 1)
            scale = cell_scale(x, y) if cell_scale is not None else 1.0
            grid[y][x] += amplitude * decay * scale


def _clamp_grid(grid: FloatGrid) -> FloatGrid:
    return [[max(0.0, min(1.0, value)) for value in row] for row in grid]


def _build_influence_maps(
    state: SeedInitialState,
    observed_settlements: list[_ObservedSettlementSummary],
    proxies: LatentProxySummary,
    features: SeedFeatureGrid | None = None,
) -> _InfluenceMaps:
    support_map = _blank_grid(state)
    winter_risk_map = _blank_grid(state)
    trade_map = _blank_grid(state)
    rebuild_map = _blank_grid(state)
    conflict_map = _blank_grid(state)

    for y, row in enumerate(state.grid):
        for x, code in enumerate(row):
            if code in OCEAN_CODES or code in MOUNTAIN_CODES:
                continue
            support = features.support[y][x] if features else _support_fallback(state, x, y)
            support_map[y][x] = support
            dens = features.density[y][x] if features else _density_fallback(state, x, y)
            isolation = max(0.0, 1.0 - min(dens, 3) / 3.0)
            is_coast = features.coastal[y][x] if features else _is_coastal_land_fallback(state.grid, x, y)
            inland_penalty = 0.18 if not is_coast else 0.05
            winter_risk_map[y][x] = max(
                0.0,
                min(
                    1.0,
                    0.12 + (1.0 - support) * 0.42 + isolation * 0.18 + inland_penalty + proxies.winter_severity * 0.28,
                ),
            )
            corr = features.corridor_score[y][x] if features else coastal_corridor_score(state.grid, x, y, radius=3)
            ocean = features.ocean_score[y][x] if features else _ocean_score_fallback(state.grid, x, y)
            trade_map[y][x] = max(
                0.0,
                min(1.0, proxies.trade_strength * 0.25 + corr * 0.45 + ocean * 0.20),
            )
            rebuild_sup = features.rebuild_support[y][x] if features else _rebuild_support_fallback(state, x, y)
            rebuild_map[y][x] = max(
                0.0,
                min(1.0, rebuild_sup * 0.65 + proxies.rebuild_strength * 0.25 + (0.10 if code == 3 else 0.0)),
            )
            front = features.frontier[y][x] if features else _frontier_score_fallback(state, x, y)
            conflict_map[y][x] = max(
                0.0,
                min(
                    1.0,
                    proxies.conflict_pressure * 0.18
                    + (0.15 if front > 0 else 0.0)
                    + (0.08 if code in INITIAL_OCCUPIED_CODES else 0.0),
                ),
            )

    for summary in observed_settlements:
        support_amp = _observed_expansion_strength(summary) * (0.55 + proxies.expansion_pressure * 0.45)
        _spread_influence(
            support_map,
            center_x=summary.x,
            center_y=summary.y,
            radius=SUPPORT_RADIUS,
            amplitude=support_amp * 0.45,
            state=state,
            cell_scale=lambda x, y: (
                0.35 + (features.support[y][x] if features else _support_fallback(state, x, y)) * 0.65
            ),
        )

        winter_amp = _observed_winter_fragility(summary) * (0.55 + proxies.winter_severity * 0.45)
        _spread_influence(
            winter_risk_map,
            center_x=summary.x,
            center_y=summary.y,
            radius=WINTER_RADIUS,
            amplitude=winter_amp * 0.42,
            state=state,
            cell_scale=lambda x, y: (
                0.55
                + (
                    0.25
                    if not (features.coastal[y][x] if features else _is_coastal_land_fallback(state.grid, x, y))
                    else 0.0
                )
            ),
        )

        s_coast = (
            features.coastal[summary.y][summary.x]
            if features
            else _is_coastal_land_fallback(state.grid, summary.x, summary.y)
        )
        if summary.has_port > 0.35 or s_coast:
            trade_amp = _observed_trade_strength(summary) * (0.55 + proxies.trade_strength * 0.45)
            _spread_influence(
                trade_map,
                center_x=summary.x,
                center_y=summary.y,
                radius=TRADE_RADIUS,
                amplitude=trade_amp * 0.48,
                state=state,
                cell_scale=lambda x, y: (
                    0.30
                    + (
                        features.corridor_score[y][x]
                        if features
                        else coastal_corridor_score(state.grid, x, y, radius=3)
                    )
                    * 0.70
                ),
            )

        rebuild_amp = _observed_rebuild_strength(summary) * (0.50 + proxies.rebuild_strength * 0.50)
        _spread_influence(
            rebuild_map,
            center_x=summary.x,
            center_y=summary.y,
            radius=REBUILD_RADIUS,
            amplitude=rebuild_amp * 0.45,
            state=state,
            cell_scale=lambda x, y: (
                0.25 + (features.rebuild_support[y][x] if features else _rebuild_support_fallback(state, x, y)) * 0.75
            ),
        )

        s_front = (
            features.frontier[summary.y][summary.x]
            if features
            else _frontier_score_fallback(state, summary.x, summary.y)
        )
        if s_front > 0 and _observed_winter_fragility(summary) > 0.55:
            _spread_influence(
                conflict_map,
                center_x=summary.x,
                center_y=summary.y,
                radius=CONFLICT_RADIUS,
                amplitude=(0.18 + _observed_winter_fragility(summary) * 0.22)
                * (0.50 + proxies.conflict_pressure * 0.50),
                state=state,
                cell_scale=lambda x, y: (
                    0.25
                    + min(1.0, (features.frontier[y][x] if features else _frontier_score_fallback(state, x, y)) / 4.0)
                    * 0.75
                ),
            )

    owner_observed = [item for item in observed_settlements if item.owner_id is not None]
    for index, left in enumerate(owner_observed):
        for right in owner_observed[index + 1 :]:
            if left.owner_id == right.owner_id:
                continue
            distance = abs(left.x - right.x) + abs(left.y - right.y)
            if distance > 14:
                continue
            mid_x = round((left.x + right.x) / 2)
            mid_y = round((left.y + right.y) / 2)
            amplitude = (0.30 + (left.owner_diversity + right.owner_diversity) * 0.35) * (
                0.55 + proxies.conflict_pressure * 0.45
            )
            _spread_influence(
                conflict_map,
                center_x=mid_x,
                center_y=mid_y,
                radius=CONFLICT_RADIUS,
                amplitude=amplitude,
                state=state,
                cell_scale=lambda x, y: (
                    0.25
                    + min(1.0, (features.frontier[y][x] if features else _frontier_score_fallback(state, x, y)) / 4.0)
                    * 0.75
                ),
            )

    return _InfluenceMaps(
        support_map=_clamp_grid(support_map),
        winter_risk_map=_clamp_grid(winter_risk_map),
        trade_map=_clamp_grid(trade_map),
        rebuild_map=_clamp_grid(rebuild_map),
        conflict_map=_clamp_grid(conflict_map),
    )


def _scaled_multiplier(multiplier: float, strength: float) -> float:
    return 1.0 + (multiplier - 1.0) * max(0.0, min(1.0, strength))


def _apply_latent_proxy_shift(
    state: SeedInitialState,
    prior: ProbabilityTensor,
    proxies: LatentProxySummary,
    *,
    floor: float,
    strength: float,
    features: SeedFeatureGrid | None = None,
) -> ProbabilityTensor:
    adjusted: ProbabilityTensor = []
    for y, row in enumerate(prior):
        output_row: list[list[float]] = []
        for x, distribution in enumerate(row):
            code = state.grid[y][x]
            if code in OCEAN_CODES or code in MOUNTAIN_CODES:
                output_row.append(distribution)
                continue
            coastal = features.coastal[y][x] if features else _is_coastal_land_fallback(state.grid, x, y)
            frontier = (features.frontier[y][x] > 0) if features else (_frontier_score_fallback(state, x, y) > 0)
            current = list(distribution)
            if code in INITIAL_OCCUPIED_CODES:
                current[1] *= _scaled_multiplier(0.68 + proxies.settlement_survival * 1.35, strength)
                current[2] *= _scaled_multiplier(
                    0.60 + proxies.trade_strength * (1.35 if coastal else 0.15) + proxies.port_prevalence * 0.60,
                    strength,
                )
                current[3] *= _scaled_multiplier(
                    0.64 + proxies.ruin_intensity * 1.55 + proxies.winter_severity * 0.45,
                    strength,
                )
            if frontier:
                current[1] *= _scaled_multiplier(0.72 + proxies.expansion_pressure * 1.60, strength)
                current[2] *= _scaled_multiplier(0.80 + proxies.trade_strength * (0.90 if coastal else 0.15), strength)
                current[3] *= _scaled_multiplier(
                    0.72 + proxies.conflict_pressure * 0.95 + proxies.ruin_intensity * 0.65,
                    strength,
                )
            if code in FOREST_CODES or code == 3:
                current[4] *= _scaled_multiplier(
                    0.74 + proxies.reclamation_rate * 1.50 + proxies.rebuild_strength * 0.20,
                    strength,
                )
            if coastal:
                current[1] *= _scaled_multiplier(
                    0.94 + proxies.trade_strength * 0.28 + proxies.port_prevalence * 0.18, strength
                )
                current[2] *= _scaled_multiplier(
                    0.82 + proxies.port_prevalence * 0.80 + proxies.trade_strength * 0.70, strength
                )
            current[3] *= _scaled_multiplier(0.84 + proxies.winter_severity * 0.38, strength)
            output_row.append(normalize_distribution(current, floor=floor))
        adjusted.append(output_row)
    return adjusted


def _apply_local_influence_maps(
    state: SeedInitialState,
    tensor: ProbabilityTensor,
    influence_maps: _InfluenceMaps,
    proxies: LatentProxySummary,
    *,
    floor: float,
    features: SeedFeatureGrid | None = None,
) -> ProbabilityTensor:
    output: ProbabilityTensor = []
    for y, row in enumerate(tensor):
        output_row: list[list[float]] = []
        for x, distribution in enumerate(row):
            code = state.grid[y][x]
            if code in OCEAN_CODES or code in MOUNTAIN_CODES:
                output_row.append(distribution)
                continue
            coastal = features.coastal[y][x] if features else _is_coastal_land_fallback(state.grid, x, y)
            support = influence_maps.support_map[y][x]
            winter = influence_maps.winter_risk_map[y][x]
            trade = influence_maps.trade_map[y][x]
            rebuild = influence_maps.rebuild_map[y][x]
            conflict = influence_maps.conflict_map[y][x]
            current = list(distribution)
            current[0] *= 0.84 + (1.0 - support) * 0.32 + winter * 0.10
            current[1] *= 0.78 + support * 0.92 + trade * 0.18 + proxies.expansion_pressure * 0.18
            current[2] *= (
                0.55 + trade * (1.25 if coastal else 0.12) + rebuild * (0.55 if code == 3 and coastal else 0.05)
            )
            current[3] *= 0.74 + winter * 0.90 + conflict * 0.86 + proxies.conflict_pressure * 0.14
            current[4] *= 0.78 + rebuild * (0.85 if code == 3 else 0.25) + (0.35 if code in FOREST_CODES else 0.0)
            if code in FOREST_CODES:
                current[1] *= 0.40 + support * 0.15
                current[2] *= 0.18 if coastal else 0.05
                current[4] *= 1.45
            if code in INITIAL_OCCUPIED_CODES:
                current[1] *= 1.00 + trade * 0.16 + support * 0.12
                current[3] *= 0.90 + winter * 0.28
            output_row.append(normalize_distribution(current, floor=floor))
        output.append(output_row)
    return output


def _apply_transition_policy_to_cell(
    state: SeedInitialState,
    *,
    x: int,
    y: int,
    distribution: list[float],
    influence_maps: _InfluenceMaps,
    floor: float,
    aggressive: bool = False,
    features: SeedFeatureGrid | None = None,
) -> list[float]:
    code = state.grid[y][x]
    if code in OCEAN_CODES:
        return normalize_distribution([1.0, 0.0, 0.0, 0.0, 0.0, 0.0], floor=floor)
    if code in MOUNTAIN_CODES:
        return normalize_distribution([0.0, 0.0, 0.0, 0.0, 0.0, 1.0], floor=floor)

    coastal = features.coastal[y][x] if features else _is_coastal_land_fallback(state.grid, x, y)
    support = influence_maps.support_map[y][x]
    winter = influence_maps.winter_risk_map[y][x]
    rebuild = influence_maps.rebuild_map[y][x]
    conflict = influence_maps.conflict_map[y][x]
    current = list(distribution)

    if not coastal:
        current[2] = 0.0

    if code in VACANT_CODES:
        front = features.frontier[y][x] if features else _frontier_score_fallback(state, x, y)
        forest_unlock = rebuild + conflict * 0.25 + winter * 0.20 + min(1.0, front / 4.0)
        if aggressive:
            if distribution[4] < 0.34 and forest_unlock < 0.95:
                current[4] = 0.0
            current[1] *= 0.78 + support * 0.55
        else:
            f_score = features.forest_score[y][x] if features else _forest_score_fallback(state.grid, x, y)
            f_adj = features.adj_forest[y][x] if features else _adj_forest_fallback(state.grid, x, y)
            forest_support = max(f_score, f_adj / 4.0)
            current[4] *= 0.15 + forest_support * 0.95 + forest_unlock * 0.40
            current[1] *= 0.82 + support * 0.40
            current[0] *= 0.96 + (1.0 - support) * 0.10

    if code in FOREST_CODES:
        current[1] *= (0.28 + support * 0.20) if aggressive else (0.38 + support * 0.24)
        current[2] *= 0.10 if coastal else 0.0
        current[4] *= 1.65 if aggressive else 1.38
        current[0] *= 1.10 if aggressive else 1.08

    if code in INITIAL_OCCUPIED_CODES:
        current[4] *= (0.38 + rebuild * 0.28) if aggressive else (0.52 + rebuild * 0.24)
        current[3] *= 0.92 + winter * 0.30 + conflict * 0.22

    if code == 3:
        f_adj = features.adj_forest[y][x] if features else _adj_forest_fallback(state.grid, x, y)
        current[1] *= 1.00 + rebuild * 0.55 + support * 0.20
        current[2] *= 1.00 + (rebuild + influence_maps.trade_map[y][x]) * (0.75 if coastal else 0.05)
        current[4] *= 1.00 + (0.80 if f_adj > 0 else 0.30) + rebuild * 0.20

    current[5] = 0.0
    return normalize_distribution(current, floor=floor)


def _apply_transition_policy(
    state: SeedInitialState,
    tensor: ProbabilityTensor,
    *,
    influence_maps: _InfluenceMaps,
    floor: float,
    aggressive: bool = False,
    features: SeedFeatureGrid | None = None,
) -> ProbabilityTensor:
    output: ProbabilityTensor = []
    for y, row in enumerate(tensor):
        output_row: list[list[float]] = []
        for x, distribution in enumerate(row):
            output_row.append(
                _apply_transition_policy_to_cell(
                    state,
                    x=x,
                    y=y,
                    distribution=distribution,
                    influence_maps=influence_maps,
                    floor=floor,
                    aggressive=aggressive,
                    features=features,
                )
            )
        output.append(output_row)
    return output


def _dynamic_strength(state: SeedInitialState, x: int, y: int, features: SeedFeatureGrid | None = None) -> float:
    if features is not None:
        return features.dynamic_strength[y][x]
    code = state.grid[y][x]
    if code in OCEAN_CODES or code in MOUNTAIN_CODES:
        return 0.0
    strength = 0.25 + _support_fallback(state, x, y) * 1.35 + _rebuild_support_fallback(state, x, y) * 0.45
    if code in INITIAL_OCCUPIED_CODES:
        strength += 1.20
    if _frontier_score_fallback(state, x, y) > 0:
        strength += 0.75
    if _is_coastal_land_fallback(state.grid, x, y):
        strength += 0.40
    if code in FOREST_CODES or code == 3:
        strength += 0.35
    return strength


def _spatially_smooth_non_static(
    state: SeedInitialState,
    tensor: ProbabilityTensor,
    *,
    influence_maps: _InfluenceMaps,
    floor: float,
    parameters: PredictorParameters,
    features: SeedFeatureGrid | None = None,
) -> ProbabilityTensor:
    height = len(state.grid)
    width = len(state.grid[0])
    static_set = OCEAN_CODES | MOUNTAIN_CODES

    def _smooth_pass(source: ProbabilityTensor, strength_scale: float) -> ProbabilityTensor:
        result = [[list(cell) for cell in row] for row in source]
        for y in range(height):
            for x in range(width):
                if state.grid[y][x] in static_set:
                    continue
                neighbor_values: list[list[float]] = []
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx = x + dx
                    ny = y + dy
                    if 0 <= nx < width and 0 <= ny < height and state.grid[ny][nx] not in static_set:
                        neighbor_values.append(source[ny][nx])
                if not neighbor_values:
                    continue
                neighbor_average = [
                    sum(values[index] for values in neighbor_values) / len(neighbor_values) for index in range(6)
                ]
                local_support = influence_maps.support_map[y][x]
                local_trade = influence_maps.trade_map[y][x]
                dyn = features.dynamic_strength[y][x] if features else _dynamic_strength(state, x, y)
                strength = min(
                    0.24,
                    (
                        parameters.smoothing_base
                        + dyn * parameters.smoothing_dynamic_scale
                        + local_support * 0.02
                        + local_trade * 0.01
                    )
                    * strength_scale,
                )
                result[y][x] = _blend_distributions(
                    [(source[y][x], 1.0 - strength), (neighbor_average, strength)], floor
                )
        return result

    pass1 = _smooth_pass(tensor, 1.0)
    return _smooth_pass(pass1, 0.6)


def _restore_observed_direct_posteriors(
    tensor: ProbabilityTensor,
    observed_direct_posteriors: dict[tuple[int, int], tuple[list[float], int]],
    *,
    floor: float,
) -> ProbabilityTensor:
    if not observed_direct_posteriors:
        return tensor
    output = [[list(cell) for cell in row] for row in tensor]
    for (x, y), (posterior, samples) in observed_direct_posteriors.items():
        confidence = max(posterior)
        if confidence < 0.45:
            continue
        confidence_scale = min(1.0, max(0.0, (confidence - 0.45) / 0.35))
        strength = min(0.88, (0.42 + min(samples, 4) * 0.05) * confidence_scale)
        if strength <= 0.01:
            continue
        output[y][x] = _blend_distributions(
            [
                (posterior, strength),
                (tensor[y][x], 1.0 - strength),
            ],
            floor,
        )
    return output


def _build_uncertainty_summaries(
    round_detail: RoundDetail,
    tensors: list[ProbabilityTensor],
    observed_counts: list[dict[tuple[int, int], int]],
    feature_grids: list[SeedFeatureGrid] | None = None,
) -> list[SeedUncertaintySummary]:
    summaries: list[SeedUncertaintySummary] = []
    for seed_index, state in enumerate(round_detail.initial_states):
        fg = feature_grids[seed_index] if feature_grids else None
        dynamic_candidate_cells = 0
        under_sampled_dynamic_cells = 0
        observed_dynamic_cells = 0
        total_entropy = 0.0
        high_entropy_cells = 0
        for y, row in enumerate(tensors[seed_index]):
            for x, cell in enumerate(row):
                if _dynamic_strength(state, x, y, fg) <= 0.75:
                    continue
                dynamic_candidate_cells += 1
                entropy = prediction_entropy(cell)
                total_entropy += entropy
                if entropy >= 1.0:
                    high_entropy_cells += 1
                if observed_counts[seed_index].get((x, y), 0) > 0:
                    observed_dynamic_cells += 1
                else:
                    under_sampled_dynamic_cells += 1
        observed_fraction = observed_dynamic_cells / dynamic_candidate_cells if dynamic_candidate_cells else 0.0
        mean_entropy = total_entropy / dynamic_candidate_cells if dynamic_candidate_cells else 0.0
        uncertainty_score = mean_entropy * (1.0 + under_sampled_dynamic_cells / max(1, dynamic_candidate_cells))
        summaries.append(
            SeedUncertaintySummary(
                seed_index=seed_index,
                observed_fraction=round(observed_fraction, 4),
                dynamic_candidate_cells=dynamic_candidate_cells,
                under_sampled_dynamic_cells=under_sampled_dynamic_cells,
                mean_entropy=round(mean_entropy, 4),
                high_entropy_cells=high_entropy_cells,
                uncertainty_score=round(uncertainty_score, 4),
            )
        )
    return summaries
