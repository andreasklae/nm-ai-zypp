"""Ground truth calibration pipeline.

Fetches post-round analysis data from the API, computes empirical transition
distributions bucketed by terrain features, and produces calibrated priors that
can be used by both the simulator and the predictor.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astar_island.client import AstarIslandClient
from astar_island.models import LatentProxySummary, ObservationCollection, RoundDetail, RoundId
from astar_island.predictor import (
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
)

logger = logging.getLogger(__name__)

CLASS_LABELS = ["empty", "settlement", "port", "ruin", "forest", "mountain"]
SIMILARITY_PROXY_FIELDS = (
    "settlement_survival",
    "port_prevalence",
    "expansion_pressure",
    "reclamation_rate",
    "winter_severity",
    "rebuild_strength",
)
SIMILARITY_PROXY_SCALES = {
    "settlement_survival": 0.18,
    "port_prevalence": 0.06,
    "expansion_pressure": 0.12,
    "reclamation_rate": 0.18,
    "winter_severity": 0.08,
    "rebuild_strength": 0.08,
}


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


def _terrain_name(code: int) -> str:
    names = {0: "empty", 1: "settlement", 2: "port", 3: "ruin", 4: "forest", 5: "mountain", 10: "ocean", 11: "plains"}
    return names.get(code, str(code))


@dataclass
class CalibrationBucket:
    """Accumulated ground truth for a specific feature combination."""

    gt_sum: list[float] = field(default_factory=lambda: [0.0] * 6)
    count: float = 0.0

    def add(self, gt_probs: list[float], *, weight: float = 1.0) -> None:
        for i in range(6):
            self.gt_sum[i] += gt_probs[i] * weight
        self.count += weight

    def average(self) -> list[float]:
        if self.count == 0:
            return [1.0 / 6.0] * 6
        return [self.gt_sum[i] / self.count for i in range(6)]


# Feature bucket key type: (terrain_name, is_coastal, is_frontier, distance_band)
BucketKey = tuple[str, bool, bool, str]


@dataclass
class CalibrationData:
    """Empirical transition distributions from ground truth analysis."""

    buckets: dict[str, CalibrationBucket] = field(default_factory=dict)
    rounds_used: list[int] = field(default_factory=list)
    seeds_used: int = 0
    total_cells: int = 0

    def get_distribution(self, terrain: str, coastal: bool, frontier: bool, dist_band: str) -> list[float] | None:
        """Look up empirical distribution with hierarchical fallback."""
        # Try exact match
        key = f"{terrain}|{coastal}|{frontier}|{dist_band}"
        bucket = self.buckets.get(key)
        if bucket is not None and bucket.count >= 5:
            return bucket.average()

        # Fallback: same terrain + coastal + frontier, any distance
        for db in ["1_2", "3_4", "5_6", "7_plus", "0"]:
            fallback_key = f"{terrain}|{coastal}|{frontier}|{db}"
            fb = self.buckets.get(fallback_key)
            if fb is not None and fb.count >= 10:
                return fb.average()

        # Fallback: same terrain + coastal, any frontier/distance
        for front in [True, False]:
            for db in ["1_2", "3_4", "5_6", "7_plus", "0"]:
                fallback_key = f"{terrain}|{coastal}|{front}|{db}"
                fb = self.buckets.get(fallback_key)
                if fb is not None and fb.count >= 10:
                    return fb.average()

        # Fallback: same terrain only (aggregate all)
        terrain_bucket = self.buckets.get(f"{terrain}|aggregate")
        if terrain_bucket is not None and terrain_bucket.count >= 5:
            return terrain_bucket.average()

        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rounds_used": self.rounds_used,
            "seeds_used": self.seeds_used,
            "total_cells": self.total_cells,
            "buckets": {k: {"gt_sum": v.gt_sum, "count": v.count} for k, v in self.buckets.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationData:
        cal = cls(
            rounds_used=data.get("rounds_used", []),
            seeds_used=data.get("seeds_used", 0),
            total_cells=data.get("total_cells", 0),
        )
        for k, v in data.get("buckets", {}).items():
            cal.buckets[k] = CalibrationBucket(gt_sum=v["gt_sum"], count=v["count"])
        return cal


def fetch_ground_truth(client: AstarIslandClient, round_id: RoundId, seed_index: int) -> dict | None:
    """Fetch analysis data for a completed round seed. Returns None on failure."""
    try:
        return client.get_analysis(round_id, seed_index)
    except Exception as exc:
        logger.warning("Failed to fetch analysis for round %s seed %d: %s", round_id, seed_index, exc)
        return None


def _analysis_cache_path(base_dir: Path, round_detail: RoundDetail, seed_index: int) -> Path:
    round_key = round_detail.round_number if round_detail.round_number is not None else round_detail.id
    return base_dir / f"round_{round_key}_seed_{seed_index}.json"


def _load_cached_analysis(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cached_analysis(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _load_or_fetch_analysis(
    *,
    round_detail: RoundDetail,
    seed_index: int,
    analysis_cache_dir: Path | None,
    client: AstarIslandClient | None,
) -> dict | None:
    cache_path = _analysis_cache_path(analysis_cache_dir, round_detail, seed_index) if analysis_cache_dir is not None else None
    if cache_path is not None:
        cached = _load_cached_analysis(cache_path)
        if cached is not None:
            return cached
    if client is None:
        return None
    analysis = fetch_ground_truth(client, round_detail.id, seed_index)
    if analysis is not None and cache_path is not None:
        _save_cached_analysis(cache_path, analysis)
    return analysis


def _accumulate_analysis(
    calibration: CalibrationData,
    *,
    feature_grid: SeedFeatureGrid,
    ground_truth: list[list[list[float]]],
    initial_grid: list[list[int]],
    weight: float = 1.0,
) -> int:
    added_cells = 0
    height = len(ground_truth)
    width = len(ground_truth[0]) if height > 0 else 0

    for y in range(height):
        for x in range(width):
            gt_probs = ground_truth[y][x]
            eps = 1e-10
            entropy = -sum(gt_probs[i] * math.log(gt_probs[i] + eps) for i in range(6))
            if entropy <= 0.01:
                continue

            init_code = initial_grid[y][x]
            terrain = _terrain_name(init_code)
            coastal = feature_grid.coastal[y][x]
            frontier = feature_grid.frontier[y][x] > 0
            dist_band = _distance_band(feature_grid.distance[y][x])

            key = f"{terrain}|{coastal}|{frontier}|{dist_band}"
            calibration.buckets.setdefault(key, CalibrationBucket()).add(gt_probs, weight=weight)

            agg_key = f"{terrain}|aggregate"
            calibration.buckets.setdefault(agg_key, CalibrationBucket()).add(gt_probs, weight=weight)

            calibration.total_cells += 1
            added_cells += 1
    return added_cells


def build_round_calibration(
    round_detail: RoundDetail,
    *,
    client: AstarIslandClient | None = None,
    analysis_cache_dir: Path | None = None,
) -> CalibrationData | None:
    """Build calibration buckets for a single scored round using cached or fetched analysis."""
    calibration = CalibrationData()
    feature_grids = [build_feature_grid(s) for s in round_detail.initial_states]
    round_added = False

    for seed_idx in range(len(round_detail.initial_states)):
        analysis = _load_or_fetch_analysis(
            round_detail=round_detail,
            seed_index=seed_idx,
            analysis_cache_dir=analysis_cache_dir,
            client=client,
        )
        if analysis is None:
            continue

        gt = analysis.get("ground_truth")
        init_grid = analysis.get("initial_grid")
        if gt is None or init_grid is None:
            continue

        added = _accumulate_analysis(
            calibration,
            feature_grid=feature_grids[seed_idx],
            ground_truth=gt,
            initial_grid=init_grid,
        )
        if added > 0:
            calibration.seeds_used += 1
            round_added = True

    if not round_added:
        return None
    if round_detail.round_number is not None:
        calibration.rounds_used.append(round_detail.round_number)
    return calibration


def _derive_round_latent_proxies(
    round_detail: RoundDetail,
    observations: ObservationCollection,
) -> LatentProxySummary:
    feature_grids = [build_feature_grid(s) for s in round_detail.initial_states]
    proxy_source = _phase1_only_observations(round_detail, observations)
    observed_settlements = _build_observed_settlement_index(round_detail, proxy_source)
    return _derive_latent_proxies(
        round_detail,
        proxy_source,
        observed_settlements,
        feature_grids=feature_grids,
        legacy_trade_proxy_mode=False,
    )


def _proxy_similarity(target: LatentProxySummary, source: LatentProxySummary, *, temperature: float) -> float:
    dist2 = 0.0
    for field_name in SIMILARITY_PROXY_FIELDS:
        scale = SIMILARITY_PROXY_SCALES[field_name]
        delta = (getattr(target, field_name) - getattr(source, field_name)) / scale
        dist2 += delta * delta
    return math.exp(-0.5 * dist2 / max(1e-6, temperature))


def _artifact_rank(local_artifact: Any) -> tuple[int, float]:
    observations = getattr(local_artifact, "observations", None)
    query_count = observations.total_queries if observations is not None else -1
    artifact_dir = getattr(local_artifact, "artifact_dir", None)
    mtime = artifact_dir.stat().st_mtime if isinstance(artifact_dir, Path) and artifact_dir.exists() else 0.0
    return (query_count, mtime)


def _dedupe_local_artifacts(local_artifacts: list[Any], *, current_round_id: RoundId) -> list[Any]:
    best_by_round: dict[str, tuple[tuple[int, float], Any]] = {}
    current_id = str(current_round_id)
    for artifact in local_artifacts:
        round_detail = getattr(artifact, "round_detail", None)
        observations = getattr(artifact, "observations", None)
        if round_detail is None or observations is None or not observations.samples:
            continue
        if str(round_detail.id) == current_id:
            continue
        round_key = (
            f"round-number:{round_detail.round_number}"
            if round_detail.round_number is not None
            else f"round-id:{round_detail.id}"
        )
        rank = _artifact_rank(artifact)
        existing = best_by_round.get(round_key)
        if existing is None or rank > existing[0]:
            best_by_round[round_key] = (rank, artifact)
    return [artifact for _, artifact in best_by_round.values()]


def _merge_calibration(target: CalibrationData, source: CalibrationData, *, weight: float) -> None:
    if weight <= 0:
        return
    for key, bucket in source.buckets.items():
        merged = target.buckets.setdefault(key, CalibrationBucket())
        for index in range(6):
            merged.gt_sum[index] += bucket.gt_sum[index] * weight
        merged.count += bucket.count * weight


def build_similarity_calibration(
    round_detail: RoundDetail,
    observations: ObservationCollection | None,
    local_artifacts: list[Any],
    *,
    client: AstarIslandClient | None = None,
    analysis_cache_dir: Path | None = None,
    allowed_round_numbers: set[int] | None = None,
    top_k: int = 3,
    temperature: float = 1.5,
) -> CalibrationData | None:
    """Build a round-adaptive calibration blend from the most similar scored local rounds."""
    if observations is None or not observations.samples:
        return None

    target_proxies = _derive_round_latent_proxies(round_detail, observations)
    candidates: list[tuple[float, RoundDetail, CalibrationData]] = []

    for artifact in _dedupe_local_artifacts(local_artifacts, current_round_id=round_detail.id):
        history_detail = getattr(artifact, "round_detail", None)
        history_observations = getattr(artifact, "observations", None)
        if history_detail is None or history_observations is None or not history_observations.samples:
            continue
        if allowed_round_numbers is not None:
            history_round_number = history_detail.round_number
            if history_round_number is None or history_round_number not in allowed_round_numbers:
                continue

        round_calibration = build_round_calibration(
            history_detail,
            client=client,
            analysis_cache_dir=analysis_cache_dir,
        )
        if round_calibration is None:
            continue

        history_proxies = _derive_round_latent_proxies(history_detail, history_observations)
        weight = _proxy_similarity(target_proxies, history_proxies, temperature=temperature)
        candidates.append((weight, history_detail, round_calibration))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = candidates[:top_k]
    adaptive = CalibrationData(
        rounds_used=[
            detail.round_number
            for _, detail, _ in selected
            if detail.round_number is not None
        ],
        seeds_used=sum(calibration.seeds_used for _, _, calibration in selected),
        total_cells=sum(calibration.total_cells for _, _, calibration in selected),
    )
    for weight, _, round_calibration in selected:
        _merge_calibration(adaptive, round_calibration, weight=weight)

    logger.info(
        "Adaptive calibration selected rounds %s",
        [
            {
                "round_number": detail.round_number,
                "round_id": detail.id,
                "weight": round(weight, 4),
            }
            for weight, detail, _ in selected
        ],
    )
    return adaptive


def build_calibration_data(
    client: AstarIslandClient,
    *,
    max_rounds: int = 20,
) -> CalibrationData:
    """Fetch ground truth from all completed rounds and compute empirical transition distributions."""
    calibration = CalibrationData()

    my_rounds = client.get_my_rounds()
    scored = [r for r in my_rounds if r.get("round_score") is not None]
    scored.sort(key=lambda r: r.get("round_number", 0), reverse=True)
    scored = scored[:max_rounds]

    for round_info in scored:
        round_id = round_info["id"]
        round_num = round_info.get("round_number", 0)
        seeds_count = round_info.get("seeds_submitted", 0)
        if seeds_count == 0:
            continue

        try:
            detail = client.get_round(round_id)
        except Exception as exc:
            logger.warning("Failed to fetch round detail for %s: %s", round_id, exc)
            continue

        feature_grids = [build_feature_grid(s) for s in detail.initial_states]

        round_added = False
        for seed_idx in range(len(detail.initial_states)):
            analysis = fetch_ground_truth(client, round_id, seed_idx)
            if analysis is None:
                continue

            gt = analysis.get("ground_truth")
            init_grid = analysis.get("initial_grid")
            if gt is None or init_grid is None:
                continue

            added = _accumulate_analysis(
                calibration,
                feature_grid=feature_grids[seed_idx],
                ground_truth=gt,
                initial_grid=init_grid,
            )
            if added > 0:
                calibration.seeds_used += 1
                round_added = True

        if round_added:
            calibration.rounds_used.append(round_num)
            logger.info("Calibration: added round %d (%s)", round_num, round_id[:8])

    logger.info(
        "Calibration complete: %d rounds, %d seeds, %d cells, %d buckets",
        len(calibration.rounds_used),
        calibration.seeds_used,
        calibration.total_cells,
        len(calibration.buckets),
    )
    return calibration


def save_calibration(path: Path, calibration: CalibrationData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calibration.to_dict(), indent=2))


def load_calibration(path: Path) -> CalibrationData | None:
    if not path.exists():
        return None
    return CalibrationData.from_dict(json.loads(path.read_text()))


def get_calibrated_distribution(
    calibration: CalibrationData | None,
    terrain: str,
    coastal: bool,
    frontier: bool,
    dist_band: str,
) -> list[float] | None:
    """Get calibrated distribution with fallback to None if no calibration data."""
    if calibration is None:
        return None
    return calibration.get_distribution(terrain, coastal, frontier, dist_band)
