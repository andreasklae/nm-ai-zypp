import json
with open('src/ai_accounting_agent/api_index_data.json', 'r') as f:
    data = json.load(f)

for op in data['customer']:
    if 'request_body' in op:
        print(op['method'])
        print(list(op['request_body'].keys()))
        print(list(op['request_body']['properties'].keys())[:5])
        break
