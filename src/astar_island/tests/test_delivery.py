from __future__ import annotations

from astar_island.config import AstarIslandSettings
from astar_island.delivery import deliver_round
from astar_island.models import RoundSummary
from astar_island.storage import load_observation_plan
from astar_island.tests.test_support import load_sample_round_detail


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {"status": "ok"}
        self.text = str(self._body)

    def json(self) -> dict[str, object]:
        return self._body


class FakeDeliveryClient:
    def __init__(self, detail) -> None:
        self.detail = detail

    def get_active_round(self):
        return RoundSummary(id=self.detail.id, status="active", round_number=self.detail.round_number)

    def get_round(self, round_id: int):
        assert round_id == self.detail.id
        return self.detail

    def simulate(self, request):
        grid = [[11 for _ in range(request.viewport_w)] for _ in range(request.viewport_h)]
        mid_x = request.viewport_w // 2
        mid_y = request.viewport_h // 2
        if request.seed_index == 0 and request.viewport_x <= 8:
            grid[mid_y][mid_x] = 3 if (request.viewport_x + request.viewport_y) % 2 == 0 else 1
        else:
            grid[mid_y][mid_x] = 2 if request.seed_index % 2 else 1
        from astar_island.models import SimulationResult, Viewport

        return SimulationResult(
            grid=grid,
            settlements=[],
            viewport=Viewport(x=request.viewport_x, y=request.viewport_y, w=request.viewport_w, h=request.viewport_h),
        )

    def submit_prediction(self, *, round_id: int, seed_index: int, prediction):
        assert round_id == self.detail.id
        assert prediction
        return FakeResponse(body={"seed_index": seed_index, "status": "accepted"})


class FakeBudgetExhaustedClient(FakeDeliveryClient):
    def simulate(self, request):
        raise RuntimeError("Live API returned repeated 429 responses for /simulate: {'detail': 'Query budget exhausted'}")


class FakePhase2BudgetExhaustedClient(FakeDeliveryClient):
    def __init__(self, detail) -> None:
        super().__init__(detail)
        self.simulate_calls = 0

    def simulate(self, request):
        self.simulate_calls += 1
        if self.simulate_calls > 20:
            raise RuntimeError("Live API returned repeated 429 responses for /simulate: {'detail': 'Query budget exhausted'}")
        return super().simulate(request)


def test_deliver_round_builds_manifest_and_predictions(tmp_path) -> None:
    detail = load_sample_round_detail()
    settings = AstarIslandSettings(access_token="token")

    artifact_dir, manifest = deliver_round(
        settings=settings,
        artifact_dir=tmp_path,
        submit=False,
        force_resume=True,
        client=FakeDeliveryClient(detail),
    )

    assert artifact_dir == tmp_path
    assert manifest.status == "predictions_ready"
    assert manifest.observations is not None
    assert manifest.observations.total_queries == 50
    assert manifest.prediction_validation is not None
    assert manifest.prediction_validation.valid is True


def test_deliver_round_falls_back_when_budget_exhausted(tmp_path) -> None:
    detail = load_sample_round_detail()
    settings = AstarIslandSettings(access_token="token")

    artifact_dir, manifest = deliver_round(
        settings=settings,
        artifact_dir=tmp_path,
        submit=False,
        force_resume=True,
        client=FakeBudgetExhaustedClient(detail),
    )

    assert artifact_dir == tmp_path
    assert manifest.status == "predictions_ready"
    assert manifest.observations is not None
    assert manifest.observations.total_queries == 0
    assert manifest.prediction_validation is not None
    assert manifest.prediction_validation.valid is True
    assert any("budget exhausted" in warning.lower() for warning in manifest.warnings)


def test_deliver_round_persists_phase2_plan_when_budget_exhausts_after_phase1(tmp_path) -> None:
    detail = load_sample_round_detail()
    settings = AstarIslandSettings(access_token="token")

    artifact_dir, manifest = deliver_round(
        settings=settings,
        artifact_dir=tmp_path,
        submit=False,
        force_resume=True,
        client=FakePhase2BudgetExhaustedClient(detail),
    )

    assert artifact_dir == tmp_path
    assert manifest.observations is not None
    assert manifest.phase1_plan is not None
    assert manifest.observations.total_queries == manifest.phase1_plan.budget
    assert manifest.phase1_plan is not None
    assert manifest.phase2_plan is not None
    saved_plan = load_observation_plan(tmp_path)
    assert len(saved_plan.phases) == 2
    assert saved_plan.phases[1].budget == 50 - manifest.phase1_plan.budget
    assert any("budget exhausted" in warning.lower() for warning in manifest.warnings)
