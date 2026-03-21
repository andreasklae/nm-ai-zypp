"""Monte Carlo forward simulator for terrain predictions.

Estimates per-round parameters from observations, then samples independent cell outcomes
to build probability tensors. Hard constraints match the Bayesian predictor (ocean,
mountain, inland ports).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from astar_island.models import (
    LatentProxySummary,
    ObservationCollection,
    PredictionBundle,
    PredictionDiagnostics,
    RoundDetail,
    SeedInitialState,
    SeedPrediction,
    SeedUncertaintySummary,
    SimulationParameters,
)
from astar_island.predictor import (
    _apply_transition_policy,
    _build_observed_count_index,
    _build_influence_maps,
    _build_observed_settlement_index,
    _derive_latent_proxies,
    _phase1_only_observations,
)
from astar_island.terrain import (
    FOREST_CODES,
    INITIAL_OCCUPIED_CODES,
    MOUNTAIN_CODES,
    OCEAN_CODES,
    VACANT_CODES,
    SeedFeatureGrid,
    build_feature_grid,
    normalize_distribution,
    prediction_entropy,
    terrain_code_to_class_index,
)


def _smooth_probs(counts: list[float], *, alpha: float = 1.5) -> list[float]:
    adjusted = [c + alpha for c in counts]
    total = sum(adjusted)
    return [x / total for x in adjusted]


def _terrain_bucket(code: int) -> str:
    if code in OCEAN_CODES:
        return "ocean"
    if code in MOUNTAIN_CODES:
        return "mountain"
    if code in VACANT_CODES:
        return "vacant"
    if code in FOREST_CODES:
        return "forest"
    if code == 3:
        return "ruin"
    if code in INITIAL_OCCUPIED_CODES:
        return "occupied"
    return "other"


def _default_simulation_parameters(proxies: LatentProxySummary) -> SimulationParameters:
    """Fallback when there are no observations.

    Distributions are calibrated against ground truth from completed rounds.
    Key empirical findings (averaged across 4 rounds, 20 seeds):
    - Plains: 76.7% empty, 16.6% settlement, 1.0% port, 1.6% ruin, 4.2% forest
    - Forest: 8.8% empty, 17.4% settlement, 1.0% port, 1.7% ruin, 71.2% forest
    - Settlement survival ~33.5%, death → 63% empty, 5% ruin, 31% forest
    """
    exp = max(0.08, min(0.38, proxies.expansion_pressure * 1.1))

    # Vacant frontier: GT ~[0.72, 0.20, 0.01, 0.02, 0.05, 0.0] for frontier plains
    settle_vf = max(0.08, min(0.28, exp * 0.9))
    port_vf = max(0.005, min(0.04, proxies.port_prevalence * 0.15))
    ruin_vf = max(0.005, min(0.02, 0.01 + 0.01 * proxies.winter_severity))
    forest_vf = max(0.02, min(0.08, 0.04 + 0.08 * proxies.reclamation_rate))
    stay_vf = max(0.55, 1.0 - settle_vf - port_vf - ruin_vf - forest_vf)
    vf = [stay_vf, settle_vf - port_vf, port_vf, ruin_vf, forest_vf, 0.0]
    s = sum(vf)
    vf = [x / s for x in vf]

    # Vacant interior: GT ~[0.88, 0.09, 0.005, 0.01, 0.02, 0.0]
    settle_vi = max(0.03, min(0.15, exp * 0.45))
    stay_vi = max(0.80, 1.0 - settle_vi - 0.01 - 0.008 - 0.02)
    vi = [stay_vi, settle_vi, 0.005, 0.008, 0.02, 0.0]
    s = sum(vi)
    vi = [x / s for x in vi]

    # Forest frontier: GT ~[0.10, 0.20, 0.01, 0.02, 0.67, 0.0] — settlement colonization!
    settle_ff = max(0.10, min(0.30, exp * 1.2 + 0.06))
    port_ff = max(0.005, min(0.04, proxies.port_prevalence * 0.12))
    stay_ff = max(0.50, min(0.78, 0.70 - 0.35 * exp))
    ff = [0.08, settle_ff - port_ff, port_ff, 0.015, stay_ff, 0.0]
    s = sum(ff)
    ff = [x / s for x in ff]

    # Forest interior: GT ~[0.05, 0.10, 0.005, 0.01, 0.84, 0.0]
    fi = [0.05, 0.08 + exp * 0.15, 0.005, 0.01, 0.0, 0.0]
    fi[4] = max(0.70, 1.0 - sum(fi))
    s = sum(fi)
    fi = [x / s for x in fi]

    # Settlement survival: GT ~33.5% average, highly round-dependent
    p_surv = max(0.15, min(0.88, proxies.settlement_survival))
    # Death distribution: GT → 63.5% empty, 4.7% ruin, 31.1% forest, 0% mountain
    death = [0.63, 0.0, 0.0, 0.05, 0.31, 0.01]
    ds = sum(death)
    death = [x / ds for x in death]

    # Ruin outcome distribution (for initial ruins, not settlement→ruin)
    ruin_raw = [0.20, 0.15 * proxies.rebuild_strength, 0.06 * proxies.port_prevalence, 0.25, 0.30, 0.0]
    rs = sum(ruin_raw)
    ruin_dist = [x / rs for x in ruin_raw]

    return SimulationParameters(
        settlement_survival_rate=p_surv,
        settlement_death_class_distribution=death,
        vacant_frontier_class_distribution=vf,
        vacant_interior_class_distribution=vi,
        forest_frontier_class_distribution=ff,
        forest_interior_class_distribution=fi,
        ruin_class_distribution=ruin_dist,
        expansion_pressure_anchor=exp,
        port_prevalence_anchor=max(0.02, min(0.45, proxies.port_prevalence)),
        winter_severity_anchor=proxies.winter_severity,
        trade_strength_anchor=proxies.trade_strength,
        rebuild_strength_anchor=proxies.rebuild_strength,
        reclamation_rate_anchor=proxies.reclamation_rate,
    )


def estimate_parameters(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
    feature_grids: list[SeedFeatureGrid],
    proxies: LatentProxySummary,
) -> SimulationParameters:
    """Derive simulation knobs from observations + latent proxies."""
    if observations is None or not observations.samples:
        return _default_simulation_parameters(proxies)

    # Default count buckets: [ocean, settlement, port, ruin, forest, mountain] — unused for static
    vacant_frontier = [0.0] * 6
    vacant_interior = [0.0] * 6
    forest_frontier = [0.0] * 6
    forest_interior = [0.0] * 6
    occupied_survive = 0.0
    occupied_death = [0.0] * 6
    ruin_out = [0.0] * 6

    if observations is not None and observations.samples:
        for sample in observations.samples:
            seed_idx = sample.planned_query.seed_index
            state = round_detail.initial_states[seed_idx]
            fg = feature_grids[seed_idx]
            viewport = sample.result.viewport
            for row_off, row in enumerate(sample.result.grid):
                for col_off, code in enumerate(row):
                    wx = viewport.x + col_off
                    wy = viewport.y + row_off
                    init = state.grid[wy][wx]
                    final_cls = terrain_code_to_class_index(code)
                    front = fg.frontier[wy][wx] > 0
                    dist = fg.distance[wy][wx]

                    if init in OCEAN_CODES or init in MOUNTAIN_CODES:
                        continue

                    if init in VACANT_CODES:
                        bucket = vacant_frontier if front and dist <= 4 else vacant_interior
                        bucket[final_cls] += 1.0
                    elif init in FOREST_CODES:
                        bucket = forest_frontier if front and dist <= 4 else forest_interior
                        bucket[final_cls] += 1.0
                    elif init in INITIAL_OCCUPIED_CODES:
                        if final_cls in (1, 2):
                            occupied_survive += 1.0
                        else:
                            occupied_death[final_cls] += 1.0
                    elif init == 3:
                        ruin_out[final_cls] += 1.0

    def norm6(raw: list[float]) -> list[float]:
        if sum(raw) <= 0:
            return [1.0 / 6.0] * 6
        return _smooth_probs(raw, alpha=2.0)

    defaults = _default_simulation_parameters(proxies)
    vf = norm6(vacant_frontier) if sum(vacant_frontier) >= 1 else list(defaults.vacant_frontier_class_distribution)
    vi = norm6(vacant_interior) if sum(vacant_interior) >= 1 else list(defaults.vacant_interior_class_distribution)
    ff = norm6(forest_frontier) if sum(forest_frontier) >= 1 else list(defaults.forest_frontier_class_distribution)
    fi = norm6(forest_interior) if sum(forest_interior) >= 1 else list(defaults.forest_interior_class_distribution)

    occ_total = occupied_survive + sum(occupied_death)
    if occ_total > 0:
        # Bayesian prior: 0.34 survival (GT average), pseudo-count of 6
        prior_survive = 0.34
        p_survive = (occupied_survive + 6.0 * prior_survive) / (occ_total + 6.0)
        death_raw = [occupied_death[i] for i in range(6)]
        death_raw[1] = death_raw[2] = 0.0
        if sum(death_raw) <= 0:
            # GT calibrated: 63% empty, 5% ruin, 31% forest
            death_dist = [0.63, 0.0, 0.0, 0.05, 0.31, 0.01]
        else:
            death_dist = _smooth_probs(death_raw, alpha=1.0)
    else:
        p_survive = max(0.15, min(0.92, proxies.settlement_survival))
        # GT calibrated fallback
        death_dist = [0.63, 0.0, 0.0, 0.05, 0.31, 0.01]

    ruin_total = sum(ruin_out)
    if ruin_total > 0:
        ruin_dist = norm6(ruin_out)
    else:
        ruin_raw = [
            0.20 + 0.06 * (1.0 - proxies.rebuild_strength),
            0.15 * proxies.rebuild_strength,
            0.06 * proxies.port_prevalence,
            0.25,
            0.30 + 0.08 * proxies.reclamation_rate,
            0.0,
        ]
        s = sum(ruin_raw)
        ruin_dist = [x / s for x in ruin_raw]

    # Blend empirical vacant frontier with proxy so low-data rounds still react
    exp_p = max(0.06, min(0.48, proxies.expansion_pressure * 1.15))
    if sum(vacant_frontier) >= 8:
        target_settle = vacant_frontier[1] + vacant_frontier[2]
        tot_v = sum(vacant_frontier)
        emp_exp = target_settle / tot_v if tot_v > 0 else exp_p
        exp_p = 0.35 * emp_exp + 0.65 * exp_p

    return SimulationParameters(
        settlement_survival_rate=max(0.08, min(0.94, p_survive)),
        settlement_death_class_distribution=death_dist,
        vacant_frontier_class_distribution=vf,
        vacant_interior_class_distribution=vi,
        forest_frontier_class_distribution=ff,
        forest_interior_class_distribution=fi,
        ruin_class_distribution=ruin_dist,
        expansion_pressure_anchor=exp_p,
        port_prevalence_anchor=max(0.02, min(0.45, proxies.port_prevalence)),
        winter_severity_anchor=proxies.winter_severity,
        trade_strength_anchor=proxies.trade_strength,
        rebuild_strength_anchor=proxies.rebuild_strength,
        reclamation_rate_anchor=proxies.reclamation_rate,
    )


def expected_cell_distribution(
    code: int,
    *,
    x: int,
    y: int,
    features: SeedFeatureGrid,
    params: SimulationParameters,
) -> list[float]:
    """Exact expected probability distribution for a cell, replacing Monte Carlo draws."""
    if code in OCEAN_CODES:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    if code in MOUNTAIN_CODES:
        return [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    coastal = features.coastal[y][x]
    support = features.support[y][x]
    front = features.frontier[y][x]
    dist = features.distance[y][x]
    is_frontier = front > 0 and dist <= 4

    exp_factor = 0.5 + 0.5 * params.expansion_pressure_anchor
    dist_dampen = 1.0
    if dist >= 7:
        dist_dampen = 0.50 * exp_factor
    elif dist >= 5:
        dist_dampen = 0.75 * exp_factor

    if code in INITIAL_OCCUPIED_CODES:
        p_surv = params.settlement_survival_rate * (0.72 + 0.28 * support)
        p_surv *= 1.0 - 0.22 * params.winter_severity_anchor * (1.0 - support)
        p_surv = max(0.06, min(0.96, p_surv))

        dist_out = [0.0] * 6
        if coastal and code == 1:
            port_chance = 0.15 + 0.10 * params.port_prevalence_anchor + 0.08 * params.trade_strength_anchor
            dist_out[2] = p_surv * port_chance
            dist_out[1] = p_surv * (1.0 - port_chance)
        else:
            cls = 1 if code == 1 else 2
            dist_out[cls] = p_surv

        dist_death = list(params.settlement_death_class_distribution)
        if sum(dist_death) <= 0:
            dist_death = [0.63, 0.0, 0.0, 0.05, 0.31, 0.01]

        s = sum(dist_death)
        if s > 0:
            dist_death = [v / s for v in dist_death]

        for i in range(6):
            dist_out[i] += (1.0 - p_surv) * dist_death[i]

        return dist_out

    if code == 3:
        dist_r = list(params.ruin_class_distribution)
        s = sum(dist_r)
        if s <= 0:
            return [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        dist_out = [v / s for v in dist_r]
        if not coastal:
            dist_out[1] += dist_out[2]
            dist_out[2] = 0.0
        return dist_out

    if code in VACANT_CODES:
        base = params.vacant_frontier_class_distribution if is_frontier else params.vacant_interior_class_distribution
        dist_v = list(base)
        if dist_dampen < 1.0:
            for i in (1, 2, 3, 4):
                dist_v[i] *= dist_dampen
            dist_v[0] = max(dist_v[0], 1.0 - sum(dist_v[1:]))
            s = sum(dist_v)
            dist_v = [v / s for v in dist_v]
        if is_frontier:
            settle_mass = dist_v[1] + dist_v[2]
            target_exp = max(
                settle_mass, params.expansion_pressure_anchor * (0.35 + 0.65 * support) * (0.4 + 0.6 * (front / 4.0))
            )
            target_exp = min(0.32, max(0.05, target_exp)) * dist_dampen
            stay = dist_v[0]
            other = sum(dist_v[i] for i in (3, 4)) + 1e-9
            scale = (1.0 - target_exp) / max(1e-9, stay + other)
            dist_v = [
                stay * scale,
                target_exp * (0.92 if not coastal else 0.80),
                target_exp * (0.08 if not coastal else (0.12 + 0.08 * params.trade_strength_anchor)),
                dist_v[3] * scale * 0.3,
                dist_v[4] * scale,
                0.0,
            ]
            if not coastal:
                dist_v[2] = 0.0
            else:
                port_share = 0.10 + 0.06 * params.port_prevalence_anchor + 0.05 * params.trade_strength_anchor
                total_settle = dist_v[1] + dist_v[2]
                if total_settle > 0:
                    dist_v[2] = total_settle * min(0.60, port_share / max(0.01, target_exp))
                    dist_v[1] = total_settle - dist_v[2]
            s = sum(dist_v)
            dist_v = [v / s for v in dist_v]

        if not coastal:
            dist_v[1] += dist_v[2]
            dist_v[2] = 0.0

        return dist_v

    if code in FOREST_CODES:
        base = params.forest_frontier_class_distribution if is_frontier else params.forest_interior_class_distribution
        dist_f = list(base)
        if dist_dampen < 1.0:
            for i in (0, 1, 2, 3):
                dist_f[i] *= dist_dampen
            dist_f[4] = max(dist_f[4], 1.0 - sum(dist_f[:4]) - dist_f[5])
            s = sum(dist_f)
            dist_f = [v / s for v in dist_f]
        if is_frontier:
            settle_mass = dist_f[1] + dist_f[2]
            target_exp = max(
                settle_mass,
                params.expansion_pressure_anchor * (0.4 + 0.6 * support) * (0.45 + 0.55 * (front / 4.0)),
            )
            target_exp = min(0.35, max(0.08, target_exp))
            stay = dist_f[4]
            other = dist_f[0] + dist_f[3] + 1e-9
            scale = (1.0 - target_exp) / max(1e-9, stay + other)
            dist_f = [
                dist_f[0] * scale,
                target_exp * (0.92 if not coastal else 0.80),
                target_exp * (0.08 if not coastal else (0.12 + 0.08 * params.port_prevalence_anchor)),
                dist_f[3] * scale * 0.3,
                stay * scale,
                0.0,
            ]
            if not coastal:
                dist_f[2] = 0.0
            else:
                port_share = 0.12 + 0.06 * params.port_prevalence_anchor
                total_settle = dist_f[1] + dist_f[2]
                if total_settle > 0:
                    dist_f[2] = total_settle * min(0.55, port_share / max(0.01, target_exp))
                    dist_f[1] = total_settle - dist_f[2]
            s = sum(dist_f)
            dist_f = [v / s for v in dist_f]

        if not coastal:
            dist_f[1] += dist_f[2]
            dist_f[2] = 0.0

        return dist_f

    dist_out = [0.0] * 6
    dist_out[terrain_code_to_class_index(code)] = 1.0
    return dist_out

def _apply_hard_constraints(
    state: SeedInitialState,
    distribution: list[float],
    *,
    x: int,
    y: int,
    features: SeedFeatureGrid,
    floor: float,
) -> list[float]:
    code = state.grid[y][x]
    if code in OCEAN_CODES:
        return normalize_distribution([1.0, 0.0, 0.0, 0.0, 0.0, 0.0], floor=floor)
    if code in MOUNTAIN_CODES:
        return normalize_distribution([0.0, 0.0, 0.0, 0.0, 0.0, 1.0], floor=floor)
    coastal = features.coastal[y][x]
    out = list(distribution)
    if not coastal:
        out[2] = 0.0
    out[5] = 0.0  # Mountain never appears on non-mountain cells
    return normalize_distribution(out, floor=floor)


def _legal_class_mask(
    state: SeedInitialState,
    *,
    x: int,
    y: int,
    features: SeedFeatureGrid,
) -> list[bool]:
    code = state.grid[y][x]
    if code in OCEAN_CODES:
        return [True, False, False, False, False, False]
    if code in MOUNTAIN_CODES:
        return [False, False, False, False, False, True]
    if features.coastal[y][x]:
        return [True, True, True, True, True, False]
    return [True, True, False, True, True, False]


def _normalize_with_legal_mask(
    values: list[float],
    *,
    legal_mask: list[bool],
    legal_floor: float,
    illegal_floor: float,
) -> list[float]:
    raw = [max(float(value), 0.0) for value in values]
    legal_indices = [index for index, allowed in enumerate(legal_mask) if allowed]
    illegal_indices = [index for index, allowed in enumerate(legal_mask) if not allowed]
    if not legal_indices:
        raise ValueError("At least one class must be legal")

    illegal_mass = illegal_floor * len(illegal_indices)
    remaining_mass = 1.0 - illegal_mass
    if remaining_mass <= 0.0:
        raise ValueError("illegal_floor leaves no mass for legal classes")
    if legal_floor * len(legal_indices) >= remaining_mass:
        raise ValueError("legal_floor is too large for the number of legal classes")

    output = [illegal_floor if index in illegal_indices else 0.0 for index in range(len(raw))]
    legal_total = sum(raw[index] for index in legal_indices)
    if legal_total <= 0.0:
        uniform = remaining_mass / len(legal_indices)
        if uniform < legal_floor:
            raise ValueError("cannot satisfy legal_floor with uniform fallback")
        for index in legal_indices:
            output[index] = uniform
        return output

    legal_remaining = remaining_mass - legal_floor * len(legal_indices)
    for index in legal_indices:
        output[index] = legal_floor + legal_remaining * (raw[index] / legal_total)
    return output


def _legalize_distribution(
    state: SeedInitialState,
    distribution: list[float],
    *,
    x: int,
    y: int,
    features: SeedFeatureGrid,
    legal_floor: float,
    illegal_floor: float,
) -> list[float]:
    return _normalize_with_legal_mask(
        distribution,
        legal_mask=_legal_class_mask(state, x=x, y=y, features=features),
        legal_floor=legal_floor,
        illegal_floor=illegal_floor,
    )


def _apply_structural_mask(
    state: SeedInitialState,
    tensor: list[list[list[float]]],
    *,
    features: SeedFeatureGrid,
    legal_floor: float,
    illegal_floor: float,
) -> list[list[list[float]]]:
    masked: list[list[list[float]]] = []
    for y, row in enumerate(tensor):
        output_row: list[list[float]] = []
        for x, distribution in enumerate(row):
            output_row.append(
                _legalize_distribution(
                    state,
                    distribution,
                    x=x,
                    y=y,
                    features=features,
                    legal_floor=legal_floor,
                    illegal_floor=illegal_floor,
                )
            )
        masked.append(output_row)
    return masked


def _blend_tensors(
    left: list[list[list[float]]],
    right: list[list[list[float]]],
    *,
    weight: float,
) -> list[list[list[float]]]:
    clamped = max(0.0, min(1.0, weight))
    if clamped <= 0.0:
        return left
    if clamped >= 1.0:
        return right

    blended: list[list[list[float]]] = []
    for y, row in enumerate(left):
        output_row: list[list[float]] = []
        for x, cell in enumerate(row):
            output_row.append(
                [
                    (1.0 - clamped) * cell[class_index] + clamped * right[y][x][class_index]
                    for class_index in range(len(cell))
                ]
            )
        blended.append(output_row)
    return blended


def _build_uncertainty_from_tensor(
    round_detail: RoundDetail,
    all_seed_tensors: list[list[list[list[float]]]],
    observed_counts: list[dict[tuple[int, int], int]],
    feature_grids: list[SeedFeatureGrid],
) -> list[SeedUncertaintySummary]:
    from astar_island.predictor import _dynamic_strength

    summaries: list[SeedUncertaintySummary] = []
    for seed_index, state in enumerate(round_detail.initial_states):
        tensor = all_seed_tensors[seed_index]
        fg = feature_grids[seed_index]
        dynamic_candidate_cells = 0
        under_sampled_dynamic_cells = 0
        observed_dynamic_cells = 0
        total_entropy = 0.0
        high_entropy_cells = 0
        for y, row in enumerate(tensor):
            for x, cell in enumerate(row):
                if _dynamic_strength(state, x, y, fg) <= 0.75:
                    continue
                dynamic_candidate_cells += 1
                ent = prediction_entropy(cell)
                total_entropy += ent
                if ent >= 1.0:
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


def _terrain_name_for_cal(code: int) -> str:
    names = {0: "empty", 1: "settlement", 2: "port", 3: "ruin", 4: "forest", 5: "mountain", 10: "ocean", 11: "plains"}
    return names.get(code, str(code))


@dataclass(slots=True)
class SimulatorPredictor:
    """Monte Carlo terrain predictor; use for final submission after observations."""

    model_name: str = "analytic-simulator-v3"
    floor: float = 1e-6
    legal_floor: float = 0.003
    n_runs: int = 1000
    seed: int = 42
    calibration_weight: float = 0.2
    use_transition_policy: bool = True
    transition_blend: float = 0.40
    last_simulation_parameters: SimulationParameters | None = field(default=None, init=False, repr=False)

    def predict(
        self, round_detail: RoundDetail, observations: ObservationCollection | None = None, calibration=None
    ) -> PredictionBundle:
        bundle, _ = self.predict_with_diagnostics(round_detail, observations, calibration=calibration)
        return bundle

    def predict_with_diagnostics(
        self,
        round_detail: RoundDetail,
        observations: ObservationCollection | None = None,
        calibration=None,
    ) -> tuple[PredictionBundle, PredictionDiagnostics]:
        feature_grids = [build_feature_grid(s) for s in round_detail.initial_states]
        proxy_source = _phase1_only_observations(round_detail, observations)
        proxy_settlements = _build_observed_settlement_index(round_detail, proxy_source)
        latent_proxies = _derive_latent_proxies(
            round_detail,
            proxy_source,
            proxy_settlements,
            feature_grids=feature_grids,
            legacy_trade_proxy_mode=False,
        )
        params = estimate_parameters(round_detail, observations, feature_grids, latent_proxies)
        self.last_simulation_parameters = params
        observed_counts = _build_observed_count_index(round_detail, observations)

        seeds: list[SeedPrediction] = []
        tensors: list[list[list[list[float]]]] = []

        for seed_index, state in enumerate(round_detail.initial_states):
            fg = feature_grids[seed_index]
            height = round_detail.map_height
            width = round_detail.map_width
            tensor: list[list[list[float]]] = []
            for y in range(height):
                row: list[list[float]] = []
                for x in range(width):
                    code = state.grid[y][x]
                    # Get analytic expected distribution instead of running Monte Carlo
                    expected_dist = expected_cell_distribution(code, x=x, y=y, features=fg, params=params)
                    mc_dist = _apply_hard_constraints(
                        state, expected_dist, x=x, y=y, features=fg, floor=self.legal_floor
                    )

                    # Blend with calibration prior if available
                    if calibration is not None and state.grid[y][x] not in (OCEAN_CODES | MOUNTAIN_CODES):
                        terrain = _terrain_name_for_cal(state.grid[y][x])
                        coastal = fg.coastal[y][x]
                        frontier = fg.frontier[y][x] > 0
                        dist_band = _distance_band(fg.distance[y][x])
                        cal_dist = calibration.get_distribution(terrain, coastal, frontier, dist_band)
                        if cal_dist is not None:
                            w_cal = self.calibration_weight
                            w_mc = 1.0 - w_cal
                            blended = [w_mc * mc_dist[i] + w_cal * cal_dist[i] for i in range(6)]
                            # Re-apply a floor over legal classes before final structural masking.
                            mc_dist = normalize_distribution(blended, floor=self.legal_floor)

                    row.append(mc_dist)
                tensor.append(row)

            if self.use_transition_policy:
                influence_maps = _build_influence_maps(
                    state,
                    proxy_settlements[seed_index],
                    latent_proxies,
                    features=fg,
                )
                transitioned = _apply_transition_policy(
                    state,
                    tensor,
                    influence_maps=influence_maps,
                    floor=self.legal_floor,
                    aggressive=False,
                    features=fg,
                    proxies=latent_proxies,
                )
                tensor = _blend_tensors(tensor, transitioned, weight=self.transition_blend)

            tensor = _apply_structural_mask(
                state,
                tensor,
                features=fg,
                legal_floor=self.legal_floor,
                illegal_floor=self.floor,
            )
            tensors.append(tensor)
            seeds.append(
                SeedPrediction(
                    seed_index=seed_index,
                    height=height,
                    width=width,
                    prediction=tensor,
                )
            )

        uncertainty_summaries = _build_uncertainty_from_tensor(round_detail, tensors, observed_counts, feature_grids)
        bundle = PredictionBundle(round_id=round_detail.id, model_name=self.model_name, floor=self.floor, seeds=seeds)
        diagnostics = PredictionDiagnostics(
            latent_proxies=latent_proxies,
            uncertainty_summaries=uncertainty_summaries,
        )
        return bundle, diagnostics
