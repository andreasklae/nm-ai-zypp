from __future__ import annotations

import os

import pytest

from astar_island.client import AstarIslandClient
from astar_island.config import load_settings
from astar_island.delivery import deliver_round
from astar_island.models import ObservationSample, ViewportRequest
from astar_island.planner import build_observation_collection, build_phase1_observation_plan
from astar_island.predictor import BaselinePredictor
from astar_island.storage import save_round_artifacts


SETTINGS = load_settings()
RUN_LIVE_ASTAR_ISLAND_TESTS = os.environ.get("RUN_LIVE_ASTAR_ISLAND_TESTS", "").lower() in {"1", "true", "yes"}
RUN_LIVE_ASTAR_ISLAND_SUBMIT_TESTS = os.environ.get("RUN_LIVE_ASTAR_ISLAND_SUBMIT_TESTS", "").lower() in {"1", "true", "yes"}

pytestmark = [
    pytest.mark.live_astar_island,
    pytest.mark.skipif(
        not RUN_LIVE_ASTAR_ISLAND_TESTS,
        reason="Set RUN_LIVE_ASTAR_ISLAND_TESTS=1 to run live Astar Island API tests.",
    ),
    pytest.mark.skipif(
        not SETTINGS.access_token,
        reason="ASTAR_ISLAND_ACCESS_TOKEN is required for live Astar Island tests.",
    ),
]


def test_live_round_fetch_phase1_smoke_and_predict(tmp_path) -> None:
    client = AstarIslandClient(settings=SETTINGS)
    summary = client.get_active_round()
    detail = client.get_round(summary.id)
    save_round_artifacts(tmp_path, summary, detail)

    phase1 = build_phase1_observation_plan(detail)
    first_query = phase1.queries[0]
    result = client.simulate(
        ViewportRequest(
            round_id=detail.id,
            seed_index=first_query.seed_index,
            viewport_x=first_query.viewport_x,
            viewport_y=first_query.viewport_y,
            viewport_w=first_query.viewport_w,
            viewport_h=first_query.viewport_h,
        )
    )
    collection = build_observation_collection(
        detail.id,
        [ObservationSample(planned_query=first_query, result=result)],
    )
    predictions, diagnostics = BaselinePredictor().predict_with_diagnostics(detail, collection)

    assert len(predictions.seeds) == detail.seeds_count
    assert predictions.seeds[0].height == detail.map_height
    assert diagnostics.uncertainty_summaries


@pytest.mark.skipif(
    not RUN_LIVE_ASTAR_ISLAND_SUBMIT_TESTS,
    reason="Set RUN_LIVE_ASTAR_ISLAND_SUBMIT_TESTS=1 to run a real live submission test.",
)
def test_live_deliver_round_submit(tmp_path) -> None:
    artifact_dir, manifest = deliver_round(
        settings=SETTINGS,
        artifact_dir=tmp_path,
        submit=True,
        force_resume=True,
    )

    assert artifact_dir == tmp_path
    assert manifest.submission is not None
