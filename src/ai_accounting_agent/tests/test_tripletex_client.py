from __future__ import annotations

import pytest

from ai_accounting_agent.tripletex_client import TripletexApiError, TripletexClient


class FakeResponse:
    def __init__(self, *, status_code: int, payload, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {"x-tlx-request-id": "req-123"}
        self.text = payload if isinstance(payload, str) else ""

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_tripletex_client_encodes_summary_paths_and_uses_basic_auth(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    def fake_log_event(event: str, severity: str = "INFO", **payload):
        events.append({"event": event, "severity": severity, **payload})

    session = FakeSession(FakeResponse(status_code=200, payload={"value": {"employeeId": 1}}))
    client = TripletexClient(
        base_url="https://example.tripletex.dev/v2",
        session_token="secret-token",
        run_id="run-1",
        session=session,
    )
    monkeypatch.setattr("ai_accounting_agent.tripletex_client.log_event", fake_log_event)

    response = client.get("/token/session/>whoAmI")

    assert response == {"value": {"employeeId": 1}}
    assert session.calls[0]["auth"] == ("0", "secret-token")
    assert session.calls[0]["url"] == "https://example.tripletex.dev/v2/token/session/%3EwhoAmI"
    assert [event["event"] for event in events] == ["tripletex_http_request", "tripletex_http_response"]


def test_tripletex_client_raises_structured_error_on_failure() -> None:
    session = FakeSession(FakeResponse(status_code=422, payload={"code": 18000, "message": "bad"}))
    client = TripletexClient(
        base_url="https://example.tripletex.dev/v2",
        session_token="secret-token",
        run_id="run-2",
        session=session,
    )

    with pytest.raises(TripletexApiError) as exc_info:
        client.post("/invoice", json_body={"invoiceDate": "2026-03-20"})

    assert exc_info.value.status_code == 422
    assert exc_info.value.response_body == {"code": 18000, "message": "bad"}
