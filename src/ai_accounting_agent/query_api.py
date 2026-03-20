import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional
from urllib import error, request


ENV_PATH = Path(__file__).with_name(".env")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def join_url(base_url: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def build_tripletex_headers(company_id: Optional[str] = None) -> dict[str, str]:
    session_token = os.environ["TRIPLETEX_SESSION_TOKEN"]
    company = company_id or os.environ.get("TRIPLETEX_COMPANY_ID", "0")
    encoded = base64.b64encode(f"{company}:{session_token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
    }


def build_agent_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = os.environ.get("AI_ACCOUNTING_AGENT_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def build_headers(provider: str, company_id: Optional[str], extra_headers: list[str]) -> dict[str, str]:
    if provider == "tripletex":
        headers = build_tripletex_headers(company_id)
    else:
        headers = build_agent_headers()

    for header in extra_headers:
        if ":" not in header:
            raise ValueError(f"Invalid header '{header}'. Expected KEY:VALUE.")
        key, value = header.split(":", 1)
        headers[key.strip()] = value.strip()

    return headers


def parse_payload(data: Optional[str], data_file: Optional[str]) -> Optional[bytes]:
    if data and data_file:
        raise ValueError("Use either --data or --data-file, not both.")

    if data_file:
        raw = Path(data_file).read_text()
    else:
        raw = data

    if raw is None:
        return None

    parsed = json.loads(raw)
    return json.dumps(parsed).encode()


def send_request(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: Optional[bytes],
    timeout: int,
) -> tuple[int, Any, str]:
    request_headers = dict(headers)

    if payload is not None:
        request_headers["Content-Type"] = "application/json; charset=utf-8"

    http_request = request.Request(
        url,
        headers=request_headers,
        data=payload,
        method=method,
    )

    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, response.headers, body
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, exc.headers, body


def format_body(body: str, raw: bool) -> str:
    if raw:
        return body

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body

    return json.dumps(parsed, indent=2, ensure_ascii=True)


def main() -> int:
    load_env_file(ENV_PATH)

    parser = argparse.ArgumentParser(description="Query Tripletex or the deployed accounting agent.")
    parser.add_argument("path", nargs="?", default="/", help="API path or full URL")
    parser.add_argument("--provider", choices=["tripletex", "agent"], default="tripletex")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--data")
    parser.add_argument("--data-file")
    parser.add_argument("--company-id", help="Tripletex company id override for accountant-client access")
    parser.add_argument("--header", action="append", default=[], help="Extra header in KEY:VALUE form")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--raw", action="store_true", help="Print response body without JSON pretty-printing")
    parser.add_argument("--include-headers", action="store_true")
    args = parser.parse_args()

    try:
        payload = parse_payload(args.data, args.data_file)
        headers = build_headers(args.provider, args.company_id, args.header)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    base_url_key = "TRIPLETEX_API_URL" if args.provider == "tripletex" else "AI_ACCOUNTING_AGENT_API_URL"
    try:
        url = join_url(os.environ[base_url_key], args.path)
    except KeyError as exc:
        print(f"Missing environment variable: {exc}", file=sys.stderr)
        return 2

    status, response_headers, body = send_request(
        method=args.method.upper(),
        url=url,
        headers=headers,
        payload=payload,
        timeout=args.timeout,
    )

    print(f"{args.method.upper()} {url}")
    print(f"Status: {status}")

    for header_name in [
        "x-tlx-request-id",
        "x-rate-limit-limit",
        "x-rate-limit-remaining",
        "x-rate-limit-reset",
        "content-type",
    ]:
        header_value = response_headers.get(header_name)
        if header_value:
            print(f"{header_name}: {header_value}")

    if args.include_headers:
        for header_name, header_value in response_headers.items():
            if header_name.lower() in {
                "x-tlx-request-id",
                "x-rate-limit-limit",
                "x-rate-limit-remaining",
                "x-rate-limit-reset",
                "content-type",
            }:
                continue
            print(f"{header_name}: {header_value}")

    if body:
        print()
        print(format_body(body, args.raw))

    return 0 if status < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
