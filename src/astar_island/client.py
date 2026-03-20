from __future__ import annotations

import time
from typing import Any

import requests

from astar_island.config import AstarIslandSettings
from astar_island.models import (
    ApiProbeResult,
    LeaderboardEntry,
    PredictionBundle,
    RoundDetail,
    RoundId,
    RoundSummary,
    ScoreSnapshot,
    SimulationResult,
    ViewportRequest,
)


class AstarIslandClient:
    def __init__(
        self,
        *,
        settings: AstarIslandSettings,
        session: requests.Session | None = None,
        timeout: int = 60,
        max_retries: int = 6,
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        if settings.access_token:
            self.session.headers["Authorization"] = f"Bearer {settings.access_token}"

    def _url(self, path: str) -> str:
        return f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _root_url(self, path: str) -> str:
        api_root = self.settings.base_url.rstrip("/").removesuffix("/astar-island")
        return f"{api_root.rstrip('/')}/{path.lstrip('/')}"

    def _raise_if_missing_token(self) -> None:
        if not self.settings.access_token:
            raise RuntimeError("ASTAR_ISLAND_ACCESS_TOKEN is required for live API calls.")

    def _request_with_retry(self, method: str, path: str, *, root: bool = False, **kwargs) -> requests.Response:
        last_response: requests.Response | None = None
        for attempt in range(self.max_retries + 1):
            url = self._root_url(path) if root else self._url(path)
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            if response.status_code != 429:
                response.raise_for_status()
                return response
            last_response = response
            retry_after = response.headers.get("Retry-After", "").strip()
            try:
                wait_seconds = float(retry_after) if retry_after else 2.0 * (attempt + 1)
            except ValueError:
                wait_seconds = 2.0 * (attempt + 1)
            time.sleep(min(wait_seconds, 20.0))
        if last_response is not None:
            try:
                body = last_response.json()
            except ValueError:
                body = last_response.text
            raise RuntimeError(f"Live API returned repeated 429 responses for {path}: {body}")
        raise RuntimeError(f"Request failed for {path} without a response.")

    def _request_optional(self, method: str, path: str, *, root: bool = False) -> requests.Response:
        self._raise_if_missing_token()
        url = self._root_url(path) if root else self._url(path)
        response = self.session.request(method, url, timeout=self.timeout)
        if response.status_code not in {200, 403, 404, 405}:
            response.raise_for_status()
        return response

    def list_rounds(self) -> list[RoundSummary]:
        self._raise_if_missing_token()
        response = self._request_with_retry("GET", "/rounds")
        payload = response.json()
        return [RoundSummary.model_validate(item) for item in payload]

    def get_completed_rounds(self) -> list[RoundSummary]:
        return [round_item for round_item in self.list_rounds() if round_item.status == "completed"]

    def get_active_round(self) -> RoundSummary:
        rounds = self.list_rounds()
        active = next((item for item in rounds if item.status == "active"), None)
        if active is None:
            raise RuntimeError("No active round found.")
        return active

    def get_round(self, round_id: RoundId) -> RoundDetail:
        self._raise_if_missing_token()
        response = self._request_with_retry("GET", f"/rounds/{round_id}")
        return RoundDetail.model_validate(response.json())

    def get_user_profile(self) -> dict[str, Any]:
        response = self._request_with_retry("GET", "/users/me", root=True)
        return response.json()

    def get_leaderboard(self) -> list[LeaderboardEntry]:
        response = self._request_with_retry("GET", "/leaderboard")
        payload = response.json()
        return [LeaderboardEntry.model_validate(item) for item in payload]

    def probe_round_result_data(self, round_id: RoundId) -> list[ApiProbeResult]:
        candidate_paths = [
            f"/rounds/{round_id}/scores",
            f"/rounds/{round_id}/score",
            f"/rounds/{round_id}/results",
            f"/rounds/{round_id}/result",
            f"/rounds/{round_id}/submissions",
            f"/rounds/{round_id}/leaderboard",
            f"/rounds/{round_id}/ground-truth",
            f"/rounds/{round_id}/ground_truth",
            f"/rounds/{round_id}/truth",
        ]
        probes: list[ApiProbeResult] = []
        for path in candidate_paths:
            response = self._request_optional("GET", path)
            payload: dict[str, Any] | list[Any] | None
            try:
                payload = response.json()
            except ValueError:
                payload = None
            probes.append(
                ApiProbeResult(
                    path=path,
                    status_code=response.status_code,
                    payload=payload if response.status_code == 200 else None,
                )
            )
        return probes

    def fetch_score_snapshot(self, round_id: RoundId) -> ScoreSnapshot:
        return ScoreSnapshot(
            round_id=round_id,
            user_profile=self.get_user_profile(),
            leaderboard=self.get_leaderboard(),
            probes=self.probe_round_result_data(round_id),
        )

    def simulate(self, request: ViewportRequest) -> SimulationResult:
        self._raise_if_missing_token()
        response = self._request_with_retry(
            "POST",
            "/simulate",
            json=request.model_dump(mode="json"),
        )
        return SimulationResult.model_validate(response.json())

    def submit_prediction(
        self,
        *,
        round_id: RoundId,
        seed_index: int,
        prediction: list[list[list[float]]],
    ) -> requests.Response:
        self._raise_if_missing_token()
        return self._request_with_retry(
            "POST",
            "/submit",
            json={
                "round_id": round_id,
                "seed_index": seed_index,
                "prediction": prediction,
            },
        )

    def submit_bundle(self, bundle: PredictionBundle) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        for seed in bundle.seeds:
            response = self.submit_prediction(
                round_id=bundle.round_id,
                seed_index=seed.seed_index,
                prediction=seed.prediction,
            )
            try:
                body = response.json()
            except ValueError:
                body = {"raw_text": response.text}
            receipts.append(
                {
                    "seed_index": seed.seed_index,
                    "status_code": response.status_code,
                    "response_body": body,
                }
            )
        return receipts
