"""Evaluate model improvement candidates against ground-truth for completed rounds.

Tests three specific fixes:
  A  current baseline
  B  lower survival floor: estimate_parameters cap 0.08→0.04, expected_cell floor 0.06→0.03
  C  B + reduce ruin in death distribution when ruin_intensity is near-zero
  D  C + lower default no-obs survival prior from 0.34→0.20

Rounds tested: 16, 17, 18, 19, 20, 21
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from astar_island.config import load_settings
from astar_island.models import ObservationCollection, RoundDetail
from astar_island.simulator import (
    SimulatorPredictor,
    _default_simulation_parameters,
    estimate_parameters,
    expected_cell_distribution,
)
from astar_island.terrain import build_feature_grid, terrain_code_to_class_index
from astar_island.predictor import (
    _phase1_only_observations,
    _build_observed_settlement_index,
    _derive_latent_proxies,
)

CLASS_NAMES = ["empty", "settlement", "port", "ruin", "forest", "mountain"]

ROUND_ARTIFACTS = [
    # (round_number, round_id, timestamp)
    (16, "8f664aed-8839-4c85-bed0-77a2cac7c6f5", "20260321T174227.1573420000"),
    (17, "3eb0c25d-28fa-48ca-b8e1-fc249e3918e9", "20260321T202928.7670890000"),
    (18, "b0f9d1bf-4b71-4e6e-816c-19c718d29056", "20260321T210444.3020560000"),
    (19, "597e60cf-d1a1-4627-ac4d-2a61da68b6df", "20260322T020753.5971220000"),
    (20, "fd82f643-15e2-40e7-9866-8d8f5157081c", "20260322T030828.3145990000"),
    # Round 21: use the run with 50 observations (not the zero-obs submitted run)
    (21, "b3a0be6b-b48b-419d-916a-b7a77fa58c4d", "20260322T063419.4094510000"),
]


@dataclass
class VariantResult:
    round_number: int
    seed_index: int
    overall_nll: float
    settlement_nll: float | None
    settlement_count: int
    empty_nll: float
    forest_nll: float
    empty_avg_pred: float
    forest_avg_pred: float


def compute_nll_vs_gt(
    predictions: list[list[list[float]]],
    gt: list[list[Any]],
) -> VariantResult | None:
    """Compute per-class NLL of predictions against ground truth."""
    H, W = len(gt), len(gt[0]) if gt else 0
    if not H or not W:
        return None

    # GT might be class-index or distribution
    if isinstance(gt[0][0], (list, tuple)):
        gt_classes = [[max(range(6), key=lambda k: gt[r][c][k]) for c in range(W)] for r in range(H)]
    else:
        gt_classes = [[int(gt[r][c]) for c in range(W)] for r in range(H)]

    total_nll = 0.0
    class_nlls = [0.0] * 6
    class_preds = [0.0] * 6
    class_counts = [0] * 6

    for r in range(H):
        for c in range(W):
            g = gt_classes[r][c]
            p = max(predictions[r][c][g], 1e-10)
            nll = -math.log(p)
            total_nll += nll
            class_nlls[g] += nll
            class_preds[g] += predictions[r][c][g]
            class_counts[g] += 1

    total_cells = H * W
    return {
        "overall_nll": total_nll / total_cells,
        "class_nlls": [class_nlls[i] / class_counts[i] if class_counts[i] > 0 else None for i in range(6)],
        "class_preds": [class_preds[i] / class_counts[i] if class_counts[i] > 0 else None for i in range(6)],
        "class_counts": class_counts,
    }


def _compute_raw_survival(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
    feature_grids: list,
    default_survival: float,
) -> float:
    """Compute raw (uncapped) Bayesian survival estimate from observations."""
    from astar_island.terrain import INITIAL_OCCUPIED_CODES
    if observations is None or not observations.samples:
        return default_survival

    occupied_survive = 0.0
    occupied_death_total = 0.0
    for sample in observations.samples:
        seed_idx = sample.planned_query.seed_index
        state = round_detail.initial_states[seed_idx]
        viewport = sample.result.viewport
        for row_off, row in enumerate(sample.result.grid):
            for col_off, code in enumerate(row):
                wx = viewport.x + col_off
                wy = viewport.y + row_off
                init = state.grid[wy][wx]
                final_cls = terrain_code_to_class_index(code)
                if init in INITIAL_OCCUPIED_CODES:
                    if final_cls in (1, 2):
                        occupied_survive += 1.0
                    else:
                        occupied_death_total += 1.0

    occ_total = occupied_survive + occupied_death_total
    if occ_total <= 0:
        return default_survival
    # Bayesian with prior=0.34, pseudo-count=6 (same as estimate_parameters)
    return (occupied_survive + 6.0 * 0.34) / (occ_total + 6.0)


def _compute_obs_sett_density(observations: ObservationCollection | None) -> float:
    """Fraction of non-ocean cells observed as settlement or port."""
    if not observations or not observations.samples:
        return 0.0
    from astar_island.terrain import OCEAN_CODES, MOUNTAIN_CODES
    sett = 0
    non_ocean = 0
    for s in observations.samples:
        for row in s.result.grid:
            for v in row:
                if v in OCEAN_CODES or v == 5:  # ocean or mountain
                    continue
                non_ocean += 1
                if v in (1, 2):
                    sett += 1
    return sett / non_ocean if non_ocean > 0 else 0.0


def run_prediction_with_patches(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
    *,
    survival_cap_estimate: float = 0.08,
    p_surv_floor: float = 0.06,
    default_survival: float = 0.34,
    reduce_ruin_in_death: bool = False,
    adaptive_support_blend: bool = False,
    dynamic_expansion_cap: bool = False,
    obs_density_boost: bool = False,
    obs_density_threshold: float = 0.25,
) -> list[list[list[list[float]]]]:
    """Run the simulator with patched parameters and return per-seed tensors."""
    feature_grids = [build_feature_grid(s) for s in round_detail.initial_states]
    proxy_source = _phase1_only_observations(round_detail, observations)
    proxy_settlements = _build_observed_settlement_index(round_detail, proxy_source)

    has_obs = observations is not None and bool(observations.samples)

    proxies = _derive_latent_proxies(
        round_detail,
        proxy_source,
        proxy_settlements,
        feature_grids=feature_grids,
        legacy_trade_proxy_mode=False,
    )

    # Override default survival if no observations
    if not has_obs and default_survival != 0.34:
        from astar_island.models import LatentProxySummary
        proxies = LatentProxySummary(
            settlement_survival=default_survival,
            ruin_intensity=proxies.ruin_intensity,
            port_prevalence=proxies.port_prevalence,
            expansion_pressure=proxies.expansion_pressure,
            reclamation_rate=proxies.reclamation_rate,
            winter_severity=proxies.winter_severity,
            trade_strength=proxies.trade_strength,
            conflict_pressure=proxies.conflict_pressure,
            rebuild_strength=proxies.rebuild_strength,
        )

    params = estimate_parameters(round_detail, observations, feature_grids, proxies)

    # Compute observed settlement density for density-boost variant
    obs_density = _compute_obs_sett_density(observations) if obs_density_boost else 0.0

    # Re-compute raw survival estimate and apply NEW lower cap
    raw_survival = _compute_raw_survival(round_detail, observations, feature_grids, default_survival)
    patched_rate = max(survival_cap_estimate, min(raw_survival, 0.94))

    # Patch death distribution for low ruin
    death_d = list(params.settlement_death_class_distribution)
    if reduce_ruin_in_death and proxies.ruin_intensity < 0.015:
        ruin_saved = death_d[3]
        death_d[3] = max(0.005, death_d[3] * 0.10)
        extra = ruin_saved - death_d[3]
        death_d[0] += extra * 0.60
        death_d[4] += extra * 0.40
        s = sum(death_d)
        death_d = [x / s for x in death_d]

    from astar_island.models import SimulationParameters
    params = SimulationParameters(
        settlement_survival_rate=patched_rate,
        settlement_death_class_distribution=death_d,
        vacant_frontier_class_distribution=params.vacant_frontier_class_distribution,
        vacant_interior_class_distribution=params.vacant_interior_class_distribution,
        forest_frontier_class_distribution=params.forest_frontier_class_distribution,
        forest_interior_class_distribution=params.forest_interior_class_distribution,
        ruin_class_distribution=params.ruin_class_distribution,
        expansion_pressure_anchor=params.expansion_pressure_anchor,
        port_prevalence_anchor=params.port_prevalence_anchor,
        winter_severity_anchor=params.winter_severity_anchor,
        trade_strength_anchor=params.trade_strength_anchor,
        rebuild_strength_anchor=params.rebuild_strength_anchor,
        reclamation_rate_anchor=params.reclamation_rate_anchor,
    )

    # Compute per-seed tensors using patched expected_cell_distribution logic
    from astar_island.terrain import INITIAL_OCCUPIED_CODES, OCEAN_CODES, MOUNTAIN_CODES, VACANT_CODES, FOREST_CODES

    # Dynamic expansion cap: min(0.50, 0.20 + 0.60 * exp_pressure) when enabled
    dyn_exp_cap_vacant = min(0.50, 0.20 + 0.60 * params.expansion_pressure_anchor) if dynamic_expansion_cap else 0.32
    dyn_exp_cap_forest = min(0.55, 0.25 + 0.60 * params.expansion_pressure_anchor) if dynamic_expansion_cap else 0.35

    all_tensors = []
    for seed_index, state in enumerate(round_detail.initial_states):
        fg = feature_grids[seed_index]
        h, w = round_detail.map_height, round_detail.map_width
        tensor = []
        for y in range(h):
            row = []
            for x in range(w):
                code = state.grid[y][x]
                cell_dist = expected_cell_distribution(code, x=x, y=y, features=fg, params=params)

                if code in (VACANT_CODES | FOREST_CODES) and dynamic_expansion_cap:
                    front = fg.frontier[y][x]
                    cell_dist_val = fg.distance[y][x]
                    is_frontier = front > 0 and cell_dist_val <= 4
                    if is_frontier:
                        support = fg.support[y][x]
                        coastal = fg.coastal[y][x]
                        exp_p = params.expansion_pressure_anchor
                        exp_factor = 0.5 + 0.5 * exp_p
                        dist_dampen = 1.0
                        if cell_dist_val >= 7:
                            dist_dampen = 0.50 * exp_factor
                        elif cell_dist_val >= 5:
                            dist_dampen = 0.75 * exp_factor

                        if code in VACANT_CODES:
                            settle_mass = cell_dist[1] + cell_dist[2]
                            target_exp = max(
                                settle_mass, exp_p * (0.35 + 0.65 * support) * (0.4 + 0.6 * (front / 4.0))
                            )
                            new_cap = dyn_exp_cap_vacant
                            new_target = min(new_cap, max(0.05, target_exp)) * dist_dampen
                            old_cap = 0.32
                            old_target = min(old_cap, max(0.05, target_exp)) * dist_dampen
                            if new_target > old_target + 1e-6:
                                delta = new_target - old_target
                                stay = cell_dist[0]
                                scale = max(0, 1.0 - delta) / max(1e-9, stay + sum(cell_dist[3:5]))
                                new_d = list(cell_dist)
                                new_d[1] = cell_dist[1] + delta * (0.92 if not coastal else 0.80)
                                new_d[2] = cell_dist[2] + delta * (0.08 if not coastal else (0.12 + 0.08 * params.trade_strength_anchor))
                                if not coastal:
                                    new_d[1] += new_d[2]
                                    new_d[2] = 0.0
                                new_d[0] = max(0, cell_dist[0] - delta)
                                new_d[3] = max(0, cell_dist[3] - delta * 0.1)
                                s = sum(new_d)
                                cell_dist = [v / s for v in new_d]

                        elif code in FOREST_CODES:
                            settle_mass = cell_dist[1] + cell_dist[2]
                            target_exp = max(
                                settle_mass, exp_p * (0.4 + 0.6 * support) * (0.45 + 0.55 * (front / 4.0))
                            )
                            new_cap = dyn_exp_cap_forest
                            new_target = min(new_cap, max(0.08, target_exp))
                            old_cap = 0.35
                            old_target = min(old_cap, max(0.08, target_exp))
                            if new_target > old_target + 1e-6:
                                delta = new_target - old_target
                                new_d = list(cell_dist)
                                new_d[1] = cell_dist[1] + delta * (0.92 if not coastal else 0.80)
                                new_d[2] = cell_dist[2] + delta * (0.08 if not coastal else (0.12 + 0.08 * params.port_prevalence_anchor))
                                if not coastal:
                                    new_d[1] += new_d[2]
                                    new_d[2] = 0.0
                                new_d[4] = max(0, cell_dist[4] - delta)
                                s = sum(new_d)
                                cell_dist = [v / s for v in new_d]
                # Observed-density boost: when we observe high settlement density,
                # boost frontier non-occupied cells toward the observed density.
                if (
                    obs_density_boost
                    and obs_density > obs_density_threshold
                    and code not in INITIAL_OCCUPIED_CODES
                    and code not in OCEAN_CODES
                    and code not in MOUNTAIN_CODES
                ):
                    front_val = fg.frontier[y][x]
                    dist_val = fg.distance[y][x]
                    is_frontier_cell = front_val > 0 and dist_val <= 4
                    if is_frontier_cell:
                        obs_blend = (obs_density - obs_density_threshold) * 0.60
                        current_sett = cell_dist[1] + cell_dist[2]
                        target_sett = min(0.65, current_sett + obs_blend)
                        delta = max(0.0, target_sett - current_sett)
                        if delta > 1e-6:
                            coastal = fg.coastal[y][x]
                            new_d = list(cell_dist)
                            new_d[1] += delta * (0.92 if not coastal else 0.80)
                            new_d[2] += delta * (0.08 if coastal else 0.0)
                            if not coastal:
                                new_d[1] += new_d[2]
                                new_d[2] = 0.0
                            new_d[0] = max(1e-6, cell_dist[0] - delta)
                            s = sum(new_d)
                            cell_dist = [v / s for v in new_d]

                dist = cell_dist

                if code in INITIAL_OCCUPIED_CODES:
                    support = fg.support[y][x]
                    coastal = fg.coastal[y][x]
                    if adaptive_support_blend:
                        # When global survival is high, local support matters less.
                        # blend=0 at rate=1 (pure global), blend=1 at rate=0 (full local).
                        blend = 1.0 - params.settlement_survival_rate
                        local_factor = blend * (0.75 + 0.50 * support) + (1.0 - blend) * 1.0
                        p = params.settlement_survival_rate * local_factor
                        p *= 1.0 - 0.25 * blend * params.winter_severity_anchor * (0.5 - support)
                    else:
                        p = params.settlement_survival_rate * (0.75 + 0.50 * support)
                        p *= 1.0 - 0.25 * params.winter_severity_anchor * (0.5 - support)
                    p = max(p_surv_floor, min(0.96, p))  # patched floor

                    d2 = [0.0] * 6
                    if coastal and code == 1:
                        port_chance = 0.15 + 0.10 * params.port_prevalence_anchor + 0.08 * params.trade_strength_anchor
                        d2[2] = p * port_chance
                        d2[1] = p * (1.0 - port_chance)
                    else:
                        cls = 1 if code == 1 else 2
                        d2[cls] = p

                    death_d = list(params.settlement_death_class_distribution)
                    sd = sum(death_d)
                    if sd > 0:
                        death_d = [v / sd for v in death_d]
                    for i in range(6):
                        d2[i] += (1.0 - p) * death_d[i]
                    dist = d2

                # Apply hard constraints (ocean stays ocean, mountain stays mountain)
                from astar_island.simulator import _apply_hard_constraints
                dist = _apply_hard_constraints(state, dist, x=x, y=y, features=fg, floor=1e-6)
                row.append(dist)
            tensor.append(row)
        all_tensors.append(tensor)

    return all_tensors


def evaluate_variant(
    round_number: int,
    round_id: str,
    artifact_ts: str,
    *,
    survival_cap_estimate: float,
    p_surv_floor: float,
    default_survival: float,
    reduce_ruin_in_death: bool,
    adaptive_support_blend: bool = False,
    dynamic_expansion_cap: bool = False,
    obs_density_boost: bool = False,
    obs_density_threshold: float = 0.25,
) -> list[dict]:
    """Run a variant and compute NLL for all seeds."""
    base = f"data/astar_island/round_{round_id}/{artifact_ts}"
    cache_dir = f"data/astar_island/analysis_cache/{round_id}"

    with open(f"{base}/round_detail.json") as f:
        rd_raw = json.load(f)
    round_detail = RoundDetail.model_validate(rd_raw)

    obs_path = f"{base}/observations.json"
    observations: ObservationCollection | None = None
    if os.path.exists(obs_path):
        with open(obs_path) as f:
            obs_raw = json.load(f)
        observations = ObservationCollection.model_validate(obs_raw)
        if not observations.samples:
            observations = None

    tensors = run_prediction_with_patches(
        round_detail,
        observations,
        survival_cap_estimate=survival_cap_estimate,
        p_surv_floor=p_surv_floor,
        default_survival=default_survival,
        reduce_ruin_in_death=reduce_ruin_in_death,
        adaptive_support_blend=adaptive_support_blend,
        dynamic_expansion_cap=dynamic_expansion_cap,
        obs_density_boost=obs_density_boost,
        obs_density_threshold=obs_density_threshold,
    )

    results = []
    for seed_idx, tensor in enumerate(tensors):
        gt_path = f"{cache_dir}/seed_{seed_idx}.json"
        if not os.path.exists(gt_path):
            continue
        with open(gt_path) as f:
            analysis = json.load(f)
        gt = analysis.get("ground_truth", [])
        if not gt:
            continue
        metrics = compute_nll_vs_gt(tensor, gt)
        if metrics:
            metrics["round_number"] = round_number
            metrics["seed_index"] = seed_idx
            results.append(metrics)

    return results


VARIANTS = {
    "A_baseline": dict(survival_cap_estimate=0.08, p_surv_floor=0.06, default_survival=0.34, reduce_ruin_in_death=False, adaptive_support_blend=False, dynamic_expansion_cap=False, obs_density_boost=False),
    "C_lower_caps_low_ruin": dict(survival_cap_estimate=0.04, p_surv_floor=0.03, default_survival=0.34, reduce_ruin_in_death=True, adaptive_support_blend=False, dynamic_expansion_cap=False, obs_density_boost=False),
    "G_obs_density_boost": dict(survival_cap_estimate=0.04, p_surv_floor=0.03, default_survival=0.34, reduce_ruin_in_death=True, adaptive_support_blend=False, dynamic_expansion_cap=False, obs_density_boost=True, obs_density_threshold=0.25),
}


def main() -> None:
    print("=" * 80)
    print("MODEL IMPROVEMENT EVALUATION")
    print("=" * 80)

    # Collect per-variant, per-round avg NLL
    all_results: dict[str, dict[int, list[float]]] = {v: {} for v in VARIANTS}

    for variant_name, variant_params in VARIANTS.items():
        print(f"\n--- Variant: {variant_name} ---")
        for round_num, round_id, ts in ROUND_ARTIFACTS:
            results = evaluate_variant(round_num, round_id, ts, **variant_params)
            if not results:
                print(f"  Round {round_num}: no ground truth")
                continue
            seed_nlls = [r["overall_nll"] for r in results]
            avg_nll = sum(seed_nlls) / len(seed_nlls)
            all_results[variant_name][round_num] = seed_nlls

            # Per-class detail for first run (baseline only)
            if variant_name == "A_baseline":
                # Print class-level detail
                class_nlls = [r["class_nlls"] for r in results]
                class_counts = [r["class_counts"] for r in results]
                # Aggregate
                agg_nll = [0.0] * 6
                agg_cnt = [0] * 6
                for r in results:
                    for i in range(6):
                        if r["class_counts"][i] > 0:
                            agg_nll[i] += r["class_nlls"][i] * r["class_counts"][i]
                            agg_cnt[i] += r["class_counts"][i]
                parts = []
                for i in range(6):
                    if agg_cnt[i] > 0:
                        parts.append(f"{CLASS_NAMES[i]}={agg_nll[i]/agg_cnt[i]:.3f}(n={agg_cnt[i]})")
                print(f"  Round {round_num}: avg_nll={avg_nll:.4f} | {' '.join(parts)}")
            else:
                print(f"  Round {round_num}: avg_nll={avg_nll:.4f}")

    # Summary table
    print("\n" + "=" * 80)
    print("SUMMARY: Average NLL per variant per round (lower = better)")
    print("=" * 80)
    header = f"{'Round':>6} | " + " | ".join(f"{v[:18]:>18}" for v in VARIANTS)
    print(header)
    print("-" * len(header))

    total_baseline = []
    for round_num, _, _ in ROUND_ARTIFACTS:
        row = f"{round_num:>6} | "
        baseline_nll = None
        for v_name in VARIANTS:
            nlls = all_results[v_name].get(round_num, [])
            if not nlls:
                row += f"{'N/A':>18} | "
                continue
            avg = sum(nlls) / len(nlls)
            if v_name == "A_baseline":
                baseline_nll = avg
            delta = f"({avg - baseline_nll:+.4f})" if baseline_nll is not None and v_name != "A_baseline" else ""
            row += f"{avg:.4f}{delta:>10} | "
        print(row)

    # Overall avg
    print("-" * len(header))
    row = f"{'TOTAL':>6} | "
    baseline_total = None
    for v_name in VARIANTS:
        all_nlls = [nll for round_nlls in all_results[v_name].values() for nll in round_nlls]
        if not all_nlls:
            row += f"{'N/A':>18} | "
            continue
        avg = sum(all_nlls) / len(all_nlls)
        if v_name == "A_baseline":
            baseline_total = avg
        delta = f"({avg - baseline_total:+.4f})" if baseline_total is not None and v_name != "A_baseline" else ""
        row += f"{avg:.4f}{delta:>10} | "
    print(row)

    print("\nInterpretation:")
    print("  Negative delta = improvement over baseline")
    print("  Variants B/C/D should improve extinction rounds (16,17,19,20,21)")
    print("  Must NOT significantly worsen expansion round (18)")


if __name__ == "__main__":
    main()
