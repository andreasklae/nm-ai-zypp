from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests

from ai_accounting_agent.telemetry import log_event


class TripletexApiError(RuntimeError):
    def __init__(
        self,
        *,
        message: str,
        status_code: int,
        response_body: Any,
        response_headers: dict[str, str],
        request_id: str | None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.response_headers = response_headers
        self.request_id = request_id


@dataclass(slots=True)
class TripletexClient:
    base_url: str
    session_token: str
    run_id: str
    timeout_seconds: float = 30.0
    session: requests.Session = field(default_factory=requests.Session)
    cache: dict[str, Any] = field(default_factory=dict)

    def _auth(self) -> tuple[str, str]:
        return ("0", self.session_token)

    def _normalize_path(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            raise ValueError("Tripletex tools must use relative paths, not full URLs.")

        cleaned = path if path.startswith("/") else f"/{path}"
        segments = []
        for segment in cleaned.split("/"):
            if segment.startswith(">"):
                segments.append(f"%3E{segment[1:]}")
            else:
                segments.append(segment)
        return "/".join(segments)

    def _build_url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", self._normalize_path(path).lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        url = self._build_url(path)
        started = time.perf_counter()
        log_event(
            "tripletex_http_request",
            run_id=self.run_id,
            method=method.upper(),
            path=path,
            url=url,
            params=params or {},
            json_body=json_body,
        )

        response = self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=json_body,
            auth=self._auth(),
            headers={"Accept": "application/json"},
            timeout=timeout_seconds or self.timeout_seconds,
        )

        duration_ms = round((time.perf_counter() - started) * 1000)
        request_id = response.headers.get("x-tlx-request-id")

        try:
            body: Any = response.json()
        except ValueError:
            body = response.text

        log_event(
            "tripletex_http_response",
            run_id=self.run_id,
            method=method.upper(),
            path=path,
            url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
            response_headers=dict(response.headers),
            response_body=body,
        )

        if response.status_code >= 400:
            raise TripletexApiError(
                message=f"Tripletex API returned {response.status_code} for {method.upper()} {path}",
                status_code=response.status_code,
                response_body=body,
                response_headers=dict(response.headers),
                request_id=request_id,
            )

        return body

    def get(self, path: str, *, params: dict[str, Any] | None = None, cache_key: str | None = None) -> Any:
        if cache_key and cache_key in self.cache:
            return self.cache[cache_key]

        result = self.request("GET", path, params=params)
        if cache_key:
            self.cache[cache_key] = result
        return result

    def post(self, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, params=params, json_body=json_body)

    def put(self, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> Any:
        return self.request("PUT", path, params=params, json_body=json_body)

    def delete(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("DELETE", path, params=params)

    def upload(
        self,
        path: str,
        *,
        file_data: bytes,
        filename: str,
        mime_type: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = self._build_url(path)
        started = time.perf_counter()
        log_event(
            "tripletex_http_request",
            run_id=self.run_id,
            method="POST",
            path=path,
            url=url,
            params=params or {},
            json_body={"_upload": True, "filename": filename, "mime_type": mime_type, "size": len(file_data)},
        )

        response = self.session.post(
            url=url,
            params=params,
            files={"file": (filename, file_data, mime_type)},
            auth=self._auth(),
            headers={"Accept": "application/json"},
            timeout=self.timeout_seconds,
        )

        duration_ms = round((time.perf_counter() - started) * 1000)
        request_id = response.headers.get("x-tlx-request-id")

        try:
            body: Any = response.json()
        except ValueError:
            body = response.text

        log_event(
            "tripletex_http_response",
            run_id=self.run_id,
            method="POST",
            path=path,
            url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
            response_headers=dict(response.headers),
            response_body=body,
        )

        if response.status_code >= 400:
            raise TripletexApiError(
                message=f"Tripletex API returned {response.status_code} for POST {path} (upload)",
                status_code=response.status_code,
                response_body=body,
                response_headers=dict(response.headers),
                request_id=request_id,
            )

        return body
