"""Analyze Cloud Run logs for recent evaluator submissions."""
import json
import subprocess
import sys
import os

GCLOUD_CONFIG = "/Users/andreasklaeboe/repos/nm-ai-zypp/.gcloud"
PROJECT = "ai-nm26osl-1850"
SERVICE = "ai-accounting-agent"

RUN_IDS = sys.argv[1:]

def fetch_logs(run_id: str) -> list[dict]:
    filt = (
        f'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{SERVICE}" '
        f'AND jsonPayload.run_id="{run_id}"'
    )
    env = os.environ.copy()
    env["CLOUDSDK_CONFIG"] = GCLOUD_CONFIG
    result = subprocess.run(
        ["gcloud", "logging", "read", filt, f"--project={PROJECT}", "--limit=200", "--format=json"],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        return []
    return json.loads(result.stdout or "[]")


def analyze(run_id: str) -> None:
    entries = fetch_logs(run_id)
    entries.sort(key=lambda e: e.get("timestamp", ""))

    prompt = ""
    tool_seq: list[str] = []
    http_errors: list[str] = []
    http_calls = 0
    writes = 0
    task_status = "unknown"
    task_output = ""
    search_queries: list[str] = []

    for e in entries:
        jp = e.get("jsonPayload", {})
        event = jp.get("event", "")

        if event == "request_received":
            prompt = jp.get("prompt", "")[:200]
        elif event == "tool_call":
            tool = jp.get("tool", "")
            tool_seq.append(tool)
            if tool == "search_tripletex_reference":
                q = jp.get("arguments", {}).get("kwargs", {}).get("query", "")
                search_queries.append(q)
        elif event == "tripletex_http_response":
            http_calls += 1
            status = jp.get("status_code", 0)
            method = jp.get("method", "")
            path = jp.get("path", "")
            if method in ("POST", "PUT", "DELETE"):
                writes += 1
            if isinstance(status, int) and status >= 400:
                rb = jp.get("response_body", {})
                msg = ""
                if isinstance(rb, dict):
                    msg = str(rb.get("message", ""))[:120]
                http_errors.append(f"{method} {path} -> {status} {msg}")
        elif event == "task_complete":
            task_status = "COMPLETED"
            task_output = jp.get("output", "")[:200]
        elif event == "task_error":
            task_status = "ERROR"
            task_output = f'{jp.get("error_type","")}: {jp.get("error_message","")[:200]}'

    print(f"{'='*70}")
    print(f"RUN: {run_id}")
    print(f"Prompt: {prompt}")
    print(f"Status: {task_status}")
    print(f"Tools ({len(tool_seq)}): {' -> '.join(tool_seq)}")
    print(f"HTTP: {http_calls} calls, {writes} writes, {len(http_errors)} errors")
    if http_errors:
        for err in http_errors:
            print(f"  ERR: {err}")
    if search_queries:
        print(f"Searches ({len(search_queries)}):")
        for q in search_queries:
            print(f"  - {q}")
    print(f"Output: {task_output}")
    print()


for rid in RUN_IDS:
    analyze(rid)
