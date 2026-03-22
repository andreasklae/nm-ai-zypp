import re
import json
import math
from pathlib import Path

from astar_island.storage import load_round_detail, load_observations
from astar_island.simulator import SimulatorPredictor
from astar_island.config import load_settings
from astar_island.calibration import build_calibration_data
import astar_island.simulator

def calc_kl(pred: list[float], gt: list[float]) -> float:
    floor = 0.01
    pred_safe = [max(p, floor) for p in pred]
    s = sum(pred_safe)
    pred_safe = [p / s for p in pred_safe]

    kl = 0.0
    for p, g in zip(pred_safe, gt):
        if g > 1e-9:
            kl += g * math.log(g / p)
    return kl

def test_model():
    settings = load_settings()
    data_dir = settings.data_dir
    analysis_cache_dir = data_dir / "analysis_cache"
    
    artifact_dirs = {}
    for path in data_dir.rglob("round_detail.json"):
        if "backtests" in str(path):
            continue
        try:
            detail = load_round_detail(path.parent)
            if detail.round_number is not None:
                artifact_dirs[detail.round_number] = path.parent
        except Exception:
            pass

    total_kl = 0.0
    total_cells = 0

    for round_num, artifact_dir in sorted(artifact_dirs.items()):
        detail = load_round_detail(artifact_dir)
        observations = load_observations(artifact_dir)
        
        predictor = SimulatorPredictor(calibration_weight=0.0, use_transition_policy=True, transition_blend=0.40, legal_floor=0.003)
        
        gts = {}
        for seed_idx in range(detail.seeds_count):
            gt_path = analysis_cache_dir / f"round_{round_num}_seed_{seed_idx}.json"
            if gt_path.exists():
                with open(gt_path, "r") as f:
                    data = json.load(f)
                    gts[seed_idx] = data.get("ground_truth")
        
        if not gts:
            continue

        predictions, diagnostics = predictor.predict_with_diagnostics(detail, observations, calibration=None)
        
        round_kl = 0.0
        round_cells = 0

        for seed_idx, gt_grid in gts.items():
            if not gt_grid:
                continue
            pred_grid = predictions.seeds[seed_idx].prediction
            
            for y in range(detail.map_height):
                for x in range(detail.map_width):
                    gt_cell = gt_grid[y][x]
                    
                    eps = 1e-10
                    entropy = -sum(gt_cell[i] * math.log(gt_cell[i] + eps) for i in range(6))
                    if entropy <= 0.01:
                        continue
                        
                    pred_cell = pred_grid[y][x]
                    kl = calc_kl(pred_cell, gt_cell)
                    round_kl += kl
                    round_cells += 1
        
        if round_cells > 0:
            total_kl += round_kl
            total_cells += round_cells

    if total_cells > 0:
        print(f"Overall Avg KL Divergence: {total_kl / total_cells:.5f} over {total_cells} dynamic cells")

if __name__ == '__main__':
    test_model()
