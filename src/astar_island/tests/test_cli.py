from __future__ import annotations

from pathlib import Path

from astar_island.cli import main
from astar_island.models import RoundSummary
from astar_island.storage import (
    load_backtest_summary,
    load_predictions,
    load_round_detail,
    save_observations,
    save_round_artifacts,
)
from astar_island.tests.test_support import build_mock_observations, load_sample_round_detail


class FakeClient:
    def __init__(self, detail) -> None:
        self._detail = detail

    def list_rounds(self):
        return [
            RoundSummary(
                id=self._detail.id,
                status=self._detail.status or "active",
                round_number=self._detail.round_number,
                map_width=self._detail.map_width,
                map_height=self._detail.map_height,
                seeds_count=self._detail.seeds_count,
            )
        ]

    def get_active_round(self):
        return self.list_rounds()[0]

    def get_round(self, round_id: int):
        assert round_id == self._detail.id
        return self._detail


def test_cli_fetch_round_writes_artifact(monkeypatch, tmp_path) -> None:
    detail = load_sample_round_detail()
    monkeypatch.setattr("astar_island.cli.AstarIslandClient", lambda settings: FakeClient(detail))

    exit_code = main(["fetch-round", "--round-id", str(detail.id), "--artifact-dir", str(tmp_path)])

    assert exit_code == 0
    loaded = load_round_detail(tmp_path)
    assert loaded.id == detail.id


def test_cli_predict_writes_prediction_bundle(tmp_path) -> None:
    detail = load_sample_round_detail()
    summary = RoundSummary(id=detail.id, status=detail.status or "active")
    save_round_artifacts(tmp_path, summary, detail)
    save_observations(tmp_path, build_mock_observations(detail))

    exit_code = main(["predict", "--artifact-dir", str(tmp_path)])

    assert exit_code == 0
    bundle = load_predictions(tmp_path)
    assert bundle.round_id == detail.id
    assert len(bundle.seeds) == detail.seeds_count


def test_cli_backtest_writes_summary(monkeypatch, tmp_path) -> None:
    detail = load_sample_round_detail()
    artifact_dir = tmp_path / "round_101" / "fixture"
    output_dir = tmp_path / "backtests" / "cli"
    summary = RoundSummary(id=detail.id, status=detail.status or "completed")
    save_round_artifacts(artifact_dir, summary, detail)
    save_observations(artifact_dir, build_mock_observations(detail, variant="mixed"))

    monkeypatch.setenv("ASTAR_ISLAND_DATA_DIR", str(tmp_path))

    exit_code = main(["backtest", "--round-id", str(detail.round_number), "--artifact-dir", str(output_dir)])

    assert exit_code == 0
    saved_summary = load_backtest_summary(output_dir)
    assert saved_summary is not None
    assert saved_summary.reports
