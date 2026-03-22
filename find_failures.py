import json
from collections import defaultdict
import datetime

try:
    with open('latest_runs.json', 'r') as f:
        logs = json.load(f)
except Exception as e:
    print("Failed to read logs:", e)
    exit(1)

runs = defaultdict(list)
for e in logs:
    jp = e.get('jsonPayload', {})
    if not jp: continue
    run_id = jp.get('run_id')
    if run_id:
        runs[run_id].append(e)

print(f"Found {len(runs)} total runs since 4 AM.")

# We define a failure as either generating a "task_error" event OR having unresolved HTTP errors OR just not completing properly.
run_summaries = []

for run_id, entries in runs.items():
    entries.sort(key=lambda x: x.get('timestamp', ''))
    start_time = entries[0].get('timestamp', '')
    
    prompt = "Unknown"
    status = "Incomplete"
    tools = []
    errors = []
    http_errors = []
    
    for e in entries:
        jp = e.get('jsonPayload', {})
        event = jp.get('event')
        
        if event == 'request_received':
            prompt = jp.get('prompt', 'Unknown')
        elif event == 'task_complete':
            status = 'Completed'
        elif event == 'task_error':
            status = f"TaskError: {jp.get('error', jp.get('error_type', 'Unknown'))}"
        elif event == 'tool_call':
            tools.append(jp.get('tool', jp.get('tool_name', 'Unknown')))
        elif event == 'tool_error':
            err_msg = jp.get('error_message') or jp.get('error')
            errors.append(f"{jp.get('tool', jp.get('tool_name', 'Unknown'))}: {err_msg}")
        elif event == 'tripletex_http_response':
            sc = jp.get('status_code', 0)
            if sc >= 400:
                body = jp.get('response_body', {})
                method = jp.get('method', '')
                path = jp.get('path', '')
                msg = ""
                if isinstance(body, dict):
                    if body.get('validationMessages'):
                        msg = " ".join([f"{v.get('field')}: {v.get('message')}" for v in body['validationMessages']])
                    elif body.get('message'):
                        msg = body['message']
                if not msg:
                    msg = str(body)[:100]
                http_errors.append(f"HTTP {sc} {method} {path} - {msg}")
                
    run_summaries.append({
        'run_id': run_id,
        'start_time': start_time,
        'prompt': prompt,
        'status': status,
        'tools': tools,
        'tool_errors': errors,
        'http_errors': http_errors
    })

# We'll print the ones that have errors or didn't complete successfully
failed_runs = [r for r in run_summaries if r['status'] != 'Completed' or r['tool_errors'] or r['http_errors']]

# Sort chronologically
failed_runs.sort(key=lambda x: x['start_time'])

print(f"\nFound {len(failed_runs)} runs with errors/issues:")

for r in failed_runs:
    print(f"\n--- Run: {r['run_id']} ---")
    print(f"Time: {r['start_time']}")
    print(f"Prompt: {r['prompt']}")
    print(f"Status: {r['status']}")
    print(f"Tools ({len(r['tools'])}): {' -> '.join(r['tools'])}")
    if r['tool_errors']:
        print("Tool Errors:")
        for err in r['tool_errors']:
            print(f"  - {err}")
    if r['http_errors']:
        print("HTTP Errors:")
        for err in r['http_errors']:
            print(f"  - {err}")

