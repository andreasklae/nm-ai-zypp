import json
import sys
from collections import defaultdict
from datetime import datetime

try:
    with open('ai_agent_logs_latest.json', 'r') as f:
        logs = json.load(f)
except FileNotFoundError:
    print("Logs file not found.")
    sys.exit(1)

runs = defaultdict(list)
for entry in logs:
    if 'jsonPayload' in entry:
        run_id = entry['jsonPayload'].get('run_id')
        if run_id:
            runs[run_id].append(entry)

print(f"Total runs found: {len(runs)}")

run_summaries = []
for run_id, entries in runs.items():
    entries.sort(key=lambda x: x.get('timestamp', ''))
    start_time = entries[0].get('timestamp')
    
    # Filter for 1 AM and later (assuming UTC or Local, just show all for now and see the times)
    # 2026-03-22T01:..
    
    prompt = "Unknown"
    final_status = "Unknown"
    errors = []
    tool_calls = []
    
    for e in entries:
        jp = e.get('jsonPayload', {})
        event = jp.get('event')
        
        if event == 'request_received':
            prompt = jp.get('prompt', 'Unknown')
        elif event == 'task_complete':
            final_status = 'Success'
        elif event == 'task_error':
            final_status = f"Error: {jp.get('error', 'Unknown')}"
        elif event == 'tool_call':
            tool_calls.append(jp.get('tool', 'Unknown'))
        elif event == 'tool_error':
            errors.append(f"Tool Error ({jp.get('tool', 'Unknown')}): {jp.get('error_message', jp.get('error'))}")
        elif event == 'tripletex_http_response':
            status = jp.get('status_code', 0)
            if status >= 400:
                body = jp.get('response_body')
                method = jp.get('method')
                path = jp.get('path')
                msgs = []
                if isinstance(body, dict) and 'validationMessages' in body and body['validationMessages']:
                    msgs = [f"{vm.get('field')}: {vm.get('message')}" for vm in body['validationMessages']]
                elif isinstance(body, dict) and 'message' in body:
                    msgs = [body['message']]
                
                err_str = f"HTTP {status} on {method} {path}"
                if msgs:
                    err_str += " - " + ", ".join(msgs)
                errors.append(err_str)

    run_summaries.append((start_time, run_id, prompt, final_status, tool_calls, errors))

# Sort chronologically
run_summaries.sort(key=lambda x: x[0])

for start_time, run_id, prompt, status, tools, errors in run_summaries:
    print(f"\n--- Run: {run_id} ---")
    print(f"Time: {start_time}")
    print(f"Prompt: {prompt}")
    print(f"Status: {status}")
    if tools:
        print(f"Tools ({len(tools)}): {' -> '.join(tools)}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for err in errors:
            print(f"  - {err}")
