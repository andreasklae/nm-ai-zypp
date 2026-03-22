import json
from pathlib import Path

base = Path("src/ai_accounting_agent/test_runs")

for file in base.rglob("cloud_logging.json"):
    with open(file, "r") as f:
        data = json.load(f)
        for entry in data.get("entries", []):
            if "jsonPayload" in entry:
                payload = entry["jsonPayload"]
                if "status_code" in payload and payload["status_code"] >= 400:
                    print(payload)
                if "error" in payload or payload.get("event") == "task_error":
                    print(payload)
            if "textPayload" in entry:
                if "ERROR" in entry["textPayload"] or "WARNING" in entry["textPayload"]:
                    print(entry["textPayload"])
