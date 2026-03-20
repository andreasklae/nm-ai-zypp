from __future__ import annotations

from astar_island.models import DeliveryRunManifest, RoundSummary
from astar_island.storage import load_round_detail, load_run_manifest, save_round_artifacts, save_run_manifest
from astar_island.tests.test_support import load_sample_round_detail


def test_round_detail_fixture_round_trips_through_storage(tmp_path) -> None:
    detail = load_sample_round_detail()
    summary = RoundSummary(
        id=detail.id,
        status=detail.status or "active",
        round_number=detail.round_number,
        map_width=detail.map_width,
        map_height=detail.map_height,
        seeds_count=detail.seeds_count,
    )

    save_round_artifacts(tmp_path, summary, detail)
    loaded = load_round_detail(tmp_path)

    assert loaded == detail


def test_run_manifest_round_trips(tmp_path) -> None:
    manifest = DeliveryRunManifest(
        round_id=101,
        round_number=7,
        active_round_status="active",
        artifact_dir=str(tmp_path),
        status="predictions_ready",
    )

    save_run_manifest(tmp_path, manifest)
    loaded = load_run_manifest(tmp_path)

    assert loaded is not None
    assert loaded.round_id == 101
    assert loaded.status == "predictions_ready"
