import json
import subprocess
import sys
import os

GCLOUD_CONFIG = "/Users/andreasklaeboe/repos/nm-ai-zypp/.gcloud"
PROJECT = "ai-nm26osl-1850"
SERVICE = "ai-accounting-agent"

run_id = sys.argv[1]

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
entries = json.loads(result.stdout or "[]")

for e in entries:
    jp = e.get("jsonPayload", {})
    if jp.get("event") == "request_received":
        print(jp.get("prompt"))
