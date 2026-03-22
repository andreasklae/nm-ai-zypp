import json
import subprocess
from collections import defaultdict

print("Querying logs...")

cmd = """CLOUDSDK_CONFIG=.gcloud gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="ai-accounting-agent" AND timestamp>="2026-03-21T20:38:00Z"' --project=ai-nm26osl-1850 --limit=5000 --format=json"""

result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
if not result.stdout.strip():
    print("No logs found.")
    exit(0)

try:
    entries = json.loads(result.stdout)
    runs = defaultdict(list)
    for e in entries:
        payload = e.get("jsonPayload", {})
        run_id = payload.get("run_id")
        if run_id:
            runs[run_id].append(payload)

    for run_id, events in runs.items():
        request = next((e for e in events if e.get("event") == "request_received"), None)
        task_complete = next((e for e in events if e.get("event") == "task_complete"), None)
        task_error = next((e for e in events if e.get("event") == "task_error"), None)
        
        prompt = request.get("prompt", "Unknown prompt") if request else "Unknown prompt"
        
        # Check if it has failed tools
        tool_errors = [e for e in events if e.get("event") == "tool_error"]
        retry_decisions = [e for e in events if e.get("event") == "tripletex_retry_decision"]
        http_errors = [e for e in events if e.get("event") == "tripletex_http_response" and e.get("status_code", 0) >= 400]
        
        if tool_errors or retry_decisions or http_errors or task_error:
            print(f"\n--- Run ID: {run_id} ---")
            print(f"Prompt: {prompt}")
            
            if task_error:
                 print(f"FAILED: {task_error.get('error_type')} - {task_error.get('error_message')}")
            else:
                 print(f"COMPLETED (but had internal errors)")
                 
            for err in tool_errors:
                print(f"  Tool Error: {err.get('tool_name')} -> {err.get('error_type')}: {err.get('error_message')}")
                
            for retry in retry_decisions:
                print(f"  Retry Decision: {retry.get('operation')} -> {retry.get('retry_classification')} ({retry.get('status_code')})")
                print(f"  Hint: {retry.get('retry_hint')}")
                
            for http in http_errors:
                print(f"  HTTP Error: {http.get('method')} {http.get('path')} -> {http.get('status_code')}")
                resp_body = http.get("response_body", {})
                if isinstance(resp_body, dict):
                    print(f"    Messages: {resp_body.get('validationMessages') or resp_body.get('message')}")
                    
except Exception as e:
    print(f"Error parsing logs: {e}")

