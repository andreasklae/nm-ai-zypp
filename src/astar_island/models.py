from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


CLASS_LABELS = ["empty", "settlement", "port", "ruin", "forest", "mountain"]
RoundId = str | int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class InitialSettlement(ApiBaseModel):
    x: int
    y: int
    has_port: bool = False
    alive: bool = True


class SeedInitialState(ApiBaseModel):
    grid: list[list[int]]
    settlements: list[InitialSettlement] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_grid(self) -> "SeedInitialState":
        if not self.grid:
            raise ValueError("grid must not be empty")
        width = len(self.grid[0])
        if width == 0:
            raise ValueError("grid rows must not be empty")
        if any(len(row) != width for row in self.grid):
            raise ValueError("grid rows must all have the same width")
        return self


class RoundSummary(ApiBaseModel):
    id: RoundId
    status: str
    round_number: int | None = None
    map_width: int | None = None
    map_height: int | None = None
    seeds_count: int | None = None


class RoundDetail(ApiBaseModel):
    id: RoundId
    status: str | None = None
    round_number: int | None = None
    map_width: int
    map_height: int
    seeds_count: int
    initial_states: list[SeedInitialState]

    @model_validator(mode="after")
    def _validate_shape(self) -> "RoundDetail":
        if len(self.initial_states) != self.seeds_count:
            raise ValueError("initial_states must match seeds_count")
        for state in self.initial_states:
            if len(state.grid) != self.map_height:
                raise ValueError("grid height does not match map_height")
            if any(len(row) != self.map_width for row in state.grid):
                raise ValueError("grid width does not match map_width")
        return self


class Viewport(ApiBaseModel):
    x: int
    y: int
    w: int
    h: int


class ViewportRequest(StrictBaseModel):
    round_id: RoundId
    seed_index: int
    viewport_x: int
    viewport_y: int
    viewport_w: int = Field(ge=1, le=15)
    viewport_h: int = Field(ge=1, le=15)


class SettlementObservation(ApiBaseModel):
    x: int
    y: int
    has_port: bool | None = None
    alive: bool | None = None
    owner_id: int | None = None
    population: float | None = None
    food: float | None = None
    wealth: float | None = None
    defense: float | None = None
    tech_level: float | None = None


class SimulationResult(ApiBaseModel):
    grid: list[list[int]]
    settlements: list[SettlementObservation] = Field(default_factory=list)
    viewport: Viewport

    @model_validator(mode="after")
    def _validate_shape(self) -> "SimulationResult":
        if len(self.grid) != self.viewport.h:
            raise ValueError("grid height does not match viewport")
        if any(len(row) != self.viewport.w for row in self.grid):
            raise ValueError("grid width does not match viewport")
        return self


class PlannedWindow(StrictBaseModel):
    viewport_x: int
    viewport_y: int
    viewport_w: int
    viewport_h: int
    score: float
    settlement_count: int
    coastline_cells: int
    frontier_cells: int
    cluster_id: int
    expected_information_gain: float = 0.0
    overlap_penalty: float = 0.0
    selection_reasons: list[str] = Field(default_factory=list)


class PlannedQuery(StrictBaseModel):
    query_index: int
    seed_index: int
    viewport_x: int
    viewport_y: int
    viewport_w: int
    viewport_h: int
    repeat_index: int
    cluster_rank: int
    purpose: str
    phase: str = "phase1"
    selection_reason: str = ""
    expected_information_gain: float = 0.0


class SeedObservationPlan(StrictBaseModel):
    phase: str
    seed_index: int
    diversity_score: float
    allocated_queries: int
    cluster_windows: list[PlannedWindow]
    queries: list[PlannedQuery]
    uncertainty_score: float = 0.0
    selection_reason: str = ""


class ObservationPhasePlan(StrictBaseModel):
    phase: Literal["phase1", "phase2"]
    description: str
    budget: int
    seed_plans: list[SeedObservationPlan]
    queries: list[PlannedQuery]


class ObservationPlan(StrictBaseModel):
    round_id: RoundId
    created_at: str = Field(default_factory=utc_now_iso)
    viewport_size: int = 15
    max_queries: int = 50
    phases: list[ObservationPhasePlan] = Field(default_factory=list)
    queries: list[PlannedQuery] = Field(default_factory=list)


class ObservationSample(StrictBaseModel):
    planned_query: PlannedQuery
    result: SimulationResult


class SeedObservationSummary(StrictBaseModel):
    seed_index: int
    query_count: int
    observed_cells: int
    unique_observed_cells: int
    class_counts: list[int]
    phase_query_counts: dict[str, int] = Field(default_factory=dict)


class ObservationCollection(StrictBaseModel):
    round_id: RoundId
    created_at: str = Field(default_factory=utc_now_iso)
    total_queries: int
    samples: list[ObservationSample]
    per_seed: list[SeedObservationSummary]
    phase_query_counts: dict[str, int] = Field(default_factory=dict)


class SeedPrediction(StrictBaseModel):
    seed_index: int
    height: int
    width: int
    prediction: list[list[list[float]]]

    @model_validator(mode="after")
    def _validate_tensor(self) -> "SeedPrediction":
        if len(self.prediction) != self.height:
            raise ValueError("prediction height mismatch")
        if any(len(row) != self.width for row in self.prediction):
            raise ValueError("prediction width mismatch")
        if any(len(cell) != len(CLASS_LABELS) for row in self.prediction for cell in row):
            raise ValueError("prediction cells must match class count")
        return self


class PredictionBundle(StrictBaseModel):
    round_id: RoundId
    created_at: str = Field(default_factory=utc_now_iso)
    model_name: str
    class_labels: list[str] = Field(default_factory=lambda: list(CLASS_LABELS))
    floor: float = 0.01
    seeds: list[SeedPrediction]


class SeedSubmissionReceipt(StrictBaseModel):
    seed_index: int
    status_code: int
    response_body: dict[str, Any]
    skipped: bool = False


class SubmissionBundle(StrictBaseModel):
    round_id: RoundId
    created_at: str = Field(default_factory=utc_now_iso)
    receipts: list[SeedSubmissionReceipt]


class LeaderboardEntry(ApiBaseModel):
    team_id: str
    team_name: str
    team_slug: str | None = None
    rank: int | None = None
    weighted_score: float | None = None
    hot_streak_score: float | None = None
    rounds_participated: int | None = None
    updated_at: str | None = None
    all_verified: bool | None = None
    is_verified: bool | None = None
    is_u23: bool | None = None


class ApiProbeResult(StrictBaseModel):
    path: str
    status_code: int
    payload: dict[str, Any] | list[Any] | None = None


class ScoreSnapshot(StrictBaseModel):
    round_id: RoundId
    fetched_at: str = Field(default_factory=utc_now_iso)
    user_profile: dict[str, Any] | None = None
    leaderboard: list[LeaderboardEntry] = Field(default_factory=list)
    probes: list[ApiProbeResult] = Field(default_factory=list)


class SeedUncertaintySummary(StrictBaseModel):
    seed_index: int
    observed_fraction: float
    dynamic_candidate_cells: int
    under_sampled_dynamic_cells: int
    mean_entropy: float
    high_entropy_cells: int
    uncertainty_score: float


class LatentProxySummary(StrictBaseModel):
    settlement_survival: float
    ruin_intensity: float
    port_prevalence: float
    expansion_pressure: float
    reclamation_rate: float
    winter_severity: float
    trade_strength: float
    conflict_pressure: float
    rebuild_strength: float


class PredictionDiagnostics(StrictBaseModel):
    latent_proxies: LatentProxySummary
    uncertainty_summaries: list[SeedUncertaintySummary]


class PredictorParameters(StrictBaseModel):
    direct_observation_weight: float = 0.86
    prior_weight: float = 0.34
    exact_weight: float = 0.22
    relaxed_weight: float = 0.16
    round_transition_weight: float = 0.28
    transition_weight: float = 0.20
    nearest_weight: float = 0.08
    cross_seed_nearest_weight: float = 0.04
    transition_alpha: float = 2.0
    round_transition_alpha: float = 0.7
    latent_proxy_strength: float = 0.20
    smoothing_base: float = 0.03
    smoothing_dynamic_scale: float = 0.018
    use_local_influence_pass: bool = False
    aggressive_transition_policy: bool = False
    legacy_trade_proxy_mode: bool = False


class PredictionValidationSummary(StrictBaseModel):
    valid: bool
    seed_count: int
    map_height: int
    map_width: int
    class_count: int
    min_probability: float
    max_sum_error: float


class HoldoutMetrics(StrictBaseModel):
    overall_nll: float
    changed_cell_nll: float | None = None
    observed_cell_count: int
    changed_cell_count: int
    class_average_probability: dict[str, float] = Field(default_factory=dict)
    class_average_nll: dict[str, float] = Field(default_factory=dict)


class ModelComparisonSummary(StrictBaseModel):
    candidate_model_name: str
    baseline_model_name: str
    overall_nll_delta: float | None = None
    changed_cell_nll_delta: float | None = None
    candidate_better: bool | None = None


class EvaluationReport(StrictBaseModel):
    round_id: RoundId
    round_number: int | None = None
    artifact_dir: str
    model_name: str
    evaluation_mode: str
    official_round_status: str | None = None
    official_score_metadata: dict[str, Any] | None = None
    prediction_validation: PredictionValidationSummary | None = None
    holdout_metrics: HoldoutMetrics | None = None
    legacy_holdout_metrics: HoldoutMetrics | None = None
    model_comparison_summary: ModelComparisonSummary | None = None
    query_budget_used: int = 0
    observed_coverage_fraction: float = 0.0
    calibrated_parameters: PredictorParameters | None = None


class BacktestSummary(StrictBaseModel):
    created_at: str = Field(default_factory=utc_now_iso)
    model_name: str
    calibrated_parameters: PredictorParameters | None = None
    reports: list[EvaluationReport] = Field(default_factory=list)
    aggregate_metrics: dict[str, float] = Field(default_factory=dict)


class DeliveryRunManifest(StrictBaseModel):
    round_id: RoundId
    artifact_dir: str
    active_round_status: str
    round_number: int | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    status: str = "initialized"
    phase1_plan: ObservationPhasePlan | None = None
    phase2_plan: ObservationPhasePlan | None = None
    observations: ObservationCollection | None = None
    latent_proxies: LatentProxySummary | None = None
    uncertainty_summaries: list[SeedUncertaintySummary] = Field(default_factory=list)
    prediction_model_name: str | None = None
    prediction_created_at: str | None = None
    prediction_validation: PredictionValidationSummary | None = None
    submission: SubmissionBundle | None = None
    official_score_metadata: dict[str, Any] | None = None
    evaluation_mode: str | None = None
    model_comparison_summary: ModelComparisonSummary | None = None
    warnings: list[str] = Field(default_factory=list)
