import json

with open('ai_agent_logs.json', 'r') as f:
    logs = json.load(f)

count_req = 0
count_res = 0

for e in logs:
    jp = e.get('jsonPayload', {})
    if jp.get('run_id') == '37b354c63d874971bec0131fb55d3d6b':
        if jp.get('event') == 'tripletex_http_request' and 'employment' in jp.get('path', ''):
            count_req += 1
            print(f"REQ: {e.get('timestamp')}")
        if jp.get('event') == 'tripletex_http_response' and 'employment' in jp.get('path', ''):
            count_res += 1
            print(f"RES: {e.get('timestamp')}")

print(f"Reqs: {count_req}, Res: {count_res}")
