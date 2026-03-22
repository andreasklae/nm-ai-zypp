import math
from pathlib import Path
from collections import defaultdict
from astar_island.config import load_settings
from astar_island.client import AstarIslandClient
from astar_island.terrain import terrain_code_to_class_index, build_feature_grid
from astar_island.simulator import SimulatorPredictor
from astar_island.storage import load_round_detail, load_observations

CLASS_LABELS = ["empty", "settlement", "port", "ruin", "forest", "mountain"]

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

def main():
    settings = load_settings()
    client = AstarIslandClient(settings=settings)
    round_id = "3eb0c25d-28fa-48ca-b8e1-fc249e3918e9"
    
    try:
        artifact_dir = Path("data/astar_island/round_3eb0c25d-28fa-48ca-b8e1-fc249e3918e9/20260321T202644.3382130000")
        detail = load_round_detail(artifact_dir)
        observations = load_observations(artifact_dir)
        
        predictor = SimulatorPredictor(calibration_weight=0.0, use_transition_policy=True, transition_blend=0.40, legal_floor=0.003)
        predictions, _ = predictor.predict_with_diagnostics(detail, observations, calibration=None)
        
        feature_grids = [build_feature_grid(s) for s in detail.initial_states]
        
        total_kl = 0.0
        total_cells = 0
        
        kl_by_init_class = defaultdict(list)
        
        for seed_idx in range(detail.seeds_count):
            analysis = client.get_analysis(round_id, seed_idx)
            if not analysis:
                continue
                
            pred_grid = predictions.seeds[seed_idx].prediction
            gt_grid = analysis["ground_truth"]
            init_grid = analysis["initial_grid"]
            
            for y in range(detail.map_height):
                for x in range(detail.map_width):
                    gt_cell = gt_grid[y][x]
                    
                    eps = 1e-10
                    entropy = -sum(gt_cell[i] * math.log(gt_cell[i] + eps) for i in range(6))
                    if entropy <= 0.01:
                        continue
                        
                    pred_cell = pred_grid[y][x]
                    kl = calc_kl(pred_cell, gt_cell)
                    
                    total_kl += kl
                    total_cells += 1
                    
                    init_code = init_grid[y][x]
                    
                    if init_code in (1, 2):
                        kl_by_init_class["Settlement (init)"].append(kl)
                    elif init_code == 3:
                        kl_by_init_class["Ruin (init)"].append(kl)
                    elif init_code == 4:
                        kl_by_init_class["Forest (init)"].append(kl)
                    elif init_code == 11:
                        kl_by_init_class["Vacant (init)"].append(kl)

        if total_cells > 0:
            print(f"Overall KL Divergence: {total_kl / total_cells:.5f} over {total_cells} dynamic cells")
            
            print("\nBy Initial Class:")
            for cls, scores in kl_by_init_class.items():
                print(f"  {cls}: {sum(scores)/len(scores):.5f} (n={len(scores)})")

            print("\nExample Settlement Predictions (New Code):")
            # print a few settlement predictions
            count = 0
            for seed_idx in range(detail.seeds_count):
                analysis = client.get_analysis(round_id, seed_idx)
                pred_grid = predictions.seeds[seed_idx].prediction
                gt_grid = analysis["ground_truth"]
                init_grid = analysis["initial_grid"]
                for y in range(detail.map_height):
                    for x in range(detail.map_width):
                        if init_grid[y][x] in (1, 2):
                            gt_cell = gt_grid[y][x]
                            pred_cell = pred_grid[y][x]
                            print(f"Init: {init_grid[y][x]} | GT: {[round(p, 3) for p in gt_cell]} | Pred: {[round(p, 3) for p in pred_cell]}")
                            count += 1
                            if count >= 10:
                                return

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
